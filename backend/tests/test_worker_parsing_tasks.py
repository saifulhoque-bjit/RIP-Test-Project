"""Unit tests for worker parsing tasks (document/image/code)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import NAMESPACE_URL, UUID, uuid5

from celery.exceptions import SoftTimeLimitExceeded
import pytest

from app.core.constants import (
    SOURCE_CODE_CONCURRENT_PIPELINE_RETRY_COUNTDOWN_SECONDS,
    SOURCE_CODE_TASK_RETRY_COUNTDOWN_SECONDS,
    SOURCE_INGESTION_STATUS_FAILED,
    SOURCE_INGESTION_STATUS_READY_FOR_REVIEW,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_READY_FOR_REVIEW,
    SOURCE_STATUS_RUNNING,
    TASK_AI_SOFT_TIME_LIMIT,
    TASK_AI_TIME_LIMIT,
    TASK_STATUS_CANCELLED,
)
from app.core.enums.activity_type import ActivityType
from app.core.enums.notification_type import NotificationType
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.llm_errors import LLMErrorClassification, LLMErrorReason, NonRetryableLLMError
from app.core.messages import MSG_SOURCE_CODE_MODULE_PERSIST_TIME_LIMIT_EXCEEDED
from app.models.neo4j.module_feature_model import ChangeType
from app.workers.document_task import (
    generate_modules_and_features_task,
    parse_document_task,
    regenerate_modules_and_features_task,
)
from app.workers.document_task_stages import (
    _add_run_stage_by_source_ids,
    _async_build_fragments,
    _count_module_feature_regeneration_changes,
    _fail_ingestion_for_parse_failure,
    _notify_module_feature_status,
    _notify_story_feedback_patch_status,
    _notify_user_story_regeneration_status,
    _notify_user_story_status,
    _record_feedback_regenerated_activity,
    _remap_skip_processing_feature_ids,
    _run_backlog_phase,
    _run_module_feature_phase,
    _run_module_feature_regeneration_task,
    _run_parse_document_task,
    _run_source_module_feature_generation_task,
    _run_source_module_feature_phase,
    _run_story_feedback_patch_task,
    _run_user_story_backlog_task,
)
from app.workers.image_task import _async_parse_image, _parse_image_task
from app.workers.source_code_task import (
    _add_source_code_stage,
    _apply_feature_regeneration,
    _async_parse_code,
    _build_backlog_result_from_results,
    _cleanup_project_folder_task,
    _finalize_source_code_cancellation,
    _finalize_source_code_pipeline,
    _generate_and_persist_pipeline_documents,
    _generate_pipeline_documents_task,
    _notify_feature_regeneration_status,
    _notify_module_failed,
    _notify_source_code_pipeline_status,
    _parse_code_task,
    _persist_architecture_document_artifact,
    _persist_domain_knowledge_artifact,
    _persist_single_module_task,
    _process_single_module_task,
    _record_feature_regeneration_activity,
    _record_global_artifacts_activity,
    _record_module_activity,
    _record_pipeline_documents_failure,
    _run_feature_mfu_regeneration_task,
    _run_pipeline_orchestrator,
    _update_feature_regeneration_ingestion_status,
    _upsert_one_regenerated_story,
)


@pytest.fixture(autouse=True)
def _mock_record_activity():
    """Prevent RFP-pipeline completion paths from opening a real UnitOfWork.

    ``record_activity`` opens its own UnitOfWork independent of any mocked
    repository in a given test — patch it at the module where it's
    imported/used so no real DB connection is attempted.
    """
    with patch("app.workers.document_task_stages.record_activity") as mock:
        yield mock


@pytest.fixture(autouse=True)
def _mock_source_code_record_activity():
    """Same as `_mock_record_activity`, for the source-code pipeline module."""
    with patch("app.workers.source_code_task.record_activity") as mock:
        yield mock


class _S3ClientCM:
    def __init__(self, body_bytes: bytes) -> None:
        self._body_bytes = body_bytes

    async def __aenter__(self) -> _S3ClientCM:
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await asyncio.sleep(0)
        return None

    async def get_object(self, **_: object) -> dict[str, object]:
        await asyncio.sleep(0)
        body = AsyncMock()
        body.read = AsyncMock(return_value=self._body_bytes)
        return {"Body": body}


class _AwsSession:
    def __init__(self, body_bytes: bytes) -> None:
        self._body_bytes = body_bytes

    def client(self, _name: str) -> _S3ClientCM:
        return _S3ClientCM(self._body_bytes)


def _mock_uow(source: object | None) -> MagicMock:
    uow = MagicMock()
    uow.sources.get_by_uuid.return_value = source
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None
    return cm


def test_parse_image_task_completed() -> None:
    with (
        patch("app.workers.image_task._mark_status"),
        patch("app.workers.image_task._run_async", return_value=3),
    ):
        result = _parse_image_task.run("p1", "00000000-0000-0000-0000-000000000001")

    assert result["status"] == SOURCE_STATUS_READY_FOR_REVIEW
    assert result["elements"] == 3


def test_parse_image_task_retry_path() -> None:
    with (
        patch("app.workers.image_task._mark_status"),
        patch("app.workers.image_task._run_async", side_effect=RuntimeError("boom")),
        patch("app.workers.image_task._handle_task_exception") as mock_handle,
    ):
        mock_handle.return_value = {
            "source_id": "00000000-0000-0000-0000-000000000001",
            "status": "failed",
        }
        result = _parse_image_task.run("p1", "00000000-0000-0000-0000-000000000001")

    mock_handle.assert_called_once()
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_async_parse_image_happy_path() -> None:
    source = SimpleNamespace(
        id=10,
        project_id="p1",
        original_name="x.png",
        file_type="png",
        mime_type="image/png",
        storage_key="s3/key",
        checksum_sha256="sha-123",
    )

    repo = MagicMock()
    repo.upsert_file_node = AsyncMock()
    repo.upsert_ui_elements = AsyncMock()

    element = MagicMock()
    element.model_dump.return_value = {"id": "e1"}
    omni_response = SimpleNamespace(elements=[element])

    with (
        patch("app.clients.aws_session.get_aws_session", return_value=_AwsSession(b"img")),
        patch(
            "app.clients.omniparser_client.omniparser_client.parse_image_base64",
            AsyncMock(return_value=omni_response),
        ),
        patch("app.db.neo4j.get_neo4j_driver", return_value=object()),
        patch("app.repositories.neo4j.source_repository.SourceRepository", return_value=repo),
        patch("app.db.unit_of_work.UnitOfWork", return_value=_mock_uow(source)),
    ):
        count = await _async_parse_image("p1", "00000000-0000-0000-0000-000000000001")

    assert count == 1
    repo.upsert_file_node.assert_awaited_once()
    repo.upsert_ui_elements.assert_awaited_once()


def test_parse_code_task_completed(_mock_source_code_record_activity) -> None:
    summary = {"modules_dispatched": 8}
    with (
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event") as mock_emit,
        patch("app.workers.source_code_task._run_async", return_value=summary),
    ):
        result = _parse_code_task.run(
            "00000000-0000-0000-0000-000000000c01", "00000000-0000-0000-0000-000000000001"
        )

    assert result["status"] == SOURCE_STATUS_RUNNING
    assert result["summary"] == summary
    stages = [call.kwargs["stage"] for call in mock_emit.call_args_list]
    assert "source_code.parsing.started" in stages
    assert "source_code.pipeline.started" in stages
    _mock_source_code_record_activity.assert_called_once()
    assert (
        _mock_source_code_record_activity.call_args.kwargs["activity_type"].value
        == "source_code_pipeline_started"
    )
    # No terminal status was observed from the chain's last link (a Redis
    # error while checking module completion, per _persist_single_module_task) —
    # Source/SourceIngestion are already sitting at "running" untouched, so
    # nothing here should re-write or re-broadcast that value.
    mark_status_stages = [c.kwargs.get("stage") for c in mock_mark_status.call_args_list]
    assert "source_code.module_chains.dispatched" not in mark_status_stages
    assert "source_code.module_chains.dispatched" not in stages


def test_parse_code_task_reports_cancelled_status_without_remarking_running() -> None:
    """When the pipeline was cancelled before module dispatch,
    _run_pipeline_orchestrator's checkpoint already finalized the source as
    cancelled — _parse_code_task must surface that status as-is, not
    re-mark/re-emit it as running and clobber the terminal status."""
    summary = {"modules_dispatched": 0, "status": TASK_STATUS_CANCELLED}
    with (
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event") as mock_emit,
        patch("app.workers.source_code_task._run_async", return_value=summary),
    ):
        result = _parse_code_task.run(
            "00000000-0000-0000-0000-000000000c02", "00000000-0000-0000-0000-000000000001"
        )

    assert result["status"] == TASK_STATUS_CANCELLED
    assert result["summary"] == summary
    mark_status_stages = [c.kwargs.get("stage") for c in mock_mark_status.call_args_list]
    assert "source_code.module_chains.dispatched" not in mark_status_stages
    emit_stages = [c.kwargs["stage"] for c in mock_emit.call_args_list]
    assert "source_code.module_chains.dispatched" not in emit_stages


def test_parse_code_task_notifies_running() -> None:
    with (
        patch("app.workers.source_code_task._mark_status"),
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", return_value={"modules_dispatched": 1}),
        patch("app.workers.source_code_task._notify_source_code_pipeline_status") as mock_notify,
    ):
        _parse_code_task.run(
            "00000000-0000-0000-0000-000000000c03", "00000000-0000-0000-0000-000000000001"
        )

    mock_notify.assert_called_once_with(
        project_id="00000000-0000-0000-0000-000000000c03",
        status=SourceIngestionStatus.RUNNING.value,
    )


def test_parse_code_task_fails_immediately_on_circuit_breaker_without_retry() -> None:
    """CircuitBreakerError is deliberately a BaseException (see llm_client.py's
    class docstring) so a circuit-breaker abort can never be swallowed into a
    "failed module, continue" result deep in the module-processing chain. But
    that property also means it bypasses a plain `except Exception` — without
    explicit handling it used to escape _parse_code_task uncaught (whether
    raised directly during global-artifact generation, or re-raised by the
    orchestrator's blocking chain .get() after a module's process task hit
    it), leaving the source's status stuck at "running" forever with no
    failure ever recorded.

    It must fail the source (both Source.status via _mark_status and
    SourceIngestion.status via _finalize_source_code_pipeline) immediately —
    NOT retried via Celery's self.retry(): the breaker only trips after the
    LLM client's own bounded retry ladder is already exhausted, so retrying
    the whole pipeline task would just redo every already-processed module
    only to hit the same provider issue again.
    """
    from app.services.source_code_pipeline.src.ai.llm_client import CircuitBreakerError

    breaker_exc = CircuitBreakerError("8 consecutive LLM timeout(s) >= 8 cap")
    project_id = "00000000-0000-0000-0000-000000000c04"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=breaker_exc),
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
    ):
        result = _parse_code_task.run(project_id, source_id)

    assert result == {
        "source_id": source_id,
        "status": SOURCE_STATUS_FAILED,
        "error": str(breaker_exc),
    }
    mock_mark_status.assert_called_with(
        source_id,
        SOURCE_STATUS_FAILED,
        str(breaker_exc),
        stage="task.failed",
        task_db_id=None,
        project_id=project_id,
    )
    mock_finalize.assert_called_once_with(
        project_id=project_id,
        source_id=source_id,
        status=SourceIngestionStatus.FAILED.value,
        task_db_id=None,
        error=str(breaker_exc),
        error_reason="circuit_breaker",
    )


def test_parse_code_task_fails_immediately_on_propagated_circuit_breaker_task_failure() -> None:
    """A circuit breaker tripped inside a module's process_single_module_task
    is caught there and re-raised as CircuitBreakerTaskFailure (a plain
    Exception Celery can record as a normal FAILURE, unlike the original
    CircuitBreakerError BaseException) — propagated here by
    _run_pipeline_orchestrator's blocking chain .get(). Must be handled
    identically to a CircuitBreakerError raised directly in this task: fail
    the source immediately, no retry."""
    from app.workers._task_helpers import CircuitBreakerTaskFailure

    wrapped_exc = CircuitBreakerTaskFailure("8 consecutive LLM timeout(s) >= 8 cap")
    project_id = "00000000-0000-0000-0000-000000000c05"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=wrapped_exc),
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
        patch(
            "app.workers.source_code_task._handle_source_code_cancellation", return_value=False
        ),
    ):
        result = _parse_code_task.run(project_id, source_id)

    assert result == {
        "source_id": source_id,
        "status": SOURCE_STATUS_FAILED,
        "error": str(wrapped_exc),
    }
    mock_mark_status.assert_called_with(
        source_id,
        SOURCE_STATUS_FAILED,
        str(wrapped_exc),
        stage="task.failed",
        task_db_id=None,
        project_id=project_id,
    )
    mock_finalize.assert_called_once_with(
        project_id=project_id,
        source_id=source_id,
        status=SourceIngestionStatus.FAILED.value,
        task_db_id=None,
        error=str(wrapped_exc),
        error_reason="circuit_breaker",
    )


def test_parse_code_task_entry_checkpoint_bails_out_when_already_cancelled() -> None:
    """A Celery-retried or redelivered invocation of THIS task landing after
    the user already cancelled the request must not resurrect the run — no
    RUNNING re-mark, no S3 re-download, no re-emitted "started" events. Every
    other checkpoint in this file already guards this way
    (_process_single_module_task); the root task itself was the one gap."""
    project_id = "00000000-0000-0000-0000-000000000c06"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch(
            "app.workers.source_code_task._handle_source_code_cancellation", return_value=True
        ) as mock_handle_cancel,
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event") as mock_emit,
        patch("app.workers.source_code_task._run_async") as mock_run_async,
    ):
        result = _parse_code_task.run(project_id, source_id, task_db_id="task-c06")

    assert result == {"source_id": source_id, "status": TASK_STATUS_CANCELLED}
    mock_handle_cancel.assert_called_once_with(
        request_id=None,
        task_db_id="task-c06",
        project_id=project_id,
        source_id=source_id,
    )
    mock_mark_status.assert_not_called()
    mock_emit.assert_not_called()
    mock_run_async.assert_not_called()


def test_parse_code_task_catches_propagated_task_cancelled_error_without_retry() -> None:
    """TaskCancelledError raised deep in the module chain (e.g. by
    _process_single_module_task's own checkpoint) propagates up through
    _run_pipeline_orchestrator's blocking chain .get() and must be caught
    here directly — NOT fall through to `except Exception`, which would mark
    the source FAILED for what is actually a confirmed, already-finalized
    cancellation."""
    from app.workers._task_helpers import TaskCancelledError

    exc = TaskCancelledError("request_id=req-1 cancelled")
    project_id = "00000000-0000-0000-0000-000000000c07"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=exc),
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
    ):
        result = _parse_code_task.run(project_id, source_id)

    # Never marked FAILED — the checkpoint that raised this already
    # finalized the run as cancelled.
    mock_finalize.assert_not_called()
    assert all(
        call.args[1:2] != (SOURCE_STATUS_FAILED,) for call in mock_mark_status.call_args_list
    )
    assert result == {
        "source_id": source_id,
        "status": TASK_STATUS_CANCELLED,
        "error": str(exc),
    }


def test_parse_code_task_circuit_breaker_after_cancel_finalizes_as_cancelled() -> None:
    """If the circuit breaker trips after the user already cancelled the
    request (both racing off the same burst of LLM timeouts), the run must
    finalize as cancelled — not overwrite the already-cancelled
    Source/SourceIngestion rows back to "failed"."""
    from app.services.source_code_pipeline.src.ai.llm_client import CircuitBreakerError

    breaker_exc = CircuitBreakerError("8 consecutive LLM timeout(s) >= 8 cap")
    project_id = "00000000-0000-0000-0000-000000000c08"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status"),
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=breaker_exc),
        patch(
            "app.workers.source_code_task._handle_source_code_cancellation",
            side_effect=[False, True],
        ) as mock_handle_cancel,
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
    ):
        result = _parse_code_task.run(project_id, source_id, task_db_id="task-c08")

    assert mock_handle_cancel.call_count == 2
    mock_finalize.assert_not_called()
    assert result == {
        "source_id": source_id,
        "status": TASK_STATUS_CANCELLED,
        "error": str(breaker_exc),
    }


def test_parse_code_task_fails_immediately_on_credit_balance_exhausted_without_retry() -> None:
    """CreditBalanceExhaustedError is deliberately a BaseException (mirrors
    CircuitBreakerError) so an account-wide billing/quota failure (e.g.
    Anthropic's "Your credit balance is too low") can never be swallowed
    into a "failed module, continue" result — every other module still
    queued would hit the identical wall. It must fail the source (both
    Source.status via _mark_status and SourceIngestion.status via
    _finalize_source_code_pipeline) immediately, with the original provider
    error message intact — NOT retried via Celery's self.retry()."""
    from app.services.source_code_pipeline.src.ai.llm_client import (
        CreditBalanceExhaustedError,
    )

    credit_exc = CreditBalanceExhaustedError(
        "[LLMClient] Provider billing/credits exhausted — provider='anthropic', "
        "model='anthropic/claude-sonnet-5'. Original error: Your credit balance "
        "is too low to access the Anthropic API."
    )
    project_id = "00000000-0000-0000-0000-000000000c10"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=credit_exc),
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
        patch(
            "app.workers.source_code_task._handle_source_code_cancellation", return_value=False
        ),
    ):
        result = _parse_code_task.run(project_id, source_id)

    assert result == {
        "source_id": source_id,
        "status": SOURCE_STATUS_FAILED,
        "error": str(credit_exc),
    }
    mock_mark_status.assert_called_with(
        source_id,
        SOURCE_STATUS_FAILED,
        str(credit_exc),
        stage="task.failed",
        task_db_id=None,
        project_id=project_id,
    )
    mock_finalize.assert_called_once_with(
        project_id=project_id,
        source_id=source_id,
        status=SourceIngestionStatus.FAILED.value,
        task_db_id=None,
        error=str(credit_exc),
        error_reason="credit_exhausted",
    )


def test_parse_code_task_fails_immediately_on_propagated_credit_balance_task_failure() -> None:
    """A credit-balance/quota exhaustion tripped inside a module's
    process_single_module_task is caught there and re-raised as
    CreditBalanceExhaustedTaskFailure (a plain Exception Celery can record
    as a normal FAILURE, unlike the original BaseException) — propagated
    here by _run_pipeline_orchestrator's blocking chain .get(). Must be
    handled identically to a CreditBalanceExhaustedError raised directly in
    this task: fail the source immediately, no retry, and — because the
    per-module chain is a single sequential chain — every module still
    queued behind the one that failed is never dispatched at all."""
    from app.workers._task_helpers import CreditBalanceExhaustedTaskFailure

    wrapped_exc = CreditBalanceExhaustedTaskFailure(
        "[LLMClient] Provider billing/credits exhausted — Your credit balance "
        "is too low to access the Anthropic API."
    )
    project_id = "00000000-0000-0000-0000-000000000c11"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=wrapped_exc),
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
        patch(
            "app.workers.source_code_task._handle_source_code_cancellation", return_value=False
        ),
    ):
        result = _parse_code_task.run(project_id, source_id)

    assert result == {
        "source_id": source_id,
        "status": SOURCE_STATUS_FAILED,
        "error": str(wrapped_exc),
    }
    mock_mark_status.assert_called_with(
        source_id,
        SOURCE_STATUS_FAILED,
        str(wrapped_exc),
        stage="task.failed",
        task_db_id=None,
        project_id=project_id,
    )
    mock_finalize.assert_called_once_with(
        project_id=project_id,
        source_id=source_id,
        status=SourceIngestionStatus.FAILED.value,
        task_db_id=None,
        error=str(wrapped_exc),
        error_reason="credit_exhausted",
    )


def test_parse_code_task_credit_balance_exhausted_after_cancel_finalizes_as_cancelled() -> None:
    """If credit-balance exhaustion is detected after the user already
    cancelled the request, the run must finalize as cancelled — not
    overwrite the already-cancelled Source/SourceIngestion rows back to
    "failed"."""
    from app.services.source_code_pipeline.src.ai.llm_client import (
        CreditBalanceExhaustedError,
    )

    credit_exc = CreditBalanceExhaustedError("Your credit balance is too low")
    project_id = "00000000-0000-0000-0000-000000000c12"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status"),
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=credit_exc),
        patch(
            "app.workers.source_code_task._handle_source_code_cancellation",
            side_effect=[False, True],
        ) as mock_handle_cancel,
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
    ):
        result = _parse_code_task.run(project_id, source_id, task_db_id="task-c12")

    assert mock_handle_cancel.call_count == 2
    mock_finalize.assert_not_called()
    assert result == {
        "source_id": source_id,
        "status": TASK_STATUS_CANCELLED,
        "error": str(credit_exc),
    }


def test_parse_code_task_fails_immediately_on_non_retryable_llm_error_without_retry() -> None:
    """Every OTHER non-retryable LLM reason (auth, invalid model, invalid
    request, context-length-exceeded, content policy, permission denied)
    gets the same "abort the whole run" treatment as CircuitBreakerError/
    CreditBalanceExhaustedError above — fail the source immediately, no
    Celery retry, with the classification's reason forwarded for
    notify/activity purposes."""
    from app.core.llm_errors import LLMErrorClassification, LLMErrorReason, NonRetryableLLMError

    classification = LLMErrorClassification(
        retryable=False,
        reason=LLMErrorReason.PERMISSION_DENIED,
        provider="openai",
        message="Access to this resource is denied.",
        user_message="The AI provider denied access for this operation.",
    )
    non_retryable_exc = NonRetryableLLMError(classification)
    project_id = "00000000-0000-0000-0000-000000000c13"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=non_retryable_exc),
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
        patch(
            "app.workers.source_code_task._handle_source_code_cancellation", return_value=False
        ),
    ):
        result = _parse_code_task.run(project_id, source_id)

    assert result == {
        "source_id": source_id,
        "status": SOURCE_STATUS_FAILED,
        "error": str(non_retryable_exc),
    }
    mock_mark_status.assert_called_with(
        source_id,
        SOURCE_STATUS_FAILED,
        str(non_retryable_exc),
        stage="task.failed",
        task_db_id=None,
        project_id=project_id,
    )
    mock_finalize.assert_called_once_with(
        project_id=project_id,
        source_id=source_id,
        status=SourceIngestionStatus.FAILED.value,
        task_db_id=None,
        error=str(non_retryable_exc),
        error_reason="permission_denied",
    )


def test_parse_code_task_non_retryable_llm_error_after_cancel_finalizes_as_cancelled() -> None:
    """If a non-retryable LLM error is detected after the user already
    cancelled the request, the run must finalize as cancelled — not
    overwrite the already-cancelled Source/SourceIngestion rows back to
    "failed"."""
    from app.core.llm_errors import LLMErrorClassification, LLMErrorReason, NonRetryableLLMError

    classification = LLMErrorClassification(
        retryable=False,
        reason=LLMErrorReason.INVALID_MODEL,
        provider="openai",
        message="The model does not exist.",
        user_message="The configured AI model is invalid or unsupported.",
    )
    non_retryable_exc = NonRetryableLLMError(classification)
    project_id = "00000000-0000-0000-0000-000000000c14"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status"),
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=non_retryable_exc),
        patch(
            "app.workers.source_code_task._handle_source_code_cancellation",
            side_effect=[False, True],
        ) as mock_handle_cancel,
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
    ):
        result = _parse_code_task.run(project_id, source_id, task_db_id="task-c14")

    assert mock_handle_cancel.call_count == 2
    mock_finalize.assert_not_called()
    assert result == {
        "source_id": source_id,
        "status": TASK_STATUS_CANCELLED,
        "error": str(non_retryable_exc),
    }


def test_parse_code_task_fails_immediately_on_concurrent_pipeline_error_without_retry() -> None:
    """ConcurrentPipelineError means LLMClient.pipeline_run_guard's
    process-wide single-flight lock rejected this run because another
    pipeline currently holds it in this worker process — e.g. a different
    project's task sharing this worker child (--max-tasks-per-child). Must
    fail immediately, with no Celery-level retry: no Celery/Redis-level
    retry layer is added on top of the pipeline's own retry handling, and
    re-running the whole task would just redo already-completed work only to
    hit the same contention again."""
    from app.services.source_code_pipeline.src.ai.llm_client import ConcurrentPipelineError

    guard_exc = ConcurrentPipelineError("Another pipeline run is already active in this process")
    project_id = "00000000-0000-0000-0000-000000000c09"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=guard_exc),
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
    ):
        result = _parse_code_task.run(project_id, source_id)

    assert result == {
        "source_id": source_id,
        "status": SOURCE_STATUS_FAILED,
        "error": str(guard_exc),
    }
    mock_mark_status.assert_called_with(
        source_id,
        SOURCE_STATUS_FAILED,
        str(guard_exc),
        stage="task.failed",
        task_db_id=None,
        project_id=project_id,
    )
    mock_finalize.assert_called_once_with(
        project_id=project_id,
        source_id=source_id,
        status=SourceIngestionStatus.FAILED.value,
        task_db_id=None,
        error=str(guard_exc),
        error_reason=None,
    )


def test_parse_code_task_fails_immediately_on_unexpected_exception_without_retry() -> None:
    """The generic `except Exception` fallback in _parse_code_task uses
    _handle_source_code_task_exception, not the shared, retrying
    _handle_task_exception used by document/image tasks — this task never
    schedules a Celery retry for ANY exception, since
    app/services/source_code_pipeline already retries LLM calls internally
    and a Celery-level retry on top would only redo already-completed work
    (S3 download, extraction, every earlier module)."""
    exc = RuntimeError("unexpected pipeline error")
    project_id = "00000000-0000-0000-0000-000000000c10"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", side_effect=exc),
        patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
    ):
        result = _parse_code_task.run(project_id, source_id)

    assert result == {
        "source_id": source_id,
        "status": SOURCE_STATUS_FAILED,
        "error": str(exc),
    }
    mock_mark_status.assert_called_with(
        source_id,
        SOURCE_STATUS_FAILED,
        str(exc),
        stage="task.failed",
        task_db_id=None,
        project_id=project_id,
    )
    mock_finalize.assert_called_once_with(
        project_id=project_id,
        source_id=source_id,
        status=SourceIngestionStatus.FAILED.value,
        task_db_id=None,
        error=str(exc),
        error_reason=None,
    )


def test_parse_code_task_skips_duplicate_redelivered_invocation() -> None:
    """Celery's at-least-once broker redelivery (worker crash/ack timeout) can
    redeliver the same message. Since this task never retries at the Celery
    level, without a dedup guard a redelivered invocation would re-mark the
    source "running" and redo S3 download/extraction as a second, independent
    run. task_control.claim_task_execution returning False (already claimed
    by the original delivery) must short-circuit before any of that."""
    project_id = "00000000-0000-0000-0000-000000000c11"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.core.task_control.claim_task_execution", return_value=False) as mock_claim,
        patch("app.workers.source_code_task._mark_status") as mock_mark_status,
        patch("app.workers.source_code_task.emit_task_event") as mock_emit,
        patch("app.workers.source_code_task._run_async") as mock_run_async,
        patch(
            "app.workers.source_code_task._handle_source_code_cancellation"
        ) as mock_handle_cancel,
    ):
        result = _parse_code_task.run(project_id, source_id, task_db_id="task-dup")

    assert result == {"source_id": source_id, "status": "duplicate_skipped"}
    mock_claim.assert_called_once_with("task-dup")
    mock_mark_status.assert_not_called()
    mock_emit.assert_not_called()
    mock_run_async.assert_not_called()
    mock_handle_cancel.assert_not_called()


def test_parse_code_task_proceeds_without_task_db_id() -> None:
    """No task_db_id means the redelivery-dedup checkpoint has no stable id
    to claim against — it must not block (or error on) a plain single-source
    upload that never sets one."""
    project_id = "00000000-0000-0000-0000-000000000c12"
    source_id = "00000000-0000-0000-0000-000000000001"

    with (
        patch("app.core.task_control.claim_task_execution") as mock_claim,
        patch("app.workers.source_code_task._mark_status"),
        patch("app.workers.source_code_task.emit_task_event"),
        patch("app.workers.source_code_task._run_async", return_value={"modules_dispatched": 1}),
    ):
        result = _parse_code_task.run(project_id, source_id)

    mock_claim.assert_not_called()
    assert result["status"] == SOURCE_STATUS_RUNNING


