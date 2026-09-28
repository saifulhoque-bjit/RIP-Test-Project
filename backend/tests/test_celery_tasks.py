"""Unit tests for Celery background tasks.

Strategy:
- We never start a real Celery worker.  Instead we call the underlying task
  function directly using ``.run()`` (the unwrapped sync function).
- UnitOfWork and service methods are fully mocked so no
  database or network I/O occurs.
- ``send_notification_email`` calls Amazon SES via boto3; ``boto3.client`` is
  patched (same pattern as ``tests/test_auth_service.py``) so no real AWS
  call is made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from botocore.exceptions import BotoCoreError
import pytest

# ── send_notification_email ────────────────────────────────────────────────


class TestSendNotificationEmail:
    def test_returns_sent_status(self) -> None:
        from app.workers.tasks import send_notification_email

        ses = MagicMock()
        with patch("app.workers.tasks.boto3.client", return_value=ses):
            result = send_notification_email.run(
                recipient="alice@example.com",
                subject="Test Subject",
                body="Hello",
            )
        assert result["status"] == "sent"
        ses.send_email.assert_called_once()

    def test_masks_recipient_in_result(self) -> None:
        from app.workers.tasks import send_notification_email

        ses = MagicMock()
        with patch("app.workers.tasks.boto3.client", return_value=ses):
            result = send_notification_email.run(
                recipient="bob@example.com",
                subject="Subj",
                body="Body text",
            )
        assert result["recipient_masked"] == "bob***"

    def test_retries_on_exception(self) -> None:
        from app.workers.tasks import send_notification_email

        ses = MagicMock()
        ses.send_email.side_effect = BotoCoreError()
        with patch("app.workers.tasks.boto3.client", return_value=ses):
            with pytest.raises(Exception):
                send_notification_email.run(
                    recipient="x@x.com",
                    subject="S",
                    body="B",
                )
