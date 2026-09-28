"""Serializes every domain enum in ``app.core.enums`` into one JSON catalog.

Frontend clients use this to populate dropdowns/filters from the same
values the backend persists/validates against, instead of hardcoding them.

Also carries the feedback-regeneration and source-code stage/status maps from
``app.core.constants`` (see ``_STAGE_STATUS_MAP_REGISTRY``) — not themselves
enums, but cross-references between a pipeline's stage values and the
realtime status (``SourceIngestionStatus`` or ``SourceProcessingStatus``)
frontend clients need alongside the enum catalog to interpret
`/ws/projects/{project_id}` events.
"""

from __future__ import annotations

from enum import Enum

from app.core.constants import (
    FEEDBACK_OR_INCREMENTAL_STAGES_STATUS_MAP,
    RFP_BASELINE_STAGES_STATUS_MAP,
    SOURCE_CODE_BASELINE_STAGES_STATUS_MAP,
)
from app.core.enums.activity_type import ACTIVITY_TYPE_DISPLAY_LABELS, ActivityType
from app.core.enums.context_mode import CONTEXT_MODE_DISPLAY_LABELS, ContextMode
from app.core.enums.invitation_status import INVITATION_STATUS_DISPLAY_LABELS, InvitationStatus
from app.core.enums.llm_provider import LLM_PROVIDER_DISPLAY_LABELS, LLMProvider
from app.core.enums.notification_type import NOTIFICATION_TYPE_DISPLAY_LABELS, NotificationType
from app.core.enums.project_member_role import (
    PROJECT_MEMBER_ROLE_DISPLAY_LABELS,
    ProjectMemberRole,
)
from app.core.enums.project_progress_filter import (
    PROJECT_PROGRESS_FILTER_DISPLAY_LABELS,
    ProjectProgressFilter,
)
from app.core.enums.project_status import PROJECT_STATUS_DISPLAY_LABELS, ProjectStatus
from app.core.enums.project_type import PROJECT_TYPE_DISPLAY_LABELS, ProjectType
from app.core.enums.source_ingestion_stage import (
    SOURCE_INGESTION_STAGE_DISPLAY_LABELS,
    SourceIngestionStage,
)
from app.core.enums.source_ingestion_status import (
    SOURCE_INGESTION_STATUS_DISPLAY_LABELS,
    SourceIngestionStatus,
)
from app.core.enums.source_layout_type import SOURCE_LAYOUT_TYPE_LABELS, SourceLayoutType
from app.core.enums.source_status import SOURCE_STATUS_DISPLAY_LABELS, SourceProcessingStatus
from app.core.enums.source_type import SOURCE_TYPE_DISPLAY_LABELS, SourceType
from app.core.enums.tenant_status import TENANT_STATUS_DISPLAY_LABELS, TenantStatus
from app.schemas.setting_schema import EnumCatalogValue

# Registry key -> enum class. The key becomes the top-level field name in the
# catalog response; keep it aligned with the enum's module name under
# app/core/enums so callers can find the source of truth easily.
_ENUM_REGISTRY: dict[str, type[Enum]] = {
    "activity_type": ActivityType,
    "context_mode": ContextMode,
    "invitation_status": InvitationStatus,
    "llm_provider": LLMProvider,
    "notification_type": NotificationType,
    "project_member_role": ProjectMemberRole,
    "project_progress_filter": ProjectProgressFilter,
    "project_status": ProjectStatus,
    "project_type": ProjectType,
    "source_ingestion_stage": SourceIngestionStage,
    "source_ingestion_status": SourceIngestionStatus,
    "source_layout_type": SourceLayoutType,
    "source_status": SourceProcessingStatus,
    "source_type": SourceType,
    "tenant_status": TenantStatus,
}

# Registry key -> that enum's display-labels mapping, for the enums that have
# one. A key with no entry here falls back to using each member's own value
# as its label, so every catalog entry has the same {value, label} shape.
_ENUM_LABELS_REGISTRY: dict[str, dict[str, str]] = {
    "activity_type": ACTIVITY_TYPE_DISPLAY_LABELS,
    "context_mode": CONTEXT_MODE_DISPLAY_LABELS,
    "invitation_status": INVITATION_STATUS_DISPLAY_LABELS,
    "llm_provider": LLM_PROVIDER_DISPLAY_LABELS,
    "notification_type": NOTIFICATION_TYPE_DISPLAY_LABELS,
    "project_member_role": PROJECT_MEMBER_ROLE_DISPLAY_LABELS,
    "project_progress_filter": PROJECT_PROGRESS_FILTER_DISPLAY_LABELS,
    "project_status": PROJECT_STATUS_DISPLAY_LABELS,
    "project_type": PROJECT_TYPE_DISPLAY_LABELS,
    "source_ingestion_stage": SOURCE_INGESTION_STAGE_DISPLAY_LABELS,
    "source_ingestion_status": SOURCE_INGESTION_STATUS_DISPLAY_LABELS,
    "source_layout_type": SOURCE_LAYOUT_TYPE_LABELS,
    "source_status": SOURCE_STATUS_DISPLAY_LABELS,
    "source_type": SOURCE_TYPE_DISPLAY_LABELS,
    "tenant_status": TENANT_STATUS_DISPLAY_LABELS,
}

# Registry key -> a static, non-enum stage/status cross-reference map, merged
# into the catalog verbatim under the same key. Not enum listings ({value,
# label} entries) — each is a stage-key -> possible-realtime-status-values map.
_STAGE_STATUS_MAP_REGISTRY: dict[str, dict[str, list[str]]] = {
    "rfp_baseline_stages_status_map": RFP_BASELINE_STAGES_STATUS_MAP,
    "feedback_or_incremental_stages_status_map": FEEDBACK_OR_INCREMENTAL_STAGES_STATUS_MAP,
    "source_code_baseline_stages_status_map": SOURCE_CODE_BASELINE_STAGES_STATUS_MAP,
}


class EnumCatalogService:
    """Builds the full catalog of app enums, keyed by registry name."""

    def get_enum_catalog(self) -> dict[str, EnumCatalogValue]:
        """Return every registered enum as a {value: label} map.

        ``label`` uses the enum's dedicated display-labels mapping when one
        is registered in ``_ENUM_LABELS_REGISTRY``, falling back to the raw
        value otherwise — so every enum has the same response shape whether
        or not it has custom labels, and a caller can look up a label
        directly by value instead of scanning a list. Also includes every
        entry from ``_STAGE_STATUS_MAP_REGISTRY`` (see module docstring) so a
        single call gives the frontend the enum catalog plus the stage/status
        cross-references.
        """
        catalog: dict[str, EnumCatalogValue] = {
            key: {
                member.value: _ENUM_LABELS_REGISTRY.get(key, {}).get(member.value, member.value)
                for member in enum_cls
            }
            for key, enum_cls in _ENUM_REGISTRY.items()
        }
        catalog.update(_STAGE_STATUS_MAP_REGISTRY)
        return catalog