class TestCleanupProjectFolderTaskCancellationGuard:
    """_cleanup_project_folder_task deletes temp/source_codes/{project_id} —
    project-scoped, not run-scoped. Dispatched from a cancellation with a 30s
    countdown, so a re-run started in that window would have its freshly
    downloaded/extracted files wiped unless guarded — mirrors
    _delete_project_backlog_task's guard against a newer active task."""

    def test_proceeds_unconditionally_when_cancelled_at_is_none(self):
        """Normal-completion callers never pass cancelled_at — their cleanup
        must be unaffected by this guard."""
        with (
            patch(
                "app.workers.source_code_task._cleanup_project_source_code_folder"
            ) as mock_cleanup_folder,
            patch("app.db.unit_of_work.UnitOfWork") as mock_uow_cls,
        ):
            _cleanup_project_folder_task.run("proj-1")

        mock_cleanup_folder.assert_called_once_with("proj-1")
        mock_uow_cls.assert_not_called()

    def test_proceeds_when_no_newer_task_exists(self):
        uow = MagicMock()
        uow.project_tasks.list_active_by_project.return_value = []
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch(
                "app.workers.source_code_task._cleanup_project_source_code_folder"
            ) as mock_cleanup_folder,
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
        ):
            _cleanup_project_folder_task.run(
                "00000000-0000-0000-0000-0000000000f6", cancelled_at="2026-09-01T00:00:00+00:00"
            )

        mock_cleanup_folder.assert_called_once_with("00000000-0000-0000-0000-0000000000f6")

    def test_skips_when_a_newer_task_exists(self):
        newer_task = SimpleNamespace(created_at=datetime(2026, 9, 1, 1, 0, tzinfo=UTC))
        uow = MagicMock()
        uow.project_tasks.list_active_by_project.return_value = [newer_task]
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch(
                "app.workers.source_code_task._cleanup_project_source_code_folder"
            ) as mock_cleanup_folder,
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
        ):
            _cleanup_project_folder_task.run(
                "00000000-0000-0000-0000-0000000000f6", cancelled_at="2026-09-01T00:00:00+00:00"
            )

        mock_cleanup_folder.assert_not_called()


def test_finalize_source_code_cancellation_dispatches_cleanup_with_matching_timestamp() -> None:
    """Both deferred rollback tasks must agree on the same cancellation
    instant, so _cleanup_project_folder_task's guard and
    _delete_project_backlog_task's guard draw the identical cutoff line."""
    with (
        patch("app.workers.source_code_task.emit_task_event"),
        patch(
            "app.workers.source_code_task._delete_project_backlog_task.apply_async"
        ) as mock_delete_backlog,
        patch(
            "app.workers.source_code_task._cleanup_project_folder_task.apply_async"
        ) as mock_cleanup,
    ):
        _finalize_source_code_cancellation(
            request_id="req-1",
            task_db_id="task-1",
            project_id="proj-1",
        )

    backlog_args = mock_delete_backlog.call_args.kwargs["args"]
    cleanup_args = mock_cleanup.call_args.kwargs["args"]
    assert backlog_args[0] == "proj-1"
    assert cleanup_args[0] == "proj-1"
    assert backlog_args[1] == cleanup_args[1]  # same cancelled_at instant
    assert mock_cleanup.call_args.kwargs["countdown"] == 30


def test_finalize_source_code_pipeline_default_status_is_ready_for_review(
    _mock_source_code_record_activity,
) -> None:
    """The source-code pipeline never uses COMPLETED for this transition —
    the default *status* (when a caller omits it) is READY_FOR_REVIEW."""
    summary = {"total_modules": 4, "total_features": 9, "total_user_stories": 21}

    with (
        patch("app.workers.source_code_task._run_async", return_value=summary),
        patch("app.workers.source_code_task._update_source_ingestion_fields") as mock_update_fields,
        patch("app.workers.source_code_task._notify_source_code_pipeline_status") as mock_notify,
        patch("app.workers.source_code_task._add_source_code_stage") as mock_add_stage,
        patch("app.workers.source_code_task._add_source_ingestion_error") as mock_add_error,
    ):
        _finalize_source_code_pipeline(
            project_id="00000000-0000-0000-0000-000000000777",
            source_id="00000000-0000-0000-0000-000000000778",
            task_db_id="task-777",
        )

    mock_update_fields.assert_called_once()
    assert mock_update_fields.call_args.kwargs["fields"]["tot_modules_failed"] == 0
    mock_notify.assert_called_once_with(
        project_id="00000000-0000-0000-0000-000000000777",
        status=SourceIngestionStatus.READY_FOR_REVIEW.value,
        total_modules=4,
        total_features=9,
        total_user_stories=21,
        error=None,
        error_reason=None,
    )
    mock_add_stage.assert_called_once_with(
        "00000000-0000-0000-0000-000000000778", SourceIngestionStage.READY_FOR_REVIEW
    )
    mock_add_error.assert_not_called()
    _mock_source_code_record_activity.assert_called_once()
    call_kwargs = _mock_source_code_record_activity.call_args.kwargs
    assert call_kwargs["project_id"] == UUID("00000000-0000-0000-0000-000000000777")
    assert call_kwargs["activity_type"].value == "source_code_pipeline_completed"
    assert call_kwargs["data"] == {"source_id": "00000000-0000-0000-0000-000000000778", **summary}


def test_finalize_source_code_pipeline_notifies_ready_for_review(
    _mock_source_code_record_activity,
) -> None:
    """The real completion call site (_persist_single_module_task) now
    passes READY_FOR_REVIEW on success, not COMPLETED — activity logging
    must still fire for it."""
    summary = {"total_modules": 4, "total_features": 9, "total_user_stories": 21}

    with (
        patch("app.workers.source_code_task._run_async", return_value=summary),
        patch("app.workers.source_code_task._update_source_ingestion_fields") as mock_update_fields,
        patch("app.workers.source_code_task._notify_source_code_pipeline_status") as mock_notify,
        patch("app.workers.source_code_task._add_source_code_stage") as mock_add_stage,
    ):
        _finalize_source_code_pipeline(
            project_id="00000000-0000-0000-0000-000000000777",
            source_id="00000000-0000-0000-0000-000000000778",
            status=SourceIngestionStatus.READY_FOR_REVIEW.value,
            task_db_id="task-777",
        )

    mock_update_fields.assert_called_once()
    mock_notify.assert_called_once_with(
        project_id="00000000-0000-0000-0000-000000000777",
        status=SourceIngestionStatus.READY_FOR_REVIEW.value,
        total_modules=4,
        total_features=9,
        total_user_stories=21,
        error=None,
        error_reason=None,
    )
    mock_add_stage.assert_called_once_with(
        "00000000-0000-0000-0000-000000000778", SourceIngestionStage.READY_FOR_REVIEW
    )
    _mock_source_code_record_activity.assert_called_once()


def test_finalize_source_code_pipeline_records_failed_activity(
    _mock_source_code_record_activity,
) -> None:
    summary = {"total_modules": 0, "total_features": 0, "total_user_stories": 0}

    with (
        patch("app.workers.source_code_task._run_async", return_value=summary),
        patch("app.workers.source_code_task._update_source_ingestion_fields") as mock_update_fields,
        patch("app.workers.source_code_task._notify_source_code_pipeline_status"),
        patch("app.workers.source_code_task._add_source_code_stage") as mock_add_stage,
        patch("app.workers.source_code_task._add_source_ingestion_error") as mock_add_error,
    ):
        _finalize_source_code_pipeline(
            project_id="00000000-0000-0000-0000-000000000777",
            source_id="00000000-0000-0000-0000-000000000778",
            status=SourceIngestionStatus.FAILED.value,
            task_db_id="task-777",
            error="All 2/2 modules failed.",
            failed_modules=2,
        )

    assert mock_update_fields.call_args.kwargs["fields"]["tot_modules_failed"] == 2
    _mock_source_code_record_activity.assert_called_once()
    call_kwargs = _mock_source_code_record_activity.call_args.kwargs
    assert call_kwargs["activity_type"].value == "source_code_pipeline_failed"
    assert call_kwargs["data"]["error"] == "All 2/2 modules failed."
    mock_add_stage.assert_not_called()
    mock_add_error.assert_called_once_with(
        source_ids=["00000000-0000-0000-0000-000000000778"], error="All 2/2 modules failed."
    )


class TestUpdateFeatureRegenerationIngestionStatus:
    def test_records_error_when_status_is_failed(self) -> None:
        with (
            patch(
                "app.workers.source_code_task._update_source_ingestion_fields_by_id"
            ) as mock_update_fields,
            patch(
                "app.workers.source_code_task._add_source_ingestion_error_by_id"
            ) as mock_add_error,
        ):
            _update_feature_regeneration_ingestion_status(
                "ing-1", SourceIngestionStatus.FAILED.value, error="boom"
            )

        mock_update_fields.assert_called_once_with(
            ingestion_id="ing-1", fields={"status": SourceIngestionStatus.FAILED.value}
        )
        mock_add_error.assert_called_once_with(ingestion_id="ing-1", error="boom")

    def test_does_not_record_error_on_non_failed_status(self) -> None:
        with (
            patch("app.workers.source_code_task._update_source_ingestion_fields_by_id"),
            patch(
                "app.workers.source_code_task._add_source_ingestion_error_by_id"
            ) as mock_add_error,
        ):
            _update_feature_regeneration_ingestion_status(
                "ing-1", SourceIngestionStatus.COMPLETED.value
            )

        mock_add_error.assert_not_called()

    def test_mark_completed_sets_single_completed_at_field(self) -> None:
        with patch(
            "app.workers.source_code_task._update_source_ingestion_fields_by_id"
        ) as mock_update_fields:
            _update_feature_regeneration_ingestion_status(
                "ing-1", SourceIngestionStatus.READY_FOR_REVIEW.value, mark_completed=True
            )

        _, kwargs = mock_update_fields.call_args
        assert kwargs["ingestion_id"] == "ing-1"
        assert set(kwargs["fields"]) == {"status", "completed_at"}
        assert kwargs["fields"]["status"] == SourceIngestionStatus.READY_FOR_REVIEW.value


class TestNotifySourceCodePipelineStatus:
    def test_ready_for_review_branch_notifies_success(self) -> None:
        project = SimpleNamespace(
            name="Demo Project", owner_id=UUID("00000000-0000-0000-0000-0000000000aa")
        )
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_source_code_pipeline_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                total_modules=3,
                total_features=7,
                total_user_stories=15,
            )

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["title"] == "Source Code Pipeline Ready for Review"
        assert "3" in call_kwargs["message"]
        assert "7" in call_kwargs["message"]
        assert call_kwargs["notification_type"] == NotificationType.SUCCESS


class TestNotifyModuleFailed:
    def test_notifies_owner_with_module_failed_message(self) -> None:
        project = SimpleNamespace(
            name="Demo Project", owner_id=UUID("00000000-0000-0000-0000-0000000000aa")
        )
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_module_failed(
                project_id="00000000-0000-0000-0000-000000000001",
                module_id="MOD-1",
                module_name="Billing",
                error="boom",
            )

        mock_publish.assert_called_once_with(
            user_id=project.owner_id,
            title="Module Processing Failed",
            message="Failed to process module Billing: boom",
            notification_type=NotificationType.ERROR,
            data={
                "project_id": "00000000-0000-0000-0000-000000000001",
                "module_id": "MOD-1",
                "module_name": "Billing",
                "error": "boom",
            },
        )

    def test_noop_when_project_id_missing(self) -> None:
        with patch("app.db.unit_of_work.UnitOfWork") as mock_uow_cls:
            _notify_module_failed(project_id=None, module_id="MOD-1", module_name="Billing")
        mock_uow_cls.assert_not_called()

    def test_noop_when_owner_missing(self) -> None:
        project = SimpleNamespace(name="Demo Project", owner_id=None)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_module_failed(
                project_id="00000000-0000-0000-0000-000000000001",
                module_id="MOD-1",
                module_name="Billing",
            )

        mock_publish.assert_not_called()

    def test_swallows_exceptions(self) -> None:
        with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")):
            _notify_module_failed(
                project_id="00000000-0000-0000-0000-000000000001",
                module_id="MOD-1",
                module_name="Billing",
            )  # must not raise


class TestRecordModuleActivityNotifiesOnFailure:
    def test_failed_outcome_notifies_module_failed(self) -> None:
        with patch("app.workers.source_code_task._notify_module_failed") as mock_notify:
            _record_module_activity(
                project_id="00000000-0000-0000-0000-000000000001",
                source_id="src-1",
                task_db_id="task-1",
                module_id="MOD-1",
                module_name="Billing",
                outcome="failed",
                error="boom",
            )

        mock_notify.assert_called_once_with(
            project_id="00000000-0000-0000-0000-000000000001",
            module_id="MOD-1",
            module_name="Billing",
            error="boom",
        )

    def test_completed_outcome_does_not_notify(self) -> None:
        with patch("app.workers.source_code_task._notify_module_failed") as mock_notify:
            _record_module_activity(
                project_id="00000000-0000-0000-0000-000000000001",
                source_id="src-1",
                task_db_id="task-1",
                module_id="MOD-1",
                module_name="Billing",
                outcome="completed",
            )

        mock_notify.assert_not_called()


class TestNotifyFeatureRegenerationStatus:
    def test_ready_for_review_branch_notifies_owner_and_members(self) -> None:
        owner_id = UUID("00000000-0000-0000-0000-0000000000aa")
        member_id = UUID("00000000-0000-0000-0000-0000000000bb")
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        # The owner also holds a membership row — must be notified only once.
        uow.project_members.list_by_project.return_value = [
            SimpleNamespace(user_id=member_id),
            SimpleNamespace(user_id=owner_id),
        ]
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_feature_regeneration_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                target_dicts=[
                    {"module_id": "MOD-1", "mfu_id": "MFU-1"},
                    {"module_id": "MOD-1", "mfu_id": "MFU-2"},
                ],
            )

        notified_ids = {c.kwargs["user_id"] for c in mock_publish.call_args_list}
        assert notified_ids == {owner_id, member_id}
        for c in mock_publish.call_args_list:
            assert c.kwargs["title"] == "Features Regenerated from Feedback"
            assert "2" in c.kwargs["message"]
            assert c.kwargs["notification_type"] == NotificationType.SUCCESS

    def test_failed_branch_notifies_error(self) -> None:
        owner_id = UUID("00000000-0000-0000-0000-0000000000aa")
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = []
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_feature_regeneration_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.FAILED.value,
                target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
                error="boom",
            )

        mock_publish.assert_called_once_with(
            user_id=owner_id,
            title="Feature Regeneration Failed",
            message='Feature regeneration from feedback failed for "Demo Project". Error: boom',
            notification_type=NotificationType.ERROR,
            data={
                "project_id": "00000000-0000-0000-0000-000000000001",
                "status": SourceIngestionStatus.FAILED.value,
                "targets": [{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
                "error_reason": None,
            },
        )

    def test_running_branch_notifies_owner_and_members(self) -> None:
        owner_id = UUID("00000000-0000-0000-0000-0000000000aa")
        member_id = UUID("00000000-0000-0000-0000-0000000000bb")
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = [SimpleNamespace(user_id=member_id)]
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_feature_regeneration_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.RUNNING.value,
                target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
            )

        notified_ids = {c.kwargs["user_id"] for c in mock_publish.call_args_list}
        assert notified_ids == {owner_id, member_id}
        for c in mock_publish.call_args_list:
            assert c.kwargs["title"] == "Feature Regeneration from Feedback Started"
            assert c.kwargs["notification_type"] == NotificationType.INFO

    def test_unrecognized_status_does_not_notify(self) -> None:
        project = SimpleNamespace(
            name="Demo Project", owner_id=UUID("00000000-0000-0000-0000-0000000000aa")
        )
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = []
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_feature_regeneration_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status="bogus_status",
            )

        mock_publish.assert_not_called()


class TestNotifyUserStoryStatus:
    def test_ready_for_review_branch_notifies_success(self) -> None:
        project = SimpleNamespace(
            name="Demo Project", owner_id=UUID("00000000-0000-0000-0000-0000000000aa")
        )
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_user_story_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                total_user_stories=5,
            )

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["title"] == "User Stories Ready for Review"
        assert "5" in call_kwargs["message"]
        assert call_kwargs["notification_type"] == NotificationType.SUCCESS

    def test_completed_status_no_longer_notifies(self) -> None:
        """COMPLETED is no longer the terminal status for story_generation —
        the branch now keys off READY_FOR_REVIEW, so a stray COMPLETED value
        must fall through to a no-op rather than firing a notification."""
        with patch("app.db.unit_of_work.UnitOfWork") as mock_uow_cls:
            uow = MagicMock()
            uow.projects.get_by_uuid.return_value = SimpleNamespace(
                name="Demo Project", owner_id=UUID("00000000-0000-0000-0000-0000000000aa")
            )
            cm = MagicMock()
            cm.__enter__.return_value = uow
            cm.__exit__.return_value = None
            mock_uow_cls.return_value = cm

            with patch("app.services.notification_service.publish_notification") as mock_publish:
                _notify_user_story_status(
                    project_id="00000000-0000-0000-0000-000000000001",
                    status=SourceIngestionStatus.COMPLETED.value,
                    total_user_stories=5,
                )

        mock_publish.assert_not_called()


class TestNotifyModuleFeatureStatus:
    def test_ready_for_review_branch_notifies_approval_for_initial_generation(self) -> None:
        """The initial (non-regeneration) module_feature-generation completion
        path resolves to READY_FOR_REVIEW like every other checkpoint, but
        ``is_regeneration=False`` still picks the "awaiting approval" wording
        — the module/feature-vs-user-story distinction now lives in
        SourceIngestionStage/``stages``, not a separate status value."""
        project = SimpleNamespace(
            name="Demo Project", owner_id=UUID("00000000-0000-0000-0000-0000000000aa")
        )
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_module_feature_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                is_regeneration=False,
                modules_added=3,
                features_added=7,
            )

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["title"] == "Modules & Features Ready for Approval"
        assert "3" in call_kwargs["message"]
        assert "7" in call_kwargs["message"]
        assert call_kwargs["notification_type"] == NotificationType.SUCCESS
        assert call_kwargs["data"]["tot_modules"] == 3
        assert call_kwargs["data"]["tot_features"] == 7
        assert call_kwargs["data"]["tot_modules_updated"] is None
        assert call_kwargs["data"]["tot_features_updated"] is None

    def test_ready_for_review_branch_still_notifies_regeneration(self) -> None:
        """Regeneration resolves to the same READY_FOR_REVIEW status but keeps
        the "ready for review" wording via ``is_regeneration=True``."""
        project = SimpleNamespace(
            name="Demo Project", owner_id=UUID("00000000-0000-0000-0000-0000000000aa")
        )
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_module_feature_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                is_regeneration=True,
                modules_added=2,
                modules_updated=1,
                features_added=5,
                features_updated=2,
            )

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["title"] == "Modules & Features Ready for Review"
        assert "3" in call_kwargs["message"]
        assert "7" in call_kwargs["message"]
        assert call_kwargs["notification_type"] == NotificationType.SUCCESS
        assert call_kwargs["data"]["tot_modules"] == 2
        assert call_kwargs["data"]["tot_modules_updated"] == 1
        assert call_kwargs["data"]["tot_features"] == 5
        assert call_kwargs["data"]["tot_features_updated"] == 2


class TestNotifyStoryFeedbackPatchStatus:
    def test_ready_for_review_branch_notifies_owner_and_members(self) -> None:
        owner_id = UUID("00000000-0000-0000-0000-0000000000aa")
        member_id = UUID("00000000-0000-0000-0000-0000000000bb")
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        # The owner also holds a membership row — must be notified only once.
        uow.project_members.list_by_project.return_value = [
            SimpleNamespace(user_id=member_id),
            SimpleNamespace(user_id=owner_id),
        ]
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_story_feedback_patch_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                stories_added=3,
                stories_updated=2,
            )

        notified_ids = {c.kwargs["user_id"] for c in mock_publish.call_args_list}
        assert notified_ids == {owner_id, member_id}
        for c in mock_publish.call_args_list:
            assert c.kwargs["title"] == "User Stories Regenerated from Feedback"
            assert "5" in c.kwargs["message"]
            assert c.kwargs["notification_type"] == NotificationType.SUCCESS

    def test_failed_branch_notifies_error(self) -> None:
        owner_id = UUID("00000000-0000-0000-0000-0000000000aa")
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = []
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_story_feedback_patch_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.FAILED.value,
                error="boom",
            )

        mock_publish.assert_called_once_with(
            user_id=owner_id,
            title="User Story Regeneration Failed",
            message='User story regeneration from feedback failed for "Demo Project". Error: boom',
            notification_type=NotificationType.ERROR,
            data={
                "project_id": "00000000-0000-0000-0000-000000000001",
                "status": SourceIngestionStatus.FAILED.value,
                "tot_user_stories": None,
                "tot_user_stories_updated": None,
                "error_reason": None,
            },
        )

    def test_running_branch_notifies_owner_and_members(self) -> None:
        owner_id = UUID("00000000-0000-0000-0000-0000000000aa")
        member_id = UUID("00000000-0000-0000-0000-0000000000bb")
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = [SimpleNamespace(user_id=member_id)]
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_story_feedback_patch_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.RUNNING.value,
            )

        notified_ids = {c.kwargs["user_id"] for c in mock_publish.call_args_list}
        assert notified_ids == {owner_id, member_id}
        for c in mock_publish.call_args_list:
            assert c.kwargs["title"] == "User Story Regeneration from Feedback Started"
            assert c.kwargs["notification_type"] == NotificationType.INFO

    def test_unrecognized_status_does_not_notify(self) -> None:
        project = SimpleNamespace(
            name="Demo Project", owner_id=UUID("00000000-0000-0000-0000-0000000000aa")
        )
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = []
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_story_feedback_patch_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status="bogus_status",
            )

        mock_publish.assert_not_called()


class TestNotifyUserStoryRegenerationStatus:
    def test_ready_for_review_branch_notifies_owner_and_members(self) -> None:
        owner_id = UUID("00000000-0000-0000-0000-0000000000aa")
        member_id = UUID("00000000-0000-0000-0000-0000000000bb")
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = [
            SimpleNamespace(user_id=member_id),
            SimpleNamespace(user_id=owner_id),
        ]
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_user_story_regeneration_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.READY_FOR_REVIEW.value,
                total_user_stories=3,
            )

        notified_ids = {c.kwargs["user_id"] for c in mock_publish.call_args_list}
        assert notified_ids == {owner_id, member_id}
        for c in mock_publish.call_args_list:
            assert c.kwargs["title"] == "User Stories Regenerated"
            assert "3" in c.kwargs["message"]
            assert c.kwargs["notification_type"] == NotificationType.SUCCESS

    def test_failed_branch_notifies_error(self) -> None:
        owner_id = UUID("00000000-0000-0000-0000-0000000000aa")
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = []
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_user_story_regeneration_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.FAILED.value,
                error="boom",
            )

        mock_publish.assert_called_once_with(
            user_id=owner_id,
            title="User Story Regeneration Failed",
            message='User story regeneration failed for "Demo Project". Error: boom',
            notification_type=NotificationType.ERROR,
            data={
                "project_id": "00000000-0000-0000-0000-000000000001",
                "status": SourceIngestionStatus.FAILED.value,
                "total_user_stories": None,
                "error_reason": None,
            },
        )

    def test_running_branch_notifies_owner_and_members(self) -> None:
        owner_id = UUID("00000000-0000-0000-0000-0000000000aa")
        member_id = UUID("00000000-0000-0000-0000-0000000000bb")
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = [SimpleNamespace(user_id=member_id)]
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_user_story_regeneration_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status=SourceIngestionStatus.RUNNING.value,
            )

        notified_ids = {c.kwargs["user_id"] for c in mock_publish.call_args_list}
        assert notified_ids == {owner_id, member_id}
        for c in mock_publish.call_args_list:
            assert c.kwargs["title"] == "User Story Regeneration Started"
            assert c.kwargs["notification_type"] == NotificationType.INFO

    def test_unrecognized_status_does_not_notify(self) -> None:
        project = SimpleNamespace(
            name="Demo Project", owner_id=UUID("00000000-0000-0000-0000-0000000000aa")
        )
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = []
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _notify_user_story_regeneration_status(
                project_id="00000000-0000-0000-0000-000000000001",
                status="bogus_status",
            )

        mock_publish.assert_not_called()


class TestRecordGlobalArtifactsActivity:
    """Tests for _record_global_artifacts_activity, called once at
    _run_pipeline_orchestrator's "source_code.global_artifacts.completed" stage."""

    def test_builds_message_and_resolves_actor(self, _mock_source_code_record_activity):
        actor_id = UUID("00000000-0000-0000-0000-000000000abc")
        with patch(
            "app.workers.source_code_task.resolve_actor_from_task",
            return_value=actor_id,
        ) as mock_resolve:
            _record_global_artifacts_activity(
                project_id="00000000-0000-0000-0000-000000000f01",
                task_db_id="task-1",
                source_id="00000000-0000-0000-0000-000000000778",
                modules_detected=6,
            )

        mock_resolve.assert_called_once_with("task-1")
        _mock_source_code_record_activity.assert_called_once()
        call_kwargs = _mock_source_code_record_activity.call_args.kwargs
        assert call_kwargs["project_id"] == UUID("00000000-0000-0000-0000-000000000f01")
        assert call_kwargs["activity_type"].value == "source_code_global_artifacts_completed"
        assert call_kwargs["actor_user_id"] == actor_id
        assert call_kwargs["data"] == {
            "source_id": "00000000-0000-0000-0000-000000000778",
            "modules_detected": 6,
        }
        assert "6" in call_kwargs["message"]


