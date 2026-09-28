"""Unit tests for app.clients.llm_factory.get_balance.

``httpx.AsyncClient`` is patched at the module import site so no real network
call is made — mirrors ``tests/test_tap_client.py``'s pattern.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.llm_factory import LLMBalance, get_balance, get_llm
from app.core.exceptions import AIServiceError, AIWorkflowError


class _FakeResponse:
    def __init__(self, status_code: int, json_data: object = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self) -> object:
        return self._json


def _patch_httpx(response: _FakeResponse):
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=response)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_client)
    ctx.__aexit__ = AsyncMock(return_value=False)

    return patch("app.clients.llm_factory.httpx.AsyncClient", return_value=ctx), mock_client


class TestGetBalance:
    async def test_raises_for_unsupported_provider(self) -> None:
        with pytest.raises(AIWorkflowError):
            await get_balance("anthropic", "sk-key")

    async def test_success_returns_balance(self) -> None:
        response = _FakeResponse(
            200,
            json_data={
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "USD",
                        "total_balance": "42.50",
                        "granted_balance": "10.00",
                        "topped_up_balance": "32.50",
                    }
                ],
            },
        )
        patcher, mock_client = _patch_httpx(response)

        with patcher:
            result = await get_balance("deepseek", "sk-key")

        assert result == LLMBalance(balance=42.5, currency="USD")
        mock_client.get.assert_awaited_once_with(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": "Bearer sk-key"},
        )

    async def test_raises_on_non_200_status(self) -> None:
        response = _FakeResponse(401, text="invalid api key")
        patcher, _ = _patch_httpx(response)

        with patcher, pytest.raises(AIServiceError):
            await get_balance("deepseek", "bad-key")

    async def test_raises_on_empty_balance_infos(self) -> None:
        response = _FakeResponse(200, json_data={"is_available": True, "balance_infos": []})
        patcher, _ = _patch_httpx(response)

        with patcher, pytest.raises(AIServiceError):
            await get_balance("deepseek", "sk-key")

    async def test_raises_on_malformed_payload(self) -> None:
        response = _FakeResponse(
            200,
            json_data={"is_available": True, "balance_infos": [{"currency": "USD"}]},
        )
        patcher, _ = _patch_httpx(response)

        with patcher, pytest.raises(AIServiceError):
            await get_balance("deepseek", "sk-key")


class TestGetLlm:
    def test_sonnet_5_omits_temperature(self) -> None:
        with patch("langchain_anthropic.ChatAnthropic") as chat_anthropic:
            get_llm("anthropic", "anthropic/claude-sonnet-5", temperature=0.0, api_key="sk-key")

        chat_anthropic.assert_called_once_with(
            model="anthropic/claude-sonnet-5",
            api_key="sk-key",
            max_tokens=128000,
        )

    def test_older_claude_models_keep_temperature(self) -> None:
        with patch("langchain_anthropic.ChatAnthropic") as chat_anthropic:
            get_llm("anthropic", "claude-sonnet-4-6", temperature=0.2, api_key="sk-key")

        chat_anthropic.assert_called_once_with(
            model="claude-sonnet-4-6",
            temperature=0.2,
            api_key="sk-key",
            max_tokens=128000,
        )
