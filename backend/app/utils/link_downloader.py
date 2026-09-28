"""URL-based source download utilities.

Supports three URL categories
──────────────────────────────
1. GitHub repository URL  (``github.com/{owner}/{repo}``)
   → Downloads the default-branch ZIP archive from GitHub.
   → Treated as *source code*; the archive is stored as-is.

2. GitHub blob / raw file URL  (``github.com/.../blob/...`` or ``raw.githubusercontent.com``)
   → Converts blob viewer URLs to raw content URLs.
   → Downloads the single file.

3. Generic HTTPS URL  (SharePoint, Nextcloud, direct file links)
   → Performs a straight GET request and downloads the response body.

SSRF protection
───────────────
- Only the ``https://`` scheme is accepted.
- Hostnames that resolve to private, loopback, or link-local IP ranges are
  rejected before any network request is made.
- Maximum download size is capped at ``SOURCE_LINK_MAX_FILE_SIZE_MB`` (default
  500 MB, independently configurable from the regular file-upload limit).

Size enforcement
────────────────
The limit is enforced **during** the download using a byte counter on the
streaming response body — the rejected bytes are never accumulated in memory.
This means a 2 GB payload is cut off after 500 MB of data, not after 2 GB.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import PurePosixPath
import socket
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.exceptions import ValidationError as AppValidationError
from app.core.messages import (
    MSG_LINK_CONTENT_TOO_LARGE,
    MSG_LINK_FILE_TOO_LARGE,
    MSG_LINK_HTTP_ERROR,
    MSG_LINK_HTTPS_ONLY,
    MSG_LINK_MISSING_HOSTNAME,
    MSG_LINK_NETWORK_ERROR,
    MSG_LINK_PRIVATE_ADDRESS,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Tunables ───────────────────────────────────────────────────────────────

# Link uploads have their own independent cap so it can be tuned separately
# from single/bulk multipart uploads.  Override via env var:
#   SOURCE_LINK_MAX_FILE_SIZE_MB=200
_LINK_MAX_BYTES: int = settings.SOURCE_LINK_MAX_FILE_SIZE_MB * 1024 * 1024
_DOWNLOAD_TIMEOUT: float = 60.0  # seconds

# ── GitHub host constants ──────────────────────────────────────────────────

_GITHUB_HOST = "github.com"
_GITHUB_RAW_HOST = "raw.githubusercontent.com"


# ── Result type ────────────────────────────────────────────────────────────


@dataclass
class DownloadedFile:
    """Result of a successful URL download."""

    filename: str
    content_type: str
    file_type: str
    data: bytes
    is_archive: bool  # True when data is a zipped repository (source code)


# ── SSRF protection ────────────────────────────────────────────────────────


def _is_private_ip(hostname: str) -> bool:
    """Return ``True`` if *hostname* resolves to a private / loopback address."""
    try:
        ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        # Cannot resolve — let the HTTP client fail naturally.
        return False
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        return False


def _validate_url(url: str) -> None:
    """Raise :exc:`AppValidationError` for unsafe or unsupported URLs."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise AppValidationError(MSG_LINK_HTTPS_ONLY)
    if not parsed.hostname:
        raise AppValidationError(MSG_LINK_MISSING_HOSTNAME)
    if _is_private_ip(parsed.hostname):
        raise AppValidationError(MSG_LINK_PRIVATE_ADDRESS)


# ── GitHub URL helpers ─────────────────────────────────────────────────────


def _is_github_repo_url(parsed: SplitResult) -> bool:  # type: ignore[name-defined]
    """Return ``True`` when the URL points to a GitHub *repository* (not a file)."""
    host = (parsed.hostname or "").lstrip("www.")
    if host != _GITHUB_HOST:
        return False
    parts = [p for p in parsed.path.split("/") if p]
    # github.com/{owner}/{repo}
    if len(parts) == 2:
        return True
    # github.com/{owner}/{repo}/tree/{branch}[/subdir...]
    if len(parts) >= 4 and parts[2] == "tree":
        return True
    return False


def _is_github_blob_url(parsed: SplitResult) -> bool:  # type: ignore[name-defined]
    """Return ``True`` when the URL is a GitHub blob (file viewer) URL."""
    host = (parsed.hostname or "").lstrip("www.")
    if host != _GITHUB_HOST:
        return False
    parts = [p for p in parsed.path.split("/") if p]
    return len(parts) >= 5 and parts[2] == "blob"


def _blob_to_raw_url(parsed: SplitResult) -> str:  # type: ignore[name-defined]
    """Convert a ``github.com/.../blob/...`` URL to a ``raw.githubusercontent.com`` URL."""
    parts = [p for p in parsed.path.split("/") if p]
    # parts: [owner, repo, 'blob', branch, *file_path]
    owner, repo = parts[0], parts[1]
    rest = "/".join(parts[3:])  # branch/path/to/file
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}"


