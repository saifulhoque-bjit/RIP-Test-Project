"""Activity type enum used for project activity-log classification."""

from __future__ import annotations

from enum import Enum


class ActivityType(str, Enum):
    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    RFP_MODULES_GENERATION_STARTED = "rfp_modules_generation_started"
    RFP_MODULES_GENERATED = "rfp_modules_generated"
    RFP_MODULES_GENERATION_FAILED = "rfp_modules_generation_failed"
    RFP_MODULES_GENERATION_CANCELLED = "rfp_modules_generation_cancelled"
    RFP_MODULES_REGENERATION_STARTED = "rfp_modules_regeneration_started"
    RFP_MODULES_REGENERATED = "rfp_modules_regenerated"
    RFP_MODULES_REGENERATION_FAILED = "rfp_modules_regeneration_failed"
    RFP_MODULES_REGENERATION_CANCELLED = "rfp_modules_regeneration_cancelled"
    RFP_USER_STORIES_GENERATION_STARTED = "rfp_user_stories_generation_started"
    RFP_USER_STORIES_GENERATED = "rfp_user_stories_generated"
    RFP_USER_STORIES_GENERATION_FAILED = "rfp_user_stories_generation_failed"
    RFP_USER_STORIES_GENERATION_CANCELLED = "rfp_user_stories_generation_cancelled"
    RFP_USER_STORIES_REGENERATION_STARTED = "rfp_user_stories_regeneration_started"
    RFP_USER_STORIES_REGENERATED = "rfp_user_stories_regenerated"
    RFP_USER_STORIES_REGENERATION_FAILED = "rfp_user_stories_regeneration_failed"
    RFP_USER_STORIES_REGENERATION_CANCELLED = "rfp_user_stories_regeneration_cancelled"
    RFP_FEEDBACK_REGENERATION_STARTED = "rfp_feedback_regeneration_started"
    RFP_FEEDBACK_REGENERATED = "rfp_feedback_regenerated"
    RFP_FEEDBACK_REGENERATION_FAILED = "rfp_feedback_regeneration_failed"
    RFP_FEEDBACK_REGENERATION_CANCELLED = "rfp_feedback_regeneration_cancelled"
    MODULE_FEATURE_APPROVED = "module_feature_approved"
    USER_STORIES_APPROVED = "user_stories_approved"
    SOURCE_CODE_PIPELINE_STARTED = "source_code_pipeline_started"
    SOURCE_CODE_PIPELINE_COMPLETED = "source_code_pipeline_completed"
    SOURCE_CODE_PIPELINE_FAILED = "source_code_pipeline_failed"
    SOURCE_CODE_GLOBAL_ARTIFACTS_COMPLETED = "source_code_global_artifacts_completed"
    SOURCE_CODE_MODULE_STARTED = "source_code_module_started"
    SOURCE_CODE_MODULE_COMPLETED = "source_code_module_completed"
    SOURCE_CODE_MODULE_FAILED = "source_code_module_failed"
    SOURCE_CODE_MODULE_CANCELLED = "source_code_module_cancelled"
    SOURCE_CODE_DOMAIN_KNOWLEDGE_COMPLETED = "source_code_domain_knowledge_completed"
    SOURCE_CODE_DOMAIN_KNOWLEDGE_FAILED = "source_code_domain_knowledge_failed"
    SOURCE_CODE_ARCHITECTURE_DOCUMENT_COMPLETED = "source_code_architecture_document_completed"
    SOURCE_CODE_ARCHITECTURE_DOCUMENT_FAILED = "source_code_architecture_document_failed"
    SOURCE_CODE_FEEDBACK_REGENERATION_STARTED = "source_code_feedback_regeneration_started"
    SOURCE_CODE_FEEDBACK_REGENERATED = "source_code_feedback_regenerated"
    SOURCE_CODE_FEEDBACK_REGENERATION_FAILED = "source_code_feedback_regeneration_failed"
    SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED = "source_code_feedback_regeneration_cancelled"
    INCREMENTAL_CHANGESET_STARTED = "incremental_changeset_started"
    INCREMENTAL_CHANGESET_INGESTED = "incremental_changeset_ingested"
    INCREMENTAL_CHANGESET_FAILED = "incremental_changeset_failed"
    INCREMENTAL_CHANGESET_CANCELLED = "incremental_changeset_cancelled"
    INCREMENTAL_CHANGE_ACCEPTED = "incremental_change_accepted"
    INCREMENTAL_CHANGE_REJECTED = "incremental_change_rejected"
    FEEDBACK_CHANGE_ACCEPTED = "feedback_change_accepted"
    FEEDBACK_CHANGE_REJECTED = "feedback_change_rejected"
    REQUIREMENT_UPDATE_REVIEW_COMPLETED = "requirement_update_review_completed"
    JIRA_SYNC_STARTED = "jira_sync_started"
    JIRA_SYNC_COMPLETED = "jira_sync_completed"
    JIRA_SYNC_FAILED = "jira_sync_failed"
    TAP_SYNC_STARTED = "tap_sync_started"
    TAP_SYNC_COMPLETED = "tap_sync_completed"
    TAP_SYNC_FAILED = "tap_sync_failed"


