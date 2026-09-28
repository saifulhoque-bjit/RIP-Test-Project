"""Unit tests for app/services/export_service.py.

Covers ``ExportService.build_export_zip`` (success paths + the sole
``NotFoundError`` branch) and the pure helper functions
(``_strip_redundant_code_prefix``, ``_filter_approved``) that back it.

Neo4j repositories (``UserStoryRepository``, ``GroupSpecRepository``,
``ProjectMetadataRepository``) and the S3 streaming client
(``iter_s3_chunks``) are mocked — no real Neo4j/S3 call is made.
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock, patch
import zipfile

import pytest

from app.core.exceptions import NotFoundError
from app.models.neo4j.group_spec_model import GroupSpecModel
from app.schemas.export_schema import ExportRequest
from app.services.export_service import ExportService, _strip_redundant_code_prefix
from tests.conftest import make_project

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_modules_tree(
    *, story_one_status: str = "ready", story_two_status: str = "needs_edit"
) -> list[dict]:
    return [
        {
            "mod_code": "MOD-1",
            "name": "MOD-1 - Module One",
            "description": "module description",
            "children": [
                {
                    "fea_code": "FEA-1",
                    "name": "FEA-1 - Feature One",
                    "description": "feature description",
                    "children": [
                        {"id": "us-1", "status": story_one_status, "title": "Story 1"},
                        {"id": "us-2", "status": story_two_status, "title": "Story 2"},
                    ],
                }
            ],
        }
    ]


def _make_group_spec(
    *, mod_code: str = "MOD-1", fea_code: str = "FEA-1", storage_key: str | None = "srs/key1.md"
) -> GroupSpecModel:
    return GroupSpecModel(
        id="gs-1",
        mod_code=mod_code,
        fea_code=fea_code,
        filename="spec.md",
        storage_key=storage_key,
        source_id=None,
        project_id=None,
    )


def _fake_iter_s3_chunks(chunks_by_key: dict[str, list[bytes]], raise_for: set[str] | None = None):
    """Build a stand-in async-generator function for ``iter_s3_chunks``."""

    async def _fake(object_key: str):
        if raise_for and object_key in raise_for:
            raise RuntimeError("simulated S3 failure")
        for chunk in chunks_by_key.get(object_key, [b"file content"]):
            yield chunk

    return _fake


def _make_metadata(
    *,
    domain_knowledge_storage_key: str | None = "pipeline/domain_knowledge.md",
    architecture_document_storage_key: str | None = "pipeline/architecture_document.md",
) -> dict:
    """Mirror the dict shape returned by ``ProjectMetadataRepository.get``."""
    return {
        "business_requirements": [],
        "exclusions": [],
        "persona_glossary": [],
        "domain_knowledge_storage_key": domain_knowledge_storage_key,
        "architecture_document_storage_key": architecture_document_storage_key,
    }


def _make_service(
    *,
    modules: list[dict] | None = None,
    group_specs: list[GroupSpecModel] | None = None,
    metadata: dict | None = None,
) -> ExportService:
    service = ExportService(
        user_story_repo=AsyncMock(),
        group_spec_repo=AsyncMock(),
        # ``ProjectMetadataRepository.get`` is sync (the service offloads it via
        # asyncio.to_thread), so this must be a MagicMock, not an AsyncMock.
        project_metadata_repo=MagicMock(),
    )
    service._user_story_repo.list_full_backlog_tree_for_project = AsyncMock(
        return_value=modules if modules is not None else _make_modules_tree()
    )
    service._group_spec_repo.list_by_project = AsyncMock(
        return_value=group_specs if group_specs is not None else [_make_group_spec()]
    )
    service._project_metadata_repo.get = MagicMock(
        return_value=metadata if metadata is not None else _make_metadata()
    )
    return service


def _read_manifest(zip_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return json.loads(zf.read("export_manifest.json"))


# ── build_export_zip ────────────────────────────────────────────────────────


class TestBuildExportZip:
    @pytest.mark.asyncio
    async def test_project_not_found_raises(self, uow) -> None:
        uow.projects.get_by_uuid.return_value = None
        service = _make_service()
        request = ExportRequest()
        project_id = make_project().id

        with pytest.raises(NotFoundError):
            await service.build_export_zip(
                project_id=project_id,
                user_email="a@example.com",
                request=request,
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_backlog_json_and_srs_success(self, uow) -> None:
        project = make_project(name="Proj X")
        uow.projects.get_by_uuid.return_value = project
        service = _make_service()
        request = ExportRequest(
            status_filter="all", include_backlog=True, backlog_format="json", include_srs=True
        )

        with patch(
            "app.services.export_service.iter_s3_chunks",
            _fake_iter_s3_chunks({"srs/key1.md": [b"hello "]}),
        ):
            zip_bytes, project_name = await service.build_export_zip(
                project_id=project.id, user_email="tester@example.com", request=request, uow=uow
            )

        assert project_name == "Proj X"
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert "requirements_backlog.json" in names
            assert "export_manifest.json" in names
            srs_names = [n for n in names if n.startswith("srs/")]
            assert len(srs_names) == 1
            assert zf.read(srs_names[0]) == b"hello "

            backlog = json.loads(zf.read("requirements_backlog.json"))
            assert backlog[0]["mod_code"] == "MOD-1"

        manifest = _read_manifest(zip_bytes)
        assert manifest["project_id"] == str(project.id)
        assert manifest["project_name"] == "Proj X"
        assert manifest["exported_by"] == "tester@example.com"
        assert manifest["status_filter"] == "all"
        assert manifest["included"] == ["backlog", "srs"]
        assert manifest["backlog_item_count"] == 2
        assert manifest["srs_file_count"] == 1

    @pytest.mark.asyncio
    async def test_backlog_only_when_srs_not_requested(self, uow) -> None:
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service()
        request = ExportRequest(include_backlog=True, include_srs=False)

        zip_bytes, _ = await service.build_export_zip(
            project_id=project.id, user_email=None, request=request, uow=uow
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert "requirements_backlog.json" in names
            assert not any(n.startswith("srs/") for n in names)
        # group_spec_repo must never be queried when SRS isn't requested.
        service._group_spec_repo.list_by_project.assert_not_called()
        manifest = _read_manifest(zip_bytes)
        assert manifest["included"] == ["backlog"]
        assert manifest["srs_file_count"] == 0

    @pytest.mark.asyncio
    async def test_srs_only_when_backlog_not_requested(self, uow) -> None:
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service()
        request = ExportRequest(include_backlog=False, include_srs=True)

        with patch(
            "app.services.export_service.iter_s3_chunks",
            _fake_iter_s3_chunks({"srs/key1.md": [b"content"]}),
        ):
            zip_bytes, _ = await service.build_export_zip(
                project_id=project.id, user_email=None, request=request, uow=uow
            )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert "requirements_backlog.json" not in names
            assert "requirements_backlog.pdf" not in names
            assert any(n.startswith("srs/") for n in names)
        service._user_story_repo.list_full_backlog_tree_for_project.assert_not_called()
        manifest = _read_manifest(zip_bytes)
        assert manifest["included"] == ["srs"]
        assert manifest["backlog_item_count"] == 0

    @pytest.mark.asyncio
    async def test_backlog_pdf_format_renders_pdf_bytes(self, uow) -> None:
        project = make_project(name="Proj PDF")
        uow.projects.get_by_uuid.return_value = project
        service = _make_service()
        request = ExportRequest(include_backlog=True, backlog_format="pdf", include_srs=False)

        zip_bytes, _ = await service.build_export_zip(
            project_id=project.id, user_email="a@b.com", request=request, uow=uow
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert "requirements_backlog.pdf" in names
            assert "requirements_backlog.json" not in names
            pdf_bytes = zf.read("requirements_backlog.pdf")
            assert pdf_bytes.startswith(b"%PDF")

    @pytest.mark.asyncio
    async def test_status_filter_approved_drops_non_approved_stories(self, uow) -> None:
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        modules = _make_modules_tree(story_one_status="approved", story_two_status="needs_edit")
        service = _make_service(modules=modules)
        request = ExportRequest(status_filter="approved", include_backlog=True, include_srs=False)

        zip_bytes, _ = await service.build_export_zip(
            project_id=project.id, user_email=None, request=request, uow=uow
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            backlog = json.loads(zf.read("requirements_backlog.json"))
        stories = backlog[0]["children"][0]["children"]
        assert len(stories) == 1
        assert stories[0]["id"] == "us-1"
        manifest = _read_manifest(zip_bytes)
        assert manifest["backlog_item_count"] == 1

    @pytest.mark.asyncio
    async def test_status_filter_approved_drops_empty_modules_and_features(self, uow) -> None:
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        # Neither story is approved -> feature and module both drop out entirely.
        modules = _make_modules_tree(story_one_status="needs_edit", story_two_status="failed")
        service = _make_service(modules=modules)
        request = ExportRequest(status_filter="approved", include_backlog=True, include_srs=False)

        zip_bytes, _ = await service.build_export_zip(
            project_id=project.id, user_email=None, request=request, uow=uow
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            backlog = json.loads(zf.read("requirements_backlog.json"))
        assert backlog == []
        manifest = _read_manifest(zip_bytes)
        assert manifest["backlog_item_count"] == 0

    @pytest.mark.asyncio
    async def test_srs_file_missing_storage_key_is_skipped(self, uow) -> None:
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service(group_specs=[_make_group_spec(storage_key=None)])
        request = ExportRequest(include_backlog=False, include_srs=True)

        zip_bytes, _ = await service.build_export_zip(
            project_id=project.id, user_email=None, request=request, uow=uow
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert not any(n.startswith("srs/") for n in zf.namelist())
        manifest = _read_manifest(zip_bytes)
        assert manifest["srs_file_count"] == 0

    @pytest.mark.asyncio
    async def test_srs_file_s3_error_is_skipped_and_others_continue(self, uow) -> None:
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        specs = [
            _make_group_spec(mod_code="MOD-1", fea_code="FEA-1", storage_key="broken.md"),
            _make_group_spec(mod_code="MOD-1", fea_code="FEA-2", storage_key="ok.md"),
        ]
        service = _make_service(group_specs=specs)
        request = ExportRequest(include_backlog=False, include_srs=True)

        with patch(
            "app.services.export_service.iter_s3_chunks",
            _fake_iter_s3_chunks({"ok.md": [b"good"]}, raise_for={"broken.md"}),
        ):
            zip_bytes, _ = await service.build_export_zip(
                project_id=project.id, user_email=None, request=request, uow=uow
            )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            srs_names = [n for n in zf.namelist() if n.startswith("srs/")]
            assert len(srs_names) == 1
            assert zf.read(srs_names[0]) == b"good"
        manifest = _read_manifest(zip_bytes)
        assert manifest["srs_file_count"] == 1


# ── Pipeline documents (domain knowledge / architecture document) ───────────


class TestPipelineDocuments:
    @pytest.mark.asyncio
    async def test_both_documents_included_at_zip_root(self, uow) -> None:
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service()
        request = ExportRequest(
            include_backlog=False,
            include_srs=False,
            include_domain_knowledge=True,
            include_architecture_document=True,
        )

        with patch(
            "app.services.export_service.iter_s3_chunks",
            _fake_iter_s3_chunks(
                {
                    "pipeline/domain_knowledge.md": [b"# Domain", b" Knowledge"],
                    "pipeline/architecture_document.md": [b"# Architecture"],
                }
            ),
        ):
            zip_bytes, _ = await service.build_export_zip(
                project_id=project.id, user_email=None, request=request, uow=uow
            )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert zf.read("domain_knowledge.md") == b"# Domain Knowledge"
            assert zf.read("architecture_document.md") == b"# Architecture"
        manifest = _read_manifest(zip_bytes)
        assert manifest["included"] == ["domain_knowledge", "architecture_document"]
        # A single metadata read serves both documents.
        service._project_metadata_repo.get.assert_called_once_with(project_id=project.id)

    @pytest.mark.asyncio
    async def test_only_requested_document_is_included(self, uow) -> None:
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service()
        request = ExportRequest(
            include_backlog=False, include_srs=False, include_domain_knowledge=True
        )

        with patch(
            "app.services.export_service.iter_s3_chunks",
            _fake_iter_s3_chunks({"pipeline/domain_knowledge.md": [b"content"]}),
        ):
            zip_bytes, _ = await service.build_export_zip(
                project_id=project.id, user_email=None, request=request, uow=uow
            )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert "domain_knowledge.md" in names
            assert "architecture_document.md" not in names
        assert _read_manifest(zip_bytes)["included"] == ["domain_knowledge"]

    @pytest.mark.asyncio
    async def test_missing_storage_key_is_skipped(self, uow) -> None:
        """A project whose pipeline never produced the documents still exports."""
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service(
            metadata=_make_metadata(
                domain_knowledge_storage_key=None,
                architecture_document_storage_key=None,
            )
        )
        request = ExportRequest(
            include_backlog=False,
            include_srs=False,
            include_domain_knowledge=True,
            include_architecture_document=True,
        )

        zip_bytes, _ = await service.build_export_zip(
            project_id=project.id, user_email=None, request=request, uow=uow
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert zf.namelist() == ["export_manifest.json"]
        assert _read_manifest(zip_bytes)["included"] == []

    @pytest.mark.asyncio
    async def test_s3_error_is_skipped_and_other_document_continues(self, uow) -> None:
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service()
        request = ExportRequest(
            include_backlog=False,
            include_srs=False,
            include_domain_knowledge=True,
            include_architecture_document=True,
        )

        with patch(
            "app.services.export_service.iter_s3_chunks",
            _fake_iter_s3_chunks(
                {"pipeline/architecture_document.md": [b"ok"]},
                raise_for={"pipeline/domain_knowledge.md"},
            ),
        ):
            zip_bytes, _ = await service.build_export_zip(
                project_id=project.id, user_email=None, request=request, uow=uow
            )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert "domain_knowledge.md" not in names
            assert zf.read("architecture_document.md") == b"ok"
        # The failed document must not be advertised in the manifest.
        assert _read_manifest(zip_bytes)["included"] == ["architecture_document"]

    @pytest.mark.asyncio
    async def test_metadata_not_read_when_no_document_requested(self, uow) -> None:
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        service = _make_service()
        request = ExportRequest(include_backlog=True, include_srs=False)

        await service.build_export_zip(
            project_id=project.id, user_email=None, request=request, uow=uow
        )

        service._project_metadata_repo.get.assert_not_called()


# ── _strip_redundant_code_prefix ────────────────────────────────────────────


class TestStripRedundantCodePrefix:
    def test_strips_matching_prefix(self) -> None:
        assert _strip_redundant_code_prefix("MOD-1 - Module One", "MOD-1") == "Module One"

    def test_tries_multiple_codes_in_order(self) -> None:
        # First code doesn't match, second does.
        assert _strip_redundant_code_prefix("FEA-1: Feature One", "MOD-1", "FEA-1") == "Feature One"

    def test_no_match_returns_original_name(self) -> None:
        assert _strip_redundant_code_prefix("Just a name", "MOD-1") == "Just a name"

    def test_none_name_returns_empty_string(self) -> None:
        assert _strip_redundant_code_prefix(None, "MOD-1") == ""

    def test_no_codes_returns_original_name(self) -> None:
        assert _strip_redundant_code_prefix("MOD-1 - Module One") == "MOD-1 - Module One"

    def test_matched_prefix_with_empty_remainder_returns_original(self) -> None:
        # Nothing left after stripping the code+separator -> falls back to name.
        assert _strip_redundant_code_prefix("MOD-1 - ", "MOD-1") == "MOD-1 - "

    def test_falsy_code_in_codes_list_is_skipped(self) -> None:
        # A None/empty code (e.g. a feature with no parent mod_code) must not
        # raise and must be skipped in favor of a later, valid code.
        assert _strip_redundant_code_prefix("FEA-1 - Feature One", None, "FEA-1") == "Feature One"
