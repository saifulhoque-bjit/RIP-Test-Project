"""Fernet symmetric encryption for secrets stored at rest (e.g. Jira API
tokens, tenant LLM provider API keys).

Usage
-----
Generate a key once and add it to your .env file::

    python -c "from cryptography.fernet import Fernet; print('JIRA_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"

The key must be the same across all server instances (API + Celery workers).
Never change the key after secrets are stored — rotate by re-encrypting first.

Each secret domain (Jira, LLM provider keys, ...) uses its own settings key
so they can be rotated independently without affecting the other.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.exceptions import ServiceError


def _fernet(key: str, key_setting_name: str) -> Fernet:
    if not key:
        raise ServiceError(
            f"{key_setting_name} is not configured. "
            'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def _encrypt(plain: str, *, key: str, key_setting_name: str) -> str:
    return _fernet(key, key_setting_name).encrypt(plain.encode()).decode()


def _decrypt(ciphertext: str, *, key: str, key_setting_name: str, secret_label: str) -> str:
    try:
        return _fernet(key, key_setting_name).decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ServiceError(
            f"Failed to decrypt {secret_label}. "
            f"Check that {key_setting_name} has not changed since it was stored."
        ) from exc


def encrypt_token(plain: str) -> str:
    """Encrypt a plaintext Jira API token. Returns a URL-safe base64 ciphertext string."""
    from app.core.config import settings  # late import to avoid circular deps at module load

    return _encrypt(plain, key=settings.JIRA_ENCRYPTION_KEY, key_setting_name="JIRA_ENCRYPTION_KEY")


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a previously encrypted Jira API token. Raises ServiceError on bad key or corrupted data."""
    from app.core.config import settings

    return _decrypt(
        ciphertext,
        key=settings.JIRA_ENCRYPTION_KEY,
        key_setting_name="JIRA_ENCRYPTION_KEY",
        secret_label="Jira API token",
    )


def encrypt_llm_api_key(plain: str) -> str:
    """Encrypt a plaintext tenant LLM provider API key. Returns a URL-safe base64 ciphertext string."""
    from app.core.config import settings

    return _encrypt(plain, key=settings.LLM_ENCRYPTION_KEY, key_setting_name="LLM_ENCRYPTION_KEY")


def decrypt_llm_api_key(ciphertext: str) -> str:
    """Decrypt a previously encrypted tenant LLM provider API key. Raises ServiceError on bad key or corrupted data."""
    from app.core.config import settings

    return _decrypt(
        ciphertext,
        key=settings.LLM_ENCRYPTION_KEY,
        key_setting_name="LLM_ENCRYPTION_KEY",
        secret_label="LLM provider API key",
    )


def encrypt_tap_api_key(plain: str) -> str:
    """Encrypt a plaintext TAP API key for per-project storage."""
    from app.core.config import settings

    return _encrypt(plain, key=settings.TAP_ENCRYPTION_KEY, key_setting_name="TAP_ENCRYPTION_KEY")


def decrypt_tap_api_key(ciphertext: str) -> str:
    """Decrypt a previously encrypted TAP API key."""
    from app.core.config import settings

    return _decrypt(
        ciphertext,
        key=settings.TAP_ENCRYPTION_KEY,
        key_setting_name="TAP_ENCRYPTION_KEY",
        secret_label="TAP API key",
    )
