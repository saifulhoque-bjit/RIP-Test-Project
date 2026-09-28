"""Unit tests for GroupSpecService — GroupSpec S3 upload + Neo4j upsert."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import StorageError
from app.services.group_spec_service import GroupSpecService


def _make_service() -> tuple[GroupSpecService, MagicMock]:
    repo = MagicMock()
    repo.upsert_many_for_project = AsyncMock(return_value=0)
    return GroupSpecService(repository=repo), repo


class TestUpsertManyForProject:
    async def test_returns_zero_and_skips_work_when_rows_none(self):
        service, repo = _make_service()

        with patch(
            "app.services.group_spec_service.upload_to_s3", new_callable=AsyncMock
        ) as mock_upload:
            result = await service.upsert_many_for_project(project_id="proj-1", rows=None)

        assert result == 0
        mock_upload.assert_not_called()
        repo.upsert_many_for_project.assert_not_called()

    async def test_returns_zero_and_skips_work_when_rows_empty(self):
        service, repo = _make_service()

        with patch(
            "app.services.group_spec_service.upload_to_s3", new_callable=AsyncMock
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
                "filename": "spec.md",
                "content": "# Heading",
            },
            {
                "id": "row-2",
                "mod_code": "MOD2",
                "fea_code": "FEA2",
                "filename": "spec2.md",
                "content": {"other": 1},
            },
        ]

        with patch(
            "app.services.group_spec_service.upload_to_s3", new_callable=AsyncMock
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
            "projects/proj-1/modules/MOD1/stage4_specs/FEA1/spec.md"
        )
        assert prepared_rows[1]["storage_key"] == (
            "projects/proj-1/modules/MOD2/stage4_specs/FEA2/spec2.md"
        )

    async def test_skips_non_dict_rows(self):
        service, repo = _make_service()
        repo.upsert_many_for_project.return_value = 1
        rows = ["not-a-dict", {"id": "row-1", "content": "text"}]

        with patch(
            "app.services.group_spec_service.upload_to_s3", new_callable=AsyncMock
        ) as mock_upload:
            result = await service.upsert_many_for_project(project_id="proj-1", rows=rows)

        assert result == 1
        assert mock_upload.await_count == 1
        _, kwargs = repo.upsert_many_for_project.await_args
        assert len(kwargs["rows"]) == 1

    async def test_propagates_storage_error_from_s3_upload_failure(self):
        service, repo = _make_service()
        rows = [{"id": "row-1", "content": "text"}]

        with (
            patch(
                "app.services.group_spec_service.upload_to_s3",
                new_callable=AsyncMock,
                side_effect=StorageError("upload failed"),
            ),
            pytest.raises(StorageError),
        ):
            await service.upsert_many_for_project(project_id="proj-1", rows=rows)

        repo.upsert_many_for_project.assert_not_called()


class TestBuildStorageKey:
    def test_uses_fallback_segments_when_fields_missing(self):
        key = GroupSpecService._build_storage_key(
            project_id="proj-1",
            module_id=None,
            feature_unit_id=None,
            file_name=None,
            row_id="row-42",
        )

        assert key == (
            "projects/proj-1/modules/unknown_module/stage4_specs/unknown_feature/row-42.md"
        )

    def test_appends_md_extension_when_missing(self):
        key = GroupSpecService._build_storage_key(
            project_id="proj-1",
            module_id="MOD1",
            feature_unit_id="FEA1",
            file_name="spec",
            row_id="row-1",
        )

        assert key.endswith("spec.md")

    def test_sanitizes_path_segments_containing_slashes(self):
        key = GroupSpecService._build_storage_key(
            project_id="proj-1",
            module_id="mod/1",
            feature_unit_id="fea/1",
            file_name="spec.md",
            row_id="row-1",
        )

        assert "modules/mod_1/" in key
        assert "/fea_1/" in key


class TestToMarkdownText:
    def test_dict_content_serialized_as_json(self):
        text = GroupSpecService._to_markdown_text({"a": 1})

        assert text == '{\n  "a": 1\n}'

    def test_list_content_serialized_as_json(self):
        text = GroupSpecService._to_markdown_text([1, 2, 3])

        assert text == "[\n  1,\n  2,\n  3\n]"

    def test_valid_json_string_wrapping_a_string_is_unwrapped(self):
        text = GroupSpecService._to_markdown_text('"# Heading"')

        assert text == "# Heading"

    def test_valid_json_string_wrapping_a_dict_is_reformatted(self):
        text = GroupSpecService._to_markdown_text('{"a": 1}')

        assert text == '{\n  "a": 1\n}'

    def test_invalid_json_string_returned_as_is(self):
        text = GroupSpecService._to_markdown_text("# Heading not json")

        assert text == "# Heading not json"

    def test_none_content_returns_empty_string(self):
        text = GroupSpecService._to_markdown_text(None)

        assert text == ""

    def test_non_string_non_collection_content_stringified(self):
        text = GroupSpecService._to_markdown_text(42)

        assert text == "42"
