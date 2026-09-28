"""Async SQS consumer — polls a queue and dispatches messages to handlers.

Design:
- Long-polling with ``WaitTimeSeconds=20`` reduces empty-poll API calls.
- Each message is processed idempotently; the handler must be safe to replay.
- On successful processing the message is deleted from the queue.
- On unhandled failure the message visibility timeout expires and SQS retries
  it automatically; after ``maxReceiveCount`` it flows to the DLQ.
- ``run_consumer`` should be called from a background task or a separate
  process (e.g., a Celery beat trigger or a dedicated container).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json

from botocore.exceptions import BotoCoreError, ClientError

from app.clients.aws_session import get_aws_session
from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Use the shared session — never construct aioboto3.Session() locally.
_SESSION = get_aws_session()

# ── Handler registry ───────────────────────────────────────────────────────
# Map event-type string → async handler coroutine
MessageHandler = Callable[[dict], Awaitable[None]]

_HANDLERS: dict[str, MessageHandler] = {}


def register_handler(event_type: str) -> Callable[[MessageHandler], MessageHandler]:
    """Decorator to register an async handler for a given event type."""

    def decorator(fn: MessageHandler) -> MessageHandler:
        _HANDLERS[event_type] = fn
        return fn

    return decorator


async def _dispatch(message_body: str) -> None:
    try:
        payload = json.loads(message_body)
    except json.JSONDecodeError:
        logger.error("SQS message is not valid JSON; skipping", exc_info=True)
        return

    event_type = payload.get("event_type", "unknown")
    handler = _HANDLERS.get(event_type)
    if handler is None:
        logger.warning("No handler registered for event_type=%s", event_type)
        return

    await handler(payload)


async def run_consumer(
    queue_url: str = settings.AWS_SQS_QUEUE_URL,
    max_messages: int = 10,
    poll_interval_seconds: float = 0.5,
) -> None:
    """Poll *queue_url* indefinitely and dispatch each message."""
    logger.info("SQS consumer starting on queue=%s", queue_url)
    async with _SESSION.client("sqs") as sqs:
        while True:
            response = await sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=20,
                AttributeNames=["All"],
            )
            messages = response.get("Messages", [])
            for msg in messages:
                receipt_handle = msg["ReceiptHandle"]
                try:
                    await _dispatch(msg["Body"])
                except Exception:
                    # Any exception from the handler means the message is left
                    # in-flight; its visibility timeout will expire and SQS will
                    # redeliver it (or route it to the DLQ after maxReceiveCount).
                    logger.error(
                        "Handler raised; message left for SQS retry/DLQ",
                        exc_info=True,
                    )
                    continue

                # The message was processed successfully — delete it.
                try:
                    await sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                except (BotoCoreError, ClientError):
                    # Transient AWS/network error on delete.  The message will be
                    # redelivered, but handlers must already be idempotent so this
                    # is safe.  Do not suppress non-AWS errors.
                    logger.warning(
                        "Failed to delete SQS message after processing; "
                        "may be redelivered (idempotent handling expected)",
                        exc_info=True,
                    )

            if not messages:
                await asyncio.sleep(poll_interval_seconds)