class TestRunPipelineOrchestratorGlobalArtifactsStage:
    """_run_pipeline_orchestrator's Stage 1 must call the split
    generate_code_dependency_graph() + discover_modules(dependency_graph=...)
    pair (matching PipelineOrchestrator's own dependency_graph.json /
    global_artifacts_result.json demo flow), not generate_global_artifacts()."""

    @pytest.mark.asyncio
    async def test_calls_dependency_graph_then_discover_modules(self):
        source_ingestion = SimpleNamespace(
            source_language=None,
            frontend_stack=None,
            backend_stack=None,
            infrastructure_stack=None,
            architecture_stack=None,
            database_stack=None,
            coding_standard=None,
            database_strategy=None,
            architecture=None,
            security=None,
            source_layout_type=None,
        )
        source = SimpleNamespace(source_ingestion=source_ingestion)

        dependency_graph = {
            "global_index": {},
            "artifacts_detected": {},
            "artifacts_enriched": {},
        }
        global_artifacts = {
            **dependency_graph,
            "module_manifest": {},
            "budget_report": {},
            "filtered_modules": [],
        }

        mock_pipeline = MagicMock()
        mock_pipeline.generate_code_dependency_graph.return_value = dependency_graph
        mock_pipeline.discover_modules.return_value = global_artifacts

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_mock_uow(source)),
            patch(
                "app.workers.document_task_stages._get_project_llm_options",
                return_value={},
            ),
            patch("app.utils.common.dump_json_debug"),
            patch(
                "app.services.source_code_pipeline.pipeline_orchestrator.PipelineOrchestrator"
            ) as mock_orchestrator_cls,
            patch(
                "app.workers.source_code_task._handle_source_code_cancellation",
                side_effect=[False, True],
            ),
            patch("app.workers.source_code_task.emit_task_event"),
            patch("app.workers.source_code_task._add_source_code_stage") as mock_add_stage,
            patch("app.workers.source_code_task._record_global_artifacts_activity"),
            patch("app.workers.source_code_task._update_source_ingestion_fields"),
        ):
            mock_orchestrator_cls.configure_project.return_value = "/tmp/p1/config.json"
            mock_orchestrator_cls.return_value = mock_pipeline

            result = await _run_pipeline_orchestrator(
                project_id="p1",
                source_id="00000000-0000-0000-0000-000000000001",
                codebase_dir="/tmp/source_codes/p1/codebase",
                task_db_id="task-1",
            )

        mock_pipeline.generate_code_dependency_graph.assert_called_once_with()
        mock_pipeline.discover_modules.assert_called_once_with(dependency_graph=dependency_graph)
        mock_pipeline.generate_global_artifacts.assert_not_called()
        assert result == {"modules_dispatched": 0, "status": "cancelled"}

        source_id = "00000000-0000-0000-0000-000000000001"
        assert mock_add_stage.call_args_list == [
            call(source_id, SourceIngestionStage.BUILDING_CODE_DEPENDENCY_GRAPH),
            call(source_id, SourceIngestionStage.DISCOVERING_MODULES),
        ]

    @pytest.mark.asyncio
    async def test_emits_requirement_extraction_stage_when_not_cancelled(self):
        """The SourceIngestionStage.EXTRACTING_REQUIREMENTS checkpoint is tagged
        unconditionally, right before the `if filtered_modules:` dispatch
        branch — even with no modules detected, so this test avoids real
        Celery chain-building by keeping filtered_modules empty. With zero
        modules detected there is no chain to dispatch/block on, so the
        pipeline is finalized as FAILED right here instead of coming back
        "running" with nothing left to ever move it out of that state."""
        source_ingestion = SimpleNamespace(
            source_language=None,
            frontend_stack=None,
            backend_stack=None,
            infrastructure_stack=None,
            architecture_stack=None,
            database_stack=None,
            coding_standard=None,
            database_strategy=None,
            architecture=None,
            security=None,
            source_layout_type=None,
        )
        source = SimpleNamespace(source_ingestion=source_ingestion)

        dependency_graph = {
            "global_index": {},
            "artifacts_detected": {},
            "artifacts_enriched": {},
        }
        global_artifacts = {
            **dependency_graph,
            "module_manifest": {},
            "budget_report": {},
            "filtered_modules": [],
        }

        mock_pipeline = MagicMock()
        mock_pipeline.generate_code_dependency_graph.return_value = dependency_graph
        mock_pipeline.discover_modules.return_value = global_artifacts

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_mock_uow(source)),
            patch(
                "app.workers.document_task_stages._get_project_llm_options",
                return_value={},
            ),
            patch("app.utils.common.dump_json_debug"),
            patch(
                "app.services.source_code_pipeline.pipeline_orchestrator.PipelineOrchestrator"
            ) as mock_orchestrator_cls,
            patch(
                "app.workers.source_code_task._handle_source_code_cancellation",
                side_effect=[False, False],
            ),
            patch("app.workers.source_code_task.emit_task_event"),
            patch("app.workers.source_code_task._add_source_code_stage") as mock_add_stage,
            patch("app.workers.source_code_task._record_global_artifacts_activity"),
            patch(
                "app.workers.source_code_task._update_source_ingestion_fields"
            ) as mock_update_fields,
            patch("app.workers.source_code_task._mark_status") as mock_mark_status,
            patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
            patch(
                "app.workers.source_code_task._cleanup_project_folder_task.apply_async"
            ) as mock_cleanup,
        ):
            mock_orchestrator_cls.configure_project.return_value = "/tmp/p1/config.json"
            mock_orchestrator_cls.return_value = mock_pipeline

            result = await _run_pipeline_orchestrator(
                project_id="p1",
                source_id="00000000-0000-0000-0000-000000000001",
                codebase_dir="/tmp/source_codes/p1/codebase",
                task_db_id="task-1",
            )

        assert result == {
            "modules_dispatched": 0,
            "group_specs_stored": 0,
            "backlog_stories_stored": 0,
            "srs_evidence_stored": 0,
            "status": SOURCE_STATUS_FAILED,
        }
        source_id = "00000000-0000-0000-0000-000000000001"
        assert mock_add_stage.call_args_list == [
            call(source_id, SourceIngestionStage.BUILDING_CODE_DEPENDENCY_GRAPH),
            call(source_id, SourceIngestionStage.DISCOVERING_MODULES),
            call(source_id, SourceIngestionStage.EXTRACTING_REQUIREMENTS),
        ]
        # filtered_modules is empty here, so global-artifact discovery found 0
        # modules — tot_modules_from_global_artifact must reflect that count,
        # independent of tot_modules (which only counts successfully persisted
        # modules, set later at pipeline finalization).
        mock_update_fields.assert_called_once_with(
            source_ids=[source_id], fields={"tot_modules_from_global_artifact": 0}
        )
        mock_mark_status.assert_called_once_with(
            source_id,
            SOURCE_STATUS_FAILED,
            task_db_id="task-1",
            project_id="p1",
            stage="source_code.pipeline.failed",
        )
        mock_finalize.assert_called_once_with(
            project_id="p1",
            source_id=source_id,
            status=SourceIngestionStatus.FAILED.value,
            task_db_id="task-1",
            error="No modules were detected in the codebase — nothing to process.",
        )
        mock_cleanup.assert_called_once_with(args=["p1"], countdown=30)

    @pytest.mark.asyncio
    async def test_persists_modules_detected_count_from_filtered_modules(self):
        """tot_modules_from_global_artifact must be the raw discovery count —
        e.g. modules that later fail processing still count here, unlike the
        tot_modules rollup set at pipeline finalization."""
        source_ingestion = SimpleNamespace(
            source_language=None,
            frontend_stack=None,
            backend_stack=None,
            infrastructure_stack=None,
            architecture_stack=None,
            database_stack=None,
            coding_standard=None,
            database_strategy=None,
            architecture=None,
            security=None,
            source_layout_type=None,
        )
        source = SimpleNamespace(source_ingestion=source_ingestion)

        dependency_graph = {
            "global_index": {},
            "artifacts_detected": {},
            "artifacts_enriched": {},
        }
        global_artifacts = {
            **dependency_graph,
            "module_manifest": {},
            "budget_report": {},
            "filtered_modules": [{"id": "MOD-1"}, {"id": "MOD-2"}, {"id": "MOD-3"}],
        }

        mock_pipeline = MagicMock()
        mock_pipeline.generate_code_dependency_graph.return_value = dependency_graph
        mock_pipeline.discover_modules.return_value = global_artifacts

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_mock_uow(source)),
            patch(
                "app.workers.document_task_stages._get_project_llm_options",
                return_value={},
            ),
            patch("app.utils.common.dump_json_debug"),
            patch(
                "app.services.source_code_pipeline.pipeline_orchestrator.PipelineOrchestrator"
            ) as mock_orchestrator_cls,
            patch(
                "app.workers.source_code_task._handle_source_code_cancellation",
                side_effect=[False, True],
            ),
            patch("app.workers.source_code_task.emit_task_event"),
            patch("app.workers.source_code_task._add_source_code_stage"),
            patch("app.workers.source_code_task._record_global_artifacts_activity"),
            patch(
                "app.workers.source_code_task._update_source_ingestion_fields"
            ) as mock_update_fields,
        ):
            mock_orchestrator_cls.configure_project.return_value = "/tmp/p1/config.json"
            mock_orchestrator_cls.return_value = mock_pipeline

            await _run_pipeline_orchestrator(
                project_id="p1",
                source_id="00000000-0000-0000-0000-000000000001",
                codebase_dir="/tmp/source_codes/p1/codebase",
                task_db_id="task-1",
            )

        mock_update_fields.assert_called_once_with(
            source_ids=["00000000-0000-0000-0000-000000000001"],
            fields={"tot_modules_from_global_artifact": 3},
        )

    @pytest.mark.asyncio
    async def test_blocks_on_dispatched_chain_and_returns_final_pipeline_status(self):
        """_run_pipeline_orchestrator must wait for the last module chain's
        _persist_single_module_task result and surface its "status" instead
        of always returning SOURCE_STATUS_RUNNING once modules are dispatched."""
        source_ingestion = SimpleNamespace(
            source_language=None,
            frontend_stack=None,
            backend_stack=None,
            infrastructure_stack=None,
            architecture_stack=None,
            database_stack=None,
            coding_standard=None,
            database_strategy=None,
            architecture=None,
            security=None,
            source_layout_type=None,
        )
        source = SimpleNamespace(source_ingestion=source_ingestion)

        dependency_graph = {
            "global_index": {},
            "artifacts_detected": {},
            "artifacts_enriched": {},
        }
        global_artifacts = {
            **dependency_graph,
            "module_manifest": {},
            "budget_report": {},
            "filtered_modules": [{"id": "MOD-1"}],
        }

        mock_pipeline = MagicMock()
        mock_pipeline.generate_code_dependency_graph.return_value = dependency_graph
        mock_pipeline.discover_modules.return_value = global_artifacts

        final_module_result = {
            "modules_processed": 1,
            "group_specs_stored": 0,
            "backlog_stories_stored": 0,
            "srs_evidence_stored": 0,
            "status": SOURCE_STATUS_FAILED,
        }
        mock_async_result = MagicMock()
        mock_async_result.get.return_value = final_module_result
        mock_chain_signature = MagicMock()
        mock_chain_signature.apply_async.return_value = mock_async_result

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_mock_uow(source)),
            patch(
                "app.workers.document_task_stages._get_project_llm_options",
                return_value={},
            ),
            patch("app.utils.common.dump_json_debug"),
            patch(
                "app.services.source_code_pipeline.pipeline_orchestrator.PipelineOrchestrator"
            ) as mock_orchestrator_cls,
            patch(
                "app.workers.source_code_task._handle_source_code_cancellation",
                side_effect=[False, False],
            ),
            patch("app.workers.source_code_task.emit_task_event"),
            patch("app.workers.source_code_task._add_source_code_stage"),
            patch("app.workers.source_code_task._record_global_artifacts_activity"),
            patch("app.workers.source_code_task._update_source_ingestion_fields"),
            patch("app.workers.source_code_task.chain", return_value=mock_chain_signature),
        ):
            mock_orchestrator_cls.configure_project.return_value = "/tmp/p1/config.json"
            mock_orchestrator_cls.return_value = mock_pipeline

            result = await _run_pipeline_orchestrator(
                project_id="p1",
                source_id="00000000-0000-0000-0000-000000000001",
                codebase_dir="/tmp/source_codes/p1/codebase",
                task_db_id="task-1",
            )

        mock_chain_signature.apply_async.assert_called_once_with()
        mock_async_result.get.assert_called_once_with(disable_sync_subtasks=False)
        assert result["status"] == SOURCE_STATUS_FAILED
        assert result["modules_dispatched"] == 1

    @pytest.mark.asyncio
    async def test_dispatches_modules_as_one_sequential_chain_not_parallel_group(self):
        """TC-1.3: modules within one project must run one at a time, never
        in parallel — guaranteed by composing every module's
        (process -> persist) sub-chain into a single outer Celery chain()
        and dispatching that one chain, rather than a group() (which would
        run every sub-chain concurrently) or N independently-dispatched
        chains (which Celery's broker could then interleave across
        workers)."""
        source_ingestion = SimpleNamespace(
            source_language=None,
            frontend_stack=None,
            backend_stack=None,
            infrastructure_stack=None,
            architecture_stack=None,
            database_stack=None,
            coding_standard=None,
            database_strategy=None,
            architecture=None,
            security=None,
            source_layout_type=None,
        )
        source = SimpleNamespace(source_ingestion=source_ingestion)

        dependency_graph = {
            "global_index": {},
            "artifacts_detected": {},
            "artifacts_enriched": {},
        }
        global_artifacts = {
            **dependency_graph,
            "module_manifest": {},
            "budget_report": {},
            "filtered_modules": [{"id": "MOD-1"}, {"id": "MOD-2"}, {"id": "MOD-3"}],
        }

        mock_pipeline = MagicMock()
        mock_pipeline.generate_code_dependency_graph.return_value = dependency_graph
        mock_pipeline.discover_modules.return_value = global_artifacts

        mock_async_result = MagicMock()
        mock_async_result.get.return_value = {"status": SOURCE_STATUS_READY_FOR_REVIEW}
        mock_chain_signature = MagicMock()
        mock_chain_signature.apply_async.return_value = mock_async_result

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=_mock_uow(source)),
            patch(
                "app.workers.document_task_stages._get_project_llm_options",
                return_value={},
            ),
            patch("app.utils.common.dump_json_debug"),
            patch(
                "app.services.source_code_pipeline.pipeline_orchestrator.PipelineOrchestrator"
            ) as mock_orchestrator_cls,
            patch(
                "app.workers.source_code_task._handle_source_code_cancellation",
                side_effect=[False, False],
            ),
            patch("app.workers.source_code_task.emit_task_event"),
            patch("app.workers.source_code_task._add_source_code_stage"),
            patch("app.workers.source_code_task._record_global_artifacts_activity"),
            patch("app.workers.source_code_task._update_source_ingestion_fields"),
            patch(
                "app.workers.source_code_task.chain", return_value=mock_chain_signature
            ) as mock_chain,
        ):
            mock_orchestrator_cls.configure_project.return_value = "/tmp/p1/config.json"
            mock_orchestrator_cls.return_value = mock_pipeline

            result = await _run_pipeline_orchestrator(
                project_id="p1",
                source_id="00000000-0000-0000-0000-000000000001",
                codebase_dir="/tmp/source_codes/p1/codebase",
                task_db_id="task-1",
            )

        # chain() is called once per module to build its own (process ->
        # persist) sub-chain (3 calls), then ONE more time to compose all 3
        # sub-chains into a single outer chain — never group() (not even
        # imported by this module), which would run them concurrently
        # instead of one after another.
        assert mock_chain.call_count == 4
        outer_call_args = mock_chain.call_args_list[-1].args
        assert len(outer_call_args) == 3
        # That single composed chain is dispatched exactly once.
        mock_chain_signature.apply_async.assert_called_once_with()
        assert result["modules_dispatched"] == 3


class TestAddSourceCodeStage:
    def test_tags_stage_and_commits(self):
        mock_uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = mock_uow
        cm.__exit__.return_value = None
        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService.add_stage_by_source_ids"
            ) as mock_add_stage,
        ):
            _add_source_code_stage(
                "00000000-0000-0000-0000-000000000001", SourceIngestionStage.INGESTING_SOURCES
            )

        mock_add_stage.assert_called_once_with(
            mock_uow,
            [UUID("00000000-0000-0000-0000-000000000001")],
            SourceIngestionStage.INGESTING_SOURCES,
        )
        mock_uow.commit.assert_called_once()

    def test_swallows_exceptions(self):
        with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db unavailable")):
            _add_source_code_stage(
                "00000000-0000-0000-0000-000000000001", SourceIngestionStage.INGESTING_SOURCES
            )


class TestAddRunStageBySourceIds:
    def test_tags_stage_and_commits(self):
        mock_uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = mock_uow
        cm.__exit__.return_value = None
        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService.add_stage_by_source_ids"
            ) as mock_add_stage,
        ):
            _add_run_stage_by_source_ids(
                ["00000000-0000-0000-0000-000000000001"],
                SourceIngestionStage.USER_STORY_READY_FOR_REVIEW,
            )

        mock_add_stage.assert_called_once_with(
            mock_uow,
            [UUID("00000000-0000-0000-0000-000000000001")],
            SourceIngestionStage.USER_STORY_READY_FOR_REVIEW,
        )
        mock_uow.commit.assert_called_once()

    def test_swallows_exceptions(self):
        with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db unavailable")):
            _add_run_stage_by_source_ids(
                ["00000000-0000-0000-0000-000000000001"],
                SourceIngestionStage.USER_STORY_READY_FOR_REVIEW,
            )


class TestRecordFeatureRegenerationActivity:
    """Tests for _record_feature_regeneration_activity, called once at
    _run_feature_mfu_regeneration_task's success completion (any_succeeded branch)."""

    def test_builds_message_with_target_count(self, _mock_source_code_record_activity):
        target_dicts = [
            {"module_id": "MOD-1", "mfu_id": "MFU-1"},
            {"module_id": "MOD-1", "mfu_id": "MFU-2"},
        ]
        bucket_outcomes = [{"module_id": "MOD-1", "mfu_id": "MFU-1", "status": "completed"}]

        with patch(
            "app.workers.source_code_task.resolve_actor_from_task",
            return_value=None,
        ):
            _record_feature_regeneration_activity(
                project_id="00000000-0000-0000-0000-000000000f02",
                task_db_id=None,
                target_dicts=target_dicts,
                bucket_outcomes=bucket_outcomes,
            )

        _mock_source_code_record_activity.assert_called_once()
        call_kwargs = _mock_source_code_record_activity.call_args.kwargs
        assert call_kwargs["project_id"] == UUID("00000000-0000-0000-0000-000000000f02")
        assert call_kwargs["activity_type"].value == "source_code_feedback_regenerated"
        assert call_kwargs["data"] == {"targets": target_dicts, "results": bucket_outcomes}
        assert "2" in call_kwargs["message"]


def test_run_feature_mfu_regeneration_task_entry_cancel_marks_ingestion_cancelled(
    _mock_source_code_record_activity,
) -> None:
    """A cancel requested before the regeneration even starts must leave the
    per-request SourceIngestion row "cancelled", not "failed" — regression
    test for a bug where this branch wrote FAILED with an
    "Cancelled by user request." error message instead."""
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0, id="req-1"), max_retries=3)

    with (
        patch("app.workers.source_code_task.mark_cancelled_and_check", return_value=True),
        patch(
            "app.workers.source_code_task._update_feature_regeneration_ingestion_status"
        ) as mock_update_status,
    ):
        result = _run_feature_mfu_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000f03",
            feedback_items=[{"mod_code": "MOD-1", "mfu_id": "MFU-1"}],
            module_metadata_by_code={"MOD-1": {"module_response": {}, "module_manifest": {}}},
            source_paradigm="oop",
            task_db_id="task-1",
            ingestion_id="ingestion-1",
        )

    assert result == {"project_id": "00000000-0000-0000-0000-000000000f03", "status": "cancelled"}
    mock_update_status.assert_called_once_with("ingestion-1", SourceIngestionStatus.CANCELLED.value)
    _mock_source_code_record_activity.assert_called_once()
    assert (
        _mock_source_code_record_activity.call_args.kwargs["activity_type"]
        == ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED
    )


def test_run_feature_mfu_regeneration_task_fails_immediately_on_concurrent_pipeline_error() -> None:
    """Same reasoning as _parse_code_task's ConcurrentPipelineError handling:
    the pipeline single-flight guard rejecting this run is transient
    contention, not a genuine failure, but must still fail immediately
    rather than being retried via Celery — no Celery/Redis-level retry layer
    on top of the pipeline's own retry handling."""
    from app.services.source_code_pipeline.src.ai.llm_client import ConcurrentPipelineError

    guard_exc = ConcurrentPipelineError("Another pipeline run is already active in this process")
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0, id="req-1"), max_retries=3)

    with (
        patch("app.workers.source_code_task.mark_cancelled_and_check", return_value=False),
        patch("app.workers.source_code_task._run_async", side_effect=guard_exc),
        patch("app.workers.source_code_task.emit_task_event"),
        patch(
            "app.workers.source_code_task._update_feature_regeneration_ingestion_status"
        ) as mock_update_status,
        patch("app.workers.source_code_task._notify_feature_regeneration_status") as mock_notify,
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.exists", return_value=False),
    ):
        result = _run_feature_mfu_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000f04",
            feedback_items=[{"mod_code": "MOD-1", "mfu_id": "MFU-1"}],
            module_metadata_by_code={"MOD-1": {"module_response": {}, "module_manifest": {}}},
            source_paradigm="oop",
            task_db_id="task-1",
            ingestion_id="ingestion-1",
        )

    assert result == {
        "project_id": "00000000-0000-0000-0000-000000000f04",
        "status": SOURCE_INGESTION_STATUS_FAILED,
        "error": str(guard_exc),
    }
    mock_update_status.assert_called_once_with(
        "ingestion-1", SourceIngestionStatus.FAILED.value, error=str(guard_exc)
    )
    mock_notify.assert_any_call(
        project_id="00000000-0000-0000-0000-000000000f04",
        status=SourceIngestionStatus.FAILED.value,
        target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
        error=str(guard_exc),
        error_reason=None,
    )


def test_run_feature_mfu_regeneration_task_fails_immediately_on_circuit_breaker() -> None:
    """CircuitBreakerError is deliberately a BaseException (see llm_client.py's
    class docstring), so unlike ConcurrentPipelineError it would NOT be caught
    by a plain `except Exception` — left unhandled it would escape this task
    entirely uncaught, which under the real -P prefork pool crashes the
    worker child outright instead of recording a normal failure (see
    docs/CircuitBreakerError_Handling_Issue_Implications.docx). Must be
    caught explicitly and fail immediately, same as ConcurrentPipelineError,
    with no Celery retry."""
    from app.services.source_code_pipeline.src.ai.llm_client import CircuitBreakerError

    breaker_exc = CircuitBreakerError("8 consecutive LLM timeout(s) >= 8 cap")
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0, id="req-1"), max_retries=3)

    with (
        patch("app.workers.source_code_task.mark_cancelled_and_check", return_value=False),
        patch("app.workers.source_code_task._run_async", side_effect=breaker_exc),
        patch("app.workers.source_code_task.emit_task_event"),
        patch(
            "app.workers.source_code_task._update_feature_regeneration_ingestion_status"
        ) as mock_update_status,
        patch("app.workers.source_code_task._notify_feature_regeneration_status") as mock_notify,
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.exists", return_value=False),
    ):
        result = _run_feature_mfu_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000f05",
            feedback_items=[{"mod_code": "MOD-1", "mfu_id": "MFU-1"}],
            module_metadata_by_code={"MOD-1": {"module_response": {}, "module_manifest": {}}},
            source_paradigm="oop",
            task_db_id="task-1",
            ingestion_id="ingestion-1",
        )

    assert result == {
        "project_id": "00000000-0000-0000-0000-000000000f05",
        "status": SOURCE_INGESTION_STATUS_FAILED,
        "error": str(breaker_exc),
    }
    mock_update_status.assert_called_once_with(
        "ingestion-1", SourceIngestionStatus.FAILED.value, error=str(breaker_exc)
    )
    mock_notify.assert_any_call(
        project_id="00000000-0000-0000-0000-000000000f05",
        status=SourceIngestionStatus.FAILED.value,
        target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
        error=str(breaker_exc),
        error_reason="circuit_breaker",
    )


def test_run_feature_mfu_regeneration_task_fails_immediately_on_unexpected_exception() -> None:
    """TC-3.5: an unexpected (non-ConcurrentPipelineError) failure used to
    retry once via Celery (60s backoff) before failing — inconsistent with
    every other source-code-pipeline task, which never schedules a Celery
    retry (app/services/source_code_pipeline already retries LLM calls
    internally). Must now fail immediately, same as the ConcurrentPipelineError
    branch above. `task_self` deliberately has no `.retry` attribute, so a
    regression that reintroduces `self.retry(...)` fails loudly here with an
    AttributeError rather than silently passing."""
    exc = RuntimeError("unexpected regeneration error")
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0, id="req-1"), max_retries=3)

    with (
        patch("app.workers.source_code_task.mark_cancelled_and_check", return_value=False),
        patch("app.workers.source_code_task._run_async", side_effect=exc),
        patch("app.workers.source_code_task.emit_task_event"),
        patch(
            "app.workers.source_code_task._update_feature_regeneration_ingestion_status"
        ) as mock_update_status,
        patch("app.workers.source_code_task._notify_feature_regeneration_status") as mock_notify,
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.exists", return_value=False),
    ):
        result = _run_feature_mfu_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000f05",
            feedback_items=[{"mod_code": "MOD-1", "mfu_id": "MFU-1"}],
            module_metadata_by_code={"MOD-1": {"module_response": {}, "module_manifest": {}}},
            source_paradigm="oop",
            task_db_id="task-1",
            ingestion_id="ingestion-1",
        )

    assert result == {
        "project_id": "00000000-0000-0000-0000-000000000f05",
        "status": SOURCE_INGESTION_STATUS_FAILED,
        "error": str(exc),
    }
    mock_update_status.assert_called_once_with(
        "ingestion-1", SourceIngestionStatus.FAILED.value, error=str(exc)
    )
    mock_notify.assert_any_call(
        project_id="00000000-0000-0000-0000-000000000f05",
        status=SourceIngestionStatus.FAILED.value,
        target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
        error=str(exc),
    )


def test_run_feature_mfu_regeneration_task_fails_immediately_on_credit_balance_exhausted() -> None:
    """CRITICAL REGRESSION GUARD: before this fix, CreditBalanceExhaustedError
    (e.g. Anthropic's "Your credit balance is too low to access the
    Anthropic API") was not handled ANYWHERE in this task's except chain —
    it's a BaseException (see llm_client.py's class docstring), so it is not
    caught by `except Exception`, and would have escaped this task entirely
    uncaught. Under this deployment's real -P prefork pool that crashes the
    worker child outright (never resolves to FAILURE, just hangs at PENDING,
    and with acks_late=True + task_reject_on_worker_lost the broker then
    redelivers the same message, crash-looping) instead of finalizing the
    SourceIngestion as FAILED with the provider error recorded. `task_self`
    deliberately has no `.retry` attribute, so an accidental Celery retry
    fails loudly here with an AttributeError rather than silently passing."""
    from app.services.source_code_pipeline.src.ai.llm_client import (
        CreditBalanceExhaustedError,
    )

    credit_exc = CreditBalanceExhaustedError(
        "[LLMClient] Provider billing/credits exhausted — Your credit balance "
        "is too low to access the Anthropic API."
    )
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0, id="req-1"), max_retries=3)

    with (
        patch("app.workers.source_code_task.mark_cancelled_and_check", return_value=False),
        patch("app.workers.source_code_task._run_async", side_effect=credit_exc),
        patch("app.workers.source_code_task.emit_task_event"),
        patch(
            "app.workers.source_code_task._update_feature_regeneration_ingestion_status"
        ) as mock_update_status,
        patch("app.workers.source_code_task._notify_feature_regeneration_status") as mock_notify,
        patch("app.workers.source_code_task.record_activity") as mock_record_activity,
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.exists", return_value=False),
    ):
        result = _run_feature_mfu_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000f06",
            feedback_items=[{"mod_code": "MOD-1", "mfu_id": "MFU-1"}],
            module_metadata_by_code={"MOD-1": {"module_response": {}, "module_manifest": {}}},
            source_paradigm="oop",
            task_db_id="task-1",
            ingestion_id="ingestion-1",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    assert "credit balance is too low" in result["error"]
    mock_update_status.assert_called_once_with(
        "ingestion-1", SourceIngestionStatus.FAILED.value, error=result["error"]
    )
    mock_notify.assert_any_call(
        project_id="00000000-0000-0000-0000-000000000f06",
        status=SourceIngestionStatus.FAILED.value,
        target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
        error=result["error"],
        error_reason="credit_exhausted",
    )
    failed_activity = next(
        c for c in mock_record_activity.call_args_list
        if c.kwargs["activity_type"].value == "source_code_feedback_regeneration_failed"
    )
    assert failed_activity.kwargs["data"]["llm_error_reason"] == "credit_exhausted"


def test_run_feature_mfu_regeneration_task_fails_immediately_on_non_retryable_llm_error() -> None:
    """Every OTHER non-retryable LLM reason (auth, invalid model, invalid
    request, context-length-exceeded, content policy, permission denied)
    gets the same fail-fast treatment as CreditBalanceExhaustedError above."""
    from app.core.llm_errors import LLMErrorClassification, LLMErrorReason, NonRetryableLLMError

    classification = LLMErrorClassification(
        retryable=False,
        reason=LLMErrorReason.CONTENT_POLICY,
        provider="openai",
        message="The request was rejected due to content policy.",
        user_message="The AI provider rejected the request due to its content policy.",
    )
    non_retryable_exc = NonRetryableLLMError(classification)
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0, id="req-1"), max_retries=3)

    with (
        patch("app.workers.source_code_task.mark_cancelled_and_check", return_value=False),
        patch("app.workers.source_code_task._run_async", side_effect=non_retryable_exc),
        patch("app.workers.source_code_task.emit_task_event"),
        patch(
            "app.workers.source_code_task._update_feature_regeneration_ingestion_status"
        ) as mock_update_status,
        patch("app.workers.source_code_task._notify_feature_regeneration_status") as mock_notify,
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.exists", return_value=False),
    ):
        result = _run_feature_mfu_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000f07",
            feedback_items=[{"mod_code": "MOD-1", "mfu_id": "MFU-1"}],
            module_metadata_by_code={"MOD-1": {"module_response": {}, "module_manifest": {}}},
            source_paradigm="oop",
            task_db_id="task-1",
            ingestion_id="ingestion-1",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    assert "content_policy" in result["error"]
    mock_update_status.assert_called_once_with(
        "ingestion-1", SourceIngestionStatus.FAILED.value, error=result["error"]
    )
    mock_notify.assert_any_call(
        project_id="00000000-0000-0000-0000-000000000f07",
        status=SourceIngestionStatus.FAILED.value,
        target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
        error=result["error"],
        error_reason="content_policy",
    )


