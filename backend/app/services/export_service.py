"""Service for building project export ZIP bundles.

Several content types can be requested, independently or together:

- **Requirements backlog** — generated fresh (JSON or PDF) from
  ``UserStoryRepository.list_full_backlog_tree_for_project``, filtered by
  approval status.
- **SRS specifications** — never generated here. They already exist as
  per-module/feature Markdown files uploaded to S3 by ``GroupSpecService``
  during the source-code pipeline; this only locates and re-bundles them.
- **Domain knowledge** / **architecture document** — likewise never generated
  here. These are the two per-project Markdown documents the source-code
  pipeline writes to S3, pointed at by ``domain_knowledge_storage_key`` /
  ``architecture_document_storage_key`` on the project's ``:ProjectMetadata``
  node.

The result is always a single ZIP (even for one content type) containing an
``export_manifest.json`` alongside whatever was selected, returned as raw
bytes for the route to stream straight back to the client — nothing is
persisted server-side.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import io
import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID
import zipfile

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from app.clients.s3_client import iter_s3_chunks
from app.core.exceptions import NotFoundError
from app.core.messages import MSG_PROJECT_NOT_FOUND
from app.db.neo4j import get_neo4j_driver
from app.db.unit_of_work import UnitOfWork
from app.repositories.neo4j.group_spec_repository import GroupSpecRepository
from app.repositories.neo4j.project_metadata_repository import ProjectMetadataRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.export_schema import ExportRequest
from app.schemas.user_story_schema import UserStoryStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "export"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)

# Some module/feature names are stored with their own (or a parent's) code
# already baked into the text, e.g. feature.name == "MOD-FOO - Do the thing".
# The PDF export also prefixes the code as a label ("Epic FEA-1: ..."), so an
# unmodified name would show the code twice. This strips that leading
# "<code> <sep> " prefix for display only — the stored name is untouched.
_NAME_CODE_PREFIX_SEP = r"\s*[-:–—]\s*"


def _strip_redundant_code_prefix(name: str | None, *codes: str | None) -> str:
    if not name:
        return name or ""
    for code in codes:
        if not code:
            continue
        match = re.match(rf"^{re.escape(code)}{_NAME_CODE_PREFIX_SEP}", name, flags=re.IGNORECASE)
        if match:
            remainder = name[match.end() :].strip()
            if remainder:
                return remainder
    return name


class ExportService:
    """Assembles a project's requirements backlog / SRS specs / pipeline docs into one ZIP."""

    def __init__(
        self,
        user_story_repo: UserStoryRepository | None = None,
        group_spec_repo: GroupSpecRepository | None = None,
        project_metadata_repo: ProjectMetadataRepository | None = None,
    ) -> None:
        self._user_story_repo = user_story_repo or UserStoryRepository()
        self._group_spec_repo = group_spec_repo or GroupSpecRepository()
        self._project_metadata_repo = project_metadata_repo

    def _metadata_repo(self) -> ProjectMetadataRepository:
        """Return the metadata repository, building it on first use if not injected.

        Unlike the other two repositories, this one's constructor requires a
        driver — so it is created lazily rather than in ``__init__``, keeping
        construction of an ``ExportService`` free of any Neo4j connection when
        no pipeline document was requested.
        """
        if self._project_metadata_repo is None:
            self._project_metadata_repo = ProjectMetadataRepository(get_neo4j_driver())
        return self._project_metadata_repo

    async def build_export_zip(
        self,
        *,
        project_id: UUID,
        user_email: str | None,
        request: ExportRequest,
        uow: UnitOfWork,
    ) -> tuple[bytes, str]:
        """Build the export ZIP and return ``(zip_bytes, project_name)``.

        *project_name* is returned alongside the bytes so the route can build
        the ``Content-Disposition`` filename without a second project lookup.

        Raises:
            NotFoundError: if *project_id* does not exist.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        project_name = project.name
        exported_at = datetime.now(UTC)

        included: list[str] = []
        backlog_item_count = 0
        srs_file_count = 0

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            if request.include_backlog:
                modules, backlog_item_count = await self._build_backlog(
                    project_id=project_id,
                    status_filter=request.status_filter,
                )
                if request.backlog_format == "json":
                    zf.writestr(
                        "requirements_backlog.json",
                        json.dumps(modules, indent=2, default=str),
                    )
                else:
                    pdf_bytes = self._render_backlog_pdf(
                        project_id=project_id,
                        project_name=project_name,
                        modules=modules,
                        exported_at=exported_at,
                        exported_by=user_email,
                    )
                    zf.writestr("requirements_backlog.pdf", pdf_bytes)
                included.append("backlog")

            if request.include_srs:
                srs_file_count = await self._add_srs_files(zf, project_id=project_id)
                included.append("srs")

            if request.include_domain_knowledge or request.include_architecture_document:
                # ProjectMetadataRepository.get is plain sync (unlike the Neo4j
                # repos above, which wrap themselves in asyncio.to_thread), so
                # keep the blocking driver call off the event loop here. One
                # read serves both documents.
                metadata = await asyncio.to_thread(
                    self._metadata_repo().get, project_id=project_id
                )
                if request.include_domain_knowledge and await self._add_pipeline_document(
                    zf,
                    storage_key=metadata.get("domain_knowledge_storage_key"),
                    arcname="domain_knowledge.md",
                ):
                    included.append("domain_knowledge")
                if request.include_architecture_document and await self._add_pipeline_document(
                    zf,
                    storage_key=metadata.get("architecture_document_storage_key"),
                    arcname="architecture_document.md",
                ):
                    included.append("architecture_document")

            manifest = {
                "project_id": str(project_id),
                "project_name": project_name,
                "exported_at": exported_at.isoformat(),
                "exported_by": user_email,
                "status_filter": request.status_filter,
                "included": included,
                "backlog_item_count": backlog_item_count,
                "srs_file_count": srs_file_count,
            }
            zf.writestr("export_manifest.json", json.dumps(manifest, indent=2))

        return buffer.getvalue(), project_name

    async def _build_backlog(
        self,
        *,
        project_id: UUID,
        status_filter: str,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return the (optionally approved-only) module→feature→story tree and its story count."""
        modules = await self._user_story_repo.list_full_backlog_tree_for_project(
            project_id=project_id,
        )
        if status_filter == "approved":
            modules = self._filter_approved(modules)

        story_count = sum(
            len(feature.get("children") or [])
            for module in modules
            for feature in (module.get("children") or [])
        )
        return modules, story_count

    @staticmethod
    def _filter_approved(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop non-approved stories, and any feature/module left empty as a result."""
        filtered_modules: list[dict[str, Any]] = []
        for module in modules:
            filtered_features: list[dict[str, Any]] = []
            for feature in module.get("children") or []:
                stories = [
                    story
                    for story in (feature.get("children") or [])
                    if story and story.get("status") == UserStoryStatus.APPROVED.value
                ]
                if stories:
                    filtered_features.append({**feature, "children": stories})
            if filtered_features:
                filtered_modules.append({**module, "children": filtered_features})
        return filtered_modules

    def _render_backlog_pdf(
        self,
        *,
        project_id: UUID,
        project_name: str,
        modules: list[dict[str, Any]],
        exported_at: datetime,
        exported_by: str | None,
    ) -> bytes:
        feature_count = sum(len(m.get("children") or []) for m in modules)
        story_count = sum(
            len(f.get("children") or []) for m in modules for f in (m.get("children") or [])
        )
        template_modules = [
            {
                "mod_code": m.get("mod_code"),
                "name": _strip_redundant_code_prefix(m.get("name"), m.get("mod_code")),
                "description": m.get("description"),
                "features": [
                    {
                        "fea_code": f.get("fea_code"),
                        "name": _strip_redundant_code_prefix(
                            f.get("name"), f.get("fea_code"), m.get("mod_code")
                        ),
                        "description": f.get("description"),
                        "stories": f.get("children") or [],
                    }
                    for f in (m.get("children") or [])
                ],
            }
            for m in modules
        ]

        template = _jinja_env.get_template("requirements_backlog.html")
        html_string = template.render(
            project_id=str(project_id),
            project_name=project_name,
            modules=template_modules,
            feature_count=feature_count,
            story_count=story_count,
            exported_at=exported_at.strftime("%Y-%m-%d"),
            exported_by=exported_by,
        )
        return HTML(string=html_string).write_pdf()

    async def _add_srs_files(self, zf: zipfile.ZipFile, *, project_id: UUID) -> int:
        """Pull each of the project's already-generated SRS group-spec files from S3."""
        group_specs = await self._group_spec_repo.list_by_project(str(project_id))
        count = 0
        for spec in group_specs:
            if not spec.storage_key:
                continue
            try:
                chunks = [chunk async for chunk in iter_s3_chunks(spec.storage_key)]
            except Exception:
                logger.warning(
                    "Export: failed to pull SRS group spec from S3: storage_key=%s",
                    spec.storage_key,
                    exc_info=True,
                )
                continue
            content = b"".join(chunks)
            filename = spec.filename or f"{spec.mod_code}-{spec.fea_code}.md"
            zf.writestr(f"srs/{spec.mod_code}-{spec.fea_code}-{filename}", content)
            count += 1
        return count

    async def _add_pipeline_document(
        self,
        zf: zipfile.ZipFile,
        *,
        storage_key: str | None,
        arcname: str,
    ) -> bool:
        """Pull one pipeline-generated Markdown document from S3 into the ZIP.

        Returns ``True`` only if the file was actually written, so the caller
        never lists a document in the manifest that S3 didn't yield. Missing
        (``None``) keys — a project whose pipeline never produced the document —
        and S3 failures are both best-effort skips, matching ``_add_srs_files``.
        """
        if not storage_key:
            return False
        try:
            chunks = [chunk async for chunk in iter_s3_chunks(storage_key)]
        except Exception:
            logger.warning(
                "Export: failed to pull pipeline document from S3: storage_key=%s",
                storage_key,
                exc_info=True,
            )
            return False
        zf.writestr(arcname, b"".join(chunks))
        return True
