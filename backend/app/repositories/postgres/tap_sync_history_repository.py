"""Postgres repository for TapSyncHistory."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.postgres.tap_sync_history_model import TapSyncHistory
from app.repositories.postgres.base_repository import BaseRepository

# A run in this state has been staged and announced to TAP, but TAP has not
# finished with it yet — starting a second one would steal its mappings.
SYNC_STATUS_PENDING_PULL = "pending_pull"


class TapSyncHistoryRepository(BaseRepository[TapSyncHistory]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, TapSyncHistory)

    def get_pending_by_project_id(
        self, project_id: UUID, *, since: datetime | None = None
    ) -> TapSyncHistory | None:
        """Return the project's most recent in-flight sync run, if any.

        Used to reject a second concurrent sync: every mapping carries a
        single ``last_push_sync_id``, so a second run silently reassigns them
        all and the first run's ack then matches nothing.

        *since* bounds how far back a run still counts as in-flight. A run
        only leaves ``pending_pull`` when TAP acks it, so without a bound a
        single dropped ack would lock the project out of syncing forever —
        the caller passes a cutoff so an abandoned run is eventually ignored.
        """
        query = self._session.query(TapSyncHistory).filter(
            TapSyncHistory.project_id == project_id,
            TapSyncHistory.status == SYNC_STATUS_PENDING_PULL,
        )
        if since is not None:
            query = query.filter(TapSyncHistory.created_at >= since)
        return query.order_by(TapSyncHistory.created_at.desc()).first()