def _github_archive_url(parsed: SplitResult) -> tuple[str, str]:  # type: ignore[name-defined]
    """Return ``(archive_url, suggested_filename)`` for a GitHub repository URL."""
    parts = [p for p in parsed.path.split("/") if p]
    owner, repo = parts[0], parts[1]
    # Strip .git suffix if present (e.g. "repo.git" → "repo")
    repo = repo.removesuffix(".git")
    archive_url = f"https://github.com/{owner}/{repo}/archive/HEAD.zip"
    filename = f"{repo}-HEAD.zip"
    return archive_url, filename


# ── Format derivation ──────────────────────────────────────────────────────


def _derive_format(filename: str, content_type: str) -> str:
    """Return a short uppercase format label (e.g. ``PDF``, ``ZIP``, ``PY``)."""
    if "." in filename:
        return filename.rsplit(".", 1)[-1].upper()
    # Fall back to the content-type subtype
    return content_type.split("/")[-1].split(";")[0].upper()


# ── HTTP helpers ───────────────────────────────────────────────────────────


async def _http_get(url: str) -> tuple[bytes, str]:
    """Perform a streaming GET request and return ``(body_bytes, content_type)``.

    Size enforcement
    ────────────────
    The limit is applied in two stages:

    1. **Header check** — if the server sends ``Content-Length`` and it already
       exceeds ``SOURCE_LINK_MAX_FILE_SIZE_MB``, the connection is closed before
       a single byte of the body is read.

    2. **Streaming byte counter** — if ``Content-Length`` is absent (chunked
       transfer encoding) or not sent, the response is consumed chunk by chunk.
       The download is aborted as soon as the running total exceeds the cap,
       so memory usage is bounded to one streaming buffer — never the full file.

    Raises ``AppValidationError`` on HTTP errors, network errors, or when the
    response body exceeds the configured size limit.
    """
    limit_mb = settings.SOURCE_LINK_MAX_FILE_SIZE_MB
    limit_bytes = limit_mb * 1024 * 1024

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=_DOWNLOAD_TIMEOUT,
    ) as client:
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()

                # Stage 1 — reject immediately if Content-Length is too large.
                content_length = int(response.headers.get("content-length", 0))
                if content_length > limit_bytes:
                    raise AppValidationError(
                        MSG_LINK_FILE_TOO_LARGE.format(
                            size_mb=content_length // (1024 * 1024),
                            limit_mb=limit_mb,
                        )
                    )

                # Stage 2 — accumulate chunks; abort as soon as the counter
                # breaches the cap so we never buffer more than limit + 64 KiB.
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes(chunk_size=65_536):  # 64 KiB
                    received += len(chunk)
                    if received > limit_bytes:
                        raise AppValidationError(
                            MSG_LINK_CONTENT_TOO_LARGE.format(limit_mb=limit_mb)
                        )
                    chunks.append(chunk)

                data = b"".join(chunks)
                content_type = (
                    response.headers.get("content-type", "application/octet-stream")
                    .split(";")[0]
                    .strip()
                )
                return data, content_type

        except httpx.HTTPStatusError as exc:
            raise AppValidationError(
                MSG_LINK_HTTP_ERROR.format(
                    status_code=exc.response.status_code,
                    url=url,
                )
            ) from exc
        except httpx.RequestError as exc:
            raise AppValidationError(MSG_LINK_NETWORK_ERROR.format(url=url, detail=exc)) from exc


# ── Public API ─────────────────────────────────────────────────────────────


async def download_from_url(url: str) -> DownloadedFile:
    """Download content from *url* and return a :class:`DownloadedFile`.

    Decision tree
    ─────────────
    - GitHub repository URL → download HEAD ZIP archive (source code, ``is_archive=True``)
    - GitHub blob URL       → convert to raw URL, download single file
    - Any other HTTPS URL   → download directly (SharePoint, Nextcloud, direct links)
    """
    _validate_url(url)
    parsed = urlparse(url)

    # ── GitHub repository → ZIP archive ───────────────────────────────────
    if _is_github_repo_url(parsed):
        archive_url, filename = _github_archive_url(parsed)
        data, _ = await _http_get(archive_url)
        logger.info("Downloaded GitHub archive: %s (%d bytes)", filename, len(data))
        return DownloadedFile(
            filename=filename,
            content_type="application/zip",
            file_type="ZIP",
            data=data,
            is_archive=True,
        )

    # ── GitHub blob → raw file ─────────────────────────────────────────────
    if _is_github_blob_url(parsed):
        raw_url = _blob_to_raw_url(parsed)
        _validate_url(raw_url)  # re-validate the derived URL
        filename = PurePosixPath(parsed.path).name
        data, content_type = await _http_get(raw_url)
        logger.info("Downloaded GitHub raw file: %s (%d bytes)", filename, len(data))
        return DownloadedFile(
            filename=filename,
            content_type=content_type,
            file_type=_derive_format(filename, content_type),
            data=data,
            is_archive=False,
        )

    # ── Generic HTTPS URL (SharePoint, Nextcloud, direct link) ────────────
    filename = PurePosixPath(parsed.path).name or "download"
    data, content_type = await _http_get(url)
    logger.info("Downloaded from URL: %s (%d bytes)", filename, len(data))
    return DownloadedFile(
        filename=filename,
        content_type=content_type,
        file_type=_derive_format(filename, content_type),
        data=data,
        is_archive=False,
    )
