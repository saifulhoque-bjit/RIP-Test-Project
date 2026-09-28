"""Unit tests for app/utils/link_downloader.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import pytest

from app.core.exceptions import ValidationError as AppValidationError
from app.utils.link_downloader import (
    _blob_to_raw_url,
    _derive_format,
    _github_archive_url,
    _is_github_blob_url,
    _is_github_repo_url,
    _is_private_ip,
    _validate_url,
    download_from_url,
)

# ── SSRF protection ────────────────────────────────────────────────────────


class TestIsPrivateIp:
    @patch("app.utils.link_downloader.socket.gethostbyname", return_value="127.0.0.1")
    def test_loopback(self, mock_dns):
        assert _is_private_ip("localhost") is True

    @patch("app.utils.link_downloader.socket.gethostbyname", return_value="192.168.1.1")
    def test_private_range(self, mock_dns):
        assert _is_private_ip("internal.corp") is True

    @patch("app.utils.link_downloader.socket.gethostbyname", return_value="169.254.1.1")
    def test_link_local(self, mock_dns):
        assert _is_private_ip("link-local.test") is True

    @patch("app.utils.link_downloader.socket.gethostbyname", return_value="8.8.8.8")
    def test_public_ip(self, mock_dns):
        assert _is_private_ip("dns.google") is False

    @patch(
        "app.utils.link_downloader.socket.gethostbyname",
        side_effect=__import__("socket").gaierror,
    )
    def test_unresolvable_host(self, mock_dns):
        assert _is_private_ip("no-such-host.invalid") is False


class TestValidateUrl:
    def test_rejects_http(self):
        with pytest.raises(AppValidationError, match="HTTPS"):
            _validate_url("http://example.com/file.pdf")

    def test_rejects_ftp(self):
        with pytest.raises(AppValidationError, match="HTTPS"):
            _validate_url("ftp://files.example.com/file.zip")

    def test_rejects_no_hostname(self):
        with pytest.raises(AppValidationError, match="missing hostname"):
            _validate_url("https://")

    @patch("app.utils.link_downloader._is_private_ip", return_value=True)
    def test_rejects_private_ip(self, mock_priv):
        with pytest.raises(AppValidationError, match="private"):
            _validate_url("https://internal.corp/file.pdf")

    @patch("app.utils.link_downloader._is_private_ip", return_value=False)
    def test_accepts_valid_https(self, mock_priv):
        _validate_url("https://example.com/file.pdf")  # should not raise


# ── GitHub URL helpers ─────────────────────────────────────────────────────


class TestIsGithubRepoUrl:
    def test_owner_repo(self):
        parsed = urlparse("https://github.com/owner/repo")
        assert _is_github_repo_url(parsed) is True

    def test_owner_repo_tree_branch(self):
        parsed = urlparse("https://github.com/owner/repo/tree/main")
        assert _is_github_repo_url(parsed) is True

    def test_blob_url_is_not_repo(self):
        parsed = urlparse("https://github.com/owner/repo/blob/main/README.md")
        assert _is_github_repo_url(parsed) is False

    def test_non_github(self):
        parsed = urlparse("https://gitlab.com/owner/repo")
        assert _is_github_repo_url(parsed) is False


class TestIsGithubBlobUrl:
    def test_blob_url(self):
        parsed = urlparse("https://github.com/owner/repo/blob/main/src/app.py")
        assert _is_github_blob_url(parsed) is True

    def test_repo_root_is_not_blob(self):
        parsed = urlparse("https://github.com/owner/repo")
        assert _is_github_blob_url(parsed) is False


class TestBlobToRawUrl:
    def test_converts_blob(self):
        parsed = urlparse("https://github.com/owner/repo/blob/main/src/app.py")
        raw = _blob_to_raw_url(parsed)
        assert raw == "https://raw.githubusercontent.com/owner/repo/main/src/app.py"


class TestGithubArchiveUrl:
    def test_basic_repo(self):
        parsed = urlparse("https://github.com/owner/my-repo")
        url, filename = _github_archive_url(parsed)
        assert url == "https://github.com/owner/my-repo/archive/HEAD.zip"
        assert filename == "my-repo-HEAD.zip"

    def test_strips_git_suffix(self):
        parsed = urlparse("https://github.com/owner/repo.git")
        url, filename = _github_archive_url(parsed)
        assert "repo.git" not in url
        assert filename == "repo-HEAD.zip"


# ── _derive_format ─────────────────────────────────────────────────────────


class TestDeriveFormat:
    def test_from_extension(self):
        assert _derive_format("report.pdf", "application/pdf") == "PDF"

    def test_no_extension_uses_content_type(self):
        assert _derive_format("noext", "application/pdf") == "PDF"

    def test_content_type_with_params(self):
        assert _derive_format("noext", "text/html; charset=utf-8") == "HTML"


# ── download_from_url (integration with mocks) ────────────────────────────


class TestDownloadFromUrl:
    @pytest.mark.asyncio
    @patch("app.utils.link_downloader._http_get", new_callable=AsyncMock)
    @patch("app.utils.link_downloader._validate_url")
    async def test_github_repo_downloads_archive(self, mock_validate, mock_get):
        mock_get.return_value = (b"zip-bytes", "application/zip")

        result = await download_from_url("https://github.com/owner/repo")

        assert result.is_archive is True
        assert result.file_type == "ZIP"
        assert result.filename == "repo-HEAD.zip"

    @pytest.mark.asyncio
    @patch("app.utils.link_downloader._http_get", new_callable=AsyncMock)
    @patch("app.utils.link_downloader._validate_url")
    async def test_github_blob_downloads_raw_file(self, mock_validate, mock_get):
        mock_get.return_value = (b"py-content", "text/plain")

        result = await download_from_url("https://github.com/owner/repo/blob/main/app.py")

        assert result.is_archive is False
        assert result.filename == "app.py"
        # Verify raw URL was used (second call after re-validation)
        raw_url_used = mock_get.call_args[0][0]
        assert "raw.githubusercontent.com" in raw_url_used

    @pytest.mark.asyncio
    @patch("app.utils.link_downloader._http_get", new_callable=AsyncMock)
    @patch("app.utils.link_downloader._validate_url")
    async def test_generic_url_downloads_directly(self, mock_validate, mock_get):
        mock_get.return_value = (b"pdf-data", "application/pdf")

        result = await download_from_url("https://files.example.com/report.pdf")

        assert result.is_archive is False
        assert result.filename == "report.pdf"
        assert result.content_type == "application/pdf"

    @pytest.mark.asyncio
    @patch("app.utils.link_downloader._http_get", new_callable=AsyncMock)
    @patch("app.utils.link_downloader._validate_url")
    async def test_generic_url_no_path_uses_download_name(self, mock_validate, mock_get):
        mock_get.return_value = (b"data", "application/octet-stream")

        result = await download_from_url("https://files.example.com/")

        assert result.filename == "download"

    @pytest.mark.asyncio
    async def test_rejects_http_url(self):
        with pytest.raises(AppValidationError, match="HTTPS"):
            await download_from_url("http://insecure.example.com/file.pdf")
