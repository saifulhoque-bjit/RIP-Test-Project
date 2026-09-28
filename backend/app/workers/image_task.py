"""Celery task for parsing image files (PNG / JPEG / WEBP).

Images are sent to OmniParser via base64 encoding (no local disk required).
Extracted UI elements are written to Neo4j.

Retry strategy: exponential backoff — 60 s, 120 s, 240 s.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.celery_app import celery_app
from app.core.constants import (
    SOURCE_STATUS_READY_FOR_REVIEW,
    SOURCE_STATUS_RUNNING,
    TASK_MAX_RETRIES,
    TASK_PARSING_SOFT_TIME_LIMIT,
    TASK_PARSING_TIME_LIMIT,
)
from app.utils.logger import get_logger
from app.workers._task_helpers import (
    _handle_task_exception,
    _mark_status,
    _run_async,
    _upsert_neo4j_file_node,
    mark_cancelled_and_check,
)

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    name="tasks.parse_image",
    max_retries=TASK_MAX_RETRIES,
    acks_late=True,
    soft_time_limit=TASK_PARSING_SOFT_TIME_LIMIT,
    time_limit=TASK_PARSING_TIME_LIMIT,
)
def _parse_image_task(
    self,
    project_id: str,
    source_id: str,
    task_db_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Parse an image via OmniParser.

    Steps
    ─────
    1. Transition source status → running.
    2. Download file bytes from S3.
    3. Base64-encode bytes and call OmniParser.
    4. Upsert :File and :UIElement nodes in Neo4j.
    5. Transition source status → ready_for_review.
    """
    logger.info("_parse_image_task started: source_id=%s", source_id)

    if mark_cancelled_and_check(
        request_id=request_id or task_db_id,
        task_db_id=task_db_id,
        project_id=project_id,
        task_type="source_process",
    ):
        logger.info(
            "_parse_image_task: request_id=%s cancelled — skipping", request_id or task_db_id
        )
        return {"source_id": source_id, "status": "cancelled"}

    _mark_status(source_id, SOURCE_STATUS_RUNNING, task_db_id=task_db_id, project_id=project_id)

    try:
        result = _run_async(_async_parse_image(project_id, source_id))
    except Exception as exc:
        return _handle_task_exception(
            self, source_id, exc, "_parse_image_task", task_db_id=task_db_id, project_id=project_id
        )

    _mark_status(
        source_id,
        SOURCE_STATUS_READY_FOR_REVIEW,
        task_db_id=task_db_id,
        project_id=project_id,
        stage="source.ready_for_review",
    )
    logger.info("_parse_image_task completed: source_id=%s elements=%d", source_id, result)
    return {"source_id": source_id, "status": SOURCE_STATUS_READY_FOR_REVIEW, "elements": result}


async def _async_parse_image(
    _project_id: str,
    source_id: str,
) -> int:
    """Async implementation of the image parsing pipeline."""
    import base64

    from app.clients.aws_session import get_aws_session
    from app.clients.omniparser_client import omniparser_client
    from app.core.config import settings
    from app.db.neo4j import get_neo4j_driver
    from app.db.unit_of_work import UnitOfWork
    from app.repositories.neo4j.source_repository import SourceRepository

    with UnitOfWork() as uow:
        source = uow.sources.get_by_uuid(UUID(source_id))
        if source is None or not source.storage_key:
            raise ValueError(f"Source {source_id} not found or has no storage_key")
        storage_key = source.storage_key

    # ── 1. Download from S3 ───────────────────────────────────────────────
    async with get_aws_session().client("s3") as s3:
        response = await s3.get_object(
            Bucket=settings.AWS_S3_SOURCES_BUCKET,
            Key=storage_key,
        )
        file_bytes: bytes = await response["Body"].read()

    # ── 2. Parse via OmniParser (base64 path — no local disk required) ─────
    b64_image = base64.b64encode(file_bytes).decode("utf-8")
    omni_response = await omniparser_client.parse_image_base64(b64_image)
    elements = [el.model_dump() for el in omni_response.elements]

    # ── 3. Write to Neo4j ──────────────────────────────────────────────────
    driver = get_neo4j_driver()
    neo4j_repo = SourceRepository(driver)
    await _upsert_neo4j_file_node(source_id, neo4j_repo)
    await neo4j_repo.upsert_ui_elements(UUID(source_id), elements)

    return len(elements)
