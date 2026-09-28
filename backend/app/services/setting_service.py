"""Business logic for shared Setting payload backed by Neo4j."""

from __future__ import annotations

from app.db.neo4j import get_neo4j_driver
from app.repositories.neo4j.setting_repository import SettingRepository
from app.schemas.setting_schema import SettingResponse
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SettingService:
    """Fetches and seeds a shared Setting document."""

    def __init__(self, repository: SettingRepository | None = None) -> None:
        self._repository = repository or SettingRepository(get_neo4j_driver())

    def get_or_seed_default_settings(self) -> SettingResponse:
        """Return settings from DB, and seed once when missing."""
        settings = self._repository.get_setting()
        if settings is None:
            logger.info("Setting node missing in Neo4j; seeding default payload")
            settings = self._repository.seed_default_setting()
        return settings

    def seed_default_settings(self) -> SettingResponse:
        """Force seed/update default settings."""
        return self._repository.seed_default_setting()
