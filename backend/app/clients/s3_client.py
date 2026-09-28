"""AWS S3 client — wraps aioboto3 for all S3 I/O.

Responsibilities
────────────────
- Upload a file binary to the configured S3 bucket.
- Generate a pre-signed GET URL so the caller can serve the file.
- Stream-download an S3 object to a local path in configurable chunks.
- Delete an object (used by hard-delete if ever needed).
- Derive deterministic, path-safe S3 object keys.
- Compute SHA-256 checksums for dedup / integrity checking.

Placement rationale
───────────────────
This module belongs in ``app/clients/`` because it owns a stateful external
service session (``aioboto3.Session``) and all I/O crosses a network boundary
to AWS.
Pure utilities with no external I/O live in ``app/utils/`` instead.

All operations are async (aioboto3) so they never block the FastAPI
event loop.  Errors are wrapped in ``app.core.exceptions`` so the
global handler can convert them to standardised error responses.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

import aiofiles

from app.clients.aws_session import get_aws_session
from app.core.config import settings
from app.core.exceptions import StorageError
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Use the shared session — never construct aioboto3.Session() locally.
_SESSION = get_aws_session()


# ── Key helpers ────────────────────────────────────────────────────────────


def _sanitize_relative_path(relative_path: str) -> str:
    """Normalize a client-provided relative path to a safe POSIX path."""
    raw_parts = relative_path.replace("\\", "/").split("/")
    safe_parts = [part for part in raw_parts if part not in {"", ".", ".."}]
    return "/".join(safe_parts)


def _derive_s3_key(
    project_id: str,
    source_id: str,
    original_name: str,
    relative_path: str | None = None,
) -> str:
    """Build a deterministic, safe S3 object key.

    Format:  sources/{project_id}/{source_id}_{original_name}

    The ``source_id`` prefix guarantees uniqueness even when two files share
    the same name within a project.
    """
    if relative_path:
        safe_relative_path = _sanitize_relative_path(relative_path)
        if safe_relative_path:
            return f"sources/{project_id}/{source_id}/{safe_relative_path}"

    safe_name = PurePosixPath(original_name).name  # strip any path components
    return f"sources/{project_id}/{source_id}_{safe_name}"


def derive_s3_key(
    project_id: str,
    source_id: str,
    original_name: str,
    relative_path: str | None = None,
) -> str:
    """Public alias for import convenience."""
    return _derive_s3_key(project_id, source_id, original_name, relative_path)


# ── Checksum ───────────────────────────────────────────────────────────────


def compute_sha256(data: bytes) -> str:
    """Return the hex-encoded SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


# ── Upload ─────────────────────────────────────────────────────────────────


async def upload_to_s3(
    *,
    file_bytes: bytes,
    object_key: str,
    content_type: str,
) -> str:
    """Upload *file_bytes* to S3 under *object_key*.

    Returns the object key on success.
    Raises ``StorageError`` on any AWS / network failure.
    """
    try:
        async with _SESSION.client("s3") as s3:
            await s3.put_object(
                Bucket=settings.AWS_S3_SOURCES_BUCKET,
                Key=object_key,
                Body=file_bytes,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
        logger.info("S3 upload complete: key=%s size=%d", object_key, len(file_bytes))
        return object_key
    except StorageError:
        raise  # already wrapped — do not double-wrap
    except Exception as exc:
        logger.error("S3 upload failed: key=%s error=%s", object_key, exc, exc_info=True)
        raise StorageError(f"Upload failed for '{object_key}': {exc}") from exc


# ── Pre-signed URL ─────────────────────────────────────────────────────────


async def generate_presigned_url(object_key: str) -> str:
    """Return a pre-signed GET URL valid for ``AWS_S3_PRESIGNED_URL_EXPIRY`` seconds.

    Raises ``StorageError`` on any AWS / network failure.
    """
    try:
        async with _SESSION.client("s3") as s3:
            url: str = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.AWS_S3_SOURCES_BUCKET, "Key": object_key},
                ExpiresIn=settings.AWS_S3_PRESIGNED_URL_EXPIRY,
            )
        return url
    except StorageError:
        raise
    except Exception as exc:
        logger.error(
            "Pre-signed URL generation failed: key=%s error=%s", object_key, exc, exc_info=True
        )
        raise StorageError(f"Could not generate download URL for '{object_key}': {exc}") from exc


# ── Delete ─────────────────────────────────────────────────────────────────


async def delete_from_s3(object_key: str) -> None:
    """Delete an object from S3 (used when deleting a source or compensating a failed write).

    Raises ``StorageError`` on any AWS / network failure.
    """
    try:
        async with _SESSION.client("s3") as s3:
            await s3.delete_object(
                Bucket=settings.AWS_S3_SOURCES_BUCKET,
                Key=object_key,
            )
        logger.info("S3 object deleted: key=%s", object_key)
    except StorageError:
        raise
    except Exception as exc:
        logger.error("S3 delete failed: key=%s error=%s", object_key, exc, exc_info=True)
        raise StorageError(f"Delete failed for '{object_key}': {exc}") from exc


# ── Stream (no local disk write) ──────────────────────────────────────────


async def iter_s3_chunks(object_key: str):
    """Async generator that streams an S3 object as byte chunks.

    Yields successive chunks of ``S3_DOWNLOAD_CHUNK_SIZE_MB`` megabytes
    directly from S3 without writing any data to local disk.  The aioboto3
    client context is kept open for the entire duration of the generator so
    the connection is closed cleanly once the last chunk is consumed.

    Any S3 exception is intentionally allowed to bubble up to the centralized
    exception handlers.
    """
    chunk_size = settings.S3_DOWNLOAD_CHUNK_SIZE_MB * 1024 * 1024
    async with _SESSION.client("s3") as s3:
        response = await s3.get_object(
            Bucket=settings.AWS_S3_SOURCES_BUCKET,
            Key=object_key,
        )
        stream = response["Body"]
        while True:
            chunk = await stream.read(chunk_size)
            if not chunk:
                break
            yield chunk


# ── Download to local file (retained for internal/batch use) ───────────────


async def download_from_s3(object_key: str, dest_path: Path) -> str:
    """Stream an S3 object directly to *dest_path* in configurable chunks.

    Chunk size is controlled by ``settings.S3_DOWNLOAD_CHUNK_SIZE_MB``.

    Returns the content-type reported by S3.
    Raises ``StorageError`` on any AWS / network failure.
    """
    chunk_size = settings.S3_DOWNLOAD_CHUNK_SIZE_MB * 1024 * 1024
    try:
        async with _SESSION.client("s3") as s3:
            response = await s3.get_object(
                Bucket=settings.AWS_S3_SOURCES_BUCKET,
                Key=object_key,
            )
            content_type: str = response.get("ContentType", "application/octet-stream")
            stream = response["Body"]
            async with aiofiles.open(dest_path, "wb") as f:
                while True:
                    chunk = await stream.read(chunk_size)
                    if not chunk:
                        break
                    await f.write(chunk)
        logger.info("S3 download complete: key=%s dest=%s", object_key, dest_path)
        return content_type
    except StorageError:
        raise
    except Exception as exc:
        logger.error("S3 download failed: key=%s error=%s", object_key, exc, exc_info=True)
        raise StorageError(f"Download failed for '{object_key}': {exc}") from exc