class TestRunFeatureMfuRegenerationTaskFinalStatus:
    """_run_feature_mfu_regeneration_task must gate a succeeded regeneration

    behind human accept/reject rather than marking it COMPLETED outright —
    its Feature/UserStory writes only carry a pending `feedback_change_type`,
    mirroring `_run_story_feedback_patch_task`'s ready_for_review handoff.
    """

    def _run(self, *, bucket_outcomes: list[dict]) -> dict:
        task_self = SimpleNamespace(request=SimpleNamespace(retries=0, id="req-1"), max_retries=3)
        with (
            patch("app.workers.source_code_task._run_async", return_value=["raw-bucket"]),
            patch(
                "app.workers.source_code_task._persist_feature_regeneration_buckets",
                return_value=bucket_outcomes,
            ),
            patch(
                "app.workers.source_code_task._update_feature_regeneration_ingestion_status"
            ) as self.mock_update_status,
            patch("app.workers.source_code_task._add_run_stage_by_id") as self.mock_add_stage,
            patch(
                "app.workers.source_code_task._notify_feature_regeneration_status"
            ) as self.mock_notify,
            patch("app.workers.source_code_task.emit_task_event"),
            patch("pathlib.Path.mkdir"),
        ):
            return _run_feature_mfu_regeneration_task(
                self=task_self,
                project_id="00000000-0000-0000-0000-000000000f02",
                feedback_items=[{"mod_code": "MOD-1", "mfu_id": "MFU-1"}],
                module_metadata_by_code={"MOD-1": {"module_response": {}, "module_manifest": {}}},
                source_paradigm="oop",
                task_db_id="task-1",
                ingestion_id="ingestion-1",
            )

    def test_success_sets_ready_for_review_not_completed(
        self, _mock_source_code_record_activity
    ) -> None:
        bucket_outcomes = [
            {
                "module_id": "MOD-1",
                "mfu_id": "MFU-1",
                "status": SOURCE_INGESTION_STATUS_READY_FOR_REVIEW,
            }
        ]

        result = self._run(bucket_outcomes=bucket_outcomes)

        assert result["status"] == SOURCE_INGESTION_STATUS_READY_FOR_REVIEW
        self.mock_update_status.assert_called_once_with(
            "ingestion-1",
            SourceIngestionStatus.READY_FOR_REVIEW.value,
            mark_completed=True,
            error=None,
        )
        self.mock_add_stage.assert_called_once_with(
            ingestion_id="ingestion-1", stage=SourceIngestionStage.READY_FOR_REVIEW
        )
        # Notifies once at start (running) and once at completion.
        assert self.mock_notify.call_count == 2
        self.mock_notify.assert_any_call(
            project_id="00000000-0000-0000-0000-000000000f02",
            status=SourceIngestionStatus.RUNNING.value,
            target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
        )
        self.mock_notify.assert_any_call(
            project_id="00000000-0000-0000-0000-000000000f02",
            status=SourceIngestionStatus.READY_FOR_REVIEW.value,
            target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
        )
        # Records activity once at start and once at completion.
        recorded_types = {
            c.kwargs["activity_type"].value
            for c in _mock_source_code_record_activity.call_args_list
        }
        assert recorded_types == {
            "source_code_feedback_regeneration_started",
            "source_code_feedback_regenerated",
        }

    def test_all_buckets_failed_stays_failed_without_stage(
        self, _mock_source_code_record_activity
    ) -> None:
        bucket_outcomes = [
            {
                "module_id": "MOD-1",
                "mfu_id": "MFU-1",
                "status": SOURCE_INGESTION_STATUS_FAILED,
                "error": "boom",
            }
        ]

        result = self._run(bucket_outcomes=bucket_outcomes)

        assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
        self.mock_update_status.assert_called_once_with(
            "ingestion-1",
            SourceIngestionStatus.FAILED.value,
            mark_completed=False,
            error="MOD-1/MFU-1: boom",
        )
        self.mock_add_stage.assert_not_called()
        # Notifies once at start (running) and once at completion (failed).
        assert self.mock_notify.call_count == 2
        self.mock_notify.assert_any_call(
            project_id="00000000-0000-0000-0000-000000000f02",
            status=SourceIngestionStatus.RUNNING.value,
            target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
        )
        self.mock_notify.assert_any_call(
            project_id="00000000-0000-0000-0000-000000000f02",
            status=SourceIngestionStatus.FAILED.value,
            target_dicts=[{"module_id": "MOD-1", "mfu_id": "MFU-1"}],
            error="MOD-1/MFU-1: boom",
        )
        # Records activity once at start and once at completion (failed).
        recorded_types = {
            c.kwargs["activity_type"].value
            for c in _mock_source_code_record_activity.call_args_list
        }
        assert recorded_types == {
            "source_code_feedback_regeneration_started",
            "source_code_feedback_regeneration_failed",
        }


def test_process_single_module_task_returns_failed_on_unexpected_exception() -> None:
    result = _process_single_module_task.run(
        project_dir=".",
        config_path="./pyproject.toml",
        codebase_dir="./app",
        module_data={"module_id": "MOD-DEBUG", "module_name": "debug"},
        artifacts_enriched={},
        module_manifest={},
    )

    assert result["status"] == "failed"
    assert result["module_id"] == "MOD-DEBUG"
    assert result["error"]


class TestProcessSingleModuleTaskRetryPolicy:
    """CELERY_RETRY_POLICY.docx Section A: process_single_module retries once
    on transient infrastructure before failing. Uses the real bound Celery
    task's push_request/pop_request (not a fake `self`) since this task is
    not split into a wrapper + plain-inner-function like
    _run_feature_mfu_regeneration_task, and Task.request is a read-only
    property that can't be monkeypatched directly."""

    def _run(self, *, side_effect: BaseException, retries: int = 0):
        _process_single_module_task.push_request(retries=retries, id="req-1")
        try:
            with (
                patch(
                    "app.core.task_control.register_delivery_within_limit",
                    return_value=True,
                ),
                patch(
                    "app.services.source_code_pipeline.pipeline_orchestrator.PipelineContext",
                    side_effect=side_effect,
                ),
                patch.object(
                    _process_single_module_task,
                    "retry",
                    side_effect=RuntimeError("retry-triggered"),
                ) as mock_retry,
            ):
                try:
                    result = _process_single_module_task.run(
                        project_dir=".",
                        config_path="./pyproject.toml",
                        codebase_dir="./app",
                        module_data={"module_id": "MOD-1", "module_name": "one"},
                        artifacts_enriched={},
                        module_manifest={},
                    )
                except RuntimeError as exc:
                    if str(exc) != "retry-triggered":
                        raise
                    result = None
            return result, mock_retry
        finally:
            _process_single_module_task.pop_request()

    def test_retries_once_on_concurrent_pipeline_error(self) -> None:
        from app.services.source_code_pipeline.src.ai.llm_client import (
            ConcurrentPipelineError,
        )

        exc = ConcurrentPipelineError("another run holds the lock")
        result, mock_retry = self._run(side_effect=exc, retries=0)

        assert result is None
        mock_retry.assert_called_once()
        assert (
            mock_retry.call_args.kwargs["countdown"]
            == SOURCE_CODE_CONCURRENT_PIPELINE_RETRY_COUNTDOWN_SECONDS
        )


    def test_concurrent_pipeline_error_fails_when_retries_exhausted(self) -> None:
        from app.services.source_code_pipeline.src.ai.llm_client import (
            ConcurrentPipelineError,
        )

        exc = ConcurrentPipelineError("another run holds the lock")
        result, mock_retry = self._run(side_effect=exc, retries=1)

        mock_retry.assert_not_called()
        assert result == {"module_id": "MOD-1", "error": str(exc), "status": "failed"}

    def test_retries_once_on_soft_time_limit_exceeded(self) -> None:
        """Only ConcurrentPipelineError gets the long lock-contention
        countdown — a transient infra/timeout retry still uses the short
        default so it doesn't wait unnecessarily long."""
        result, mock_retry = self._run(side_effect=SoftTimeLimitExceeded(), retries=0)

        assert result is None
        mock_retry.assert_called_once()
        assert (
            mock_retry.call_args.kwargs["countdown"] == SOURCE_CODE_TASK_RETRY_COUNTDOWN_SECONDS
        )

    def test_soft_time_limit_exceeded_fails_when_retries_exhausted(self) -> None:
        result, mock_retry = self._run(side_effect=SoftTimeLimitExceeded(), retries=1)

        mock_retry.assert_not_called()
        assert result["status"] == "failed"
        assert result["module_id"] == "MOD-1"

    def test_retries_once_on_retryable_redis_error(self) -> None:
        import redis.exceptions

        exc = redis.exceptions.ConnectionError("broker blip")
        result, mock_retry = self._run(side_effect=exc, retries=0)

        assert result is None
        mock_retry.assert_called_once()
        assert (
            mock_retry.call_args.kwargs["countdown"] == SOURCE_CODE_TASK_RETRY_COUNTDOWN_SECONDS
        )

    def test_retries_once_on_retryable_boto_client_error(self) -> None:
        from botocore.exceptions import ClientError

        exc = ClientError(
            {"Error": {"Code": "Throttling"}, "ResponseMetadata": {"HTTPStatusCode": 400}},
            "GetObject",
        )
        result, mock_retry = self._run(side_effect=exc, retries=0)

        assert result is None
        mock_retry.assert_called_once()
        assert (
            mock_retry.call_args.kwargs["countdown"] == SOURCE_CODE_TASK_RETRY_COUNTDOWN_SECONDS
        )

    def test_does_not_retry_non_retryable_boto_client_error(self) -> None:
        from botocore.exceptions import ClientError

        exc = ClientError(
            {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
            "GetObject",
        )
        result, mock_retry = self._run(side_effect=exc, retries=0)

        mock_retry.assert_not_called()
        assert result["status"] == "failed"

    def test_does_not_retry_deterministic_error(self) -> None:
        """Unchanged behavior: a plain ValueError still fails immediately."""
        result, mock_retry = self._run(side_effect=ValueError("bad config"), retries=0)

        mock_retry.assert_not_called()
        assert result == {"module_id": "MOD-1", "error": "bad config", "status": "failed"}

    def test_circuit_breaker_error_raises_task_failure_without_retry(self) -> None:
        """CircuitBreakerError must never be swallowed into a "failed module,
        continue" result (that would defeat the breaker's job of stopping the
        rest of this chain) nor retried (self.retry() is never called) — it
        must propagate as CircuitBreakerTaskFailure so chain() stops
        dispatching the next module while Celery still records a normal
        FAILURE instead of crashing the -P prefork worker child (see
        docs/CircuitBreakerError_Handling_Issue_Implications.docx)."""
        from app.services.source_code_pipeline.src.ai.llm_client import CircuitBreakerError
        from app.workers._task_helpers import CircuitBreakerTaskFailure

        breaker_exc = CircuitBreakerError("8 consecutive LLM timeout(s) >= 8 cap")
        _process_single_module_task.push_request(retries=0, id="req-1")
        try:
            with (
                patch(
                    "app.core.task_control.register_delivery_within_limit",
                    return_value=True,
                ),
                patch(
                    "app.services.source_code_pipeline.pipeline_orchestrator.PipelineContext",
                    side_effect=breaker_exc,
                ),
                patch.object(_process_single_module_task, "retry") as mock_retry,
                pytest.raises(CircuitBreakerTaskFailure, match="8 consecutive LLM timeout"),
            ):
                _process_single_module_task.run(
                    project_dir=".",
                    config_path="./pyproject.toml",
                    codebase_dir="./app",
                    module_data={"module_id": "MOD-1", "module_name": "one"},
                    artifacts_enriched={},
                    module_manifest={},
                )
        finally:
            _process_single_module_task.pop_request()

        mock_retry.assert_not_called()

    def test_credit_balance_exhausted_raises_task_failure_without_retry(self) -> None:
        """CreditBalanceExhaustedError (e.g. Anthropic's "Your credit balance
        is too low") must never be swallowed into a "failed module, continue"
        result — every other module still queued behind this one would hit
        the identical account-wide billing wall — nor retried (self.retry()
        is never called). It must propagate as
        CreditBalanceExhaustedTaskFailure so chain() stops dispatching the
        next module while Celery still records a normal FAILURE instead of
        crashing the -P prefork worker child."""
        from app.services.source_code_pipeline.src.ai.llm_client import (
            CreditBalanceExhaustedError,
        )
        from app.workers._task_helpers import CreditBalanceExhaustedTaskFailure

        credit_exc = CreditBalanceExhaustedError(
            "Your credit balance is too low to access the Anthropic API."
        )
        _process_single_module_task.push_request(retries=0, id="req-1")
        try:
            with (
                patch(
                    "app.core.task_control.register_delivery_within_limit",
                    return_value=True,
                ),
                patch(
                    "app.services.source_code_pipeline.pipeline_orchestrator.PipelineContext",
                    side_effect=credit_exc,
                ),
                patch.object(_process_single_module_task, "retry") as mock_retry,
                pytest.raises(CreditBalanceExhaustedTaskFailure, match="credit balance is too low"),
            ):
                _process_single_module_task.run(
                    project_dir=".",
                    config_path="./pyproject.toml",
                    codebase_dir="./app",
                    module_data={"module_id": "MOD-1", "module_name": "one"},
                    artifacts_enriched={},
                    module_manifest={},
                )
        finally:
            _process_single_module_task.pop_request()

        mock_retry.assert_not_called()

    def test_non_retryable_llm_error_raises_task_failure_without_retry(self) -> None:
        """Every OTHER non-retryable LLM reason (auth, invalid model, invalid
        request, context-length-exceeded, content policy, permission denied)
        must get the exact same abort-the-whole-run treatment as
        CircuitBreakerError/CreditBalanceExhaustedError above — never
        swallowed into a "failed module, continue" result, never retried,
        and re-raised as NonRetryableLLMTaskFailure so chain() stops
        dispatching the next module while Celery still records a normal
        FAILURE instead of crashing the -P prefork worker child."""
        from app.core.llm_errors import LLMErrorClassification, LLMErrorReason, NonRetryableLLMError
        from app.workers._task_helpers import NonRetryableLLMTaskFailure

        classification = LLMErrorClassification(
            retryable=False,
            reason=LLMErrorReason.AUTHENTICATION,
            provider="anthropic",
            message="Invalid API key provided.",
            user_message="The AI provider rejected the request due to an invalid API key.",
        )
        auth_exc = NonRetryableLLMError(classification)
        _process_single_module_task.push_request(retries=0, id="req-1")
        try:
            with (
                patch(
                    "app.core.task_control.register_delivery_within_limit",
                    return_value=True,
                ),
                patch(
                    "app.services.source_code_pipeline.pipeline_orchestrator.PipelineContext",
                    side_effect=auth_exc,
                ),
                patch.object(_process_single_module_task, "retry") as mock_retry,
                pytest.raises(NonRetryableLLMTaskFailure, match="authentication"),
            ):
                _process_single_module_task.run(
                    project_dir=".",
                    config_path="./pyproject.toml",
                    codebase_dir="./app",
                    module_data={"module_id": "MOD-1", "module_name": "one"},
                    artifacts_enriched={},
                    module_manifest={},
                )
        finally:
            _process_single_module_task.pop_request()

        mock_retry.assert_not_called()

    def test_poison_loop_guard_dead_letters_without_processing(self) -> None:
        with (
            patch(
                "app.core.task_control.register_delivery_within_limit",
                return_value=False,
            ) as mock_guard,
            patch(
                "app.services.source_code_pipeline.pipeline_orchestrator.PipelineContext"
            ) as mock_context,
        ):
            result = _process_single_module_task.run(
                project_dir=".",
                config_path="./pyproject.toml",
                codebase_dir="./app",
                module_data={"module_id": "MOD-1", "module_name": "one"},
                artifacts_enriched={},
                module_manifest={},
            )

        mock_guard.assert_called_once()
        mock_context.assert_not_called()
        assert result["status"] == "failed"
        assert result["module_id"] == "MOD-1"
        assert "poison-loop guard" in result["error"]


def test_build_backlog_result_from_results_normalizes_story_nfrs() -> None:
    module_results = [
        {
            "feature_derivation": {
                "results": [
                    {
                        "module_id": "M-1",
                        "module_name": "Module One",
                        "features": [
                            {
                                "id": "F-1",
                                "title": "Feature One",
                                "description": "Feature description",
                                "user_stories": [
                                    {
                                        "id": "U.S 1.1.1",
                                        "title": "Story one",
                                        "as_a": "user",
                                        "i_want_to": "do thing",
                                        "so_that": "get value",
                                        "acceptance_criteria": [],
                                        "story_points": 2,
                                        "non_functional_requirements": [
                                            {
                                                "id": "NFR-1",
                                                "category": "Security",
                                                "requirement": "Use parameterized queries.",
                                                "derived_from": "implied_gap",
                                                "extra": "must-be-dropped",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        }
    ]

    result = _build_backlog_result_from_results(
        project_id="project-1",
        source_id="source-1",
        module_results=module_results,
    )

    stories = result["output"]["epics"][0]["stories"]
    assert len(stories) == 1
    assert stories[0]["nfrs"] == [
        {
            "id": "NFR-1",
            "category": "Security",
            "requirement": "Use parameterized queries.",
        }
    ]


@pytest.mark.asyncio
async def test_async_parse_code_happy_path() -> None:
    source = SimpleNamespace(
        id=99,
        project_id="p1",
        original_name="x.zip",
        file_type="zip",
        mime_type="application/zip",
        storage_key="s3/key",
        checksum_sha256="sha-456",
    )

    summary = {"modules_processed": 1}

    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_mock_uow(source)),
        patch(
            "app.workers._task_helpers._download_source_zip_from_s3",
            AsyncMock(return_value=b"zip-bytes"),
        ) as mock_download,
        patch(
            "app.workers._task_helpers._extract_zip_to_codebase_folder",
            return_value="/tmp/source_codes/p1/codebase",
        ) as mock_extract,
        patch(
            "app.workers.source_code_task._run_pipeline_orchestrator",
            AsyncMock(return_value=summary),
        ) as mock_pipeline,
    ):
        result = await _async_parse_code("p1", "00000000-0000-0000-0000-000000000001")

    assert result == summary
    mock_download.assert_awaited_once_with("00000000-0000-0000-0000-000000000001", "s3/key")
    mock_extract.assert_called_once_with(b"zip-bytes", "p1")
    mock_pipeline.assert_awaited_once_with(
        project_id="p1",
        source_id="00000000-0000-0000-0000-000000000001",
        codebase_dir="/tmp/source_codes/p1/codebase",
        task_db_id=None,
        skip_processing=False,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_async_build_fragments_sets_source_type_from_source_file_type_when_missing() -> None:
    source_id = "00000000-0000-0000-0000-000000000777"
    source = SimpleNamespace(
        original_name="x.pdf",
        mime_type="application/pdf",
        file_type="pdf",
    )
    parse_result = SimpleNamespace(
        chunks=[
            {"frag_type": "text", "content": "a", "bbox": []},
            {"frag_type": "text", "content": "b", "bbox": [], "source_type": ""},
            {"frag_type": "text", "content": "c", "bbox": [], "source_type": "md"},
        ]
    )

    parser = MagicMock()
    parser.parse_and_format_for_alternate_pipeline = AsyncMock(return_value=parse_result)
    fragment_service = MagicMock()
    fragment_service.create_fragments_from_chunks = AsyncMock(return_value=3)

    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_mock_uow(source)),
        patch("app.services.document_parser_service.DocumentService", return_value=parser),
        patch("app.services.fragment_service.FragmentService", return_value=fragment_service),
    ):
        count = await _async_build_fragments(source_id=source_id)

    assert count == 3
    fragment_service.create_fragments_from_chunks.assert_awaited_once()
    chunks = fragment_service.create_fragments_from_chunks.call_args.kwargs["chunks"]
    assert chunks[0]["source_type"] == "pdf"
    assert chunks[1]["source_type"] == "pdf"
    assert chunks[2]["source_type"] == "md"


@pytest.mark.asyncio
async def test_async_build_fragments_does_not_override_when_source_file_type_missing() -> None:
    source_id = "00000000-0000-0000-0000-000000000778"
    source = SimpleNamespace(
        original_name="x.bin",
        mime_type="application/octet-stream",
        file_type=None,
    )
    parse_result = SimpleNamespace(
        chunks=[
            {"frag_type": "text", "content": "a", "bbox": []},
            {"frag_type": "text", "content": "b", "bbox": [], "source_type": "txt"},
        ]
    )

    parser = MagicMock()
    parser.parse_and_format_for_alternate_pipeline = AsyncMock(return_value=parse_result)
    fragment_service = MagicMock()
    fragment_service.create_fragments_from_chunks = AsyncMock(return_value=2)

    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_mock_uow(source)),
        patch("app.services.document_parser_service.DocumentService", return_value=parser),
        patch("app.services.fragment_service.FragmentService", return_value=fragment_service),
    ):
        count = await _async_build_fragments(source_id=source_id)

    assert count == 2
    chunks = fragment_service.create_fragments_from_chunks.call_args.kwargs["chunks"]
    assert "source_type" not in chunks[0]
    assert chunks[1]["source_type"] == "txt"


def test_parse_document_task_dispatches_module_generation_after_processing() -> None:
    source_ids = ["00000000-0000-0000-0000-000000000011", "00000000-0000-0000-0000-000000000022"]

    # Batch existence check returns both sources found.
    sources = [SimpleNamespace(id=UUID(sid)) for sid in source_ids]
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = sources
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
        patch("app.workers.document_task_stages._mark_status"),
        patch("app.workers.document_task_stages._run_async", side_effect=[2, 3]),
        patch(
            "app.workers.document_task.generate_modules_and_features_task.apply_async"
        ) as mock_apply_async,
    ):
        result = parse_document_task.run("project-1", source_ids)

    assert result["status"] == "running"
    assert result["processed_sources"] == 2
    mock_apply_async.assert_called_once_with(args=["project-1", source_ids, None, False, None])


def test_parse_document_task_dispatches_module_generation_with_skip_processing_true() -> None:
    source_ids = ["00000000-0000-0000-0000-000000000055"]
    sources = [SimpleNamespace(id=UUID(source_ids[0]))]
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = sources
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
        patch("app.workers.document_task_stages._mark_status"),
        patch("app.workers.document_task_stages._run_async", return_value=1),
        patch(
            "app.workers.document_task.generate_modules_and_features_task.apply_async"
        ) as mock_apply_async,
    ):
        result = parse_document_task.run("project-1", source_ids, None, True)

    assert result["status"] == "running"
    mock_apply_async.assert_called_once_with(args=["project-1", source_ids, None, True, None])


class TestCountModuleFeatureRegenerationChanges:
    """A regeneration's persisted modules/features must be split into added
    vs. updated by their own feedback_change_type, not treated as a flat
    "everything in the output is new" total."""

    def test_splits_added_and_updated_across_modules_and_features(self):
        stored_modules = [
            SimpleNamespace(
                feedback_change_type=ChangeType.ADDED,
                features=[SimpleNamespace(feedback_change_type=ChangeType.UPDATED)],
            ),
            SimpleNamespace(
                feedback_change_type=ChangeType.UPDATED,
                features=[
                    SimpleNamespace(feedback_change_type=ChangeType.ADDED),
                    SimpleNamespace(feedback_change_type=None),
                ],
            ),
        ]

        result = _count_module_feature_regeneration_changes(stored_modules)

        assert result == {
            "modules_added": 1,
            "modules_updated": 1,
            "features_added": 1,
            "features_updated": 1,
        }

    def test_empty_stored_modules_returns_zero_counts(self):
        assert _count_module_feature_regeneration_changes([]) == {
            "modules_added": 0,
            "modules_updated": 0,
            "features_added": 0,
            "features_updated": 0,
        }

    def test_unchanged_module_and_features_are_not_counted(self):
        stored_modules = [
            SimpleNamespace(
                feedback_change_type=None,
                features=[SimpleNamespace(feedback_change_type=None)],
            )
        ]

        result = _count_module_feature_regeneration_changes(stored_modules)

        assert result == {
            "modules_added": 0,
            "modules_updated": 0,
            "features_added": 0,
            "features_updated": 0,
        }


def test_run_module_feature_phase_forwards_skip_processing_true() -> None:
    module_feature_result = {
        "output": {
            "feature_inventory": [],
            "business_requirements": [],
            "exclusions": [],
        }
    }

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_module_feature.run_module_feature",
            MagicMock(return_value="module-coro"),
        ) as mock_run_module_feature,
        patch(
            "app.workers.document_task_stages._run_async",
            return_value=module_feature_result,
        ),
        patch(
            "app.workers.document_task_stages._get_project_llm_options",
            return_value={},
        ),
        patch(
            "app.workers.document_task_stages._update_project_metadata_best_effort",
        ) as mock_update_project_metadata,
    ):
        _run_module_feature_phase(
            project_id="project-mf",
            fragments=[{"id": "frag-1"}],
            skip_processing=True,
        )

    assert mock_run_module_feature.call_count == 1
    assert mock_run_module_feature.call_args.kwargs["skip_processing"] is True
    mock_update_project_metadata.assert_called_once_with(
        project_id="project-mf",
        business_requirements=[],
        exclusions=[],
    )


def test_run_module_feature_phase_dumps_output_as_parsed_json() -> None:
    raw_output = {"feature_inventory": [], "business_requirements": [], "exclusions": []}
    module_feature_result = {
        "output": json.dumps(raw_output),
        "status": "PASS",
        "ai_feedback": "",
        "iteration": 1,
    }

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_module_feature.run_module_feature",
            MagicMock(return_value="module-coro"),
        ),
        patch(
            "app.workers.document_task_stages._run_async",
            return_value=module_feature_result,
        ),
        patch(
            "app.workers.document_task_stages._get_project_llm_options",
            return_value={},
        ),
        patch("app.workers.document_task_stages._update_project_metadata_best_effort"),
        patch("app.utils.common.dump_json_debug") as mock_dump_json_debug,
    ):
        _run_module_feature_phase(
            project_id="project-mf",
            fragments=[{"id": "frag-1"}],
        )

    output_dump_call = next(
        c
        for c in mock_dump_json_debug.call_args_list
        if c.args[0] == "module_feature_generation_output.json"
    )
    dumped_data = output_dump_call.args[1]
    assert dumped_data["output"] == raw_output
    assert dumped_data["status"] == "PASS"


def test_run_module_feature_phase_forwards_is_regeneration_to_upsert() -> None:
    module_feature_result = {
        "output": {
            "feature_inventory": [
                {"mod_code": "1", "name": "Module 1", "description": "desc", "features": []}
            ],
            "business_requirements": [],
            "exclusions": [],
        }
    }
    mock_service = MagicMock()
    mock_service.upsert_modules_and_features_v2 = AsyncMock()

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_module_feature.run_module_feature",
            MagicMock(return_value="module-coro"),
        ),
        patch(
            "app.workers.document_task_stages._run_async",
            return_value=module_feature_result,
        ),
        patch(
            "app.workers.document_task_stages._get_project_llm_options",
            return_value={},
        ),
        patch("app.workers.document_task_stages._update_project_metadata_best_effort"),
        patch(
            "app.workers.document_task_stages.ModuleFeatureService",
            return_value=mock_service,
        ),
    ):
        _run_module_feature_phase(
            project_id="00000000-0000-0000-0000-000000000001",
            fragments=[{"id": "frag-1"}],
            is_regeneration=True,
        )

    mock_service.upsert_modules_and_features_v2.assert_called_once()
    assert mock_service.upsert_modules_and_features_v2.call_args.kwargs["is_regeneration"] is True


def test_run_module_feature_phase_defaults_is_regeneration_false() -> None:
    module_feature_result = {
        "output": {
            "feature_inventory": [
                {"mod_code": "1", "name": "Module 1", "description": "desc", "features": []}
            ],
            "business_requirements": [],
            "exclusions": [],
        }
    }
    mock_service = MagicMock()
    mock_service.upsert_modules_and_features_v2 = AsyncMock()

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_module_feature.run_module_feature",
            MagicMock(return_value="module-coro"),
        ),
        patch(
            "app.workers.document_task_stages._run_async",
            return_value=module_feature_result,
        ),
        patch(
            "app.workers.document_task_stages._get_project_llm_options",
            return_value={},
        ),
        patch("app.workers.document_task_stages._update_project_metadata_best_effort"),
        patch(
            "app.workers.document_task_stages.ModuleFeatureService",
            return_value=mock_service,
        ),
    ):
        _run_module_feature_phase(
            project_id="00000000-0000-0000-0000-000000000001",
            fragments=[{"id": "frag-1"}],
        )

    assert mock_service.upsert_modules_and_features_v2.call_args.kwargs["is_regeneration"] is False


def test_run_module_feature_phase_forwards_source_ingestion_id_to_upsert() -> None:
    module_feature_result = {
        "output": {
            "feature_inventory": [
                {"mod_code": "1", "name": "Module 1", "description": "desc", "features": []}
            ],
            "business_requirements": [],
            "exclusions": [],
        }
    }
    mock_service = MagicMock()
    mock_service.upsert_modules_and_features_v2 = AsyncMock()

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_module_feature.run_module_feature",
            MagicMock(return_value="module-coro"),
        ),
        patch(
            "app.workers.document_task_stages._run_async",
            return_value=module_feature_result,
        ),
        patch(
            "app.workers.document_task_stages._get_project_llm_options",
            return_value={},
        ),
        patch("app.workers.document_task_stages._update_project_metadata_best_effort"),
        patch(
            "app.workers.document_task_stages.ModuleFeatureService",
            return_value=mock_service,
        ),
    ):
        _run_module_feature_phase(
            project_id="00000000-0000-0000-0000-000000000001",
            fragments=[{"id": "frag-1"}],
            source_ingestion_id="ingestion-42",
        )

    assert (
        mock_service.upsert_modules_and_features_v2.call_args.kwargs["source_ingestion_id"]
        == "ingestion-42"
    )


def test_run_source_module_feature_phase_resolves_and_forwards_source_ingestion_id() -> None:
    source_ids = ["00000000-0000-0000-0000-000000000001"]

    with (
        patch("app.workers.document_task_stages._run_async", return_value=[{"id": "frag-1"}]),
        patch(
            "app.workers.document_task_stages._resolve_source_ingestion_id",
            return_value="ingestion-77",
        ) as mock_resolve,
        patch(
            "app.workers.document_task_stages._run_module_feature_phase",
            return_value=("module-output", []),
        ) as mock_phase,
    ):
        fragments, output = _run_source_module_feature_phase(
            project_id="project-x",
            source_ids=source_ids,
        )

    mock_resolve.assert_called_once_with(source_ids=source_ids)
    assert mock_phase.call_args.kwargs["source_ingestion_id"] == "ingestion-77"
    assert output == "module-output"


