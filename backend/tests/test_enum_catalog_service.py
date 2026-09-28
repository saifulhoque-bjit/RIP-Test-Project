"""Unit tests for app/services/enum_catalog_service.py."""

from __future__ import annotations

from app.core.constants import (
    FEEDBACK_OR_INCREMENTAL_STAGES_STATUS_MAP,
    RFP_BASELINE_STAGES_STATUS_MAP,
    SOURCE_CODE_BASELINE_STAGES_STATUS_MAP,
)
from app.core.enums.project_status import PROJECT_STATUS_DISPLAY_LABELS, ProjectStatus
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.tenant_status import TenantStatus
from app.services import enum_catalog_service
from app.services.enum_catalog_service import EnumCatalogService

_STAGE_STATUS_MAP_KEYS = {
    "rfp_baseline_stages_status_map",
    "feedback_or_incremental_stages_status_map",
    "source_code_baseline_stages_status_map",
}


def test_get_enum_catalog_includes_every_registered_enum() -> None:
    catalog = EnumCatalogService().get_enum_catalog()

    assert "project_status" in catalog
    assert "tenant_status" in catalog
    assert "llm_provider" in catalog
    # 15 registered enums + the three non-enum stage/status cross-reference maps.
    assert len(catalog) == 18


def test_get_enum_catalog_includes_stage_status_maps() -> None:
    catalog = EnumCatalogService().get_enum_catalog()

    assert catalog["rfp_baseline_stages_status_map"] == RFP_BASELINE_STAGES_STATUS_MAP
    assert (
        catalog["feedback_or_incremental_stages_status_map"]
        == FEEDBACK_OR_INCREMENTAL_STAGES_STATUS_MAP
    )
    assert (
        catalog["source_code_baseline_stages_status_map"]
        == SOURCE_CODE_BASELINE_STAGES_STATUS_MAP
    )


def test_get_enum_catalog_serializes_members_with_their_display_labels() -> None:
    catalog = EnumCatalogService().get_enum_catalog()

    assert catalog["project_status"] == {
        member.value: PROJECT_STATUS_DISPLAY_LABELS[member.value] for member in ProjectStatus
    }


def test_get_enum_catalog_returns_value_to_label_maps() -> None:
    catalog = EnumCatalogService().get_enum_catalog()

    for key, entries in catalog.items():
        if key in _STAGE_STATUS_MAP_KEYS:
            continue  # not an enum listing — covered by its own test above.
        assert isinstance(entries, dict)
        for value, label in entries.items():
            assert isinstance(value, str)
            assert isinstance(label, str)


def test_get_enum_catalog_uses_dedicated_display_labels_when_registered() -> None:
    catalog = EnumCatalogService().get_enum_catalog()

    stage_by_value = catalog["source_ingestion_stage"]
    assert stage_by_value[SourceIngestionStage.GENERATING_MODULE_FEATURE.value] == (
        "Generating module & feature"
    )
    assert stage_by_value[SourceIngestionStage.INGESTING_SOURCES.value] == "Ingesting Sources"

    ingestion_status_by_value = catalog["source_ingestion_status"]
    assert ingestion_status_by_value[SourceIngestionStatus.READY_FOR_REVIEW.value] == (
        "Ready for review"
    )

    tenant_status_by_value = catalog["tenant_status"]
    assert tenant_status_by_value[TenantStatus.PENDING_INVITATION.value] == "Pending invitation"


def test_get_enum_catalog_falls_back_to_value_when_no_labels_registered(monkeypatch) -> None:
    """Every current enum has a dedicated labels mapping, so this exercises
    the fallback branch directly by simulating a not-yet-labeled entry."""
    monkeypatch.delitem(enum_catalog_service._ENUM_LABELS_REGISTRY, "project_status")

    catalog = EnumCatalogService().get_enum_catalog()

    for value, label in catalog["project_status"].items():
        assert label == value
