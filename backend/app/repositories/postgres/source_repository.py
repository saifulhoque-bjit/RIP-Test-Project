"""Domain-specific repository for the Source model."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.constants import SOURCE_STATUS_CANCELLED, SOURCE_STATUS_FAILED
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.models.postgres.source_ingestion_model import SourceIngestion
from app.models.postgres.source_model import Source
from app.repositories.postgres.base_repository import BaseRepository


class SourceRepository(BaseRepository[Source]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Source)

    # ── Single-record lookups ──────────────────────────────────────────────

    def get_by_uuid(self, source_id: UUID) -> Source | None:
        """Return a non-deleted Source by its UUID, or None."""
        return (
            self._session.query(Source)
            .options(joinedload(Source.uploader))
            .filter(Source.id == source_id, Source.is_deleted.is_(False))
            .first()
        )

    def get_by_checksum(self, checksum: str, project_id: UUID) -> Source | None:
        """Return an existing non-deleted, still-active source with the same
        SHA-256 checksum within the same project (used for duplicate detection).

        A source whose own status, or whose linked SourceIngestion status, is
        cancelled/failed is excluded — those attempts never completed, so the
        same file content must be re-uploadable rather than rejected as a
        duplicate. Source and SourceIngestion status can drift independently
        (either can be terminal while the other isn't), so both are checked.
        """
        return (
            self._session.query(Source)
            .outerjoin(SourceIngestion, Source.source_ingestion_id == SourceIngestion.id)
            .filter(
                Source.checksum_sha256 == checksum,
                Source.project_id == project_id,
                Source.is_deleted.is_(False),
                Source.status.notin_([SOURCE_STATUS_CANCELLED, SOURCE_STATUS_FAILED]),
                or_(
                    SourceIngestion.id.is_(None),
                    SourceIngestion.status.notin_(
                        [SourceIngestionStatus.CANCELLED.value, SourceIngestionStatus.FAILED.value]
                    ),
                ),
            )
            .first()
        )

    def get_by_filename(self, filename: str, project_id: UUID) -> Source | None:
        """Return an existing non-deleted, still-active source with the same
        filename (case-insensitive) within the same project (used for
        duplicate-name detection alongside ``get_by_checksum``).

        Same terminal-status exclusion as ``get_by_checksum`` — a source (or
        its linked SourceIngestion) that ended cancelled/failed never
        completed, so the same filename must be re-uploadable.
        """
        return (
            self._session.query(Source)
            .outerjoin(SourceIngestion, Source.source_ingestion_id == SourceIngestion.id)
            .filter(
                func.lower(Source.original_name) == filename.lower(),
                Source.project_id == project_id,
                Source.is_deleted.is_(False),
                Source.status.notin_([SOURCE_STATUS_CANCELLED, SOURCE_STATUS_FAILED]),
                or_(
                    SourceIngestion.id.is_(None),
                    SourceIngestion.status.notin_(
                        [SourceIngestionStatus.CANCELLED.value, SourceIngestionStatus.FAILED.value]
                    ),
                ),
            )
            .first()
        )

    # ── Filtered / paginated listing ──────────────────────────────────────

    def get_paginated(  # type: ignore[override]
        self,
        project_id: UUID,
        skip: int = 0,
        limit: int = 20,
        status: str | None = None,
        file_type: str | None = None,
        upload_type: str | None = None,
    ) -> tuple[list[Source], int]:
        """Return a filtered, paginated list of sources for a project."""
        query = self._session.query(Source).filter(
            Source.project_id == project_id,
            Source.is_deleted.is_(False),
        )
        if status:
            query = query.filter(Source.status == status)
        if file_type:
            query = query.filter(Source.file_type == file_type.upper())
        if upload_type:
            query = query.filter(Source.upload_type == upload_type)

        total = query.count()
        items = (
            query.options(joinedload(Source.uploader))
            .order_by(Source.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return items, total

    def get_many_by_uuids(self, source_ids: list[UUID]) -> list[Source]:
        """Return non-deleted sources whose IDs are in *source_ids*."""
        return (
            self._session.query(Source)
            .options(joinedload(Source.uploader))
            .filter(
                Source.id.in_(source_ids),
                Source.is_deleted.is_(False),
            )
            .all()
        )

    def get_by_ingestion_ids(self, ingestion_ids: list[UUID]) -> list[Source]:
        """Return non-deleted sources linked to any ingestion in *ingestion_ids*."""
        if not ingestion_ids:
            return []

        return (
            self._session.query(Source)
            .options(joinedload(Source.uploader))
            .filter(
                Source.source_ingestion_id.in_(ingestion_ids),
                Source.is_deleted.is_(False),
            )
            .order_by(Source.created_at.desc())
            .all()
        )

    def get_ids_by_project(self, project_id: UUID) -> list[UUID]:
        """Return IDs of non-deleted, non-cancelled, non-failed sources for a project.

        Cancelled/failed sources belong to an aborted or unsuccessful
        upload/pipeline run and must never feed later generation phases or
        have their (already terminal) SourceIngestion resurrected to
        "running" by a later, unrelated run.
        """
        rows = (
            self._session.query(Source.id)
            .filter(
                Source.project_id == project_id,
                Source.is_deleted.is_(False),
                Source.status.notin_([SOURCE_STATUS_CANCELLED, SOURCE_STATUS_FAILED]),
            )
            .all()
        )
        return [row.id for row in rows]

    def exists_by_project_and_source_type(self, project_id: UUID, source_type: str) -> bool:
        """Return True if the project has any active source of *source_type*.

        Non-deleted, non-cancelled, non-failed only. Used to gate
        incremental-update uploads on an initial RFP source already existing
        for the project.
        """
        return (
            self._session.query(Source.id)
            .filter(
                Source.project_id == project_id,
                Source.source_type == source_type,
                Source.is_deleted.is_(False),
                Source.status.notin_([SOURCE_STATUS_CANCELLED, SOURCE_STATUS_FAILED]),
            )
            .first()
            is not None
        )

    def delete_by_project_id(self, project_id: UUID) -> int:
        """Hard-delete all source rows for a project (including soft-deleted)."""
        stmt = delete(Source).where(Source.project_id == project_id)
        result = self._session.execute(stmt)
        return result.rowcount

    def get_storage_keys_by_project(self, project_id: UUID) -> list[str]:
        """Return every non-null S3 ``storage_key`` for a project's sources.

        Includes already soft-deleted rows — a straggler whose per-source S3
        cleanup previously failed still has its row (and storage_key) intact
        until the project itself is deleted, so this is the last chance to
        sweep it up. Called by project deletion before the rows are
        hard-deleted, since ``delete_by_project_id`` removes the only
        pointer to each object.
        """
        rows = (
            self._session.query(Source.storage_key)
            .filter(
                Source.project_id == project_id,
                Source.storage_key.is_not(None),
            )
            .all()
        )
        return [row.storage_key for row in rows]

    # ── Write ─────────────────────────────────────────────────────────────

    def create(self, source: Source) -> Source:
        """Stage, flush, and refresh *source* so DB-assigned fields
        (``id``, ``created_at``, ``updated_at``) are available immediately.

        Does **not** commit — the caller owns the transaction boundary.
        """
        self._session.add(source)
        self._session.flush()
        self._session.refresh(source)
        return source

    # ── Aggregation ───────────────────────────────────────────────────────

    def get_source_counts_by_project(self, project_ids: list[UUID]) -> dict[UUID, int]:
        """Return active (non-deleted, non-cancelled, non-failed) source-file
        counts keyed by project id.

        Executes a single GROUP BY query regardless of how many project ids are
        passed.  Projects with zero active sources are included with a count of 0.
        """
        if not project_ids:
            return {}

        rows = (
            self._session.query(Source.project_id, func.count(Source.id))
            .filter(
                Source.project_id.in_(project_ids),
                Source.is_deleted.is_(False),
                Source.status.notin_([SOURCE_STATUS_CANCELLED, SOURCE_STATUS_FAILED]),
            )
            .group_by(Source.project_id)
            .all()
        )

        counts: dict[UUID, int] = dict.fromkeys(project_ids, 0)
        for project_id, total in rows:
            counts[project_id] = int(total or 0)
        return counts
