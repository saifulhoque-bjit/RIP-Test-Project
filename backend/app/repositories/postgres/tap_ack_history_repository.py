"""Postgres repository for TapAckHistory."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.postgres.tap_ack_history_model import TapAckHistory
from app.repositories.postgres.base_repository import BaseRepository


class TapAckHistoryRepository(BaseRepository[TapAckHistory]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, TapAckHistory)