def test_run_backlog_phase_forwards_skip_processing_true() -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)
    backlog_items = {
        "persona_glossary": [
            {
                "persona": "Resident",
                "description": "Apartment resident recharging gas meter.",
            }
        ],
        "epics": [],
    }
    story_service = MagicMock()
    story_service.upsert_user_stories_from_backlog.return_value = "upsert-coro"

    def _strict_run_agile_backlog(
        *,
        fragments,
        modules_and_features,
        user_stories=None,
        feedback="",
        skip_processing=False,
        options=None,
    ):
        return "backlog-coro"

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog.run_agile_backlog",
            side_effect=_strict_run_agile_backlog,
        ) as mock_run_agile_backlog,
        patch(
            "app.services.user_story_service.UserStoryService",
            return_value=story_service,
        ),
        patch(
            # 3 _run_async calls when skip_processing=True: run_agile_backlog,
            # then the new _remap_skip_processing_feature_ids pass (a no-op here
            # since this fixture's backlog_items has no "output" JSON string, so
            # it returns None), then upsert_user_stories_from_backlog.
            "app.workers.document_task_stages._run_async",
            side_effect=[backlog_items, None, 0],
        ),
        patch(
            "app.workers.document_task_stages._get_project_llm_options",
            return_value={},
        ),
        patch(
            "app.workers.document_task_stages._update_project_metadata_best_effort",
        ) as mock_update_project_metadata,
    ):
        result = _run_backlog_phase(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000123",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="",
            stage_prefix="user_story.generation",
            skip_processing=True,
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_READY_FOR_REVIEW
    assert mock_run_agile_backlog.call_count == 1
    assert mock_run_agile_backlog.call_args.kwargs["skip_processing"] is True
    mock_update_project_metadata.assert_called_once_with(
        project_id="00000000-0000-0000-0000-000000000123",
        persona_glossary=[
            {
                "persona": "Resident",
                "description": "Apartment resident recharging gas meter.",
            }
        ],
    )


def test_run_backlog_phase_fails_immediately_on_non_retryable_llm_error() -> None:
    """The dict-return contract (not a raise) gains llm_error_reason/
    llm_user_message keys so the caller can notify with a reason — self.retry
    is never called, unlike a generic Exception."""
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3, retry=MagicMock())
    fake_error = _fake_non_retryable_error(reason=LLMErrorReason.CREDIT_EXHAUSTED)

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog.run_agile_backlog",
            return_value="backlog-coro",
        ),
        patch(
            "app.workers.document_task_stages._run_async",
            side_effect=fake_error,
        ),
        patch(
            "app.workers.document_task_stages._get_project_llm_options",
            return_value={},
        ),
        patch("app.workers.document_task_stages.emit_task_event") as mock_emit,
    ):
        result = _run_backlog_phase(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000123",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="",
            stage_prefix="user_story.generation",
            task_db_id="task-bp1",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    assert result["llm_error_reason"] == "credit_exhausted"
    assert result["llm_user_message"]
    assert mock_emit.call_args.kwargs["status"] == SOURCE_INGESTION_STATUS_FAILED
    task_self.retry.assert_not_called()


def test_run_user_story_backlog_task_story_generation_fails_immediately_on_non_retryable_llm_error(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch(
            "app.workers.document_task_stages._run_backlog_phase",
            return_value={
                "status": SOURCE_INGESTION_STATUS_FAILED,
                "error": "[authentication] Invalid API key",
                "llm_error_reason": "authentication",
                "llm_user_message": "The AI provider rejected the request due to an invalid API key.",
            },
        ),
        patch("app.workers.document_task_stages._notify_user_story_status") as mock_notify,
        patch("app.workers.document_task_stages._add_source_ingestion_error"),
        patch("app.workers.document_task_stages._mark_sources_status_by_project"),
        patch(
            "app.workers.document_task_stages._cancel_sibling_tasks_on_fatal_llm_error"
        ) as mock_cancel_siblings,
    ):
        result = _run_user_story_backlog_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000c9",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="",
            stage_prefix="user_story.generation",
            task_type="story_generation",
            task_db_id="task-us9",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    failed_call = mock_notify.call_args_list[-1]
    assert failed_call.kwargs["error_reason"] == "authentication"
    assert failed_call.kwargs["error"] == (
        "The AI provider rejected the request due to an invalid API key."
    )
    mock_cancel_siblings.assert_called_once_with(
        request_id="task-us9", task_db_id="task-us9", project_id="00000000-0000-0000-0000-0000000000c9"
    )
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert "rfp_user_stories_generation_failed" in recorded_types
    failed_activity = next(
        c for c in _mock_record_activity.call_args_list
        if c.kwargs["activity_type"].value == "rfp_user_stories_generation_failed"
    )
    assert failed_activity.kwargs["data"]["llm_error_reason"] == "authentication"


def test_run_user_story_backlog_task_story_regeneration_fails_immediately_on_non_retryable_llm_error(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch(
            "app.workers.document_task_stages._run_backlog_phase",
            return_value={
                "status": SOURCE_INGESTION_STATUS_FAILED,
                "error": "[context_length_exceeded] prompt too long",
                "llm_error_reason": "context_length_exceeded",
                "llm_user_message": "The input is too large for the AI model's context limit.",
            },
        ),
        patch(
            "app.workers.document_task_stages._notify_user_story_regeneration_status"
        ) as mock_notify_regen,
        patch("app.workers.document_task_stages._add_source_ingestion_error_by_id"),
        patch(
            "app.workers.document_task_stages._cancel_sibling_tasks_on_fatal_llm_error"
        ) as mock_cancel_siblings,
    ):
        result = _run_user_story_backlog_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000a3",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="tighten scope",
            stage_prefix="user_story.regeneration",
            task_type="story_regeneration",
            task_db_id="task-us10",
            ingestion_id="ingestion-direct",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    failed_call = mock_notify_regen.call_args_list[-1]
    assert failed_call.kwargs["error_reason"] == "context_length_exceeded"
    mock_cancel_siblings.assert_called_once_with(
        request_id="task-us10", task_db_id="task-us10", project_id="00000000-0000-0000-0000-0000000000a3"
    )


def test_run_user_story_backlog_task_story_generation_entry_cancel_records_activity(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with patch("app.workers.document_task_stages.mark_cancelled_and_check", return_value=True):
        result = _run_user_story_backlog_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000c9",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="",
            stage_prefix="user_story.generation",
            task_type="story_generation",
            task_db_id="task-us9",
            source_ids=["00000000-0000-0000-0000-000000000199"],
        )

    assert result == {
        "project_id": "00000000-0000-0000-0000-0000000000c9",
        "status": "cancelled",
    }
    _mock_record_activity.assert_called_once()
    call_kwargs = _mock_record_activity.call_args.kwargs
    assert call_kwargs["activity_type"] == ActivityType.RFP_USER_STORIES_GENERATION_CANCELLED
    assert call_kwargs["data"]["source_ids"] == ["00000000-0000-0000-0000-000000000199"]


def test_run_user_story_backlog_task_story_regeneration_entry_cancel_records_activity(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with patch("app.workers.document_task_stages.mark_cancelled_and_check", return_value=True):
        result = _run_user_story_backlog_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000a3",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="tighten scope",
            stage_prefix="user_story.regeneration",
            task_type="story_regeneration",
            task_db_id="task-us10",
            ingestion_id="ingestion-direct",
        )

    assert result == {
        "project_id": "00000000-0000-0000-0000-0000000000a3",
        "status": "cancelled",
    }
    _mock_record_activity.assert_called_once()
    call_kwargs = _mock_record_activity.call_args.kwargs
    assert call_kwargs["activity_type"] == ActivityType.RFP_USER_STORIES_REGENERATION_CANCELLED
    assert call_kwargs["data"]["ingestion_id"] == "ingestion-direct"


def test_run_backlog_phase_forwards_source_ingestion_id() -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)
    backlog_items = {"epics": []}
    story_service = MagicMock()
    story_service.upsert_user_stories_from_backlog.return_value = "upsert-coro"

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog.run_agile_backlog",
            return_value="backlog-coro",
        ),
        patch(
            "app.services.user_story_service.UserStoryService",
            return_value=story_service,
        ),
        patch(
            "app.workers.document_task_stages._run_async",
            side_effect=[backlog_items, 0],
        ),
        patch(
            "app.workers.document_task_stages._get_project_llm_options",
            return_value={},
        ),
        patch("app.workers.document_task_stages._update_project_metadata_best_effort"),
    ):
        _run_backlog_phase(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000123",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="",
            stage_prefix="user_story.generation",
            source_ingestion_id="ingestion-88",
        )

    assert (
        story_service.upsert_user_stories_from_backlog.call_args.kwargs["source_ingestion_id"]
        == "ingestion-88"
    )


def test_run_user_story_backlog_task_story_generation_completed_flow(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    source_ids = ["00000000-0000-0000-0000-000000000abc"]

    with (
        patch(
            "app.workers.document_task_stages._run_backlog_phase",
            return_value={"status": SOURCE_INGESTION_STATUS_READY_FOR_REVIEW, "total_items": 5},
        ),
        patch("app.workers.document_task_stages._notify_user_story_status") as mock_notify,
        patch("app.workers.document_task_stages._add_run_stage_by_source_ids") as mock_add_stage,
        patch(
            "app.workers.document_task_stages._mark_sources_status_by_project"
        ) as mock_mark_sources,
    ):
        result = _run_user_story_backlog_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000a1",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="",
            stage_prefix="user_story.generation",
            task_type="story_generation",
            task_db_id="task-us1",
            source_ids=source_ids,
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_READY_FOR_REVIEW
    # Source.status must land on the same lifecycle value as SourceIngestion.status
    # and the task-event/backlog-phase completion marker — all READY_FOR_REVIEW now.
    assert mock_mark_sources.call_args.kwargs["status"] == SOURCE_STATUS_READY_FOR_REVIEW
    assert mock_notify.call_args_list == [
        (
            (),
            {"project_id": "00000000-0000-0000-0000-0000000000a1", "status": SOURCE_STATUS_RUNNING},
        ),
        (
            (),
            {
                "project_id": "00000000-0000-0000-0000-0000000000a1",
                "status": SourceIngestionStatus.READY_FOR_REVIEW.value,
                "total_user_stories": 5,
            },
        ),
    ]
    mock_add_stage.assert_called_once_with(
        source_ids, SourceIngestionStage.USER_STORY_READY_FOR_REVIEW
    )
    # Records activity once at start and once at completion.
    assert _mock_record_activity.call_count == 2
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert recorded_types == {
        "rfp_user_stories_generation_started",
        "rfp_user_stories_generated",
    }
    call_kwargs = _mock_record_activity.call_args.kwargs
    assert call_kwargs["project_id"] == UUID("00000000-0000-0000-0000-0000000000a1")
    assert call_kwargs["activity_type"].value == "rfp_user_stories_generated"
    assert call_kwargs["data"]["total_items"] == 5


def test_run_user_story_backlog_task_story_generation_resolves_source_ingestion_id(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)
    source_ids = ["00000000-0000-0000-0000-000000000abc"]

    with (
        patch(
            "app.workers.document_task_stages._resolve_source_ingestion_id",
            return_value="ingestion-resolved",
        ) as mock_resolve,
        patch(
            "app.workers.document_task_stages._run_backlog_phase",
            return_value={"status": SOURCE_INGESTION_STATUS_READY_FOR_REVIEW, "total_items": 1},
        ) as mock_run_phase,
        patch("app.workers.document_task_stages._notify_user_story_status"),
        patch("app.workers.document_task_stages._add_run_stage_by_source_ids"),
        patch("app.workers.document_task_stages._mark_sources_status_by_project"),
    ):
        _run_user_story_backlog_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000a1",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="",
            stage_prefix="user_story.generation",
            task_type="story_generation",
            task_db_id="task-us1",
            source_ids=source_ids,
        )

    mock_resolve.assert_called_once_with(source_ids=source_ids)
    assert mock_run_phase.call_args.kwargs["source_ingestion_id"] == "ingestion-resolved"


def test_run_user_story_backlog_task_story_regeneration_forwards_ingestion_id(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch(
            "app.workers.document_task_stages._update_source_ingestion_fields_by_id"
        ) as mock_update_ingestion,
        patch(
            "app.workers.document_task_stages._run_backlog_phase",
            return_value={"status": SOURCE_INGESTION_STATUS_READY_FOR_REVIEW, "total_items": 1},
        ) as mock_run_phase,
        patch("app.workers.document_task_stages._notify_user_story_regeneration_status"),
    ):
        _run_user_story_backlog_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000a3",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="feedback",
            stage_prefix="user_story.regeneration",
            task_type="story_regeneration",
            task_db_id="task-us3",
            ingestion_id="ingestion-direct",
        )

    assert mock_run_phase.call_args.kwargs["source_ingestion_id"] == "ingestion-direct"
    # A dedicated-ingestion-row regeneration uses the generic started_at/
    # completed_at fields, not the RFP-generation-specific
    # user_story_gen_started_at/user_story_gen_completed_at pair.
    started_fields = mock_update_ingestion.call_args_list[0].kwargs["fields"]
    assert "started_at" in started_fields
    assert "user_story_gen_started_at" not in started_fields
    completed_fields = mock_update_ingestion.call_args_list[1].kwargs["fields"]
    assert "completed_at" in completed_fields
    assert "user_story_gen_completed_at" not in completed_fields


def test_run_user_story_backlog_task_story_generation_failed_flow(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch(
            "app.workers.document_task_stages._run_backlog_phase",
            return_value={"status": SOURCE_INGESTION_STATUS_FAILED, "error": "boom"},
        ),
        patch("app.workers.document_task_stages._notify_user_story_status") as mock_notify,
        patch("app.workers.document_task_stages._add_source_ingestion_error") as mock_add_error,
        patch(
            "app.workers.document_task_stages._mark_sources_status_by_project"
        ) as mock_mark_sources,
    ):
        result = _run_user_story_backlog_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000c9",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="",
            stage_prefix="user_story.generation",
            task_type="story_generation",
            task_db_id="task-us2",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    mock_add_error.assert_called_once_with(source_ids=[], error="boom")
    # Source.status must land on the same lifecycle value as SourceIngestion.status
    # (SourceIngestionStatus.FAILED), not the WebSocket-only
    # SOURCE_INGESTION_STATUS_FAILED marker.
    assert mock_mark_sources.call_args.kwargs["status"] == SOURCE_STATUS_FAILED
    assert mock_notify.call_args_list == [
        (
            (),
            {"project_id": "00000000-0000-0000-0000-0000000000c9", "status": SOURCE_STATUS_RUNNING},
        ),
        (
            (),
            {
                "project_id": "00000000-0000-0000-0000-0000000000c9",
                "status": SOURCE_INGESTION_STATUS_FAILED,
                "error": "boom",
                "error_reason": None,
            },
        ),
    ]
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert recorded_types == {
        "rfp_user_stories_generation_started",
        "rfp_user_stories_generation_failed",
    }


def test_run_user_story_backlog_task_story_regeneration_completed_notifies(
    _mock_record_activity,
) -> None:
    """Plain regeneration uses its own notifier, not the story_generation one."""
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch(
            "app.workers.document_task_stages._run_backlog_phase",
            return_value={"status": SOURCE_INGESTION_STATUS_READY_FOR_REVIEW, "total_items": 3},
        ),
        patch("app.workers.document_task_stages._notify_user_story_status") as mock_notify,
        patch(
            "app.workers.document_task_stages._notify_user_story_regeneration_status"
        ) as mock_notify_regen,
    ):
        _run_user_story_backlog_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000a3",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="tighten scope",
            stage_prefix="user_story.regeneration",
            task_type="story_regeneration",
            task_db_id="task-us3",
        )

    mock_notify.assert_not_called()
    # Notifies once at start (running) and once at completion.
    assert mock_notify_regen.call_count == 2
    mock_notify_regen.assert_any_call(
        project_id="00000000-0000-0000-0000-0000000000a3",
        status=SourceIngestionStatus.RUNNING.value,
    )
    mock_notify_regen.assert_any_call(
        project_id="00000000-0000-0000-0000-0000000000a3",
        status=SourceIngestionStatus.READY_FOR_REVIEW.value,
        total_user_stories=3,
    )
    # Records activity once at start and once at completion.
    assert _mock_record_activity.call_count == 2
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert recorded_types == {
        "rfp_user_stories_regeneration_started",
        "rfp_user_stories_regenerated",
    }


def test_run_user_story_backlog_task_story_regeneration_failed_notifies(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch(
            "app.workers.document_task_stages._run_backlog_phase",
            return_value={"status": SOURCE_INGESTION_STATUS_FAILED, "error": "boom"},
        ),
        patch("app.workers.document_task_stages._add_source_ingestion_error_by_id"),
        patch(
            "app.workers.document_task_stages._notify_user_story_regeneration_status"
        ) as mock_notify_regen,
    ):
        result = _run_user_story_backlog_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000a3",
            fragments=[],
            modules_and_features={"feature_inventory": []},
            user_stories=[],
            feedback="tighten scope",
            stage_prefix="user_story.regeneration",
            task_type="story_regeneration",
            task_db_id="task-us3",
            ingestion_id="ingestion-direct",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    # Notifies once at start (running) and once at completion (failed).
    assert mock_notify_regen.call_count == 2
    mock_notify_regen.assert_any_call(
        project_id="00000000-0000-0000-0000-0000000000a3",
        status=SourceIngestionStatus.RUNNING.value,
    )
    mock_notify_regen.assert_any_call(
        project_id="00000000-0000-0000-0000-0000000000a3",
        status=SourceIngestionStatus.FAILED.value,
        error="boom",
        error_reason=None,
    )
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert recorded_types == {
        "rfp_user_stories_regeneration_started",
        "rfp_user_stories_regeneration_failed",
    }


def test_generate_modules_and_features_task_completed_flow() -> None:
    source_ids = ["00000000-0000-0000-0000-000000000033"]

    with patch(
        "app.workers.document_task._run_source_module_feature_generation_task",
        return_value={
            "project_id": "project-2",
            "status": SOURCE_STATUS_READY_FOR_REVIEW,
            "processed_sources": 1,
            "total_fragments": 1,
            "total_modules": 2,
            "total_features": 3,
        },
    ):
        result = generate_modules_and_features_task.run("project-2", source_ids)

    assert result["status"] == SOURCE_STATUS_READY_FOR_REVIEW
    assert result["processed_sources"] == 1
    assert result["total_fragments"] == 1
    assert result["total_modules"] == 2
    assert result["total_features"] == 3


def test_regenerate_modules_and_features_task_completed_flow() -> None:
    with patch(
        "app.workers.document_task._run_module_feature_regeneration_task",
        return_value={
            "project_id": "project-3",
            "status": "completed",
            "total_modules": 2,
            "total_features": 3,
        },
    ):
        result = regenerate_modules_and_features_task.run(
            "project-3",
            ["00000000-0000-0000-0000-000000000044"],
            [{"id": "frag-1"}],
            {"feature_inventory": []},
            "Refine module boundaries",
        )

    assert result["status"] == "completed"
    assert result["total_modules"] == 2
    assert result["total_features"] == 3


def test_run_parse_document_task_returns_failed_when_source_resolution_fails() -> None:
    dispatch = MagicMock()

    with (
        patch(
            "app.workers.document_task_stages._resolve_valid_source_ids",
            return_value=([], "bad source ids"),
        ),
        patch("app.workers.document_task_stages._mark_sources_failed") as mock_mark_failed,
        patch(
            "app.workers.document_task_stages._fail_ingestion_for_parse_failure"
        ) as mock_fail_ingestion,
    ):
        result = _run_parse_document_task(
            project_id="project-x",
            source_ids=["not-a-uuid"],
            task_db_id=None,
            dispatch_generate_task=dispatch,
        )

    assert result["status"] == SOURCE_STATUS_FAILED
    assert result["error"] == "bad source ids"
    mock_mark_failed.assert_called_once()
    mock_fail_ingestion.assert_called_once_with(
        project_id="project-x",
        source_ids=["not-a-uuid"],
        errors=["bad source ids"],
    )
    dispatch.assert_not_called()


def test_run_parse_document_task_all_sources_failed_records_reason_on_ingestion() -> None:
    """Regression (P1821-376): a parse-phase failure must fail the ingestion
    with the specific reason and notify, not leave it ``running`` for the
    stale-run sweeper to fail with a generic message."""
    source_ids = ["00000000-0000-0000-0000-000000000001"]
    dispatch = MagicMock()

    with (
        patch(
            "app.workers.document_task_stages._resolve_valid_source_ids",
            return_value=(source_ids, None),
        ),
        patch(
            "app.workers.document_task_stages._process_document_sources",
            return_value=([], [{"source_id": source_ids[0], "error": "LlamaParse auth failed"}]),
        ),
        patch(
            "app.workers.document_task_stages._update_source_ingestion_fields"
        ) as mock_update_fields,
        patch("app.workers.document_task_stages._add_source_ingestion_error") as mock_add_error,
        patch("app.workers.document_task_stages._notify_module_feature_status") as mock_notify,
    ):
        result = _run_parse_document_task(
            project_id="project-z",
            source_ids=source_ids,
            task_db_id="task-1",
            dispatch_generate_task=dispatch,
        )

    assert result["status"] == SOURCE_STATUS_FAILED
    dispatch.assert_not_called()
    mock_update_fields.assert_called_once_with(
        source_ids=source_ids,
        fields={"status": SourceIngestionStatus.FAILED.value},
    )
    mock_add_error.assert_called_once_with(source_ids=source_ids, error="LlamaParse auth failed")
    mock_notify.assert_called_once_with(
        project_id="project-z",
        status=SourceIngestionStatus.FAILED.value,
        is_regeneration=False,
        error="LlamaParse auth failed",
    )


def test_run_parse_document_task_unexpected_failure_records_reason_on_ingestion() -> None:
    source_ids = ["00000000-0000-0000-0000-000000000001"]

    with (
        patch(
            "app.workers.document_task_stages._resolve_valid_source_ids",
            side_effect=RuntimeError("db down"),
        ),
        patch("app.workers.document_task_stages._mark_sources_failed"),
        patch("app.workers.document_task_stages._update_source_ingestion_fields"),
        patch("app.workers.document_task_stages._add_source_ingestion_error") as mock_add_error,
        patch("app.workers.document_task_stages._notify_module_feature_status"),
    ):
        result = _run_parse_document_task(
            project_id="project-z",
            source_ids=source_ids,
            task_db_id=None,
            dispatch_generate_task=MagicMock(),
        )

    assert result["status"] == SOURCE_STATUS_FAILED
    mock_add_error.assert_called_once_with(
        source_ids=source_ids, error="Unexpected task failure: db down"
    )


def test_fail_ingestion_for_parse_failure_dedupes_errors() -> None:
    with (
        patch("app.workers.document_task_stages._update_source_ingestion_fields"),
        patch("app.workers.document_task_stages._add_source_ingestion_error") as mock_add_error,
        patch("app.workers.document_task_stages._notify_module_feature_status") as mock_notify,
    ):
        _fail_ingestion_for_parse_failure(
            project_id="p",
            source_ids=["s1", "s2"],
            errors=["bad file", "bad file", "", "timeout"],
        )

    assert mock_add_error.call_args_list == [
        call(source_ids=["s1", "s2"], error="bad file"),
        call(source_ids=["s1", "s2"], error="timeout"),
    ]
    assert mock_notify.call_args.kwargs["error"] == "bad file; timeout"


def test_run_parse_document_task_dispatches_after_success() -> None:
    source_ids = ["00000000-0000-0000-0000-000000000001"]
    dispatch = MagicMock()

    with (
        patch(
            "app.workers.document_task_stages._resolve_valid_source_ids",
            return_value=(source_ids, None),
        ),
        patch(
            "app.workers.document_task_stages._process_document_sources",
            return_value=(source_ids, []),
        ),
        patch(
            "app.workers.document_task_stages.time.monotonic",
            side_effect=[10.0, 10.4],
        ),
    ):
        result = _run_parse_document_task(
            project_id="project-y",
            source_ids=source_ids,
            task_db_id="task-1",
            dispatch_generate_task=dispatch,
            skip_processing=False,
        )

    assert result["status"] == SOURCE_STATUS_RUNNING
    assert result["processed_sources"] == 1
    assert result["failed_sources"] == []
    assert result["duration_ms"] == 400
    dispatch.assert_called_once_with("project-y", source_ids, "task-1", False)


def test_run_source_module_feature_generation_task_completed_flow(
    _mock_record_activity,
) -> None:
    module_output = SimpleNamespace(
        feature_inventory=[
            SimpleNamespace(features=[object(), object()]),
            SimpleNamespace(features=[object()]),
        ]
    )
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch("app.workers.document_task_stages._mark_sources_status") as mock_mark_status,
        patch("app.workers.document_task_stages._notify_module_feature_status") as mock_notify,
        patch(
            "app.workers.document_task_stages._run_source_module_feature_phase",
            return_value=([{"id": "frag-1"}], module_output),
        ),
        patch("app.workers.document_task_stages._add_run_stage_by_source_ids") as mock_add_stage,
    ):
        result = _run_source_module_feature_generation_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-00000000002a",
            source_ids=["00000000-0000-0000-0000-000000000099"],
            task_db_id="task-99",
        )

    assert result["status"] == SOURCE_STATUS_READY_FOR_REVIEW
    assert result["processed_sources"] == 1
    assert result["total_fragments"] == 1
    assert result["total_modules"] == 2
    assert result["total_features"] == 3
    assert mock_mark_status.call_count == 2
    # Source.status must match SourceIngestion.status exactly at this
    # checkpoint — both report "ready_for_review", never a different string
    # for the same event (see mock_notify assertion below).
    assert mock_mark_status.call_args_list[1].kwargs["status"] == SOURCE_STATUS_READY_FOR_REVIEW
    mock_add_stage.assert_called_once_with(
        ["00000000-0000-0000-0000-000000000099"],
        SourceIngestionStage.MODULE_FEATURE_READY_FOR_REVIEW,
    )
    assert mock_notify.call_args_list == [
        (
            (),
            {
                "project_id": "00000000-0000-0000-0000-00000000002a",
                "status": SOURCE_STATUS_RUNNING,
                "is_regeneration": False,
            },
        ),
        (
            (),
            {
                "project_id": "00000000-0000-0000-0000-00000000002a",
                "status": SourceIngestionStatus.READY_FOR_REVIEW.value,
                "is_regeneration": False,
                "modules_added": 2,
                "features_added": 3,
            },
        ),
    ]
    # Records activity once at start and once at completion.
    assert _mock_record_activity.call_count == 2
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert recorded_types == {"rfp_modules_generation_started", "rfp_modules_generated"}
    call_kwargs = _mock_record_activity.call_args.kwargs
    assert call_kwargs["project_id"] == UUID("00000000-0000-0000-0000-00000000002a")
    assert call_kwargs["activity_type"].value == "rfp_modules_generated"
    assert call_kwargs["data"]["total_modules"] == 2
    assert call_kwargs["data"]["total_features"] == 3


