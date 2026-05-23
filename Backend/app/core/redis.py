from typing import Any

from redis.asyncio import Redis, from_url

from app.core.config import settings

_redis: Redis | None = None


def get_redis() -> Redis:
    """Singleton async Redis client."""
    global _redis
    if _redis is None:
        _redis = from_url(
            str(settings.redis_url),
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
    return _redis


async def close_redis() -> None:
    """Called on FastAPI shutdown."""
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None


async def healthcheck() -> dict[str, Any]:
    try:
        client = get_redis()
        pong = await client.ping()
        return {"redis": "ok" if pong else "error"}
    except Exception as e:
        return {"redis": "error", "detail": str(e)}
