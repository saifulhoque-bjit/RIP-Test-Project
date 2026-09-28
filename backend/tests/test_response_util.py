"""Unit tests for ApiResponse envelope."""

from __future__ import annotations

from app.utils.response import ApiResponse


class TestApiResponse:
    def test_ok_with_data(self):
        resp = ApiResponse.ok(data={"key": "value"}, message="Done")
        assert resp.success is True
        assert resp.message == "Done"
        assert resp.data == {"key": "value"}

    def test_ok_default_message(self):
        resp = ApiResponse.ok()
        assert resp.success is True
        assert resp.message == "Success"
        assert resp.data is None

    def test_error(self):
        resp = ApiResponse.error(message="Something failed")
        assert resp.success is False
        assert resp.message == "Something failed"
        assert resp.data is None

    def test_error_default_message(self):
        resp = ApiResponse.error()
        assert resp.success is False
        assert "error" in resp.message.lower()