# Human-readable labels for API responses.
ACTIVITY_TYPE_DISPLAY_LABELS: dict[str, str] = {
    ActivityType.PROJECT_CREATED.value: "Project created",
    ActivityType.PROJECT_UPDATED.value: "Project updated",
    ActivityType.RFP_MODULES_GENERATION_STARTED.value: "RFP modules generation started",
    ActivityType.RFP_MODULES_GENERATED.value: "RFP modules generated",
    ActivityType.RFP_MODULES_GENERATION_FAILED.value: "RFP modules generation failed",
    ActivityType.RFP_MODULES_GENERATION_CANCELLED.value: "RFP modules generation cancelled",
    ActivityType.RFP_MODULES_REGENERATION_STARTED.value: "RFP modules regeneration started",
    ActivityType.RFP_MODULES_REGENERATED.value: "RFP modules regenerated",
    ActivityType.RFP_MODULES_REGENERATION_FAILED.value: "RFP modules regeneration failed",
    ActivityType.RFP_MODULES_REGENERATION_CANCELLED.value: "RFP modules regeneration cancelled",
    ActivityType.RFP_USER_STORIES_GENERATION_STARTED.value: "RFP user stories generation started",
    ActivityType.RFP_USER_STORIES_GENERATED.value: "RFP user stories generated",
    ActivityType.RFP_USER_STORIES_GENERATION_FAILED.value: "RFP user stories generation failed",
    ActivityType.RFP_USER_STORIES_GENERATION_CANCELLED.value: (
        "RFP user stories generation cancelled"
    ),
    ActivityType.RFP_USER_STORIES_REGENERATION_STARTED.value: (
        "RFP user stories regeneration started"
    ),
    ActivityType.RFP_USER_STORIES_REGENERATED.value: "RFP user stories regenerated",
    ActivityType.RFP_USER_STORIES_REGENERATION_FAILED.value: (
        "RFP user stories regeneration failed"
    ),
    ActivityType.RFP_USER_STORIES_REGENERATION_CANCELLED.value: (
        "RFP user stories regeneration cancelled"
    ),
    ActivityType.RFP_FEEDBACK_REGENERATION_STARTED.value: "RFP feedback regeneration started",
    ActivityType.RFP_FEEDBACK_REGENERATED.value: "RFP feedback regenerated",
    ActivityType.RFP_FEEDBACK_REGENERATION_FAILED.value: "RFP feedback regeneration failed",
    ActivityType.RFP_FEEDBACK_REGENERATION_CANCELLED.value: "RFP feedback regeneration cancelled",
    ActivityType.MODULE_FEATURE_APPROVED.value: "Module & feature approved",
    ActivityType.USER_STORIES_APPROVED.value: "User stories approved",
    ActivityType.SOURCE_CODE_PIPELINE_STARTED.value: "Source code pipeline started",
    ActivityType.SOURCE_CODE_PIPELINE_COMPLETED.value: "Source code pipeline completed",
    ActivityType.SOURCE_CODE_PIPELINE_FAILED.value: "Source code pipeline failed",
    ActivityType.SOURCE_CODE_GLOBAL_ARTIFACTS_COMPLETED.value: (
        "Source code global artifacts completed"
    ),
    ActivityType.SOURCE_CODE_MODULE_STARTED.value: "Module processing started",
    ActivityType.SOURCE_CODE_MODULE_COMPLETED.value: "Module processing completed",
    ActivityType.SOURCE_CODE_MODULE_FAILED.value: "Module processing failed",
    ActivityType.SOURCE_CODE_MODULE_CANCELLED.value: "Module processing cancelled",
    ActivityType.SOURCE_CODE_DOMAIN_KNOWLEDGE_COMPLETED.value: "Domain knowledge generated",
    ActivityType.SOURCE_CODE_DOMAIN_KNOWLEDGE_FAILED.value: "Domain knowledge generation failed",
    ActivityType.SOURCE_CODE_ARCHITECTURE_DOCUMENT_COMPLETED.value: (
        "Architecture document generated"
    ),
    ActivityType.SOURCE_CODE_ARCHITECTURE_DOCUMENT_FAILED.value: (
        "Architecture document generation failed"
    ),
    ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_STARTED.value: (
        "Source code feedback regeneration started"
    ),
    ActivityType.SOURCE_CODE_FEEDBACK_REGENERATED.value: "Source code feedback regenerated",
    ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_FAILED.value: (
        "Source code feedback regeneration failed"
    ),
    ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED.value: (
        "Source code feedback regeneration cancelled"
    ),
    ActivityType.INCREMENTAL_CHANGESET_STARTED.value: "Incremental changeset started",
    ActivityType.INCREMENTAL_CHANGESET_INGESTED.value: "Incremental changeset ingested",
    ActivityType.INCREMENTAL_CHANGESET_FAILED.value: "Incremental changeset failed",
    ActivityType.INCREMENTAL_CHANGESET_CANCELLED.value: "Incremental changeset cancelled",
    ActivityType.INCREMENTAL_CHANGE_ACCEPTED.value: "Incremental change accepted",
    ActivityType.INCREMENTAL_CHANGE_REJECTED.value: "Incremental change rejected",
    ActivityType.FEEDBACK_CHANGE_ACCEPTED.value: "Feedback change accepted",
    ActivityType.FEEDBACK_CHANGE_REJECTED.value: "Feedback change rejected",
    ActivityType.REQUIREMENT_UPDATE_REVIEW_COMPLETED.value: "Requirement update review completed",
    ActivityType.JIRA_SYNC_STARTED.value: "Jira sync started",
    ActivityType.JIRA_SYNC_COMPLETED.value: "Jira sync completed",
    ActivityType.JIRA_SYNC_FAILED.value: "Jira sync failed",
    ActivityType.TAP_SYNC_STARTED.value: "TAP sync started",
    ActivityType.TAP_SYNC_COMPLETED.value: "TAP sync completed",
    ActivityType.TAP_SYNC_FAILED.value: "TAP sync failed",
}

