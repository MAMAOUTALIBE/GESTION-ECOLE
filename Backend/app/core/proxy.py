"""Client-IP resolution that is safe in front of reverse proxies.

Security fix C-4 — without this helper, the auth router pulled the IP
directly from ``request.client.host`` which, in production behind nginx /
ALB / Cloudflare, is always the *proxy* IP. The per-IP rate limit then
buckets every user under one key and is trivially defeated.

The fix is conservative: we only trust ``X-Forwarded-For`` when the
*immediate* peer (``request.client.host``) belongs to one of the operator-
declared CIDRs in ``settings.trusted_proxies``. In development the env var
is empty, so we never read XFF and an attacker cannot spoof their IP by
sending the header themselves.

Format reminders
----------------
* ``X-Forwarded-For`` is a comma-separated list, leftmost = original client
  (RFC 7239 §5.2 / MDN). We take the leftmost value and strip whitespace.
* ``settings.trusted_proxies`` is a comma-separated list of CIDRs, e.g.
  ``"10.0.0.0/8,172.16.0.0/12"``. Empty disables XFF parsing entirely.
"""
from __future__ import annotations

from functools import lru_cache
from ipaddress import (
    IPv4Network,
    IPv6Network,
    ip_address,
    ip_network,
)

from fastapi import Request
from loguru import logger

from app.core.config import settings

_XFF_HEADER = "X-Forwarded-For"


@lru_cache(maxsize=1)
def _parsed_trusted_proxies() -> tuple[IPv4Network | IPv6Network, ...]:
    """Parse ``settings.trusted_proxies`` once and cache.

    A bad CIDR is *logged and skipped* rather than crashing the app at
    request time — but logged loudly so ops notice in Loki.
    """
    raw = (settings.trusted_proxies or "").strip()
    if not raw:
        return ()
    nets: list[IPv4Network | IPv6Network] = []
    for chunk in raw.split(","):
        cidr = chunk.strip()
        if not cidr:
            continue
        try:
            nets.append(ip_network(cidr, strict=False))
        except ValueError as exc:
            logger.error(
                "proxy: invalid CIDR in TRUSTED_PROXIES ({!r}): {}", cidr, exc
            )
    return tuple(nets)


def _peer_in_trusted_nets(peer: str) -> bool:
    """True iff `peer` (an IP literal) is inside any trusted CIDR."""
    try:
        addr = ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _parsed_trusted_proxies())


def client_ip(request: Request) -> str | None:
    """Return the originating client IP, respecting trusted proxies.

    Rules:
    1. If ``TRUSTED_PROXIES`` is empty OR the immediate peer is NOT in any
       trusted CIDR, return ``request.client.host`` unchanged. This is
       fail-safe: an attacker on the open internet cannot spoof an IP by
       sending an ``X-Forwarded-For`` header.
    2. If the peer IS trusted, read the leftmost token of ``X-Forwarded-For``
       (stripped). If that token is a valid IP literal, return it.
    3. Fall back to ``request.client.host`` if anything is malformed.
    """
    peer = request.client.host if request.client else None
    nets = _parsed_trusted_proxies()
    if not nets or peer is None or not _peer_in_trusted_nets(peer):
        return peer

    xff = request.headers.get(_XFF_HEADER)
    if not xff:
        return peer
    # Leftmost token = original client per RFC 7239 §5.2 (and de-facto XFF).
    first = xff.split(",", 1)[0].strip()
    if not first:
        return peer
    try:
        ip_address(first)
    except ValueError:
        logger.warning("proxy: XFF leftmost token is not a valid IP: {!r}", first)
        return peer
    return first


def reset_trusted_proxies_cache() -> None:
    """Test helper — clear the lru_cache after monkeypatching ``settings``."""
    _parsed_trusted_proxies.cache_clear()


__all__ = ["client_ip", "reset_trusted_proxies_cache"]
