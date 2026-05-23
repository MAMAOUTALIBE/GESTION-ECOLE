"""Redis-backed rate limiter — fixed window counters.

Used by the auth module to throttle:
* Login attempts per email (5 / 15 min) and per IP (20 / 15 min)
* MFA verification attempts per user (10 / 15 min)
* Password reset requests per email + per IP (3 / hour)

Implementation notes
--------------------
* Single round-trip via a Redis pipeline: INCR + EXPIRE NX.
  EXPIRE NX (Redis 7+) ensures the TTL is only set on the first increment,
  so the window starts at the first attempt and the counter naturally
  resets after `window_seconds`.
* When Redis is unavailable, we **fail open** (allowed=True, count=0) and
  log a warning — better than locking out legitimate users during a
  Redis outage. Authentication itself remains protected by Argon2 cost.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from loguru import logger
from redis.asyncio import Redis

# Default tuning. Override per-call where needed.
LOGIN_EMAIL_LIMIT: Final = 5
LOGIN_EMAIL_WINDOW_S: Final = 15 * 60

LOGIN_IP_LIMIT: Final = 20
LOGIN_IP_WINDOW_S: Final = 15 * 60

MFA_LIMIT: Final = 10
MFA_WINDOW_S: Final = 15 * 60

PASSWORD_RESET_EMAIL_LIMIT: Final = 3
PASSWORD_RESET_EMAIL_WINDOW_S: Final = 60 * 60

PASSWORD_RESET_IP_LIMIT: Final = 10
PASSWORD_RESET_IP_WINDOW_S: Final = 60 * 60


@dataclass(slots=True, frozen=True)
class RateLimitResult:
    allowed: bool
    current: int
    limit: int
    window_seconds: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.current)


class RateLimiter:
    """Thin wrapper around `redis.asyncio.Redis` exposing fixed-window counts."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check_and_increment(
        self, key: str, limit: int, window_seconds: int
    ) -> RateLimitResult:
        """Increment the counter and report whether the call is allowed.

        Returns `allowed=False` *after* the limit is exceeded — i.e. the call
        that pushes the counter strictly above `limit` is the first rejected.
        """
        full_key = f"rl:{key}"
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.incr(full_key, 1)
                # EXPIRE NX = only set if no TTL yet => sliding from first hit.
                pipe.expire(full_key, window_seconds, nx=True)
                results = await pipe.execute()
            current = int(results[0])
        except Exception as exc:  # pragma: no cover - depends on Redis state
            logger.warning("rate_limit: Redis unavailable ({}), failing open", exc)
            return RateLimitResult(True, 0, limit, window_seconds)
        return RateLimitResult(
            allowed=current <= limit,
            current=current,
            limit=limit,
            window_seconds=window_seconds,
        )

    async def reset(self, key: str) -> None:
        """Clear the counter — called after a successful login to forgive past
        failed attempts on the same email.
        """
        try:
            await self._redis.delete(f"rl:{key}")
        except Exception as exc:  # pragma: no cover
            logger.warning("rate_limit: reset failed ({})", exc)


# ---------------------------------------------------------------------------
# Helpers — each returns the RateLimitResult so the router can decide to
# raise `RateLimitedError` and still write a single AuditLog entry.
# ---------------------------------------------------------------------------
def _norm_email(email: str) -> str:
    return email.strip().lower()


async def check_login_attempt(
    redis: Redis, email: str, ip: str
) -> tuple[RateLimitResult, RateLimitResult]:
    """Increment the per-email AND per-IP counters in one shot.

    Returns `(by_email, by_ip)` so the caller can choose which limit was
    hit first when reporting the failure.
    """
    limiter = RateLimiter(redis)
    by_email = await limiter.check_and_increment(
        f"login:email:{_norm_email(email)}",
        LOGIN_EMAIL_LIMIT,
        LOGIN_EMAIL_WINDOW_S,
    )
    by_ip = await limiter.check_and_increment(
        f"login:ip:{ip}",
        LOGIN_IP_LIMIT,
        LOGIN_IP_WINDOW_S,
    )
    return by_email, by_ip


async def reset_login_counters(redis: Redis, email: str, ip: str) -> None:
    limiter = RateLimiter(redis)
    await limiter.reset(f"login:email:{_norm_email(email)}")
    await limiter.reset(f"login:ip:{ip}")


async def check_mfa_attempt(redis: Redis, user_id: str) -> RateLimitResult:
    limiter = RateLimiter(redis)
    return await limiter.check_and_increment(
        f"mfa:user:{user_id}", MFA_LIMIT, MFA_WINDOW_S
    )


async def reset_mfa_counter(redis: Redis, user_id: str) -> None:
    limiter = RateLimiter(redis)
    await limiter.reset(f"mfa:user:{user_id}")


async def check_password_reset_request(
    redis: Redis, email: str, ip: str
) -> tuple[RateLimitResult, RateLimitResult]:
    limiter = RateLimiter(redis)
    by_email = await limiter.check_and_increment(
        f"pwreset:email:{_norm_email(email)}",
        PASSWORD_RESET_EMAIL_LIMIT,
        PASSWORD_RESET_EMAIL_WINDOW_S,
    )
    by_ip = await limiter.check_and_increment(
        f"pwreset:ip:{ip}",
        PASSWORD_RESET_IP_LIMIT,
        PASSWORD_RESET_IP_WINDOW_S,
    )
    return by_email, by_ip
