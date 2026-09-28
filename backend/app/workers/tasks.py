"""Celery workers — notification tasks."""

from __future__ import annotations

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.celery_app import celery_app
from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Notification / email ───────────────────────────────────────────────────


def _ses_client():
    return boto3.client(
        "ses",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


@celery_app.task(
    bind=True,
    name="tasks.send_notification_email",
    max_retries=3,
    default_retry_delay=30,
)
def send_notification_email(self, recipient: str, subject: str, body: str) -> dict:
    """Send a notification email asynchronously via Amazon SES.

    Recipient, subject, and body (HTML) are required; no PII or tokens
    should be logged in plain text.
    """
    try:
        logger.info(
            "Sending notification email, subject=%s, recipient_masked=%s",
            subject,
            recipient[:3] + "***",
        )
        _ses_client().send_email(
            Source=settings.AWS_SES_SENDER_EMAIL,
            Destination={"ToAddresses": [recipient]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": body, "Charset": "UTF-8"}},
            },
        )
        return {"status": "sent", "recipient_masked": recipient[:3] + "***"}
    except (ClientError, BotoCoreError) as exc:
        logger.error("Email send failed, retrying", exc_info=True)
        raise self.retry(exc=exc)
