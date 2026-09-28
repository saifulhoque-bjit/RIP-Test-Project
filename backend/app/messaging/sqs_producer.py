"""Async SQS event producer.

``send_event`` serialises *data* to JSON and publishes it to *queue_url*.
All failures are logged; the caller decides whether to treat them as fatal.

- Fire-and-forget pattern — does not block the request path.
- Uses aioboto3 for non-blocking I/O.
- Includes explicit timeout and exponential-backoff retry via tenacity.

Queue type detection
────────────────────
SQS FIFO queues require ``MessageGroupId`` and ``MessageDeduplicationId`` and
their URLs always end with ``.fifo``.  Standard queues reject both of those
parameters with ``InvalidParameterValue``.  ``send_event`` inspects the URL
suffix and only adds FIFO attributes when talking to a FIFO queue.
"""

from __future__ import annotations

import json
import uuid

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.clients.aws_session import get_aws_session
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Use the shared session — never construct aioboto3.Session() locally.
_SESSION = get_aws_session()


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def send_event(data: dict, queue_url: str) -> str:
    """Publish *data* as a JSON message to SQS *queue_url*.

    Supports both Standard and FIFO queues:
    - FIFO queues: URL ends with ``.fifo`` — ``MessageGroupId`` and
      ``MessageDeduplicationId`` are added automatically.
    - Standard queues: URL does not end with ``.fifo`` — neither FIFO
      attribute is included (Standard queues reject them outright).

    Returns the SQS message ID on success.
    Raises on final retry failure — callers may catch and route to DLQ logic.
    """
    message_body = json.dumps(data)

    params: dict = {
        "QueueUrl": queue_url,
        "MessageBody": message_body,
    }

    # FIFO queues are identified by the mandatory ``.fifo`` URL suffix (AWS
    # enforced).  Only FIFO queues accept — and require — these two attributes.
    if queue_url.endswith(".fifo"):
        params["MessageGroupId"] = "rip-events"
        params["MessageDeduplicationId"] = str(uuid.uuid4())

    async with _SESSION.client("sqs") as sqs:
        response = await sqs.send_message(**params)

    message_id: str = response.get("MessageId", "unknown")
    logger.info(
        "SQS event published",
        extra={"queue": queue_url, "message_id": message_id},
    )
    return message_id
