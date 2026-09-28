"""Unit tests for app/utils/s3_storage.py."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from app.clients.s3_client import (
    _derive_s3_key,
    compute_sha256,
    delete_from_s3,
    derive_s3_key,
    download_from_s3,
    generate_presigned_url,
    upload_to_s3,
)
from app.core.exceptions import StorageError

# ── Pure helpers ───────────────────────────────────────────────────────────


class TestDeriveS3Key:
    def test_basic_key(self):
        key = _derive_s3_key("proj-1", "src-1", "document.pdf")
        assert key == "sources/proj-1/src-1_document.pdf"

    def test_strips_directory_components(self):
        key = _derive_s3_key("p", "s", "../../etc/passwd")
        assert key == "sources/p/s_passwd"

    def test_public_alias_matches(self):
        assert derive_s3_key("p", "s", "f.txt") == _derive_s3_key("p", "s", "f.txt")


class TestComputeSha256:
    def test_correct_hash(self):
        data = b"hello world"
        expected = hashlib.sha256(data).hexdigest()
        assert compute_sha256(data) == expected

    def test_empty_bytes(self):
        expected = hashlib.sha256(b"").hexdigest()
        assert compute_sha256(b"") == expected


# ── upload_to_s3 ───────────────────────────────────────────────────────────


class TestUploadToS3:
    @pytest.mark.asyncio
    @patch("app.clients.s3_client._SESSION")
    async def test_upload_success(self, mock_session):
        mock_s3 = AsyncMock()
        mock_s3.put_object = AsyncMock()
        # Make the context manager return mock_s3
        mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await upload_to_s3(
            file_bytes=b"data",
            object_key="sources/p/file.pdf",
            content_type="application/pdf",
        )

        assert result == "sources/p/file.pdf"
        mock_s3.put_object.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.clients.s3_client._SESSION")
    async def test_upload_failure_raises_validation_error(self, mock_session):
        mock_s3 = AsyncMock()
        mock_s3.put_object = AsyncMock(side_effect=RuntimeError("S3 unavailable"))
        mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(StorageError, match="Upload failed"):
            await upload_to_s3(
                file_bytes=b"data",
                object_key="key",
                content_type="text/plain",
            )


# ── generate_presigned_url ─────────────────────────────────────────────────


class TestGeneratePresignedUrl:
    @pytest.mark.asyncio
    @patch("app.clients.s3_client._SESSION")
    async def test_presigned_url_success(self, mock_session):
        mock_s3 = AsyncMock()
        mock_s3.generate_presigned_url = AsyncMock(
            return_value="https://bucket.s3.amazonaws.com/key?Signature=..."
        )
        mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)

        url = await generate_presigned_url("sources/p/file.pdf")

        assert url.startswith("https://")
        mock_s3.generate_presigned_url.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.clients.s3_client._SESSION")
    async def test_presigned_url_failure(self, mock_session):
        mock_s3 = AsyncMock()
        mock_s3.generate_presigned_url = AsyncMock(side_effect=RuntimeError("access denied"))
        mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(StorageError, match="Could not generate"):
            await generate_presigned_url("key")


# ── delete_from_s3 ─────────────────────────────────────────────────────────


class TestDeleteFromS3:
    @pytest.mark.asyncio
    @patch("app.clients.s3_client._SESSION")
    async def test_delete_success(self, mock_session):
        mock_s3 = AsyncMock()
        mock_s3.delete_object = AsyncMock()
        mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)

        await delete_from_s3("sources/p/file.pdf")

        mock_s3.delete_object.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.clients.s3_client._SESSION")
    async def test_delete_failure(self, mock_session):
        mock_s3 = AsyncMock()
        mock_s3.delete_object = AsyncMock(side_effect=RuntimeError("fail"))
        mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(StorageError, match="Delete failed"):
            await delete_from_s3("key")


# ── download_from_s3 ──────────────────────────────────────────────────────


class TestDownloadFromS3:
    @pytest.mark.asyncio
    @patch("app.clients.s3_client._SESSION")
    async def test_download_success(self, mock_session, tmp_path):
        dest = tmp_path / "output.pdf"

        mock_stream = AsyncMock()
        # Two reads: first returns data, second returns empty (EOF)
        mock_stream.read = AsyncMock(side_effect=[b"file-content", b""])

        mock_s3 = AsyncMock()
        mock_s3.get_object = AsyncMock(
            return_value={"ContentType": "application/pdf", "Body": mock_stream}
        )
        mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)

        ct = await download_from_s3("sources/p/file.pdf", dest)

        assert ct == "application/pdf"
        assert dest.read_bytes() == b"file-content"

    @pytest.mark.asyncio
    @patch("app.clients.s3_client._SESSION")
    async def test_download_failure(self, mock_session, tmp_path):
        dest = tmp_path / "output.pdf"

        mock_s3 = AsyncMock()
        mock_s3.get_object = AsyncMock(side_effect=RuntimeError("network error"))
        mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(StorageError, match="Download failed"):
            await download_from_s3("key", dest)
