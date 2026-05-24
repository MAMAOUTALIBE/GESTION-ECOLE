"""S3 / MinIO storage helper for the reports module.

We rely on the synchronous ``boto3`` client (already a dependency) but expose
an *async-friendly* surface via ``asyncio.to_thread`` so handlers don't block
the event loop on network I/O. This keeps the dependency tree minimal (no
need for ``aioboto3``) while still letting FastAPI handlers ``await`` upload
and presign operations safely.

Design choices
--------------
* **Singleton client**: ``boto3.client('s3', ...)`` is heavy (loads endpoint
  resolvers, signers). We cache one per process via ``lru_cache``.
* **Idempotent bucket creation**: ``head_bucket`` → 404 → ``create_bucket``.
  Done lazily on first write so dev / test environments without a pre-seeded
  bucket still work.
* **Presigned URLs**: 1h TTL by default. Tunable per call.
* **Error semantics**: caller-facing errors raise ``S3Error`` (a thin wrapper
  around boto3 exceptions); the worker / service layer catches it and decides
  whether to retry.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings


class S3Error(RuntimeError):
    """Raised when an S3 operation fails for a non-recoverable reason.

    The worker treats this as a *transient* error (retried with backoff) when
    the underlying ``ClientError`` carries a 5xx response, and as *permanent*
    otherwise — see ``app.workers.pdf_tasks``.
    """


@lru_cache(maxsize=1)
def _client() -> BaseClient:
    """Return the singleton boto3 S3 client."""
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def _bucket() -> str:
    return settings.s3_bucket_reports


def reset_client_cache() -> None:
    """Drop the cached client — used by tests that monkey-patch boto3."""
    _client.cache_clear()


# ---------------------------------------------------------------------------
# Sync primitives (used by the Celery worker)
# ---------------------------------------------------------------------------
def ensure_bucket_sync() -> None:
    """Create the bucket if it doesn't exist. Idempotent. Synchronous."""
    client = _client()
    try:
        client.head_bucket(Bucket=_bucket())
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchBucket", "NotFound"}:
            client.create_bucket(Bucket=_bucket())
        else:
            raise S3Error(f"head_bucket failed: {exc}") from exc
    except BotoCoreError as exc:
        raise S3Error(f"head_bucket failed: {exc}") from exc


def upload_pdf_sync(
    key: str, pdf_bytes: bytes, metadata: dict[str, str] | None = None
) -> str:
    """Upload bytes to ``s3://<bucket>/<key>``. Synchronous version.

    Returns the canonical ``s3://`` URI for the uploaded object.
    """
    ensure_bucket_sync()
    client = _client()
    try:
        client.put_object(
            Bucket=_bucket(),
            Key=key,
            Body=pdf_bytes,
            ContentType="application/pdf",
            Metadata=metadata or {},
        )
    except (BotoCoreError, ClientError) as exc:
        raise S3Error(f"upload_pdf failed for {key}: {exc}") from exc
    return f"s3://{_bucket()}/{key}"


def get_presigned_url_sync(key: str, expires: int = 3600) -> str:
    """Generate a presigned GET URL valid for ``expires`` seconds (default 1h)."""
    client = _client()
    try:
        url: str = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": _bucket(), "Key": key},
            ExpiresIn=expires,
        )
    except (BotoCoreError, ClientError) as exc:
        raise S3Error(f"presign failed for {key}: {exc}") from exc
    return url


def head_object_sync(key: str) -> dict[str, Any] | None:
    """Return object metadata or ``None`` if it doesn't exist."""
    client = _client()
    try:
        return client.head_object(Bucket=_bucket(), Key=key)  # type: ignore[no-any-return]
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise S3Error(f"head_object failed for {key}: {exc}") from exc
    except BotoCoreError as exc:
        raise S3Error(f"head_object failed for {key}: {exc}") from exc


# ---------------------------------------------------------------------------
# Async wrappers (used by FastAPI handlers)
# ---------------------------------------------------------------------------
async def upload_pdf(
    key: str, pdf_bytes: bytes, metadata: dict[str, str] | None = None
) -> str:
    """Async wrapper around :func:`upload_pdf_sync`. Returns ``s3://`` URI."""
    return await asyncio.to_thread(upload_pdf_sync, key, pdf_bytes, metadata)


async def get_presigned_url(key: str, expires: int = 3600) -> str:
    """Async wrapper around :func:`get_presigned_url_sync`."""
    return await asyncio.to_thread(get_presigned_url_sync, key, expires)


async def head_object(key: str) -> dict[str, Any] | None:
    """Async wrapper around :func:`head_object_sync`."""
    return await asyncio.to_thread(head_object_sync, key)


# ---------------------------------------------------------------------------
# Key helper — canonical layout
# ---------------------------------------------------------------------------
def bulletin_key(school_id: str, period_id: str, student_id: str) -> str:
    """Canonical S3 key for a single bulletin PDF.

    Layout chosen to keep S3 listings cheap when narrowing by school + period
    (e.g. for an end-of-trimester re-download CLI). The exact pattern is
    enforced by ``test_worker_uploads_to_correct_s3_key_pattern``.
    """
    return f"bulletins/{school_id}/{period_id}/{student_id}.pdf"
