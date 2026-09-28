"""Unit tests for app.utils.encryption — Fernet round-trip and error handling
for both secret domains (Jira tokens, tenant LLM provider API keys)."""

from __future__ import annotations

from unittest.mock import patch

from cryptography.fernet import Fernet
import pytest

from app.core.exceptions import ServiceError
from app.utils.encryption import (
    decrypt_llm_api_key,
    decrypt_token,
    encrypt_llm_api_key,
    encrypt_token,
)

_KEY_A = Fernet.generate_key().decode()
_KEY_B = Fernet.generate_key().decode()


def _settings_with(jira_key: str = "", llm_key: str = ""):
    mock_settings = type(
        "S", (), {"JIRA_ENCRYPTION_KEY": jira_key, "LLM_ENCRYPTION_KEY": llm_key}
    )()
    return patch("app.core.config.settings", mock_settings)


class TestJiraTokenEncryption:
    def test_round_trips(self) -> None:
        with _settings_with(jira_key=_KEY_A):
            ciphertext = encrypt_token("plaintext-jira-token")
            assert decrypt_token(ciphertext) == "plaintext-jira-token"

    def test_missing_key_raises(self) -> None:
        with _settings_with(jira_key=""), pytest.raises(ServiceError):
            encrypt_token("x")

    def test_wrong_key_fails_to_decrypt(self) -> None:
        with _settings_with(jira_key=_KEY_A):
            ciphertext = encrypt_token("secret")
        with _settings_with(jira_key=_KEY_B), pytest.raises(ServiceError):
            decrypt_token(ciphertext)


class TestLLMApiKeyEncryption:
    def test_round_trips(self) -> None:
        with _settings_with(llm_key=_KEY_A):
            ciphertext = encrypt_llm_api_key("sk-real-secret")
            assert decrypt_llm_api_key(ciphertext) == "sk-real-secret"

    def test_missing_key_raises(self) -> None:
        with _settings_with(llm_key=""), pytest.raises(ServiceError):
            encrypt_llm_api_key("x")

    def test_wrong_key_fails_to_decrypt(self) -> None:
        with _settings_with(llm_key=_KEY_A):
            ciphertext = encrypt_llm_api_key("secret")
        with _settings_with(llm_key=_KEY_B), pytest.raises(ServiceError):
            decrypt_llm_api_key(ciphertext)

    def test_llm_and_jira_keys_are_configured_independently(self) -> None:
        """LLM_ENCRYPTION_KEY being set must not satisfy JIRA_ENCRYPTION_KEY's requirement, and vice versa."""
        with _settings_with(llm_key=_KEY_A, jira_key=""), pytest.raises(ServiceError):
            encrypt_token("jira-secret")
        with _settings_with(jira_key=_KEY_A, llm_key=""), pytest.raises(ServiceError):
            encrypt_llm_api_key("llm-secret")
