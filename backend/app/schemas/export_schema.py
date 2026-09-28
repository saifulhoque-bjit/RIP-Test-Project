"""Schemas for the project export feature."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ExportRequest(BaseModel):
    """Body of ``POST /projects/{project_id}/export``.

    The response is a raw ZIP file, not an ``ApiResponse`` envelope — this
    schema only describes what goes *in*.
    """

    model_config = ConfigDict(extra="forbid")

    status_filter: Literal["approved", "all"] = "approved"
    include_backlog: bool = True
    backlog_format: Literal["json", "pdf"] = "json"
    # SRS specifications are always exported as the already-generated
    # Markdown files pulled from S3 — no format choice to make.
    include_srs: bool = True
    # Same story for the two per-project pipeline documents. Both default to
    # False (unlike ``include_srs``) so an existing client that doesn't send
    # them keeps getting exactly the bundle it gets today.
    include_domain_knowledge: bool = False
    include_architecture_document: bool = False
