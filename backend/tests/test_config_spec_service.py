"""Unit tests for ConfigSpecService — ConfigSpec S3 upload + Neo4j upsert."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import StorageError
from app.services.config_spec_service import ConfigSpecService


def _make_service() -> tuple[ConfigSpecService, MagicMock]:
    repo = MagicMock()
    repo.upsert_many_for_project = AsyncMock(return_value=0)
    return ConfigSpecService(repository=repo), repo


class TestUpsertManyForProject:
    async def test_returns_zero_and_skips_work_when_rows_none(self):
        service, repo = _make_service()

        with patch(
            "app.services.config_spec_service.upload_to_s3", new_callable=AsyncMock
        ) as mock_upload:
            result = await service.upsert_many_for_project(project_id="proj-1", rows=None)

        assert result == 0
        mock_upload.assert_not_called()
        repo.upsert_many_for_project.assert_not_called()

    async def test_returns_zero_and_skips_work_when_rows_empty(self):
        service, repo = _make_service()

        with patch(
            "app.services.config_spec_service.upload_to_s3", new_callable=AsyncMock
        ) as mock_upload:
            result = await service.upsert_many_for_project(project_id="proj-1", rows=[])

        assert result == 0
        mock_upload.assert_not_called()
        repo.upsert_many_for_project.assert_not_called()

    async def test_uploads_each_row_to_s3_and_delegates_to_repository(self):
        service, repo = _make_service()
        repo.upsert_many_for_project.return_value = 2
        rows = [
            {
                "id": "row-1",
                "mod_code": "MOD1",
                "fea_code": "FEA1",
                "filename": "spec.json",
                "content": {"key": "value"},
            },
            {
                "id": "row-2",
                "mod_code": "MOD2",
                "fea_code": "FEA2",
                "filename": "spec2.json",
                "content": {"other": 1},
            },
        ]

        with patch(
            "app.services.config_spec_service.upload_to_s3", new_callable=AsyncMock
        ) as mock_upload:
            result = await service.upsert_many_for_project(project_id="proj-1", rows=rows)

        assert result == 2
        assert mock_upload.await_count == 2
        repo.upsert_many_for_project.assert_awaited_once()
        _, kwargs = repo.upsert_many_for_project.await_args
        assert kwargs["project_id"] == "proj-1"
        prepared_rows = kwargs["rows"]
        assert len(prepared_rows) == 2
        assert prepared_rows[0]["storage_key"] == (
            "projects/proj-1/modules/MOD1/stage4_specs/FEA1/spec.json"
        )
        assert prepared_rows[1]["storage_key"] == (
            "projects/proj-1/modules/MOD2/stage4_specs/FEA2/spec2.json"
        )

    async def test_skips_non_dict_rows(self):
        service, repo = _make_service()
        repo.upsert_many_for_project.return_value = 1
        rows = ["not-a-dict", {"id": "row-1", "content": {"a": 1}}]

        with patch(
            "app.services.config_spec_service.upload_to_s3", new_callable=AsyncMock
        ) as mock_upload:
            result = await service.upsert_many_for_project(project_id="proj-1", rows=rows)

        assert result == 1
        assert mock_upload.await_count == 1
        _, kwargs = repo.upsert_many_for_project.await_args
        assert len(kwargs["rows"]) == 1

    async def test_propagates_storage_error_from_s3_upload_failure(self):
        service, repo = _make_service()
        rows = [{"id": "row-1", "content": {"a": 1}}]

        with (
            patch(
                "app.services.config_spec_service.upload_to_s3",
                new_callable=AsyncMock,
                side_effect=StorageError("upload failed"),
            ),
            pytest.raises(StorageError),
        ):
            await service.upsert_many_for_project(project_id="proj-1", rows=rows)

        repo.upsert_many_for_project.assert_not_called()


class TestBuildStorageKey:
    def test_uses_fallback_segments_when_fields_missing(self):
        key = ConfigSpecService._build_storage_key(
            project_id="proj-1",
            module_id=None,
            feature_unit_id=None,
            file_name=None,
            row_id="row-42",
        )

        assert key == (
            "projects/proj-1/modules/unknown_module/stage4_specs/unknown_feature/row-42.json"
        )

    def test_appends_json_extension_when_missing(self):
        key = ConfigSpecService._build_storage_key(
            project_id="proj-1",
            module_id="MOD1",
            feature_unit_id="FEA1",
            file_name="spec",
            row_id="row-1",
        )

        assert key.endswith("spec.json")

    def test_sanitizes_path_segments_containing_slashes(self):
        key = ConfigSpecService._build_storage_key(
            project_id="proj-1",
            module_id="mod/1",
            feature_unit_id="fea/1",
            file_name="spec.json",
            row_id="row-1",
        )

        assert "modules/mod_1/" in key
        assert "/fea_1/" in key


class TestToJsonText:
    def test_dict_content_serialized_as_json(self):
        text = ConfigSpecService._to_json_text({"a": 1})

        assert json.loads(text) == {"a": 1}

    def test_valid_json_string_is_reformatted(self):
        text = ConfigSpecService._to_json_text('{"a": 1}')

        assert json.loads(text) == {"a": 1}

    def test_invalid_json_string_returned_as_is(self):
        text = ConfigSpecService._to_json_text("not json")

        assert text == "not json"
