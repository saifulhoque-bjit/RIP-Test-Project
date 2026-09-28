"""Unit tests for SourceIngestionRepository."""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

from app.core.enums.source_type import SourceType
from app.models.postgres.source_ingestion_model import SourceIngestion
from app.repositories.postgres.source_ingestion_repository import (
    SourceIngestionRepository,
    _apply_source_type_field_rules,
)


class _FakeQuery:
    def __init__(self, items):
        self._items = items
        self.filter_calls: list[tuple] = []
        self.for_update_calls = 0

    def filter(self, *args, **kwargs):
        self.filter_calls.append(args)
        return self

    def join(self, *args, **kwargs):
        return self

    def options(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        self.for_update_calls += 1
        return self

    def offset(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def count(self):
        return len(self._items)

    def first(self):
        return self._items[0] if self._items else None

    def all(self):
        return list(self._items)


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeSession:
    def __init__(self, query_items=None):
        self._query_items = query_items or []
        self.executed = []
        self.added = []
        self.flushed = False
        self.refreshed = []
        self.queries: list[_FakeQuery] = []

    def query(self, model):
        query = _FakeQuery(self._query_items)
        self.queries.append(query)
        return query

    def execute(self, statement):
        statement_text = str(statement)
        self.executed.append(statement_text)
        if "nextval('project_run_code_seq')" in statement_text:
            return _FakeScalarResult(1001)
        return _FakeScalarResult(None)

    def add(self, entity):
        self.added.append(entity)

    def flush(self):
        self.flushed = True

    def refresh(self, entity):
        self.refreshed.append(entity)


class TestCreateIngestion:
    def test_creates_ingestion_with_defaults(self):
        session = _FakeSession()
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]
        project_id = uuid.uuid4()

        ingestion = repository.create_ingestion(
            project_id=project_id, source_type=SourceType.RFP.value
        )

        assert ingestion.project_id == project_id
        assert ingestion.source_type == SourceType.RFP.value
        assert "SELECT nextval('project_run_code_seq')" in session.executed[0]
        assert ingestion.run_code == "RUN-1001"
        assert session.added == [ingestion]
        assert session.flushed is True
        assert session.refreshed == [ingestion]

    def test_drops_source_code_only_fields_for_rfp(self):
        session = _FakeSession()
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        ingestion = repository.create_ingestion(
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            source_language="cobol",
            frontend_stack="React",
            user_message="hello",
            source_layout_type="modular",
            is_incremental=True,
            description="kept regardless",
            skip_processing=True,
        )

        assert ingestion.source_language is None
        assert ingestion.frontend_stack is None
        assert ingestion.user_message is None
        # The override is dropped; source_layout_type is nullable with no
        # default, so it persists as NULL for any non-"source_code" type.
        assert ingestion.source_layout_type is None
        assert ingestion.is_incremental is None
        assert ingestion.description == "kept regardless"
        assert ingestion.skip_processing is True

    def test_keeps_source_code_only_fields_and_drops_is_incremental_and_user_message_for_source_code(
        self,
    ):
        session = _FakeSession()
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        ingestion = repository.create_ingestion(
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            source_language="cobol",
            frontend_stack="React",
            user_message="hello",
            source_layout_type="modular",
            is_incremental=True,
        )

        assert ingestion.source_language == "cobol"
        assert ingestion.frontend_stack == "React"
        assert ingestion.source_layout_type == "modular"
        assert ingestion.user_message is None
        assert ingestion.is_incremental is None

    def test_keeps_is_incremental_and_user_message_and_drops_source_code_only_fields_for_meeting_notes(
        self,
    ):
        session = _FakeSession()
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        ingestion = repository.create_ingestion(
            project_id=uuid.uuid4(),
            source_type=SourceType.MEETING_NOTES.value,
            source_language="cobol",
            user_message="hello",
            is_incremental=True,
        )

        assert ingestion.source_language is None
        assert ingestion.user_message == "hello"
        assert ingestion.is_incremental is True


class TestApplySourceTypeFieldRules:
    def test_source_code_keeps_stack_fields_drops_is_incremental_and_user_message(self):
        result = _apply_source_type_field_rules(
            SourceType.SOURCE_CODE.value,
            {
                "frontend_stack": "React",
                "is_incremental": True,
                "user_message": "hello",
                "description": "d",
            },
        )
        assert result == {"frontend_stack": "React", "description": "d"}

    def test_rfp_drops_all_conditional_groups(self):
        result = _apply_source_type_field_rules(
            SourceType.RFP.value,
            {
                "frontend_stack": "React",
                "is_incremental": True,
                "user_message": "hello",
                "description": "d",
            },
        )
        assert result == {"description": "d"}

    def test_meeting_notes_keeps_is_incremental_and_user_message_drops_stack_fields(self):
        result = _apply_source_type_field_rules(
            SourceType.MEETING_NOTES.value,
            {
                "frontend_stack": "React",
                "is_incremental": True,
                "user_message": "hello",
                "description": "d",
            },
        )
        assert result == {"is_incremental": True, "user_message": "hello", "description": "d"}


class TestGetById:
    def test_returns_none_when_not_found(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.get_by_id(uuid.uuid4()) is None

    def test_returns_matching_ingestion(self):
        ingestion = SourceIngestion(
            id=uuid.uuid4(), project_id=uuid.uuid4(), source_type=SourceType.RFP.value
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.get_by_id(ingestion.id) is ingestion


class TestGetLatestByProject:
    def test_returns_none_when_no_ingestions(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.get_latest_by_project(uuid.uuid4()) is None

    def test_returns_most_recent_ingestion(self):
        project_id = uuid.uuid4()
        latest = SourceIngestion(
            id=uuid.uuid4(), project_id=project_id, source_type=SourceType.RFP.value
        )
        older = SourceIngestion(
            id=uuid.uuid4(), project_id=project_id, source_type=SourceType.RFP.value
        )
        # order_by(created_at.desc()) is mocked away by _FakeQuery, so the
        # fake session is seeded with the already-desired order.
        session = _FakeSession(query_items=[latest, older])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.get_latest_by_project(project_id) is latest


class TestListRunning:
    def test_returns_empty_when_none_running(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.list_running() == []

    def test_returns_running_ingestions_across_projects(self):
        first = SourceIngestion(
            id=uuid.uuid4(), project_id=uuid.uuid4(), source_type=SourceType.RFP.value
        )
        second = SourceIngestion(
            id=uuid.uuid4(), project_id=uuid.uuid4(), source_type=SourceType.SOURCE_CODE.value
        )
        # status/deleted_at filtering is mocked away by _FakeQuery — the fake
        # session is seeded with items that already satisfy the filter.
        session = _FakeSession(query_items=[first, second])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.list_running()

        assert result == [first, second]


class TestExistsRunningForTenant:
    def test_returns_false_when_none_running(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.exists_running_for_tenant(uuid.uuid4()) is False

    def test_returns_true_when_a_running_ingestion_exists(self):
        running = SourceIngestion(
            id=uuid.uuid4(), project_id=uuid.uuid4(), source_type=SourceType.RFP.value
        )
        # status/deleted_at/tenant filtering is mocked away by _FakeQuery — the
        # fake session is seeded with an item that already satisfies the join+filter.
        session = _FakeSession(query_items=[running])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.exists_running_for_tenant(uuid.uuid4()) is True


class TestListOpenFeedbackOrIncrementalByProject:
    def test_returns_empty_when_none_open(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.list_open_feedback_or_incremental_by_project(uuid.uuid4()) == []

    def test_returns_ready_for_review_requirement_update_ingestions(self):
        project_id = uuid.uuid4()
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=project_id,
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status="ready_for_review",
        )
        # source_type/status/deleted_at filtering is mocked away by
        # _FakeQuery — the fake session is seeded with items that already
        # satisfy the filter.
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.list_open_feedback_or_incremental_by_project(project_id) == [ingestion]

    def test_filter_also_matches_is_incremental_uploads_not_just_requirement_update(self):
        """Regression test: a real incremental upload keeps its own document

        source_type (rfp/meeting_notes/etc.) and is distinguished only by
        is_incremental=True — the query must match that shape too, not just
        source_type == requirement_update, or a genuinely incremental
        ingestion never gets picked up for completion once its accept/reject
        round is fully resolved.
        """
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        repository.list_open_feedback_or_incremental_by_project(uuid.uuid4())

        filter_args = session.queries[-1].filter_calls[0]
        assert any("is_incremental" in str(arg) for arg in filter_args)


class TestHasUnresolvedFeedbackOrIncremental:
    def test_returns_false_when_none_open(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.has_unresolved_feedback_or_incremental(uuid.uuid4()) is False

    def test_returns_true_when_a_non_terminal_ingestion_exists(self):
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status="ready_for_review",
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.has_unresolved_feedback_or_incremental(ingestion.project_id) is True

    def test_filter_also_matches_is_incremental_uploads_not_just_requirement_update(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        repository.has_unresolved_feedback_or_incremental(uuid.uuid4())

        filter_args = session.queries[-1].filter_calls[0]
        assert any("is_incremental" in str(arg) for arg in filter_args)


class TestGetReadyForReviewGenerationIngestion:
    def test_returns_none_when_no_generation_ingestion_ready(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.get_ready_for_review_generation_ingestion(uuid.uuid4()) is None

    def test_returns_the_generation_ingestion(self):
        project_id = uuid.uuid4()
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=project_id,
            source_type=SourceType.RFP.value,
            status="ready_for_review",
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.get_ready_for_review_generation_ingestion(project_id) is ingestion


class TestGetPaginated:
    def test_returns_items_and_total(self):
        project_id = uuid.uuid4()
        items = [
            SourceIngestion(
                id=uuid.uuid4(), project_id=project_id, source_type=SourceType.RFP.value
            )
            for _ in range(3)
        ]
        session = _FakeSession(query_items=items)
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        results, total = repository.get_paginated(project_id, skip=0, limit=20)

        assert results == items
        assert total == 3


class TestUpdateFields:
    def test_returns_none_when_not_found(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.update_fields(uuid.uuid4(), tot_modules=5) is None

    def test_patches_supplied_fields(self):
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            tot_modules=0,
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]
        started_at = datetime.now(UTC)

        result = repository.update_fields(
            ingestion.id, tot_modules=5, mod_fea_gen_started_at=started_at
        )

        assert result is ingestion
        assert ingestion.tot_modules == 5
        assert ingestion.mod_fea_gen_started_at == started_at
        assert session.queries[0].for_update_calls == 0

    def test_locks_the_row_when_moving_status_away_from_cancelled(self):
        """The read must take FOR UPDATE whenever the write could move the
        row off "cancelled" — otherwise a concurrent cancel commit can land
        in the gap between this check and this write, and this call's own
        stale read would resurrect the row. Locking here even when the
        row's current status isn't cancelled yet is required: we can't know
        that without reading it first."""
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            status="running",
            tot_modules=0,
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.update_fields(ingestion.id, status="failed")

        assert result is ingestion
        assert ingestion.status == "failed"
        assert session.queries[0].for_update_calls == 1

    def test_ignores_status_change_once_cancelled(self):
        """cancelled is sticky — a later write trying to move the row to any
        other status must be dropped entirely, mirroring the same guard on
        Source.status and ProjectTask.status."""
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            status="cancelled",
            tot_modules=0,
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.update_fields(ingestion.id, status="running", tot_modules=9)

        assert result is ingestion
        assert ingestion.status == "cancelled"
        assert ingestion.tot_modules == 0
        assert session.queries[0].for_update_calls == 1

    def test_allows_reconfirming_cancelled_status_with_extra_fields(self):
        """The cancel-finalize path (mark_sources_and_ingestion_cancelled)
        merges status="cancelled" with extra bookkeeping fields — that must
        still land even though the row is already cancelled."""
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            status="cancelled",
            tot_modules=0,
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.update_fields(ingestion.id, status="cancelled", tot_modules=3)

        assert result is ingestion
        assert ingestion.status == "cancelled"
        assert ingestion.tot_modules == 3
        assert session.queries[0].for_update_calls == 0

    def test_allows_non_status_fields_once_cancelled(self):
        """A call that doesn't touch status at all must still land on an
        already-cancelled row."""
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.SOURCE_CODE.value,
            status="cancelled",
            tot_modules=0,
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.update_fields(ingestion.id, tot_modules=7)

        assert result is ingestion
        assert ingestion.tot_modules == 7
        assert session.queries[0].for_update_calls == 0


class TestIncrementReviewCount:
    def test_bumps_module_accepted_column(self):
        session = _FakeSession()
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        repository.increment_review_count(uuid.uuid4(), entity_type="module", accepted=True)

        assert (
            "tot_modules_accepted=(source_ingestions.tot_modules_accepted + "
            in (session.executed[-1])
        )

    def test_bumps_feature_rejected_column(self):
        session = _FakeSession()
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        repository.increment_review_count(uuid.uuid4(), entity_type="feature", accepted=False)

        assert (
            "tot_features_rejected=(source_ingestions.tot_features_rejected + "
            in (session.executed[-1])
        )

    def test_bumps_user_story_accepted_column(self):
        session = _FakeSession()
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        repository.increment_review_count(uuid.uuid4(), entity_type="user_story", accepted=True)

        assert (
            "tot_user_stories_accepted=(source_ingestions.tot_user_stories_accepted + "
            in (session.executed[-1])
        )


class TestIncrementModuleCompletionCounts:
    def test_bumps_only_the_non_zero_columns(self):
        session = _FakeSession()
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        repository.increment_module_completion_counts(
            uuid.uuid4(), modules=1, features=5, user_stories=12
        )

        statement = session.executed[-1]
        assert "tot_modules=(source_ingestions.tot_modules + " in statement
        assert "tot_features=(source_ingestions.tot_features + " in statement
        assert "tot_user_stories=(source_ingestions.tot_user_stories + " in statement
        assert "tot_modules_failed" not in statement

    def test_bumps_modules_failed_column(self):
        session = _FakeSession()
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        repository.increment_module_completion_counts(uuid.uuid4(), modules_failed=1)

        statement = session.executed[-1]
        assert "tot_modules_failed=(source_ingestions.tot_modules_failed + " in statement
        assert "tot_modules=(source_ingestions.tot_modules + " not in statement

    def test_noop_when_all_deltas_zero(self):
        session = _FakeSession()
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        repository.increment_module_completion_counts(uuid.uuid4())

        assert session.executed == []


class TestAddStage:
    def test_returns_none_when_not_found(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.add_stage(uuid.uuid4(), "module_feature") is None

    def test_appends_new_stage(self):
        ingestion = SourceIngestion(
            id=uuid.uuid4(), project_id=uuid.uuid4(), source_type=SourceType.RFP.value, stages=[]
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.add_stage(ingestion.id, "module_feature")

        assert result is ingestion
        assert ingestion.stages == ["module_feature"]

    def test_is_idempotent_when_stage_already_present(self):
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            stages=["module_feature"],
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.add_stage(ingestion.id, "module_feature")

        assert result is ingestion
        assert ingestion.stages == ["module_feature"]


class TestAddError:
    def test_returns_none_when_not_found(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.add_error(uuid.uuid4(), "boom") is None

    def test_appends_error(self):
        ingestion = SourceIngestion(
            id=uuid.uuid4(), project_id=uuid.uuid4(), source_type=SourceType.RFP.value, errors=[]
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.add_error(ingestion.id, "boom")

        assert result is ingestion
        assert ingestion.errors == ["boom"]

    def test_appends_to_existing_errors_without_overwriting(self):
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            errors=["first failure"],
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.add_error(ingestion.id, "second failure")

        assert result is ingestion
        assert ingestion.errors == ["first failure", "second failure"]

    def test_truncates_overlong_error(self):
        ingestion = SourceIngestion(
            id=uuid.uuid4(), project_id=uuid.uuid4(), source_type=SourceType.RFP.value, errors=[]
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.add_error(ingestion.id, "x" * 3000)

        assert result is ingestion
        assert len(ingestion.errors[0]) == 2000

    def test_no_op_on_falsy_error(self):
        ingestion = SourceIngestion(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            source_type=SourceType.RFP.value,
            errors=["existing"],
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.add_error(ingestion.id, "")

        assert result is ingestion
        assert ingestion.errors == ["existing"]


class TestSoftDelete:
    def test_returns_none_when_not_found(self):
        session = _FakeSession(query_items=[])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        assert repository.soft_delete(uuid.uuid4()) is None

    def test_sets_deleted_at(self):
        ingestion = SourceIngestion(
            id=uuid.uuid4(), project_id=uuid.uuid4(), source_type=SourceType.RFP.value
        )
        session = _FakeSession(query_items=[ingestion])
        repository = SourceIngestionRepository(session)  # type: ignore[arg-type]

        result = repository.soft_delete(ingestion.id)

        assert result is ingestion
        assert ingestion.deleted_at is not None