def test_run_source_module_feature_generation_task_retry_path() -> None:
    task_self = MagicMock()
    task_self.request.retries = 0
    task_self.max_retries = 3
    task_self.retry.side_effect = RuntimeError("retry-triggered")

    with (
        patch("app.workers.document_task_stages._mark_sources_status") as mock_mark_status,
        patch("app.workers.document_task_stages._notify_module_feature_status") as mock_notify,
        patch(
            "app.workers.document_task_stages._run_source_module_feature_phase",
            side_effect=RuntimeError("phase failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="retry-triggered"):
            _run_source_module_feature_generation_task(
                self=task_self,
                project_id="00000000-0000-0000-0000-0000000000d1",
                source_ids=["00000000-0000-0000-0000-000000000111"],
                task_db_id="task-r1",
            )

    assert mock_mark_status.call_count == 2
    task_self.retry.assert_called_once()
    # Only the initial "running" notification fires for a still-retriable
    # failure — no "failed" notification until retries are exhausted.
    mock_notify.assert_called_once_with(
        project_id="00000000-0000-0000-0000-0000000000d1",
        status=SOURCE_STATUS_RUNNING,
        is_regeneration=False,
    )


def test_run_source_module_feature_generation_task_entry_cancel_records_activity(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch("app.workers.document_task_stages.mark_cancelled_and_check", return_value=True),
        patch(
            "app.workers.document_task_stages.mark_sources_and_ingestion_cancelled"
        ) as mock_mark_cancelled,
    ):
        result = _run_source_module_feature_generation_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-00000000002a",
            source_ids=["00000000-0000-0000-0000-000000000099"],
            task_db_id="task-99",
        )

    assert result == {
        "project_id": "00000000-0000-0000-0000-00000000002a",
        "status": "cancelled",
    }
    mock_mark_cancelled.assert_called_once()
    _mock_record_activity.assert_called_once()
    call_kwargs = _mock_record_activity.call_args.kwargs
    assert call_kwargs["activity_type"] == ActivityType.RFP_MODULES_GENERATION_CANCELLED
    assert call_kwargs["project_id"] == UUID("00000000-0000-0000-0000-00000000002a")


def _fake_non_retryable_error(
    reason: LLMErrorReason = LLMErrorReason.CREDIT_EXHAUSTED,
    message: str = "Your credit balance is too low to access the Anthropic API.",
) -> NonRetryableLLMError:
    classification = LLMErrorClassification(
        retryable=False,
        reason=reason,
        provider="anthropic",
        message=message,
        user_message="The AI provider account has run out of credits or quota.",
    )
    return NonRetryableLLMError(classification)


def test_run_source_module_feature_generation_task_fails_immediately_on_non_retryable_llm_error(
    _mock_record_activity,
) -> None:
    """A non-retryable LLM error (e.g. credit exhaustion) must never retry —
    it fails on the first attempt, unlike a generic Exception which retries
    up to max_retries before failing."""
    task_self = MagicMock()
    task_self.request.retries = 0
    task_self.max_retries = 3
    fake_error = _fake_non_retryable_error()

    with (
        patch("app.workers.document_task_stages._mark_sources_status") as mock_mark_status,
        patch("app.workers.document_task_stages._notify_module_feature_status") as mock_notify,
        patch(
            "app.workers.document_task_stages._run_source_module_feature_phase",
            side_effect=fake_error,
        ),
        patch("app.workers.document_task_stages._add_source_ingestion_error") as mock_add_error,
        patch(
            "app.workers.document_task_stages._cancel_sibling_tasks_on_fatal_llm_error"
        ) as mock_cancel_siblings,
    ):
        result = _run_source_module_feature_generation_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000d3",
            source_ids=["00000000-0000-0000-0000-000000000223"],
            task_db_id="task-r3",
        )

    assert result["status"] == SOURCE_STATUS_FAILED
    assert "credit_exhausted" in result["error"]
    task_self.retry.assert_not_called()
    mock_add_error.assert_called_once()
    assert "credit_exhausted" in mock_add_error.call_args.kwargs["error"]
    failed_call = mock_notify.call_args_list[-1]
    assert failed_call.kwargs["status"] == SOURCE_STATUS_FAILED
    assert failed_call.kwargs["error_reason"] == "credit_exhausted"
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert "rfp_modules_generation_failed" in recorded_types
    failed_activity = next(
        c for c in _mock_record_activity.call_args_list
        if c.kwargs["activity_type"].value == "rfp_modules_generation_failed"
    )
    assert failed_activity.kwargs["data"]["llm_error_reason"] == "credit_exhausted"
    mock_cancel_siblings.assert_called_once_with(
        request_id=None, task_db_id="task-r3", project_id="00000000-0000-0000-0000-0000000000d3"
    )
    assert mock_mark_status.call_count == 2  # started + failed only, no retry-attempt mark


def test_run_source_module_feature_generation_task_terminal_failure(
    _mock_record_activity,
) -> None:
    task_self = MagicMock()
    task_self.request.retries = 3
    task_self.max_retries = 3

    with (
        patch("app.workers.document_task_stages._mark_sources_status") as mock_mark_status,
        patch("app.workers.document_task_stages._notify_module_feature_status") as mock_notify,
        patch(
            "app.workers.document_task_stages._run_source_module_feature_phase",
            side_effect=RuntimeError("phase failed hard"),
        ),
        patch("app.workers.document_task_stages._add_source_ingestion_error") as mock_add_error,
    ):
        result = _run_source_module_feature_generation_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000d2",
            source_ids=["00000000-0000-0000-0000-000000000222"],
            task_db_id="task-r2",
        )

    assert result["status"] == SOURCE_STATUS_FAILED
    assert "phase failed hard" in result["error"]
    assert mock_mark_status.call_count == 2
    mock_add_error.assert_called_once_with(
        source_ids=["00000000-0000-0000-0000-000000000222"], error="phase failed hard"
    )
    task_self.retry.assert_not_called()
    assert mock_notify.call_args_list == [
        (
            (),
            {
                "project_id": "00000000-0000-0000-0000-0000000000d2",
                "status": SOURCE_STATUS_RUNNING,
                "is_regeneration": False,
            },
        ),
        (
            (),
            {
                "project_id": "00000000-0000-0000-0000-0000000000d2",
                "status": SOURCE_STATUS_FAILED,
                "is_regeneration": False,
                "error": "phase failed hard",
            },
        ),
    ]
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert recorded_types == {"rfp_modules_generation_started", "rfp_modules_generation_failed"}


def test_run_module_feature_regeneration_task_completed_flow(_mock_record_activity) -> None:
    module_output = SimpleNamespace(feature_inventory=[SimpleNamespace(features=[object()])])
    stored_modules = [
        SimpleNamespace(
            feedback_change_type=ChangeType.ADDED,
            features=[SimpleNamespace(feedback_change_type=ChangeType.ADDED)],
        )
    ]
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch("app.workers.document_task_stages._mark_sources_status") as mock_mark_status,
        patch("app.workers.document_task_stages._notify_module_feature_status") as mock_notify,
        patch(
            "app.workers.document_task_stages._update_source_ingestion_fields_by_id"
        ) as mock_update_ingestion,
        patch("app.workers.document_task_stages._add_run_stage_by_id") as mock_add_run_stage,
        patch(
            "app.workers.document_task_stages._run_module_feature_phase",
            return_value=(module_output, stored_modules),
        ) as mock_run_phase,
    ):
        result = _run_module_feature_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000a5",
            source_ids=["00000000-0000-0000-0000-000000000555"],
            fragments=[{"id": "frag-1"}],
            modules_and_features={"feature_inventory": []},
            feedback="tighten scope",
            task_db_id="task-r5",
            ingestion_id="11111111-1111-1111-1111-111111111111",
        )

    assert mock_run_phase.call_args.kwargs["is_regeneration"] is True
    assert (
        mock_run_phase.call_args.kwargs["source_ingestion_id"]
        == "11111111-1111-1111-1111-111111111111"
    )

    # Regenerated modules/features are flagged with feedback_change_type and
    # await accept/reject via /feedback-updates — so the run always resolves
    # to READY_FOR_REVIEW, never COMPLETED, regardless of source type.
    assert result["status"] == SOURCE_INGESTION_STATUS_READY_FOR_REVIEW
    assert result["tot_modules"] == 1
    assert result["tot_features"] == 1
    assert result["tot_modules_updated"] == 0
    assert result["tot_features_updated"] == 0
    assert mock_mark_status.call_count == 2
    assert (
        mock_mark_status.call_args_list[1].kwargs["status"]
        == SOURCE_INGESTION_STATUS_READY_FOR_REVIEW
    )
    completed_meta = mock_mark_status.call_args_list[1].kwargs["meta"]
    assert "total_modules" not in completed_meta
    assert "total_features" not in completed_meta
    assert completed_meta["tot_modules"] == 1
    assert completed_meta["tot_features"] == 1
    assert completed_meta["tot_modules_updated"] == 0
    assert completed_meta["tot_features_updated"] == 0
    assert (
        mock_update_ingestion.call_args_list[-1].kwargs["fields"]["status"]
        == SourceIngestionStatus.READY_FOR_REVIEW.value
    )
    mock_add_run_stage.assert_called_once_with(
        ingestion_id="11111111-1111-1111-1111-111111111111",
        stage=SourceIngestionStage.READY_FOR_REVIEW,
    )
    assert mock_notify.call_args_list == [
        (
            (),
            {
                "project_id": "00000000-0000-0000-0000-0000000000a5",
                "status": SOURCE_STATUS_RUNNING,
                "is_regeneration": True,
            },
        ),
        (
            (),
            {
                "project_id": "00000000-0000-0000-0000-0000000000a5",
                "status": SourceIngestionStatus.READY_FOR_REVIEW.value,
                "is_regeneration": True,
                "modules_added": 1,
                "modules_updated": 0,
                "features_added": 1,
                "features_updated": 0,
            },
        ),
    ]
    # Records activity once at start and once at completion.
    assert _mock_record_activity.call_count == 2
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert recorded_types == {"rfp_modules_regeneration_started", "rfp_modules_regenerated"}
    call_kwargs = _mock_record_activity.call_args.kwargs
    assert call_kwargs["project_id"] == UUID("00000000-0000-0000-0000-0000000000a5")
    assert call_kwargs["activity_type"].value == "rfp_modules_regenerated"
    assert call_kwargs["data"]["ingestion_id"] == "11111111-1111-1111-1111-111111111111"
    assert "total_modules" not in call_kwargs["data"]
    assert "total_features" not in call_kwargs["data"]
    assert call_kwargs["data"]["tot_modules"] == 1
    assert call_kwargs["data"]["tot_features"] == 1
    assert call_kwargs["data"]["tot_modules_updated"] == 0
    assert call_kwargs["data"]["tot_features_updated"] == 0
    assert call_kwargs["message"] == (
        "Regenerated: 1 module(s) added, 0 module(s) updated, "
        "1 feature(s) added, 0 feature(s) updated"
    )


def test_run_module_feature_regeneration_task_completed_flow_splits_added_and_updated(
    _mock_record_activity,
) -> None:
    """Regression test: the completed-stage websocket meta, record_activity
    data, and return value must all report added and updated module/feature
    counts separately (``tot_modules``/``tot_features`` for ADDED,
    ``tot_modules_updated``/``tot_features_updated`` for UPDATED) — not the
    combined ``total_modules``/``total_features``."""
    stored_modules = [
        SimpleNamespace(
            feedback_change_type=ChangeType.ADDED,
            features=[SimpleNamespace(feedback_change_type=ChangeType.ADDED)],
        ),
        SimpleNamespace(
            feedback_change_type=ChangeType.UPDATED,
            features=[
                SimpleNamespace(feedback_change_type=ChangeType.UPDATED),
                SimpleNamespace(feedback_change_type=ChangeType.UPDATED),
                SimpleNamespace(feedback_change_type=ChangeType.UPDATED),
                SimpleNamespace(feedback_change_type=ChangeType.UPDATED),
                SimpleNamespace(feedback_change_type=ChangeType.UPDATED),
            ],
        ),
    ]
    module_output = SimpleNamespace(feature_inventory=[SimpleNamespace(features=[object()])])
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch("app.workers.document_task_stages._mark_sources_status") as mock_mark_status,
        patch("app.workers.document_task_stages._notify_module_feature_status"),
        patch("app.workers.document_task_stages._update_source_ingestion_fields_by_id"),
        patch("app.workers.document_task_stages._add_run_stage_by_id"),
        patch(
            "app.workers.document_task_stages._run_module_feature_phase",
            return_value=(module_output, stored_modules),
        ),
    ):
        result = _run_module_feature_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000a6",
            source_ids=["00000000-0000-0000-0000-000000000666"],
            fragments=[{"id": "frag-1"}],
            modules_and_features={"feature_inventory": []},
            feedback="tighten scope",
            task_db_id="task-r6",
            ingestion_id="22222222-2222-2222-2222-222222222222",
        )

    completed_meta = mock_mark_status.call_args_list[1].kwargs["meta"]
    assert "total_modules" not in completed_meta
    assert "total_features" not in completed_meta
    assert completed_meta["tot_modules"] == 1
    assert completed_meta["tot_modules_updated"] == 1
    assert completed_meta["tot_features"] == 1
    assert completed_meta["tot_features_updated"] == 5

    assert "total_modules" not in result
    assert "total_features" not in result
    assert result["tot_modules"] == 1
    assert result["tot_modules_updated"] == 1
    assert result["tot_features"] == 1
    assert result["tot_features_updated"] == 5

    # Records activity once at start and once at completion.
    assert _mock_record_activity.call_count == 2
    activity_data = _mock_record_activity.call_args.kwargs["data"]
    assert "total_modules" not in activity_data
    assert "total_features" not in activity_data
    assert activity_data["tot_modules"] == 1
    assert activity_data["tot_modules_updated"] == 1
    assert activity_data["tot_features"] == 1
    assert activity_data["tot_features_updated"] == 5
    assert _mock_record_activity.call_args.kwargs["message"] == (
        "Regenerated: 1 module(s) added, 1 module(s) updated, "
        "1 feature(s) added, 5 feature(s) updated"
    )


def test_run_module_feature_regeneration_task_entry_cancel_records_activity(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with (
        patch("app.workers.document_task_stages.mark_cancelled_and_check", return_value=True),
        patch(
            "app.workers.document_task_stages.mark_sources_and_ingestion_cancelled"
        ) as mock_mark_cancelled,
    ):
        result = _run_module_feature_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000b3",
            source_ids=["00000000-0000-0000-0000-000000000333"],
            fragments=[{"id": "frag-1"}],
            modules_and_features={"feature_inventory": []},
            feedback="cancel me",
            task_db_id="task-c3",
            ingestion_id="33333333-3333-3333-3333-333333333333",
        )

    assert result == {
        "project_id": "00000000-0000-0000-0000-0000000000b3",
        "status": "cancelled",
    }
    mock_mark_cancelled.assert_called_once()
    _mock_record_activity.assert_called_once()
    call_kwargs = _mock_record_activity.call_args.kwargs
    assert call_kwargs["activity_type"] == ActivityType.RFP_MODULES_REGENERATION_CANCELLED
    assert call_kwargs["data"]["ingestion_id"] == "33333333-3333-3333-3333-333333333333"


def test_run_module_feature_regeneration_task_retry_path() -> None:
    task_self = MagicMock()
    task_self.request.retries = 0
    task_self.max_retries = 3
    task_self.retry.side_effect = RuntimeError("regen-retry-triggered")

    with (
        patch("app.workers.document_task_stages._mark_sources_status") as mock_mark_status,
        patch("app.workers.document_task_stages._notify_module_feature_status") as mock_notify,
        patch(
            "app.workers.document_task_stages._run_module_feature_phase",
            side_effect=RuntimeError("regen phase failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="regen-retry-triggered"):
            _run_module_feature_regeneration_task(
                self=task_self,
                project_id="00000000-0000-0000-0000-0000000000b3",
                source_ids=["00000000-0000-0000-0000-000000000333"],
                fragments=[{"id": "frag-1"}],
                modules_and_features={"feature_inventory": []},
                feedback="retry feedback",
                task_db_id="task-r3",
            )

    assert mock_mark_status.call_count == 2
    for mark_call in mock_mark_status.call_args_list:
        assert mark_call.kwargs["task_type"] == "module_regeneration"
    assert (
        mock_mark_status.call_args_list[0].kwargs["stage"]
        == "modules_and_features.regeneration.started"
    )
    assert mock_mark_status.call_args_list[1].kwargs["stage"] == "modules_and_features.retry.1"
    task_self.retry.assert_called_once()
    # Only the initial "running" notification fires for a still-retriable
    # failure — no "failed" notification until retries are exhausted.
    mock_notify.assert_called_once_with(
        project_id="00000000-0000-0000-0000-0000000000b3",
        status=SOURCE_STATUS_RUNNING,
        is_regeneration=True,
    )


def test_run_module_feature_regeneration_task_terminal_failure() -> None:
    task_self = MagicMock()
    task_self.request.retries = 3
    task_self.max_retries = 3

    with (
        patch("app.workers.document_task_stages._mark_sources_status") as mock_mark_status,
        patch("app.workers.document_task_stages._notify_module_feature_status") as mock_notify,
        patch(
            "app.workers.document_task_stages._run_module_feature_phase",
            side_effect=RuntimeError("regen hard fail"),
        ),
        patch(
            "app.workers.document_task_stages._add_source_ingestion_error_by_id"
        ) as mock_add_error,
    ):
        result = _run_module_feature_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000b4",
            source_ids=["00000000-0000-0000-0000-000000000444"],
            fragments=[{"id": "frag-1"}],
            modules_and_features={"feature_inventory": []},
            feedback="final fail",
            task_db_id="task-r4",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    assert "regen hard fail" in result["error"]
    assert mock_mark_status.call_count == 2
    mock_add_error.assert_called_once_with(ingestion_id=None, error="regen hard fail")
    for mark_call in mock_mark_status.call_args_list:
        assert mark_call.kwargs["task_type"] == "module_regeneration"
    assert (
        mock_mark_status.call_args_list[1].kwargs["stage"]
        == "modules_and_features.regeneration.failed"
    )
    assert mock_notify.call_args_list == [
        (
            (),
            {
                "project_id": "00000000-0000-0000-0000-0000000000b4",
                "status": SOURCE_STATUS_RUNNING,
                "is_regeneration": True,
            },
        ),
        (
            (),
            {
                "project_id": "00000000-0000-0000-0000-0000000000b4",
                "status": SOURCE_STATUS_FAILED,
                "is_regeneration": True,
                "error": "regen hard fail",
            },
        ),
    ]
    task_self.retry.assert_not_called()


def test_run_module_feature_regeneration_task_fails_immediately_on_non_retryable_llm_error(
    _mock_record_activity,
) -> None:
    task_self = MagicMock()
    task_self.request.retries = 0
    task_self.max_retries = 3
    fake_error = _fake_non_retryable_error(
        reason=LLMErrorReason.AUTHENTICATION, message="Invalid API key"
    )

    with (
        patch("app.workers.document_task_stages._mark_sources_status") as mock_mark_status,
        patch("app.workers.document_task_stages._notify_module_feature_status") as mock_notify,
        patch(
            "app.workers.document_task_stages._run_module_feature_phase",
            side_effect=fake_error,
        ),
        patch(
            "app.workers.document_task_stages._add_source_ingestion_error_by_id"
        ) as mock_add_error,
        patch(
            "app.workers.document_task_stages._cancel_sibling_tasks_on_fatal_llm_error"
        ) as mock_cancel_siblings,
    ):
        result = _run_module_feature_regeneration_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000b5",
            source_ids=["00000000-0000-0000-0000-000000000445"],
            fragments=[{"id": "frag-1"}],
            modules_and_features={"feature_inventory": []},
            feedback="final fail",
            task_db_id="task-r5",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    assert "authentication" in result["error"]
    task_self.retry.assert_not_called()
    assert mock_mark_status.call_count == 2  # started + failed only, no retry-attempt mark
    mock_add_error.assert_called_once()
    assert "authentication" in mock_add_error.call_args.kwargs["error"]
    failed_call = mock_notify.call_args_list[-1]
    assert failed_call.kwargs["error_reason"] == "authentication"
    mock_cancel_siblings.assert_called_once_with(
        request_id="task-r5", task_db_id="task-r5", project_id="00000000-0000-0000-0000-0000000000b5"
    )


def test_run_story_feedback_patch_task_entry_cancel_records_activity(
    _mock_record_activity,
) -> None:
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)

    with patch("app.workers.document_task_stages.mark_cancelled_and_check", return_value=True):
        result = _run_story_feedback_patch_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000c1",
            story_feedbacks=[{"story_code": "US-1", "feedback": "tighten"}],
            feature_contexts=[],
            persona_glossary="",
            valid_sources="",
            story_code_to_feature_id={},
            options={},
            task_db_id="task-c1",
            ingestion_id="44444444-4444-4444-4444-444444444444",
        )

    assert result == {
        "project_id": "00000000-0000-0000-0000-0000000000c1",
        "status": "cancelled",
    }
    _mock_record_activity.assert_called_once()
    call_kwargs = _mock_record_activity.call_args.kwargs
    assert call_kwargs["activity_type"] == ActivityType.RFP_FEEDBACK_REGENERATION_CANCELLED
    assert call_kwargs["data"]["ingestion_id"] == "44444444-4444-4444-4444-444444444444"


def test_run_story_feedback_patch_task_completed_flow(_mock_record_activity) -> None:
    """Success now resolves to READY_FOR_REVIEW (not COMPLETED) and tags the
    dedicated ingestion row's stages with the generic READY_FOR_REVIEW stage
    (not the RFP-specific USER_STORY_READY_FOR_REVIEW) — regenerated stories
    await human review, matching the module/feature feedback-regeneration
    flow's resolution status."""
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)
    patch_result = {"revised_stories": []}

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog_patch."
            "run_agile_backlog_patch",
            return_value="patch-coro",
        ) as mock_run_patch,
        patch(
            "app.workers.document_task_stages._run_async",
            side_effect=[patch_result, {"added": 1, "updated": 2}],
        ),
        patch("app.workers.document_task_stages._save_story_feedback_history") as mock_save_history,
        patch(
            "app.workers.document_task_stages._update_source_ingestion_fields_by_id"
        ) as mock_update_ingestion,
        patch("app.workers.document_task_stages._add_run_stage_by_id") as mock_add_run_stage,
        patch(
            "app.workers.document_task_stages._notify_story_feedback_patch_status"
        ) as mock_notify,
        patch("app.services.user_story_service.UserStoryService") as MockUserStoryService,
        patch("app.workers.document_task_stages.emit_task_event") as mock_emit,
    ):
        MockUserStoryService.return_value.upsert_user_stories_from_patch.return_value = (
            "upsert-coro"
        )

        result = _run_story_feedback_patch_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000f00",
            story_feedbacks=[],
            feature_contexts=[],
            persona_glossary="",
            valid_sources="",
            story_code_to_feature_id={},
            options={},
            task_db_id=None,
            ingestion_id="11111111-1111-1111-1111-111111111111",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_READY_FOR_REVIEW
    assert result["tot_user_stories"] == 1
    assert result["tot_user_stories_updated"] == 2
    mock_run_patch.assert_called_once()
    mock_save_history.assert_called_once()
    mock_add_run_stage.assert_called_once_with(
        ingestion_id="11111111-1111-1111-1111-111111111111",
        stage=SourceIngestionStage.READY_FOR_REVIEW,
    )
    updated_fields = mock_update_ingestion.call_args_list[-1].kwargs["fields"]
    assert updated_fields["status"] == SourceIngestionStatus.READY_FOR_REVIEW.value
    assert updated_fields["tot_user_stories"] == 1
    assert updated_fields["tot_user_stories_updated"] == 2
    completed_meta = mock_emit.call_args_list[-1].kwargs["meta"]
    assert "total_items" not in completed_meta
    assert completed_meta["tot_user_stories"] == 1
    assert completed_meta["tot_user_stories_updated"] == 2
    # Records activity once at start and once at completion.
    assert _mock_record_activity.call_count == 2
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert recorded_types == {
        "rfp_feedback_regeneration_started",
        "rfp_feedback_regenerated",
    }
    activity_data = _mock_record_activity.call_args.kwargs["data"]
    assert "upserted_count" not in activity_data
    assert activity_data["tot_user_stories"] == 1
    assert activity_data["tot_user_stories_updated"] == 2
    # Notifies once at start (running) and once at completion.
    assert mock_notify.call_count == 2
    mock_notify.assert_any_call(
        project_id="00000000-0000-0000-0000-000000000f00",
        status=SourceIngestionStatus.RUNNING.value,
    )
    mock_notify.assert_any_call(
        project_id="00000000-0000-0000-0000-000000000f00",
        status=SourceIngestionStatus.READY_FOR_REVIEW.value,
        stories_added=1,
        stories_updated=2,
    )


def test_run_story_feedback_patch_task_terminal_failure_notifies(_mock_record_activity) -> None:
    """Terminal failure (retries exhausted) must notify, not just log — mirrors
    the completed-flow assertion above for the failure branch."""
    task_self = SimpleNamespace(request=SimpleNamespace(retries=3), max_retries=3)

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog_patch."
            "run_agile_backlog_patch",
            side_effect=RuntimeError("patch generation failed"),
        ),
        patch(
            "app.workers.document_task_stages._update_source_ingestion_fields_by_id"
        ) as mock_update_ingestion,
        patch(
            "app.workers.document_task_stages._add_source_ingestion_error_by_id"
        ) as mock_add_error,
        patch(
            "app.workers.document_task_stages._notify_story_feedback_patch_status"
        ) as mock_notify,
        patch("app.workers.document_task_stages.emit_task_event"),
    ):
        result = _run_story_feedback_patch_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000f00",
            story_feedbacks=[],
            feature_contexts=[],
            persona_glossary="",
            valid_sources="",
            story_code_to_feature_id={},
            options={},
            task_db_id=None,
            ingestion_id="11111111-1111-1111-1111-111111111111",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    mock_update_ingestion.assert_called_with(
        ingestion_id="11111111-1111-1111-1111-111111111111",
        fields={"status": SourceIngestionStatus.FAILED.value},
    )
    mock_add_error.assert_called_once()
    # Notifies once at start (running) and once at completion (failed).
    assert mock_notify.call_count == 2
    mock_notify.assert_any_call(
        project_id="00000000-0000-0000-0000-000000000f00",
        status=SourceIngestionStatus.RUNNING.value,
    )
    mock_notify.assert_any_call(
        project_id="00000000-0000-0000-0000-000000000f00",
        status=SourceIngestionStatus.FAILED.value,
        error="patch generation failed",
    )
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert recorded_types == {
        "rfp_feedback_regeneration_started",
        "rfp_feedback_regeneration_failed",
    }


def test_run_story_feedback_patch_task_fails_immediately_on_non_retryable_llm_error(
    _mock_record_activity,
) -> None:
    """No Source-status write here by design — this task has no source_ids,
    only the dedicated SourceIngestion row (by id) is touched."""
    task_self = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)
    fake_error = _fake_non_retryable_error(
        reason=LLMErrorReason.CONTEXT_LENGTH_EXCEEDED, message="prompt too long"
    )

    with (
        patch(
            "app.services.rfp_pipeline_v2_graph_service.graph_agile_backlog_patch."
            "run_agile_backlog_patch",
            side_effect=fake_error,
        ),
        patch(
            "app.workers.document_task_stages._update_source_ingestion_fields_by_id"
        ) as mock_update_ingestion,
        patch(
            "app.workers.document_task_stages._add_source_ingestion_error_by_id"
        ) as mock_add_error,
        patch(
            "app.workers.document_task_stages._notify_story_feedback_patch_status"
        ) as mock_notify,
        patch("app.workers.document_task_stages.emit_task_event"),
        patch(
            "app.workers.document_task_stages._cancel_sibling_tasks_on_fatal_llm_error"
        ) as mock_cancel_siblings,
    ):
        result = _run_story_feedback_patch_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-000000000f01",
            story_feedbacks=[],
            feature_contexts=[],
            persona_glossary="",
            valid_sources="",
            story_code_to_feature_id={},
            options={},
            task_db_id="task-f01",
            ingestion_id="11111111-1111-1111-1111-111111111112",
        )

    assert result["status"] == SOURCE_INGESTION_STATUS_FAILED
    assert "context_length_exceeded" in result["error"]
    mock_update_ingestion.assert_called_with(
        ingestion_id="11111111-1111-1111-1111-111111111112",
        fields={"status": SourceIngestionStatus.FAILED.value},
    )
    mock_add_error.assert_called_once()
    failed_call = mock_notify.call_args_list[-1]
    assert failed_call.kwargs["status"] == SourceIngestionStatus.FAILED.value
    assert failed_call.kwargs["error_reason"] == "context_length_exceeded"
    mock_cancel_siblings.assert_called_once_with(
        request_id="task-f01", task_db_id="task-f01", project_id="00000000-0000-0000-0000-000000000f01"
    )
    recorded_types = {c.kwargs["activity_type"].value for c in _mock_record_activity.call_args_list}
    assert "rfp_feedback_regeneration_failed" in recorded_types
    failed_activity = next(
        c for c in _mock_record_activity.call_args_list
        if c.kwargs["activity_type"].value == "rfp_feedback_regeneration_failed"
    )
    assert failed_activity.kwargs["data"]["llm_error_reason"] == "context_length_exceeded"


class TestRecordFeedbackRegeneratedActivity:
    """Tests for _record_feedback_regenerated_activity, called once at
    _run_story_feedback_patch_task's success completion."""

    def test_builds_message_and_resolves_actor(self, _mock_record_activity):
        actor_id = UUID("00000000-0000-0000-0000-000000000abc")
        with patch(
            "app.workers.document_task_stages.resolve_actor_from_task",
            return_value=actor_id,
        ) as mock_resolve:
            _record_feedback_regenerated_activity(
                project_id="00000000-0000-0000-0000-000000000f00",
                task_db_id="task-1",
                ingestion_id="11111111-1111-1111-1111-111111111111",
                stories_added=4,
                stories_updated=2,
            )

        mock_resolve.assert_called_once_with("task-1")
        _mock_record_activity.assert_called_once()
        call_kwargs = _mock_record_activity.call_args.kwargs
        assert call_kwargs["project_id"] == UUID("00000000-0000-0000-0000-000000000f00")
        assert call_kwargs["activity_type"].value == "rfp_feedback_regenerated"
        assert call_kwargs["actor_user_id"] == actor_id
        assert call_kwargs["data"] == {
            "tot_user_stories": 4,
            "tot_user_stories_updated": 2,
            "ingestion_id": "11111111-1111-1111-1111-111111111111",
        }
        assert "4" in call_kwargs["message"]
        assert "2" in call_kwargs["message"]