# Source-type/trigger prefix prepended to a message when recorded (e.g.
# "[RFP] Generated 5 Modules, 12 Features"). Omitted here means unprefixed:
# PROJECT_CREATED/PROJECT_UPDATED aren't pipeline-related,
# MODULE_FEATURE_APPROVED/USER_STORIES_APPROVED fire once a project's whole
# backlog is approved — not attributable to a single ingestion's source type
# — and REQUIREMENT_UPDATE_REVIEW_COMPLETED fires for one `requirement_update`
# ingestion that could equally have been fed by the feedback-regeneration or
# incremental-upload flow (both use the same SourceType.REQUIREMENT_UPDATE),
# so no single prefix would be accurate.
ACTIVITY_TYPE_MESSAGE_PREFIX: dict[str, str] = {
    ActivityType.RFP_MODULES_GENERATION_STARTED.value: "RFP",
    ActivityType.RFP_MODULES_GENERATED.value: "RFP",
    ActivityType.RFP_MODULES_GENERATION_FAILED.value: "RFP",
    ActivityType.RFP_MODULES_GENERATION_CANCELLED.value: "RFP",
    ActivityType.RFP_MODULES_REGENERATION_STARTED.value: "Feedback",
    ActivityType.RFP_MODULES_REGENERATED.value: "Feedback",
    ActivityType.RFP_MODULES_REGENERATION_FAILED.value: "Feedback",
    ActivityType.RFP_MODULES_REGENERATION_CANCELLED.value: "Feedback",
    ActivityType.RFP_USER_STORIES_GENERATION_STARTED.value: "RFP",
    ActivityType.RFP_USER_STORIES_GENERATED.value: "RFP",
    ActivityType.RFP_USER_STORIES_GENERATION_FAILED.value: "RFP",
    ActivityType.RFP_USER_STORIES_GENERATION_CANCELLED.value: "RFP",
    ActivityType.RFP_USER_STORIES_REGENERATION_STARTED.value: "Feedback",
    ActivityType.RFP_USER_STORIES_REGENERATED.value: "Feedback",
    ActivityType.RFP_USER_STORIES_REGENERATION_FAILED.value: "Feedback",
    ActivityType.RFP_USER_STORIES_REGENERATION_CANCELLED.value: "Feedback",
    ActivityType.RFP_FEEDBACK_REGENERATION_STARTED.value: "Feedback",
    ActivityType.RFP_FEEDBACK_REGENERATED.value: "Feedback",
    ActivityType.RFP_FEEDBACK_REGENERATION_FAILED.value: "Feedback",
    ActivityType.RFP_FEEDBACK_REGENERATION_CANCELLED.value: "Feedback",
    ActivityType.SOURCE_CODE_PIPELINE_STARTED.value: "Source Code",
    ActivityType.SOURCE_CODE_PIPELINE_COMPLETED.value: "Source Code",
    ActivityType.SOURCE_CODE_PIPELINE_FAILED.value: "Source Code",
    ActivityType.SOURCE_CODE_GLOBAL_ARTIFACTS_COMPLETED.value: "Source Code",
    ActivityType.SOURCE_CODE_MODULE_STARTED.value: "Source Code",
    ActivityType.SOURCE_CODE_MODULE_COMPLETED.value: "Source Code",
    ActivityType.SOURCE_CODE_MODULE_FAILED.value: "Source Code",
    ActivityType.SOURCE_CODE_MODULE_CANCELLED.value: "Source Code",
    ActivityType.SOURCE_CODE_DOMAIN_KNOWLEDGE_COMPLETED.value: "Source Code",
    ActivityType.SOURCE_CODE_DOMAIN_KNOWLEDGE_FAILED.value: "Source Code",
    ActivityType.SOURCE_CODE_ARCHITECTURE_DOCUMENT_COMPLETED.value: "Source Code",
    ActivityType.SOURCE_CODE_ARCHITECTURE_DOCUMENT_FAILED.value: "Source Code",
    ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_STARTED.value: "Feedback",
    ActivityType.SOURCE_CODE_FEEDBACK_REGENERATED.value: "Feedback",
    ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_FAILED.value: "Feedback",
    ActivityType.SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED.value: "Feedback",
    ActivityType.INCREMENTAL_CHANGESET_STARTED.value: "Incremental Update",
    ActivityType.INCREMENTAL_CHANGESET_INGESTED.value: "Incremental Update",
    ActivityType.INCREMENTAL_CHANGESET_FAILED.value: "Incremental Update",
    ActivityType.INCREMENTAL_CHANGESET_CANCELLED.value: "Incremental Update",
    ActivityType.JIRA_SYNC_STARTED.value: "Jira",
    ActivityType.JIRA_SYNC_COMPLETED.value: "Jira",
    ActivityType.JIRA_SYNC_FAILED.value: "Jira",
    ActivityType.TAP_SYNC_STARTED.value: "TAP",
    ActivityType.TAP_SYNC_COMPLETED.value: "TAP",
    ActivityType.TAP_SYNC_FAILED.value: "TAP",
}
