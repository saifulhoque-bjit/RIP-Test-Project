"""Repository for SourceIngestion — persisted upload-batch rollup rows."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, text, update
from sqlalchemy.orm import Session, joinedload

from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.source_type import SourceType
from app.models.postgres.project_model import Project
from app.models.postgres.source_ingestion_model import SourceIngestion
from app.repositories.postgres.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Only meaningful for source_type == "source_code"; dropped for any other type.
_SOURCE_CODE_ONLY_FIELDS = frozenset(
    {
        "source_language",
        "frontend_stack",
        "backend_stack",
        "infrastructure_stack",
        "architecture_stack",
        "database_stack",
        "coding_standard",
        "database_strategy",
        "architecture",
        "security",
        "source_layout_type",
    }
)

# Only meaningful when source_type is neither "rfp" nor "source_code"
# (i.e. additional_rfp / meeting_notes / requirement_update).
_NON_RFP_NON_SOURCE_CODE_ONLY_FIELDS = frozenset({"is_incremental", "user_message"})


def _apply_source_type_field_rules(
    source_type: str, fields: dict[str, object]
) -> dict[str, object]:
    """Drop SourceIngestion fields that don't apply to *source_type*.

    ``description`` and ``skip_processing`` always apply and are left alone.
    """
    filtered = dict(fields)
    if source_type != SourceType.SOURCE_CODE.value:
        for name in _SOURCE_CODE_ONLY_FIELDS:
            filtered.pop(name, None)
    if source_type in (SourceType.RFP.value, SourceType.SOURCE_CODE.value):
        for name in _NON_RFP_NON_SOURCE_CODE_ONLY_FIELDS:
            filtered.pop(name, None)
    return filtered


class SourceIngestionRepository(BaseRepository[SourceIngestion]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, SourceIngestion)

    # ── Single-row lookups ─────────────────────────────────────────────────

    def get_by_id(self, ingestion_id: UUID) -> SourceIngestion | None:
        """Return a non-deleted SourceIngestion by its UUID, or None."""
        return (
            self._session.query(SourceIngestion)
            .filter(
                SourceIngestion.id == ingestion_id,
                SourceIngestion.deleted_at.is_(None),
            )
            .first()
        )

    # ── Project-scoped queries ─────────────────────────────────────────────

    def get_latest_by_project(self, project_id: UUID) -> SourceIngestion | None:
        """Return the most recently created non-deleted ingestion for a project, or None.

        Used to resolve "the ingestion this generation run applies to" from
        call sites that only have a ``project_id`` (no ``source_ids``) in scope.
        """
        return (
            self._session.query(SourceIngestion)
            .filter(
                SourceIngestion.project_id == project_id,
                SourceIngestion.deleted_at.is_(None),
            )
            .order_by(SourceIngestion.created_at.desc())
            .first()
        )

    def get_earliest_by_project(self, project_id: UUID) -> SourceIngestion | None:
        """Return the first (oldest) non-deleted ingestion created for a project, or None.

        Used to resolve the original upload batch's settings (e.g.
        ``source_language``) for flows that revise/regenerate against the
        project's initial source-code ingestion rather than its latest one.
        """
        return (
            self._session.query(SourceIngestion)
            .filter(
                SourceIngestion.project_id == project_id,
                SourceIngestion.deleted_at.is_(None),
            )
            .order_by(SourceIngestion.created_at.asc())
            .first()
        )

    def list_running_by_project(
        self,
        project_id: UUID,
        *,
        source_types: list[str] | None = None,
    ) -> list[SourceIngestion]:
        """Return non-deleted ingestions for a project currently in `running` status.

        Used to guard against dispatching a new regeneration/feedback request
        that targets an entity already being processed by an in-flight
        ingestion. Pass `source_types` to scope the check to specific
        ingestion sources — e.g. the original upload ingestion for a given
        `SourceType` (rfp, source_code, ...), as opposed to a later
        feedback-driven `requirement_update` ingestion.
        """
        query = self._session.query(SourceIngestion).filter(
            SourceIngestion.project_id == project_id,
            SourceIngestion.status == SourceIngestionStatus.RUNNING.value,
            SourceIngestion.deleted_at.is_(None),
        )
        if source_types:
            query = query.filter(SourceIngestion.source_type.in_(source_types))
        return query.order_by(SourceIngestion.created_at.desc()).all()

    def exists_running_for_tenant(self, tenant_id: UUID) -> bool:
        """Return True if any non-deleted project under *tenant_id* has a
        SourceIngestion currently in `running` status.

        Single EXISTS-style query (joined to Project on tenant_id) rather
        than listing every project and checking each one — used to gate
        tenant-wide actions (e.g. changing the tenant's LLM provider
        configuration) that would otherwise interfere with an in-flight
        generation pipeline.
        """
        exists = (
            self._session.query(SourceIngestion.id)
            .join(Project, Project.id == SourceIngestion.project_id)
            .filter(
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
                SourceIngestion.status == SourceIngestionStatus.RUNNING.value,
                SourceIngestion.deleted_at.is_(None),
            )
            .first()
        )
        return exists is not None

    def list_running(self) -> list[SourceIngestion]:
        """Return every non-deleted SourceIngestion currently `running`, across all projects.

        Used by the periodic stale-ingestion sweep
        (``SourceIngestionService.fail_stale_running_ingestions``) — unlike
        ``list_running_by_project``, this is not scoped to one project since
        the sweep must check every in-flight run.
        """
        return (
            self._session.query(SourceIngestion)
            .filter(
                SourceIngestion.status == SourceIngestionStatus.RUNNING.value,
                SourceIngestion.deleted_at.is_(None),
            )
            .all()
        )

    def list_open_feedback_or_incremental_by_project(self, project_id: UUID) -> list[SourceIngestion]:
        """Return feedback/incremental ingestions for *project_id* awaiting review completion.

        Feedback-driven regeneration always lands on a dedicated
        `requirement_update` row. A genuine incremental upload instead keeps
        the uploaded document's own `source_type` (rfp/meeting_notes/etc.)
        and is distinguished by `is_incremental=True` instead — both shapes
        reach `ready_for_review` once their AI pipeline finishes, and both
        are the set a reviewer's accept/reject decision may just have
        finished resolving.
        """
        return (
            self._session.query(SourceIngestion)
            .filter(
                SourceIngestion.project_id == project_id,
                or_(
                    SourceIngestion.source_type == SourceType.REQUIREMENT_UPDATE.value,
                    SourceIngestion.is_incremental.is_(True),
                ),
                SourceIngestion.status == SourceIngestionStatus.READY_FOR_REVIEW.value,
                SourceIngestion.deleted_at.is_(None),
            )
            .all()
        )

    def has_unresolved_feedback_or_incremental(self, project_id: UUID) -> bool:
        """Return True if any feedback/incremental ingestion for *project_id* isn't in a terminal status.

        Matches the same two shapes as `list_open_feedback_or_incremental_by_project`
        (a `requirement_update`-typed feedback-regeneration row, or any
        `is_incremental=True` upload row) rather than `requirement_update`
        alone.
        """
        terminal_statuses = (
            SourceIngestionStatus.COMPLETED.value,
            SourceIngestionStatus.FAILED.value,
            SourceIngestionStatus.CANCELLED.value,
        )
        exists = (
            self._session.query(SourceIngestion.id)
            .filter(
                SourceIngestion.project_id == project_id,
                or_(
                    SourceIngestion.source_type == SourceType.REQUIREMENT_UPDATE.value,
                    SourceIngestion.is_incremental.is_(True),
                ),
                SourceIngestion.deleted_at.is_(None),
                ~SourceIngestion.status.in_(terminal_statuses),
            )
            .first()
        )
        return exists is not None

    def get_ready_for_review_generation_ingestion(self, project_id: UUID) -> SourceIngestion | None:
        """Return the project's rfp/additional_rfp/source_code ingestion awaiting completion, or None.

        There is at most one such ingestion open for review at a time per
        project — this is the row a fully-approved backlog (with no
        unresolved feedback/incremental ingestion left) should complete.
        """
        generation_types = (
            SourceType.RFP.value, 
            SourceType.SOURCE_CODE.value,
        )
        return (
            self._session.query(SourceIngestion)
            .filter(
                SourceIngestion.project_id == project_id,
                SourceIngestion.source_type.in_(generation_types),
                SourceIngestion.status == SourceIngestionStatus.READY_FOR_REVIEW.value,
                SourceIngestion.deleted_at.is_(None),
            )
            .order_by(SourceIngestion.created_at.desc())
            .first()
        )

    def get_paginated(
        self,
        project_id: UUID,
        skip: int = 0,
        limit: int = 20,
        status: str | None = None,
        source_type: str | None = None,
    ) -> tuple[list[SourceIngestion], int]:
        """Return a paginated, non-deleted list of ingestions for a project."""
        query = self._session.query(SourceIngestion).filter(
            SourceIngestion.project_id == project_id,
            SourceIngestion.deleted_at.is_(None),
        )
        if status:
            query = query.filter(SourceIngestion.status == status)
        if source_type:
            query = query.filter(SourceIngestion.source_type == source_type)

        total = query.count()
        items = (
            query.options(joinedload(SourceIngestion.project))
            .order_by(SourceIngestion.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return items, total

    def list_by_owner_paginated(
        self,
        owner_id: UUID,
        *,
        skip: int,
        limit: int,
        status: str | None = None,
        source_type: str | None = None,
        search: str | None = None,
    ) -> tuple[list[SourceIngestion], int]:
        """Return owner-scoped ingestions ("pipelines") and total count with optional filters.

        Filters:
        - status: exact match on ingestion status
        - source_type: exact match on ingestion source_type
        - search: case-insensitive match on run_code or project name
        """
        query = (
            self._session.query(SourceIngestion)
            .join(Project, Project.id == SourceIngestion.project_id)
            .filter(
                Project.owner_id == owner_id,
                Project.deleted_at.is_(None),
                SourceIngestion.deleted_at.is_(None),
            )
        )

        if status is not None:
            query = query.filter(SourceIngestion.status == status)

        if source_type is not None:
            query = query.filter(SourceIngestion.source_type == source_type)

        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    SourceIngestion.run_code.ilike(pattern),
                    Project.name.ilike(pattern),
                )
            )

        total = query.with_entities(func.count(SourceIngestion.id)).scalar() or 0

        items = (
            query.options(joinedload(SourceIngestion.project))
            .order_by(SourceIngestion.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return items, total

    def get_aggregated_counts_by_project_ids(
        self, project_ids: list[UUID]
    ) -> dict[UUID, dict[str, int]]:
        """Return summed module/feature/user-story counts keyed by project_id.

        Executes a single GROUP BY aggregation so callers never issue N queries
        for N projects. Missing projects default to zeros.
        """
        if not project_ids:
            return {}

        rows = (
            self._session.query(
                SourceIngestion.project_id,
                func.coalesce(func.sum(SourceIngestion.tot_modules), 0).label("tot_modules"),
                func.coalesce(func.sum(SourceIngestion.tot_features), 0).label("tot_features"),
                func.coalesce(func.sum(SourceIngestion.tot_user_stories), 0).label(
                    "tot_user_stories"
                ),
            )
            .filter(
                SourceIngestion.project_id.in_(project_ids),
                SourceIngestion.deleted_at.is_(None),
            )
            .group_by(SourceIngestion.project_id)
            .all()
        )

        counts: dict[UUID, dict[str, int]] = {
            pid: {"tot_modules": 0, "tot_features": 0, "tot_user_stories": 0} for pid in project_ids
        }
        for row in rows:
            counts[row.project_id] = {
                "tot_modules": int(row.tot_modules),
                "tot_features": int(row.tot_features),
                "tot_user_stories": int(row.tot_user_stories),
            }
        return counts

    # ── Mutations ────────────────────────────────────────────────────────────

    def create_ingestion(
        self, *, project_id: UUID, source_type: str, **fields: object
    ) -> SourceIngestion:
        """Insert a new SourceIngestion row.

        *fields* may set any other SourceIngestion column at creation time —
        e.g. ``description``, ``source_language``, the technology-stack
        fields, ``is_incremental``, ``user_message``, ``skip_processing``,
        ``source_layout_type``. Callers may pass all of these unconditionally;
        whichever don't apply to *source_type* are silently dropped here (see
        ``_apply_source_type_field_rules``) so this is the single place that
        rule is enforced, regardless of which upload flow is calling in.

        The caller is responsible for committing the session.
        """
        next_val = self._session.execute(text("SELECT nextval('project_run_code_seq')")).scalar()
        ingestion = SourceIngestion(
            project_id=project_id,
            run_code=f"RUN-{next_val}",
            source_type=source_type,
            **_apply_source_type_field_rules(source_type, fields),
        )
        self._session.add(ingestion)
        self._session.flush()
        self._session.refresh(ingestion)
        return ingestion

    def update_fields(self, ingestion_id: UUID, **fields: object) -> SourceIngestion | None:
        """Patch arbitrary column values on a non-deleted ingestion row.

        ``cancelled`` is sticky — once a row's status is ``cancelled``, a call
        trying to move it to any other status is ignored entirely. Mirrors the
        same guard on ``Source.status`` (``_mark_status`` in
        ``app/workers/_task_helpers.py``) and ``ProjectTask.status``
        (``ProjectTaskRepository.update_status``): a worker that hasn't yet
        noticed a cancel (e.g. a Celery-retried delivery, or a late
        completion/failure callback) must not resurrect a cancelled run by
        overwriting it back to ``running``/``failed``. A call that re-confirms
        ``status="cancelled"`` (e.g. to merge extra fields alongside it) still
        goes through, as does any call that doesn't touch ``status`` at all.

        The read is taken with ``FOR UPDATE`` when the call could move the row
        off ``cancelled``, so a concurrent cancel commit can't land in the gap
        between this check and this write — without it, two callers (a late
        worker write and the cancel write) racing under READ COMMITTED could
        each read "running" before the other commits, and the later commit
        would silently resurrect a cancelled row regardless of which one's
        status check "won".

        The caller is responsible for committing the session.
        """
        new_status = fields.get("status")
        query = self._session.query(SourceIngestion).filter(
            SourceIngestion.id == ingestion_id,
            SourceIngestion.deleted_at.is_(None),
        )
        if new_status is not None and new_status != SourceIngestionStatus.CANCELLED.value:
            query = query.with_for_update()
        ingestion = query.first()
        if ingestion is None:
            return None
        if (
            ingestion.status == SourceIngestionStatus.CANCELLED.value
            and new_status is not None
            and new_status != SourceIngestionStatus.CANCELLED.value
        ):
            logger.debug(
                "update_fields: ignoring status %s -> %s for ingestion_id=%s (ingestion is cancelled)",
                ingestion.status,
                new_status,
                ingestion_id,
            )
            return ingestion
        for key, value in fields.items():
            setattr(ingestion, key, value)
        return ingestion

    _REVIEW_COUNT_COLUMNS = {
        ("module", True): SourceIngestion.tot_modules_accepted,
        ("module", False): SourceIngestion.tot_modules_rejected,
        ("feature", True): SourceIngestion.tot_features_accepted,
        ("feature", False): SourceIngestion.tot_features_rejected,
        ("user_story", True): SourceIngestion.tot_user_stories_accepted,
        ("user_story", False): SourceIngestion.tot_user_stories_rejected,
    }

    def increment_review_count(
        self, ingestion_id: UUID, *, entity_type: str, accepted: bool
    ) -> None:
        """Atomically bump the accepted/rejected counter for *entity_type* on one ingestion.

        A single ``UPDATE ... col = col + 1`` avoids a read-then-write race
        under concurrent accept/reject calls against the same ingestion.
        The caller is responsible for committing the session.
        """
        column = self._REVIEW_COUNT_COLUMNS[(entity_type, accepted)]
        self._session.execute(
            update(SourceIngestion)
            .where(SourceIngestion.id == ingestion_id)
            .values(**{column.key: column + 1})
        )

    def increment_module_completion_counts(
        self,
        ingestion_id: UUID,
        *,
        modules: int = 0,
        features: int = 0,
        user_stories: int = 0,
        modules_failed: int = 0,
    ) -> None:
        """Atomically bump module/feature/user-story counters as one module finishes.

        Called once per module in the source-code pipeline (success or
        failure), so a single ``UPDATE ... col = col + N`` per touched column
        avoids a read-then-write race against other modules finishing
        concurrently. Only non-zero deltas are included in the statement.
        The caller is responsible for committing the session.
        """
        deltas = {
            SourceIngestion.tot_modules: modules,
            SourceIngestion.tot_features: features,
            SourceIngestion.tot_user_stories: user_stories,
            SourceIngestion.tot_modules_failed: modules_failed,
        }
        values = {column.key: column + delta for column, delta in deltas.items() if delta}
        if not values:
            return
        self._session.execute(
            update(SourceIngestion).where(SourceIngestion.id == ingestion_id).values(**values)
        )

    def add_stage(self, ingestion_id: UUID, stage: str) -> SourceIngestion | None:
        """Add *stage* to an ingestion's ``stages`` array if not already present.

        The caller is responsible for committing the session.
        """
        ingestion = self.get_by_id(ingestion_id)
        if ingestion is None:
            return None
        if stage not in ingestion.stages:
            ingestion.stages = [*ingestion.stages, stage]
        return ingestion

    def add_error(self, ingestion_id: UUID, error: str) -> SourceIngestion | None:
        """Append *error* to an ingestion's ``errors`` array.

        Mirrors ``add_stage`` — appends rather than overwrites, since a
        source-code pipeline (many modules under one ingestion) can record
        more than one distinct failure across its run. Truncated to guard
        against an overlong stack trace ballooning the row. No-ops on a
        falsy *error*. The caller is responsible for committing the session.
        """
        if not error:
            return self.get_by_id(ingestion_id)
        ingestion = self.get_by_id(ingestion_id)
        if ingestion is None:
            return None
        ingestion.errors = [*ingestion.errors, error[:2000]]
        return ingestion

    def soft_delete(self, ingestion_id: UUID) -> SourceIngestion | None:
        """Mark a SourceIngestion as deleted by setting ``deleted_at``.

        The caller is responsible for committing the session.
        """
        ingestion = self.get_by_id(ingestion_id)
        if ingestion is None:
            return None
        ingestion.deleted_at = datetime.now(UTC)
        return ingestion