class TestRemapSkipProcessingFeatureIds:
    """Tests for _remap_skip_processing_feature_ids (skip_processing RFP backlog fixup).

    See the function's own docstring for why this exists: the sample_result/RFP/
    agile_backlog.json fixture is a frozen snapshot from whichever project first
    captured it, so its embedded feature_id UUIDs never match the project
    skip_processing is actually being exercised against — these rewrite them by
    the project-agnostic epic_code/fea_code instead.
    """

    # A syntactically valid UUID string — the function does UUID(project_id)
    # before ever touching the (mocked) repository, so a plain slug like
    # "project-1" would raise ValueError before the test gets anywhere.
    _PROJECT_ID = "11111111-1111-1111-1111-111111111111"

    _REPO_PATCH_TARGET = "app.repositories.neo4j.module_feature_repository.ModuleFeatureRepository"

    @pytest.mark.asyncio
    async def test_remaps_matching_epic_code_to_real_feature_id(self):
        fake_module = SimpleNamespace(
            mod_code="1",
            features=[SimpleNamespace(fea_code="1.1", id="real-feature-id")],
        )
        with patch(self._REPO_PATCH_TARGET) as MockRepo:
            MockRepo.return_value.list_modules_by_project = AsyncMock(return_value=[fake_module])
            backlog_items = {
                "output": json.dumps({"epics": [{"epic_code": "1.1", "feature_id": "stale-id"}]})
            }
            await _remap_skip_processing_feature_ids(self._PROJECT_ID, backlog_items)

        parsed = json.loads(backlog_items["output"])
        assert parsed["epics"][0]["feature_id"] == "real-feature-id"

    @pytest.mark.asyncio
    async def test_partial_mismatch_remaps_matches_and_drops_unmatched_without_raising(self):
        fake_module = SimpleNamespace(
            mod_code="1",
            features=[SimpleNamespace(fea_code="1.1", id="real-feature-id")],
        )
        with patch(self._REPO_PATCH_TARGET) as MockRepo:
            MockRepo.return_value.list_modules_by_project = AsyncMock(return_value=[fake_module])
            backlog_items = {
                "output": json.dumps(
                    {
                        "epics": [
                            {"epic_code": "1.1", "feature_id": "stale-id-1"},
                            {"epic_code": "99.99", "feature_id": "stale-id-2"},
                        ]
                    }
                )
            }
            await _remap_skip_processing_feature_ids(self._PROJECT_ID, backlog_items)

        parsed = json.loads(backlog_items["output"])
        # The unresolved epic is dropped entirely rather than upserted with its
        # stale (wrong-project) feature_id.
        assert len(parsed["epics"]) == 1
        assert parsed["epics"][0]["feature_id"] == "real-feature-id"

    @pytest.mark.asyncio
    async def test_total_mismatch_raises_instead_of_silently_orphaning(self):
        fake_module = SimpleNamespace(
            mod_code="1",
            features=[SimpleNamespace(fea_code="1.1", id="real-feature-id")],
        )
        with patch(self._REPO_PATCH_TARGET) as MockRepo:
            MockRepo.return_value.list_modules_by_project = AsyncMock(return_value=[fake_module])
            backlog_items = {
                "output": json.dumps({"epics": [{"epic_code": "99.99", "feature_id": "stale-id"}]})
            }
            with pytest.raises(RuntimeError, match="doesn't match project"):
                await _remap_skip_processing_feature_ids(self._PROJECT_ID, backlog_items)

    @pytest.mark.asyncio
    async def test_non_string_output_returns_early(self):
        backlog_items = {"output": {"epics": []}}
        await _remap_skip_processing_feature_ids(self._PROJECT_ID, backlog_items)
        assert backlog_items["output"] == {"epics": []}

    @pytest.mark.asyncio
    async def test_malformed_json_output_returns_early(self):
        backlog_items = {"output": "not valid json{"}
        await _remap_skip_processing_feature_ids(self._PROJECT_ID, backlog_items)
        assert backlog_items["output"] == "not valid json{"

    @pytest.mark.asyncio
    async def test_empty_epics_returns_early(self):
        backlog_items = {"output": json.dumps({"epics": []})}
        original = backlog_items["output"]
        await _remap_skip_processing_feature_ids(self._PROJECT_ID, backlog_items)
        assert backlog_items["output"] == original


