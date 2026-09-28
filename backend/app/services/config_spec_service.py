"""Business logic for ConfigSpec graph workflows."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from app.clients.s3_client import upload_to_s3
from app.repositories.neo4j.config_spec_repository import ConfigSpecRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ConfigSpecService:
    """Validates and persists ConfigSpec rows for project-scoped source processing."""

    def __init__(self, repository: ConfigSpecRepository | None = None) -> None:
        self._repository = repository or ConfigSpecRepository()

    async def upsert_many_for_project(
        self,
        *,
        project_id: str,
        rows: list[dict] | None,
    ) -> int:
        if not rows:
            return 0

        prepared_rows = await self._upload_specs_to_s3_and_enrich_rows(
            project_id=project_id,
            rows=rows,
        )

        return await self._repository.upsert_many_for_project(
            project_id=project_id,
            rows=prepared_rows,
        )

    async def _upload_specs_to_s3_and_enrich_rows(
        self,
        *,
        project_id: str,
        rows: list[dict],
    ) -> list[dict[str, Any]]:
        prepared_rows: list[dict[str, Any]] = []

        for row in rows:
            if not isinstance(row, dict):
                continue

            prepared_row = dict(row)
            storage_key = self._build_storage_key(
                project_id=project_id,
                module_id=prepared_row.get("mod_code"),
                feature_unit_id=prepared_row.get("fea_code"),
                file_name=prepared_row.get("filename"),
                row_id=prepared_row.get("id"),
            )
            json_content = self._to_json_text(prepared_row.get("content"))

            await upload_to_s3(
                file_bytes=json_content.encode("utf-8"),
                object_key=storage_key,
                content_type="application/json; charset=utf-8",
            )

            prepared_row["storage_key"] = storage_key
            prepared_rows.append(prepared_row)

        logger.info(
            "Uploaded ConfigSpec json files to S3 for project_id=%s count=%d",
            project_id,
            len(prepared_rows),
        )
        return prepared_rows

    @staticmethod
    def _sanitize_path_segment(value: Any, fallback: str) -> str:
        raw = str(value or "").strip().replace("\\", "/").strip("/")
        if not raw:
            return fallback
        return raw.replace("/", "_")

    @staticmethod
    def _normalize_file_name(file_name: Any, fallback: str) -> str:
        normalized = PurePosixPath(str(file_name or "").strip()).name
        if not normalized:
            normalized = fallback
        if not normalized.lower().endswith(".json"):
            normalized = f"{normalized}.json"
        return normalized

    @classmethod
    def _build_storage_key(
        cls,
        *,
        project_id: str,
        module_id: Any,
        feature_unit_id: Any,
        file_name: Any,
        row_id: Any,
    ) -> str:
        safe_module_id = cls._sanitize_path_segment(module_id, "unknown_module")
        safe_feature_unit_id = cls._sanitize_path_segment(feature_unit_id, "unknown_feature")
        fallback_file_name = f"{str(row_id or 'config_spec')}.json"
        safe_file_name = cls._normalize_file_name(file_name, fallback_file_name)

        return (
            f"projects/{project_id}/modules/{safe_module_id}/stage4_specs/"
            f"{safe_feature_unit_id}/{safe_file_name}"
        )

    @staticmethod
    def _to_json_text(content: Any) -> str:
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return content

            return json.dumps(parsed, ensure_ascii=False, default=str, indent=2)

        return json.dumps(content, ensure_ascii=False, default=str, indent=2)