class TestPersistDomainKnowledgeArtifact:
    @pytest.mark.asyncio
    async def test_empty_content_short_circuits(self):
        with patch("app.clients.s3_client.upload_to_s3") as mock_upload:
            result = await _persist_domain_knowledge_artifact(project_id="p-1", content="")

        assert result is None
        mock_upload.assert_not_called()

    @pytest.mark.asyncio
    async def test_uploads_and_updates_metadata(self):
        mock_repo = MagicMock()
        with (
            patch(
                "app.clients.s3_client.upload_to_s3",
                AsyncMock(return_value="projects/p-1/pipeline-artifacts/domain_knowledge.md"),
            ) as mock_upload,
            patch("app.db.neo4j.get_neo4j_driver", return_value=MagicMock()),
            patch(
                "app.repositories.neo4j.project_metadata_repository.ProjectMetadataRepository",
                return_value=mock_repo,
            ),
        ):
            result = await _persist_domain_knowledge_artifact(
                project_id="00000000-0000-0000-0000-000000000001",
                content="# Domain knowledge",
            )

        assert (
            result
            == "projects/00000000-0000-0000-0000-000000000001/pipeline-artifacts/domain_knowledge.md"
        )
        mock_upload.assert_awaited_once()
        _, upload_kwargs = mock_upload.call_args
        assert upload_kwargs["file_bytes"] == b"# Domain knowledge"
        assert upload_kwargs["content_type"] == "text/markdown; charset=utf-8"
        mock_repo.update.assert_called_once_with(
            project_id=UUID("00000000-0000-0000-0000-000000000001"),
            domain_knowledge_storage_key=(
                "projects/00000000-0000-0000-0000-000000000001/"
                "pipeline-artifacts/domain_knowledge.md"
            ),
        )

    @pytest.mark.asyncio
    async def test_upload_failure_is_swallowed(self):
        with patch(
            "app.clients.s3_client.upload_to_s3",
            AsyncMock(side_effect=RuntimeError("S3 unavailable")),
        ):
            result = await _persist_domain_knowledge_artifact(
                project_id="p-1", content="# Domain knowledge"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_neo4j_update_failure_is_swallowed(self):
        mock_repo = MagicMock()
        mock_repo.update.side_effect = RuntimeError("neo4j unavailable")
        with (
            patch(
                "app.clients.s3_client.upload_to_s3",
                AsyncMock(return_value="projects/p-1/pipeline-artifacts/domain_knowledge.md"),
            ),
            patch("app.db.neo4j.get_neo4j_driver", return_value=MagicMock()),
            patch(
                "app.repositories.neo4j.project_metadata_repository.ProjectMetadataRepository",
                return_value=mock_repo,
            ),
        ):
            result = await _persist_domain_knowledge_artifact(
                project_id="00000000-0000-0000-0000-000000000001",
                content="# Domain knowledge",
            )

        assert result is None


class TestPersistArchitectureDocumentArtifact:
    @pytest.mark.asyncio
    async def test_empty_content_short_circuits(self):
        with patch("app.clients.s3_client.upload_to_s3") as mock_upload:
            result = await _persist_architecture_document_artifact(project_id="p-1", content="")

        assert result is None
        mock_upload.assert_not_called()

    @pytest.mark.asyncio
    async def test_uploads_and_updates_metadata(self):
        mock_repo = MagicMock()
        with (
            patch(
                "app.clients.s3_client.upload_to_s3",
                AsyncMock(return_value="projects/p-1/pipeline-artifacts/architecture_document.md"),
            ) as mock_upload,
            patch("app.db.neo4j.get_neo4j_driver", return_value=MagicMock()),
            patch(
                "app.repositories.neo4j.project_metadata_repository.ProjectMetadataRepository",
                return_value=mock_repo,
            ),
        ):
            result = await _persist_architecture_document_artifact(
                project_id="00000000-0000-0000-0000-000000000001",
                content="# Architecture",
            )

        assert result == (
            "projects/00000000-0000-0000-0000-000000000001/"
            "pipeline-artifacts/architecture_document.md"
        )
        mock_upload.assert_awaited_once()
        _, upload_kwargs = mock_upload.call_args
        assert upload_kwargs["file_bytes"] == b"# Architecture"
        assert upload_kwargs["content_type"] == "text/markdown; charset=utf-8"
        mock_repo.update.assert_called_once_with(
            project_id=UUID("00000000-0000-0000-0000-000000000001"),
            architecture_document_storage_key=(
                "projects/00000000-0000-0000-0000-000000000001/"
                "pipeline-artifacts/architecture_document.md"
            ),
        )

    @pytest.mark.asyncio
    async def test_upload_failure_is_swallowed(self):
        with patch(
            "app.clients.s3_client.upload_to_s3",
            AsyncMock(side_effect=RuntimeError("S3 unavailable")),
        ):
            result = await _persist_architecture_document_artifact(
                project_id="p-1", content="# Architecture"
            )

        assert result is None


class TestGenerateAndPersistPipelineDocuments:
    _ORCHESTRATOR_PATCH_TARGET = (
        "app.services.source_code_pipeline.pipeline_orchestrator.PipelineOrchestrator"
    )

    @pytest.mark.asyncio
    async def test_missing_paths_skips_generation(self):
        with patch(self._ORCHESTRATOR_PATCH_TARGET) as mock_orchestrator_cls:
            await _generate_and_persist_pipeline_documents(
                project_id="p-1",
                source_id="src-1",
                task_db_id="task-1",
                project_dir=None,
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
            )

        mock_orchestrator_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_generates_and_persists_both_artifacts(self):
        mock_pipeline = MagicMock()
        mock_pipeline.generate_domain_knowledge.return_value = {
            "status": "success",
            "content": "# Domain",
        }
        mock_pipeline.generate_architecture_document.return_value = {
            "status": "success",
            "content": "# Architecture",
        }
        with (
            patch(self._ORCHESTRATOR_PATCH_TARGET, return_value=mock_pipeline),
            patch(
                "app.workers.source_code_task._persist_domain_knowledge_artifact",
                AsyncMock(return_value="key-domain"),
            ) as mock_persist_domain,
            patch(
                "app.workers.source_code_task._persist_architecture_document_artifact",
                AsyncMock(return_value="key-arch"),
            ) as mock_persist_arch,
        ):
            await _generate_and_persist_pipeline_documents(
                project_id="p-1",
                source_id="src-1",
                task_db_id="task-1",
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
            )

        mock_persist_domain.assert_awaited_once_with(project_id="p-1", content="# Domain")
        mock_persist_arch.assert_awaited_once_with(project_id="p-1", content="# Architecture")

    @pytest.mark.asyncio
    async def test_both_artifacts_succeeding_logs_two_separate_completed_activities(
        self, _mock_source_code_record_activity
    ):
        """Domain-knowledge and architecture-document generation are two
        separate method calls, so each gets its own COMPLETED activity-log
        entry rather than one combined row for both."""
        mock_pipeline = MagicMock()
        mock_pipeline.generate_domain_knowledge.return_value = {
            "status": "success",
            "content": "# Domain",
        }
        mock_pipeline.generate_architecture_document.return_value = {
            "status": "success",
            "content": "# Architecture",
        }
        project_id = "00000000-0000-0000-0000-0000000000aa"
        source_id = "00000000-0000-0000-0000-0000000000bb"
        with (
            patch(self._ORCHESTRATOR_PATCH_TARGET, return_value=mock_pipeline),
            patch(
                "app.workers.source_code_task._persist_domain_knowledge_artifact",
                AsyncMock(return_value="key-domain"),
            ),
            patch(
                "app.workers.source_code_task._persist_architecture_document_artifact",
                AsyncMock(return_value="key-arch"),
            ),
        ):
            await _generate_and_persist_pipeline_documents(
                project_id=project_id,
                source_id=source_id,
                task_db_id="task-1",
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
            )

        assert _mock_source_code_record_activity.call_count == 2
        recorded_types = {
            c.kwargs["activity_type"].value
            for c in _mock_source_code_record_activity.call_args_list
        }
        assert recorded_types == {
            "source_code_domain_knowledge_completed",
            "source_code_architecture_document_completed",
        }
        for recorded_call in _mock_source_code_record_activity.call_args_list:
            assert recorded_call.kwargs["project_id"] == UUID(project_id)
            assert recorded_call.kwargs["data"] == {"source_id": source_id}

    @pytest.mark.asyncio
    async def test_domain_knowledge_failure_logs_only_that_stage_as_failed(
        self, _mock_source_code_record_activity
    ):
        mock_pipeline = MagicMock()
        mock_pipeline.generate_domain_knowledge.return_value = {
            "status": "failed",
            "error": "boom",
        }
        mock_pipeline.generate_architecture_document.return_value = {
            "status": "success",
            "content": "# Architecture",
        }
        project_id = "00000000-0000-0000-0000-0000000000cc"
        source_id = "00000000-0000-0000-0000-0000000000dd"
        with (
            patch(self._ORCHESTRATOR_PATCH_TARGET, return_value=mock_pipeline),
            patch("app.workers.source_code_task._persist_domain_knowledge_artifact", AsyncMock()),
            patch(
                "app.workers.source_code_task._persist_architecture_document_artifact",
                AsyncMock(return_value="key-arch"),
            ),
            patch("app.workers.source_code_task._record_pipeline_documents_failure"),
        ):
            await _generate_and_persist_pipeline_documents(
                project_id=project_id,
                source_id=source_id,
                task_db_id="task-1",
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
            )

        assert _mock_source_code_record_activity.call_count == 2
        by_type = {
            c.kwargs["activity_type"].value: c
            for c in _mock_source_code_record_activity.call_args_list
        }
        assert by_type["source_code_domain_knowledge_failed"].kwargs["data"] == {
            "source_id": source_id,
            "error": "boom",
        }
        assert (
            by_type["source_code_architecture_document_completed"].kwargs["data"]
            == {"source_id": source_id}
        )

    @pytest.mark.asyncio
    async def test_resets_circuit_breaker_before_generating(self):
        """generate_domain_knowledge/generate_architecture_document are the
        only pipeline stages not wrapped in LLMClient.pipeline_run_guard, so
        nothing else resets the process-wide circuit breaker before their
        calls — this project's document-generation run must not inherit a
        breaker already tripped by an unrelated project sharing this same
        recycled worker child (--max-tasks-per-child)."""
        mock_pipeline = MagicMock()
        mock_pipeline.generate_domain_knowledge.return_value = {
            "status": "success",
            "content": "# Domain",
        }
        mock_pipeline.generate_architecture_document.return_value = {
            "status": "success",
            "content": "# Architecture",
        }
        with (
            patch(self._ORCHESTRATOR_PATCH_TARGET, return_value=mock_pipeline),
            patch(
                "app.services.source_code_pipeline.src.ai.llm_client.LLMClient.reset_circuit_breaker"
            ) as mock_reset_breaker,
            patch(
                "app.workers.source_code_task._persist_domain_knowledge_artifact",
                AsyncMock(return_value="key-domain"),
            ),
            patch(
                "app.workers.source_code_task._persist_architecture_document_artifact",
                AsyncMock(return_value="key-arch"),
            ),
        ):
            await _generate_and_persist_pipeline_documents(
                project_id="p-1",
                source_id="src-1",
                task_db_id="task-1",
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
            )

        mock_reset_breaker.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_failed_domain_knowledge_skips_persist_but_still_tries_architecture(self):
        mock_pipeline = MagicMock()
        mock_pipeline.generate_domain_knowledge.return_value = {
            "status": "failed",
            "error": "boom",
        }
        mock_pipeline.generate_architecture_document.return_value = {
            "status": "success",
            "content": "# Architecture",
        }
        with (
            patch(self._ORCHESTRATOR_PATCH_TARGET, return_value=mock_pipeline),
            patch(
                "app.workers.source_code_task._persist_domain_knowledge_artifact",
                AsyncMock(),
            ) as mock_persist_domain,
            patch(
                "app.workers.source_code_task._persist_architecture_document_artifact",
                AsyncMock(return_value="key-arch"),
            ) as mock_persist_arch,
            patch(
                "app.workers.source_code_task._record_pipeline_documents_failure"
            ) as mock_record_failure,
        ):
            await _generate_and_persist_pipeline_documents(
                project_id="p-1",
                source_id="src-1",
                task_db_id="task-1",
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
            )

        mock_persist_domain.assert_not_awaited()
        mock_persist_arch.assert_awaited_once_with(project_id="p-1", content="# Architecture")
        # One artifact failed even though the other succeeded — the failure
        # must still be surfaced (error recorded, notified, activity-logged).
        mock_record_failure.assert_called_once_with(
            project_id="p-1",
            source_id="src-1",
            error="Domain knowledge generation failed: boom",
        )

    @pytest.mark.asyncio
    async def test_pipeline_construction_failure_is_swallowed(self):
        with (
            patch(
                self._ORCHESTRATOR_PATCH_TARGET,
                side_effect=RuntimeError("bad config"),
            ),
            patch(
                "app.workers.source_code_task._record_pipeline_documents_failure"
            ) as mock_record_failure,
        ):
            await _generate_and_persist_pipeline_documents(
                project_id="p-1",
                source_id="src-1",
                task_db_id="task-1",
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
            )
        # No exception propagates — best-effort per the pipeline-completion caller.
        # But the failure must still be recorded (error/notify/activity-log).
        mock_record_failure.assert_called_once_with(
            project_id="p-1",
            source_id="src-1",
            error="Unexpected error: bad config",
        )

    @pytest.mark.asyncio
    async def test_circuit_breaker_error_is_swallowed(self):
        """CircuitBreakerError is deliberately a BaseException (see
        llm_client.py's class docstring), so the plain `except Exception`
        this function otherwise relies on would NOT have caught it — left
        unhandled it would escape this best-effort task entirely uncaught,
        which under the real -P prefork pool crashes the worker child
        outright instead of recording a normal failure (see
        docs/CircuitBreakerError_Handling_Issue_Implications.docx). Must be
        logged and swallowed, same as any other failure here — and, unlike
        before, also recorded (error message/notification/activity log)."""
        from app.services.source_code_pipeline.src.ai.llm_client import CircuitBreakerError

        with (
            patch(
                self._ORCHESTRATOR_PATCH_TARGET,
                side_effect=CircuitBreakerError("8 consecutive LLM timeout(s) >= 8 cap"),
            ),
            patch(
                "app.workers.source_code_task._record_pipeline_documents_failure"
            ) as mock_record_failure,
        ):
            await _generate_and_persist_pipeline_documents(
                project_id="p-1",
                source_id="src-1",
                task_db_id="task-1",
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
            )
        # No exception propagates — best-effort per the pipeline-completion caller.
        mock_record_failure.assert_called_once_with(
            project_id="p-1",
            source_id="src-1",
            error="Circuit breaker tripped: 8 consecutive LLM timeout(s) >= 8 cap",
        )

    async def test_non_retryable_llm_error_is_swallowed(self):
        """Every OTHER non-retryable LLM reason (auth, invalid model, invalid
        request, context-length-exceeded, content policy, permission denied)
        must get the exact same best-effort swallow as CircuitBreakerError/
        CreditBalanceExhaustedError above — this stage runs AFTER the module
        pipeline already succeeded, so a fatal LLM error here must not
        silently start escaping this function's `except Exception` (which it
        would, being a BaseException) and breaking its established
        never-fail-the-overall-run contract."""
        from app.core.llm_errors import LLMErrorClassification, LLMErrorReason, NonRetryableLLMError

        classification = LLMErrorClassification(
            retryable=False,
            reason=LLMErrorReason.AUTHENTICATION,
            provider="anthropic",
            message="Invalid API key provided.",
            user_message="The AI provider rejected the request due to an invalid API key.",
        )

        with (
            patch(
                self._ORCHESTRATOR_PATCH_TARGET,
                side_effect=NonRetryableLLMError(classification),
            ),
            patch(
                "app.workers.source_code_task._record_pipeline_documents_failure"
            ) as mock_record_failure,
        ):
            await _generate_and_persist_pipeline_documents(
                project_id="p-1",
                source_id="src-1",
                task_db_id="task-1",
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
            )
        # No exception propagates — best-effort per the pipeline-completion caller.
        mock_record_failure.assert_called_once_with(
            project_id="p-1",
            source_id="src-1",
            error="Non-retryable LLM error: [authentication] Invalid API key provided.",
        )


class TestRecordPipelineDocumentsFailure:
    """Covers surfacing a domain-knowledge/architecture-document generation
    failure without reverting the already-finalized Source/SourceIngestion
    status back to FAILED (the main module pipeline already succeeded by the
    time this runs) — only the error message and a single, run-level user
    notification are recorded. Per-artifact activity-log entries are a
    separate concern, recorded by ``_record_stage_activity`` at each
    stage's own call site — not duplicated here."""

    def test_records_error_and_notifies_owner(self):
        project = SimpleNamespace(
            id=UUID("00000000-0000-0000-0000-000000000777"),
            name="Demo Project",
            owner_id=UUID("00000000-0000-0000-0000-000000000042"),
        )
        mock_uow = MagicMock()
        mock_uow.__enter__.return_value.projects.get_by_uuid.return_value = project

        with (
            patch("app.workers.source_code_task._set_source_processing_error") as mock_set_error,
            patch("app.workers.source_code_task._add_source_ingestion_error") as mock_add_error,
            patch("app.db.unit_of_work.UnitOfWork", return_value=mock_uow),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _record_pipeline_documents_failure(
                project_id="00000000-0000-0000-0000-000000000777",
                source_id="00000000-0000-0000-0000-000000000778",
                error="litellm.BadRequestError: credit balance too low",
            )

        mock_set_error.assert_called_once_with(
            "00000000-0000-0000-0000-000000000778",
            "litellm.BadRequestError: credit balance too low",
        )
        mock_add_error.assert_called_once_with(
            source_ids=["00000000-0000-0000-0000-000000000778"],
            error="litellm.BadRequestError: credit balance too low",
        )

        mock_publish.assert_called_once()
        publish_kwargs = mock_publish.call_args.kwargs
        assert publish_kwargs["user_id"] == UUID("00000000-0000-0000-0000-000000000042")
        assert publish_kwargs["notification_type"] == NotificationType.ERROR
        assert "credit balance too low" in publish_kwargs["message"]

    def test_no_owner_skips_notification_without_raising(self):
        project = SimpleNamespace(
            id=UUID("00000000-0000-0000-0000-000000000777"), name="Demo", owner_id=None
        )
        mock_uow = MagicMock()
        mock_uow.__enter__.return_value.projects.get_by_uuid.return_value = project

        with (
            patch("app.workers.source_code_task._set_source_processing_error"),
            patch("app.workers.source_code_task._add_source_ingestion_error"),
            patch("app.db.unit_of_work.UnitOfWork", return_value=mock_uow),
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            _record_pipeline_documents_failure(
                project_id="00000000-0000-0000-0000-000000000777",
                source_id="00000000-0000-0000-0000-000000000778",
                error="boom",
            )

        mock_publish.assert_not_called()

    def test_notification_failure_is_swallowed(self):
        """A notification-layer failure must not propagate — this helper is
        itself called from a best-effort context and must never raise."""
        with (
            patch("app.workers.source_code_task._set_source_processing_error"),
            patch("app.workers.source_code_task._add_source_ingestion_error"),
            patch(
                "app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db unavailable")
            ),
        ):
            _record_pipeline_documents_failure(
                project_id="00000000-0000-0000-0000-000000000777",
                source_id="00000000-0000-0000-0000-000000000778",
                error="boom",
            )
        # No exception propagates.


class _FakePipeline:
    """Minimal stand-in for a redis-py pipeline: queues incr(), executes as a batch."""

    def __init__(self, store: dict[str, int]) -> None:
        self._store = store
        self._queued_incrs: list[str] = []

    def incr(self, key: str):
        self._queued_incrs.append(key)
        return self

    def expire(self, _key: str, _ttl: int):
        return self

    def execute(self) -> list[int]:
        results = [self._store.setdefault(key, 0) + 1 for key in self._queued_incrs]
        for key, value in zip(self._queued_incrs, results, strict=True):
            self._store[key] = value
        self._queued_incrs = []
        return results


class _FakeRedisClient:
    """Minimal stand-in for `celery_app.backend.client` used by the completion counter."""

    def __init__(self) -> None:
        self._store: dict[str, int] = {}
        self._locks: dict[str, str] = {}

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self._store)

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self._locks:
            return False
        self._locks[key] = value
        return True

    def get(self, key: str):
        return self._store.get(key)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


class TestPersistSingleModuleTaskRetryPolicy:
    """CELERY_RETRY_POLICY.docx Section A: persist_single_module retries once
    on transient infrastructure (including live-Neo4j errors, since this task
    actually writes to Neo4j) before falling back to its existing "mark this
    module failed, keep going" behavior — never letting an unhandled
    exception abort the outer chain(*module_chains). total_modules=0 so the
    "last module done" completion-counter branch (covered separately by
    TestPersistSingleModuleTaskCompletion) doesn't engage."""

    def _run(self, *, side_effect: BaseException, retries: int = 0):
        _persist_single_module_task.push_request(retries=retries, id="req-1")
        try:
            with (
                patch(
                    "app.core.task_control.register_delivery_within_limit",
                    return_value=True,
                ),
                patch(
                    "app.workers.source_code_task._run_async",
                    side_effect=side_effect,
                ),
                patch.object(
                    _persist_single_module_task,
                    "retry",
                    side_effect=RuntimeError("retry-triggered"),
                ) as mock_retry,
            ):
                result = _persist_single_module_task.run(
                    {"module_id": "MOD-1", "status": "success"},
                    project_id="proj-1",
                    source_id="src-1",
                    total_modules=0,
                )
            return result, mock_retry
        finally:
            _persist_single_module_task.pop_request()

    def test_retries_once_on_soft_time_limit_exceeded(self) -> None:
        with pytest.raises(RuntimeError, match="retry-triggered"):
            self._run(side_effect=SoftTimeLimitExceeded(), retries=0)

    def test_soft_time_limit_exceeded_fails_when_retries_exhausted(self) -> None:
        result, mock_retry = self._run(side_effect=SoftTimeLimitExceeded(), retries=1)

        mock_retry.assert_not_called()
        assert result["error"] == MSG_SOURCE_CODE_MODULE_PERSIST_TIME_LIMIT_EXCEEDED

    def test_retries_once_on_retryable_redis_error(self) -> None:
        import redis.exceptions

        exc = redis.exceptions.ConnectionError("broker blip")
        with pytest.raises(RuntimeError, match="retry-triggered"):
            self._run(side_effect=exc, retries=0)

    def test_retries_once_on_retryable_neo4j_error(self) -> None:
        import neo4j.exceptions

        exc = neo4j.exceptions.ServiceUnavailable("db unreachable")
        with pytest.raises(RuntimeError, match="retry-triggered"):
            self._run(side_effect=exc, retries=0)

    def test_neo4j_error_falls_back_to_failed_module_when_retries_exhausted(self) -> None:
        import neo4j.exceptions

        exc = neo4j.exceptions.ServiceUnavailable("db unreachable")
        result, mock_retry = self._run(side_effect=exc, retries=1)

        mock_retry.assert_not_called()
        assert result["error"] == str(exc)
        assert result["modules_processed"] == 0

    def test_retries_once_on_retryable_boto_client_error(self) -> None:
        from botocore.exceptions import ClientError

        exc = ClientError(
            {"Error": {"Code": "Throttling"}, "ResponseMetadata": {"HTTPStatusCode": 400}},
            "GetObject",
        )
        with pytest.raises(RuntimeError, match="retry-triggered"):
            self._run(side_effect=exc, retries=0)

    def test_does_not_retry_non_retryable_boto_client_error(self) -> None:
        from botocore.exceptions import ClientError

        exc = ClientError(
            {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
            "GetObject",
        )
        result, mock_retry = self._run(side_effect=exc, retries=0)

        mock_retry.assert_not_called()
        assert result["error"] == str(exc)

    def test_does_not_retry_deterministic_error(self) -> None:
        """Unchanged behavior: a plain ValueError still just fails this module."""
        result, mock_retry = self._run(side_effect=ValueError("bad data"), retries=0)

        mock_retry.assert_not_called()
        assert result["error"] == "bad data"

    def test_poison_loop_guard_dead_letters_without_processing(self) -> None:
        with (
            patch(
                "app.core.task_control.register_delivery_within_limit",
                return_value=False,
            ) as mock_guard,
            patch("app.workers.source_code_task._run_async") as mock_run_async,
        ):
            result = _persist_single_module_task.run(
                {"module_id": "MOD-1", "status": "success"},
                project_id="proj-1",
                source_id="src-1",
                total_modules=0,
            )

        mock_guard.assert_called_once()
        mock_run_async.assert_not_called()
        assert "poison-loop guard" in result["error"]


class TestModuleActivityLogging:
    """Per-module activity-feed entries: module name, total features, total
    user stories, and running status (started/completed/failed/cancelled)
    for the source-code pipeline. Activity-log rows are append-only, so each
    outcome is its own row rather than one row being updated in place."""

    def test_process_single_module_task_logs_started(
        self, _mock_source_code_record_activity
    ) -> None:
        project_id = "00000000-0000-0000-0000-0000000000a1"
        source_id = "00000000-0000-0000-0000-0000000000a2"
        with (
            patch(
                "app.core.task_control.register_delivery_within_limit",
                return_value=True,
            ),
            patch(
                "app.services.source_code_pipeline.pipeline_orchestrator.PipelineContext",
                side_effect=RuntimeError("stop after logging started"),
            ),
        ):
            _process_single_module_task.run(
                project_dir=".",
                config_path="./pyproject.toml",
                codebase_dir="./app",
                module_data={"module_id": "MOD-1", "module_name": "Authorization"},
                artifacts_enriched={},
                module_manifest={},
                project_id=project_id,
                source_id=source_id,
            )

        _mock_source_code_record_activity.assert_called_once()
        call_kwargs = _mock_source_code_record_activity.call_args.kwargs
        assert call_kwargs["project_id"] == UUID(project_id)
        assert call_kwargs["activity_type"].value == "source_code_module_started"
        assert call_kwargs["data"] == {
            "source_id": source_id,
            "module_id": "MOD-1",
            "module_name": "Authorization",
            "status": "started",
        }

    def test_process_single_module_task_logs_cancelled_at_entry_checkpoint(
        self, _mock_source_code_record_activity
    ) -> None:
        project_id = "00000000-0000-0000-0000-0000000000a3"
        source_id = "00000000-0000-0000-0000-0000000000a4"
        from app.workers._task_helpers import TaskCancelledError

        with (
            patch(
                "app.workers.source_code_task._handle_source_code_cancellation",
                return_value=True,
            ),
            pytest.raises(TaskCancelledError),
        ):
            _process_single_module_task.run(
                project_dir=".",
                config_path="./pyproject.toml",
                codebase_dir="./app",
                module_data={"module_id": "MOD-1", "module_name": "Billing"},
                artifacts_enriched={},
                module_manifest={},
                project_id=project_id,
                source_id=source_id,
            )

        _mock_source_code_record_activity.assert_called_once()
        call_kwargs = _mock_source_code_record_activity.call_args.kwargs
        assert call_kwargs["activity_type"].value == "source_code_module_cancelled"
        assert call_kwargs["data"]["module_name"] == "Billing"

    def test_process_single_module_task_logs_failed_on_credit_balance_exhausted(
        self, _mock_source_code_record_activity
    ) -> None:
        """CircuitBreakerTaskFailure/CreditBalanceExhaustedTaskFailure bypass
        _persist_single_module_task entirely (they raise, not return, to stop
        the chain) — so the per-module FAILED activity must be logged
        directly here, not deferred to the persist task."""
        from app.services.source_code_pipeline.src.ai.llm_client import (
            CreditBalanceExhaustedError,
        )
        from app.workers._task_helpers import CreditBalanceExhaustedTaskFailure

        project_id = "00000000-0000-0000-0000-0000000000a5"
        source_id = "00000000-0000-0000-0000-0000000000a6"
        credit_exc = CreditBalanceExhaustedError("Your credit balance is too low")

        with (
            patch(
                "app.core.task_control.register_delivery_within_limit",
                return_value=True,
            ),
            patch(
                "app.services.source_code_pipeline.pipeline_orchestrator.PipelineContext",
                side_effect=credit_exc,
            ),
            pytest.raises(CreditBalanceExhaustedTaskFailure),
        ):
            _process_single_module_task.run(
                project_dir=".",
                config_path="./pyproject.toml",
                codebase_dir="./app",
                module_data={"module_id": "MOD-1", "module_name": "Fraud"},
                artifacts_enriched={},
                module_manifest={},
                project_id=project_id,
                source_id=source_id,
            )

        assert _mock_source_code_record_activity.call_count == 2
        outcomes = [
            c.kwargs["activity_type"].value
            for c in _mock_source_code_record_activity.call_args_list
        ]
        assert outcomes == ["source_code_module_started", "source_code_module_failed"]
        failed_call = _mock_source_code_record_activity.call_args_list[1]
        assert failed_call.kwargs["data"]["error"] == str(credit_exc)

    def test_persist_single_module_task_logs_completed_with_counts(
        self, _mock_source_code_record_activity
    ) -> None:
        project_id = "00000000-0000-0000-0000-0000000000a7"
        source_id = "00000000-0000-0000-0000-0000000000a8"
        persist_result = {
            "modules_processed": 1,
            "group_specs_stored": 5,
            "backlog_stories_stored": 12,
            "srs_evidence_stored": 3,
        }
        with (
            patch("app.workers.source_code_task._run_async", return_value=persist_result),
            patch(
                "app.workers.source_code_task._increment_source_ingestion_module_counts"
            ) as mock_increment,
        ):
            _persist_single_module_task.run(
                {"module_id": "MOD-1", "status": "success"},
                project_id=project_id,
                source_id=source_id,
                module_name="Authorization",
                total_modules=0,
            )

        _mock_source_code_record_activity.assert_called_once()
        call_kwargs = _mock_source_code_record_activity.call_args.kwargs
        assert call_kwargs["activity_type"].value == "source_code_module_completed"
        assert call_kwargs["data"] == {
            "source_id": source_id,
            "module_id": "MOD-1",
            "module_name": "Authorization",
            "status": "completed",
            "total_features": 5,
            "total_user_stories": 12,
        }
        mock_increment.assert_called_once_with(
            source_ids=[source_id], modules=1, features=5, user_stories=12
        )

    def test_persist_single_module_task_logs_failed(
        self, _mock_source_code_record_activity
    ) -> None:
        project_id = "00000000-0000-0000-0000-0000000000a9"
        source_id = "00000000-0000-0000-0000-0000000000b0"
        persist_result = {
            "modules_processed": 0,
            "group_specs_stored": 0,
            "backlog_stories_stored": 0,
            "srs_evidence_stored": 0,
            "error": "boom",
        }
        with (
            patch("app.workers.source_code_task._run_async", return_value=persist_result),
            patch(
                "app.workers.source_code_task._increment_source_ingestion_module_counts"
            ) as mock_increment,
        ):
            _persist_single_module_task.run(
                {"module_id": "MOD-1", "status": "failed", "error": "boom"},
                project_id=project_id,
                source_id=source_id,
                module_name="Billing",
                total_modules=0,
            )

        _mock_source_code_record_activity.assert_called_once()
        call_kwargs = _mock_source_code_record_activity.call_args.kwargs
        assert call_kwargs["activity_type"].value == "source_code_module_failed"
        assert call_kwargs["data"] == {
            "source_id": source_id,
            "module_id": "MOD-1",
            "module_name": "Billing",
            "status": "failed",
            "error": "boom",
        }
        mock_increment.assert_called_once_with(source_ids=[source_id], modules_failed=1)

    def test_persist_single_module_task_logs_cancelled(
        self, _mock_source_code_record_activity
    ) -> None:
        project_id = "00000000-0000-0000-0000-0000000000b1"
        source_id = "00000000-0000-0000-0000-0000000000b2"
        with (
            patch("app.core.task_control.is_request_cancelled", return_value=True),
            patch("app.workers.source_code_task._finalize_source_code_cancellation"),
        ):
            result = _persist_single_module_task.run(
                {"module_id": "MOD-1", "status": "success"},
                project_id=project_id,
                source_id=source_id,
                module_name="Fraud",
                request_id="req-cancel",
                total_modules=0,
            )

        assert result["status"] == "cancelled"
        _mock_source_code_record_activity.assert_called_once()
        call_kwargs = _mock_source_code_record_activity.call_args.kwargs
        assert call_kwargs["activity_type"].value == "source_code_module_cancelled"
        assert call_kwargs["data"]["module_name"] == "Fraud"


class TestPersistSingleModuleTaskCompletion:
    """Covers the "last module done" branch of _persist_single_module_task —
    specifically that domain-knowledge/architecture-document generation is
    dispatched as its own task (_generate_pipeline_documents_task) rather
    than run inline, so a slow LLM call there can no longer push this task
    past its own (much tighter) hard time limit — see
    _generate_pipeline_documents_task's docstring for the incident this
    fixes. Cleanup is chained as that task's `link` so it still only runs
    (and deletes project_dir) once document generation is done.
    """

    def _run_completion(
        self,
        *,
        module_status: str,
        total_modules: int,
        run_async_side_effect: list[object],
    ):
        fake_redis = _FakeRedisClient()
        mock_celery_app = MagicMock()
        mock_celery_app.backend.client = fake_redis

        with (
            patch("app.core.celery_app.celery_app", mock_celery_app),
            patch("app.workers.source_code_task._mark_status") as mock_mark_status,
            patch("app.workers.source_code_task.emit_task_event") as mock_emit,
            patch("app.workers.source_code_task._finalize_source_code_pipeline") as mock_finalize,
            patch(
                "app.workers.source_code_task._cleanup_project_folder_task.apply_async"
            ) as mock_cleanup,
            patch(
                "app.workers.source_code_task._generate_pipeline_documents_task.apply_async"
            ) as mock_generate_docs,
            patch(
                "app.workers.source_code_task._run_async",
                side_effect=run_async_side_effect,
            ) as mock_run_async,
        ):
            result = _persist_single_module_task.run(
                {"module_id": "MOD-1", "status": module_status},
                project_id="proj-1",
                source_id="src-1",
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
                task_db_id="task-1",
                total_modules=total_modules,
                request_id=None,
            )

        return {
            "result": result,
            "mock_mark_status": mock_mark_status,
            "mock_emit": mock_emit,
            "mock_finalize": mock_finalize,
            "mock_cleanup": mock_cleanup,
            "mock_generate_docs": mock_generate_docs,
            "mock_run_async": mock_run_async,
        }

    def test_dispatches_pipeline_documents_task_with_cleanup_as_link_when_not_all_failed(self):
        persist_result = {
            "modules_processed": 1,
            "group_specs_stored": 0,
            "backlog_stories_stored": 0,
            "srs_evidence_stored": 0,
        }
        mocks = self._run_completion(
            module_status="success",
            total_modules=1,
            run_async_side_effect=[persist_result],
        )

        assert mocks["result"] == persist_result
        # Surfaced so _run_pipeline_orchestrator can block on the chain's final
        # result and return the pipeline's real terminal status instead of
        # always "running".
        assert mocks["result"]["status"] == SourceIngestionStatus.READY_FOR_REVIEW.value
        mocks["mock_finalize"].assert_called_once_with(
            project_id="proj-1",
            source_id="src-1",
            status=SourceIngestionStatus.READY_FOR_REVIEW.value,
            task_db_id="task-1",
            error=None,
            failed_modules=0,
        )
        # Source.status also uses ready_for_review, never completed, for the
        # source-code pipeline's success transition.
        mocks["mock_mark_status"].assert_called_once_with(
            "src-1",
            SOURCE_STATUS_READY_FOR_REVIEW,
            task_db_id="task-1",
            project_id="proj-1",
            stage="source_code.pipeline.ready_for_review",
        )
        mocks["mock_generate_docs"].assert_called_once()
        call_kwargs = mocks["mock_generate_docs"].call_args.kwargs
        assert call_kwargs["kwargs"] == {
            "project_id": "proj-1",
            "source_id": "src-1",
            "project_dir": "/tmp/p",
            "config_path": "/tmp/p/config.json",
            "codebase_dir": "/tmp/p/code",
            "skip_processing": False,
            "task_db_id": "task-1",
        }
        # Cleanup deletes project_dir, so it must run only as a callback
        # after document generation finishes, not dispatched independently.
        link_signature = call_kwargs["link"]
        assert link_signature.task == "tasks.parse_code.cleanup_project_folder"
        assert link_signature.args == ("proj-1",)
        assert mocks["mock_run_async"].call_count == 1
        mocks["mock_cleanup"].assert_not_called()

    def test_skips_pipeline_documents_when_all_modules_failed(self):
        persist_result = {
            "modules_processed": 0,
            "group_specs_stored": 0,
            "backlog_stories_stored": 0,
            "srs_evidence_stored": 0,
            "error": "boom",
        }
        mocks = self._run_completion(
            module_status="failed",
            total_modules=1,
            run_async_side_effect=[persist_result],
        )

        mocks["mock_finalize"].assert_called_once_with(
            project_id="proj-1",
            source_id="src-1",
            status=SourceIngestionStatus.FAILED.value,
            task_db_id="task-1",
            error="All 1/1 modules failed.",
            failed_modules=1,
        )
        assert mocks["result"]["status"] == SourceIngestionStatus.FAILED.value
        mocks["mock_generate_docs"].assert_not_called()
        assert mocks["mock_run_async"].call_count == 1
        # No document generation to wait on — cleanup is dispatched directly.
        mocks["mock_cleanup"].assert_called_once_with(args=["proj-1"], countdown=30)

    def test_duplicate_completion_callback_still_reports_final_status(self):
        """A redelivered final-module task that finds the completion lock
        already held must not leave the caller's result defaulted to
        "running" — it should read back the status the earlier (winning)
        attempt already set on the source."""
        source_id = "00000000-0000-0000-0000-000000000099"
        fake_redis = _FakeRedisClient()
        lock_key = f"module_completion_lock:{source_id}"
        fake_redis._locks[lock_key] = "1"  # simulate an already-fired completion
        mock_celery_app = MagicMock()
        mock_celery_app.backend.client = fake_redis

        existing_source = SimpleNamespace(status=SOURCE_STATUS_READY_FOR_REVIEW)
        persist_result = {
            "modules_processed": 1,
            "group_specs_stored": 0,
            "backlog_stories_stored": 0,
            "srs_evidence_stored": 0,
        }

        with (
            patch("app.core.celery_app.celery_app", mock_celery_app),
            patch("app.workers.source_code_task._mark_status"),
            patch("app.workers.source_code_task.emit_task_event"),
            patch("app.workers.source_code_task._finalize_source_code_pipeline"),
            patch("app.workers.source_code_task._cleanup_project_folder_task.apply_async"),
            patch("app.workers.source_code_task._generate_pipeline_documents_task.apply_async"),
            patch("app.workers.source_code_task._run_async", return_value=persist_result),
            patch(
                "app.db.unit_of_work.UnitOfWork",
                return_value=_mock_uow(existing_source),
            ),
        ):
            result = _persist_single_module_task.run(
                {"module_id": "MOD-1", "status": "success"},
                project_id="proj-1",
                source_id=source_id,
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
                task_db_id="task-1",
                total_modules=1,
                request_id=None,
            )

        assert result["status"] == SOURCE_STATUS_READY_FOR_REVIEW

    def test_pipeline_document_generation_uses_ai_time_budget_not_persistence_budget(self):
        """Regression test for the incident this change fixes: document
        generation used to run inline inside _persist_single_module_task
        and could push it past TASK_PERSIST_SINGLE_MODULE_TIME_LIMIT (35 min)
        — a hard kill that bypassed every in-process except block and
        propagated as an uncaught TimeLimitExceeded through the orchestrator's
        blocking chain .get(), aborting the whole pipeline. It must instead
        run under _generate_pipeline_documents_task's own much larger
        AI-generation budget.
        """
        assert _generate_pipeline_documents_task.soft_time_limit == TASK_AI_SOFT_TIME_LIMIT
        assert _generate_pipeline_documents_task.time_limit == TASK_AI_TIME_LIMIT

    def test_generate_pipeline_documents_task_runs_generation_via_run_async(self):
        with (
            patch(
                "app.workers.source_code_task._generate_and_persist_pipeline_documents"
            ) as mock_generate,
            patch(
                "app.workers.source_code_task._run_async",
                side_effect=lambda coro: coro,
            ) as mock_run_async,
        ):
            _generate_pipeline_documents_task.run(
                project_id="proj-1",
                source_id="src-1",
                project_dir="/tmp/p",
                config_path="/tmp/p/config.json",
                codebase_dir="/tmp/p/code",
                skip_processing=False,
                task_db_id="task-1",
            )

        mock_generate.assert_called_once_with(
            project_id="proj-1",
            source_id="src-1",
            task_db_id="task-1",
            project_dir="/tmp/p",
            config_path="/tmp/p/config.json",
            codebase_dir="/tmp/p/code",
            skip_processing=False,
        )
        mock_run_async.assert_called_once()


class TestFeatureRegenerationSourceIngestionId:
    @pytest.mark.asyncio
    async def test_upsert_one_regenerated_story_stamps_source_ingestion_id(self):
        us_repo = MagicMock()
        us_repo.get_user_story_detail_for_project = AsyncMock(return_value=None)
        us_repo.get_user_story_version_by_id = AsyncMock(return_value=None)
        us_repo.snapshot_user_story_version = AsyncMock()
        us_repo.bulk_upsert_user_stories_for_project = AsyncMock()
        us_repo.change_user_story_status = AsyncMock()
        us_repo.update_user_story_sync_flags = AsyncMock()
        project_uuid = UUID("00000000-0000-0000-0000-000000000abc")

        change_type = await _upsert_one_regenerated_story(
            us_repo=us_repo,
            project_uuid=project_uuid,
            feature_id="feature-1",
            story={
                "id": "story-code-1",
                "title": "Create order",
                "as_a": "sales officer",
                "i_want_to": "create new order",
                "so_that": "process requests",
            },
            source_ingestion_id="ingestion-99",
        )

        assert change_type == "ADDED"
        us_repo.snapshot_user_story_version.assert_not_called()
        persisted_models = us_repo.bulk_upsert_user_stories_for_project.await_args.kwargs[
            "user_stories"
        ]
        assert persisted_models[0].source_ingestion_id == "ingestion-99"

    @pytest.mark.asyncio
    async def test_apply_feature_regeneration_forwards_source_ingestion_id(self):
        mf_repo = MagicMock()
        mf_repo.get_feature_for_module = AsyncMock(return_value=None)
        mf_repo.snapshot_feature_version = AsyncMock()
        mf_repo.update_feature = AsyncMock(return_value=True)
        mf_repo.update_feature_sync_flags = AsyncMock()
        project_uuid = UUID("00000000-0000-0000-0000-000000000abc")

        updated, change_type = await _apply_feature_regeneration(
            mf_repo=mf_repo,
            project_uuid=project_uuid,
            feature_id="feature-1",
            module_id="module-1",
            feature_payload={"id": "FEA_001", "title": "Feature", "description": "desc"},
            source_ingestion_id="ingestion-100",
        )

        assert updated is True
        assert change_type == "UPDATED"
        assert mf_repo.update_feature.call_args.kwargs["source_ingestion_id"] == "ingestion-100"


class TestUpsertOneRegeneratedStoryFieldMapping:
    """Regression tests: ``_upsert_one_regenerated_story`` must remap/carry the same
    acceptance-criteria and ``l2_sources`` fields that ``_build_backlog_result_from_results``
    already maps at initial generation time, instead of dropping them.
    """

    @pytest.mark.asyncio
    async def test_upsert_regenerated_story_maps_ac_l2_source_ref_and_l2_sources(self):
        us_repo = MagicMock()
        us_repo.get_user_story_detail_for_project = AsyncMock(return_value=None)
        us_repo.get_user_story_version_by_id = AsyncMock(return_value=None)
        us_repo.snapshot_user_story_version = AsyncMock()
        us_repo.bulk_upsert_user_stories_for_project = AsyncMock()
        us_repo.change_user_story_status = AsyncMock()
        us_repo.update_user_story_sync_flags = AsyncMock()
        project_uuid = UUID("00000000-0000-0000-0000-000000000abc")

        change_type = await _upsert_one_regenerated_story(
            us_repo=us_repo,
            project_uuid=project_uuid,
            feature_id="feature-1",
            story={
                "id": "story-code-1",
                "title": "Create order",
                "as_a": "sales officer",
                "i_want_to": "create new order",
                "so_that": "process requests",
                "acceptance_criteria": [
                    {
                        "id": "AC_001",
                        "path_type": "happy_path",
                        "given": "a valid order",
                        "when": "the officer submits it",
                        "then": "the order is created",
                        "l2_source_ref": "SRC::1",
                    }
                ],
                "l2_sources": ["SRC::1", "SRC::2"],
            },
        )

        assert change_type == "ADDED"
        persisted_models = us_repo.bulk_upsert_user_stories_for_project.await_args.kwargs[
            "user_stories"
        ]
        model = persisted_models[0]
        assert model.acceptance_criteria == [
            {
                "type": "happy_path",
                "given": "a valid order",
                "when": "the officer submits it",
                "then": "the order is created",
                "id": "AC_001",
                "ac_code": "AC_001",
                "l2_source_ref": "SRC::1",
            }
        ]
        assert model.l2_sources == ["SRC::1", "SRC::2"]

    @pytest.mark.asyncio
    async def test_upsert_regenerated_story_detects_l2_sources_only_change(self):
        existing = SimpleNamespace(
            version=1,
            title="Create order",
            description=None,
            as_a="sales officer",
            i_want_to="create new order",
            so_that="process requests",
            technical_notes=None,
            story_points=None,
            acceptance_criteria=[],
            nfrs=[],
            l2_sources=["SRC::1"],
        )
        us_repo = MagicMock()
        us_repo.get_user_story_detail_for_project = AsyncMock(return_value=existing)
        us_repo.get_user_story_version_by_id = AsyncMock(return_value=1)
        us_repo.snapshot_user_story_version = AsyncMock()
        us_repo.bulk_upsert_user_stories_for_project = AsyncMock()
        us_repo.change_user_story_status = AsyncMock()
        us_repo.update_user_story_sync_flags = AsyncMock()
        project_uuid = UUID("00000000-0000-0000-0000-000000000abc")

        change_type = await _upsert_one_regenerated_story(
            us_repo=us_repo,
            project_uuid=project_uuid,
            feature_id="feature-1",
            story={
                "id": "story-code-1",
                "title": "Create order",
                "as_a": "sales officer",
                "i_want_to": "create new order",
                "so_that": "process requests",
                "l2_sources": ["SRC::1", "SRC::2"],
            },
        )

        assert change_type == "UPDATED"
        us_repo.snapshot_user_story_version.assert_awaited_once_with(
            str(uuid5(NAMESPACE_URL, f"{project_uuid}:story-code-1"))
        )
        persisted_models = us_repo.bulk_upsert_user_stories_for_project.await_args.kwargs[
            "user_stories"
        ]
        assert persisted_models[0].version == 2

    @pytest.mark.asyncio
    async def test_upsert_regenerated_story_increments_version_even_when_project_scoped_detail_fetch_misses(
        self,
    ):
        """Regression test: a full Project->Module->Feature->UserStory traversal
        miss on ``get_user_story_detail_for_project`` must not silently reset
        an existing story's version back to 1 — the version must still come
        from ``get_user_story_version_by_id``, which matches by id alone."""
        us_repo = MagicMock()
        us_repo.get_user_story_detail_for_project = AsyncMock(return_value=None)
        us_repo.get_user_story_version_by_id = AsyncMock(return_value=4)
        us_repo.snapshot_user_story_version = AsyncMock()
        us_repo.bulk_upsert_user_stories_for_project = AsyncMock()
        us_repo.change_user_story_status = AsyncMock()
        us_repo.update_user_story_sync_flags = AsyncMock()
        project_uuid = UUID("00000000-0000-0000-0000-000000000abc")

        await _upsert_one_regenerated_story(
            us_repo=us_repo,
            project_uuid=project_uuid,
            feature_id="feature-1",
            story={
                "id": "story-code-1",
                "title": "Create order",
                "as_a": "sales officer",
                "i_want_to": "create new order",
                "so_that": "process requests",
            },
        )

        persisted_models = us_repo.bulk_upsert_user_stories_for_project.await_args.kwargs[
            "user_stories"
        ]
        assert persisted_models[0].version == 5

    @pytest.mark.asyncio
    async def test_upsert_regenerated_story_no_spurious_update_when_l2_sources_unchanged(self):
        existing = SimpleNamespace(
            version=1,
            title="Create order",
            description=None,
            as_a="sales officer",
            i_want_to="create new order",
            so_that="process requests",
            technical_notes=None,
            story_points=None,
            acceptance_criteria=[],
            nfrs=[],
            l2_sources=["SRC::1"],
        )
        us_repo = MagicMock()
        us_repo.get_user_story_detail_for_project = AsyncMock(return_value=existing)
        us_repo.get_user_story_version_by_id = AsyncMock(return_value=1)
        us_repo.snapshot_user_story_version = AsyncMock()
        us_repo.bulk_upsert_user_stories_for_project = AsyncMock()
        us_repo.change_user_story_status = AsyncMock()
        us_repo.update_user_story_sync_flags = AsyncMock()
        project_uuid = UUID("00000000-0000-0000-0000-000000000abc")

        change_type = await _upsert_one_regenerated_story(
            us_repo=us_repo,
            project_uuid=project_uuid,
            feature_id="feature-1",
            story={
                "id": "story-code-1",
                "title": "Create order",
                "as_a": "sales officer",
                "i_want_to": "create new order",
                "so_that": "process requests",
                "l2_sources": ["SRC::1"],
            },
        )

        assert change_type is None
        us_repo.snapshot_user_story_version.assert_not_called()
        us_repo.bulk_upsert_user_stories_for_project.assert_not_called()


class TestApplyFeatureRegenerationFunctionsMapping:
    """Regression tests: raw pipeline function items (``id``/``label``/``l2_source_ref``)
    must be remapped to the canonical ``fun_code``/``name``/``func_src_ref`` shape before
    being diffed/persisted — same mapping ``_build_module_feature_skeleton_from_results``
    already applies at generation time.
    """

    @pytest.mark.asyncio
    async def test_apply_feature_regeneration_remaps_raw_function_keys(self):
        mf_repo = MagicMock()
        mf_repo.get_feature_for_module = AsyncMock(return_value=None)
        mf_repo.snapshot_feature_version = AsyncMock()
        mf_repo.update_feature = AsyncMock(return_value=True)
        mf_repo.update_feature_sync_flags = AsyncMock()
        project_uuid = UUID("00000000-0000-0000-0000-000000000abc")

        updated, change_type = await _apply_feature_regeneration(
            mf_repo=mf_repo,
            project_uuid=project_uuid,
            feature_id="feature-1",
            module_id="module-1",
            feature_payload={
                "id": "FEA_001",
                "title": "Feature",
                "description": "desc",
                "functions": [
                    {
                        "id": "FN_001",
                        "label": "Create Order",
                        "description": "creates an order",
                        "l2_source_ref": "SRC::1",
                    }
                ],
            },
        )

        assert updated is True
        assert change_type == "UPDATED"
        persisted_functions = json.loads(mf_repo.update_feature.call_args.kwargs["functions_json"])
        assert persisted_functions == [
            {
                "fun_code": "FN_001",
                "name": "Create Order",
                "description": "creates an order",
                "func_src_ref": "SRC::1",
            }
        ]

    @pytest.mark.asyncio
    async def test_apply_feature_regeneration_no_spurious_update_when_functions_unchanged(self):
        from app.models.neo4j.module_feature_model import FunctionModel

        existing_feature = SimpleNamespace(
            name="Feature",
            description="desc",
            sources=[],
            l2_sources=[],
            functions=[
                FunctionModel(
                    name="Create Order",
                    fun_code="FN_001",
                    description="creates an order",
                    func_src_ref="SRC::1",
                )
            ],
        )
        mf_repo = MagicMock()
        mf_repo.get_feature_for_module = AsyncMock(return_value=existing_feature)
        mf_repo.snapshot_feature_version = AsyncMock()
        mf_repo.update_feature = AsyncMock(return_value=True)
        mf_repo.update_feature_sync_flags = AsyncMock()
        project_uuid = UUID("00000000-0000-0000-0000-000000000abc")

        updated, change_type = await _apply_feature_regeneration(
            mf_repo=mf_repo,
            project_uuid=project_uuid,
            feature_id="feature-1",
            module_id="module-1",
            feature_payload={
                "id": "FEA_NEW_002",
                "title": "Feature",
                "description": "desc",
                "functions": [
                    {
                        "id": "FN_001",
                        "label": "Create Order",
                        "description": "creates an order",
                        "l2_source_ref": "SRC::1",
                    }
                ],
                "l2_sources": [],
            },
        )

        assert updated is False
        assert change_type is None
        mf_repo.snapshot_feature_version.assert_not_called()
        mf_repo.update_feature.assert_not_called()
