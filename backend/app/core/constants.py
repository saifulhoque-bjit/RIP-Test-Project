"""Application-wide constants.

Add new constants here rather than inlining magic strings in service or
client code.  Group by domain for readability.
"""

from app.core.enums.context_mode import ContextMode
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.source_status import SourceProcessingStatus
from app.core.enums.source_type import SourceType

# ── Pagination defaults ────────────────────────────────────────────────────
DEFAULT_PAGE_SKIP = 0
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

# ── Module/Feature/UserStory versioning ───────────────────────────────────
# Starting version for a newly created Module/Feature/UserStory node; bumped
# by 1 on every subsequent content update (see ModuleFeatureRepository,
# UserStoryRepository).
INITIAL_ENTITY_VERSION = 1

# ── Source upload types ───────────────────────────────────────────────────
SOURCE_UPLOAD_BULK = "bulk"
SOURCE_UPLOAD_LINK = "link"

# ── Source processing statuses ───────────────────────────────────────────
SOURCE_STATUS_UPLOADED = SourceProcessingStatus.UPLOADED.value
SOURCE_STATUS_QUEUED = SourceProcessingStatus.QUEUED.value
SOURCE_STATUS_RUNNING = SourceProcessingStatus.RUNNING.value
SOURCE_STATUS_READY_FOR_REVIEW = SourceProcessingStatus.READY_FOR_REVIEW.value
SOURCE_STATUS_COMPLETED = SourceProcessingStatus.COMPLETED.value
SOURCE_STATUS_FAILED = SourceProcessingStatus.FAILED.value
SOURCE_STATUS_CANCELLED = SourceProcessingStatus.CANCELLED.value

# ── Source types ──────────────────────────────────────────────────────────
SOURCE_TYPE_RFP = SourceType.RFP.value
SOURCE_TYPE_ADDITIONAL_RFP = SourceType.ADDITIONAL_RFP.value
SOURCE_TYPE_SOURCE_CODE = SourceType.SOURCE_CODE.value
SOURCE_TYPE_MEETING_NOTES = SourceType.MEETING_NOTES.value
SOURCE_TYPE_REQUIREMENT_UPDATE = SourceType.REQUIREMENT_UPDATE.value

# ── Module/user-story regeneration realtime statuses ──────────────────────
# WebSocket-realtime aliases of SourceIngestionStatus, used while a module or
# user-story regeneration is in flight (not a separate persisted status).
SOURCE_INGESTION_STATUS_QUEUED = SourceIngestionStatus.QUEUED.value
SOURCE_INGESTION_STATUS_RUNNING = SourceIngestionStatus.RUNNING.value
SOURCE_INGESTION_STATUS_READY_FOR_REVIEW = SourceIngestionStatus.READY_FOR_REVIEW.value
SOURCE_INGESTION_STATUS_COMPLETED = SourceIngestionStatus.COMPLETED.value
SOURCE_INGESTION_STATUS_FAILED = SourceIngestionStatus.FAILED.value

# ── ProjectTask cancellation status ───────────────────────────────────────
# Shared across every task_type, and terminal. Written synchronously by
# ``ProjectTaskService.cancel_request`` when the cancel API is called — there
# is no intermediate "cancelling" state: a source-code run cannot be
# interrupted mid-LLM-call, so waiting for the worker to confirm would leave
# the client watching an in-between status for up to the request timeout.
TASK_STATUS_CANCELLED = "cancelled"

# ── Feedback-driven regeneration stage -> realtime status map ────────────
# For each SourceIngestionStage a feedback-driven regeneration touches, the
# SourceIngestionStatus values a client may observe while that stage is
# active — this mirrors the ingestion's own persisted `status` column, since
# that's what a client polling SourceIngestion actually sees.
# Exposed via EnumCatalogService.get_enum_catalog (`/settings/enums`).
RFP_BASELINE_STAGES_STATUS_MAP: dict[str, list[str]] = {
    SourceIngestionStage.GENERATING_MODULE_FEATURE.value: [
        SourceIngestionStatus.RUNNING.value,
        SourceIngestionStatus.FAILED.value,
        SourceIngestionStatus.CANCELLED.value,
    ],
    SourceIngestionStage.MODULE_FEATURE_READY_FOR_REVIEW.value: [
        SourceIngestionStatus.READY_FOR_REVIEW.value
    ],
    SourceIngestionStage.GENERATING_USER_STORY.value: [
        SourceIngestionStatus.RUNNING.value,
        SourceIngestionStatus.FAILED.value,
        SourceIngestionStatus.CANCELLED.value,
    ],
    SourceIngestionStage.USER_STORY_READY_FOR_REVIEW.value: [
        SourceIngestionStatus.READY_FOR_REVIEW.value
    ],
}

# Generic two-stage collapse of the above, for a feedback-or-incremental
# update flow that doesn't distinguish module/feature vs. user story phases
# (e.g. a requirement-update-driven regeneration) — a client that only needs
# "is it still generating or is it ready for review" uses this instead of
# picking apart the phase-specific map above.
FEEDBACK_OR_INCREMENTAL_STAGES_STATUS_MAP: dict[str, list[str]] = {
    SourceIngestionStage.GENERATING_REQUIREMENTS.value: [
        SourceIngestionStatus.RUNNING.value,
        SourceIngestionStatus.FAILED.value,
        SourceIngestionStatus.CANCELLED.value,
    ],
    SourceIngestionStatus.READY_FOR_REVIEW.value: [SourceIngestionStatus.READY_FOR_REVIEW.value],
}

# Source-code pipeline stage -> realtime SourceIngestionStatus values a
# client may observe while that SourceIngestionStage checkpoint is active.
# The source-code pipeline has no module/feature vs. user-story phase split
# (one automated pass, see SourceIngestionStage's module docstring), so
# unlike RFP_BASELINE_STAGES_STATUS_MAP there's only one status list shape,
# but it uses the same SourceIngestionStatus values for consistency. Exposed
# via EnumCatalogService.get_enum_catalog (`/settings/enums`).
SOURCE_CODE_BASELINE_STAGES_STATUS_MAP: dict[str, list[str]] = {
    SourceIngestionStage.INGESTING_SOURCES.value: [
        SourceIngestionStatus.RUNNING.value,
        SourceIngestionStatus.FAILED.value,
        SourceIngestionStatus.CANCELLED.value,
    ],
    SourceIngestionStage.BUILDING_CODE_DEPENDENCY_GRAPH.value: [
        SourceIngestionStatus.RUNNING.value,
        SourceIngestionStatus.FAILED.value,
        SourceIngestionStatus.CANCELLED.value,
    ],
    SourceIngestionStage.DISCOVERING_MODULES.value: [
        SourceIngestionStatus.RUNNING.value,
        SourceIngestionStatus.FAILED.value,
        SourceIngestionStatus.CANCELLED.value,
    ],
    SourceIngestionStage.EXTRACTING_REQUIREMENTS.value: [
        SourceIngestionStatus.RUNNING.value,
        SourceIngestionStatus.FAILED.value,
        SourceIngestionStatus.CANCELLED.value,
    ],
    SourceIngestionStage.READY_FOR_REVIEW.value: [SourceIngestionStatus.READY_FOR_REVIEW.value],
}

# ── TAP integration ───────────────────────────────────────────────────────
# RIP always identifies itself to TAP as this app client. It is a property of
# the product, not of a project, so it is never collected from the user nor
# stored per row — it is sent as the ``app_client_name`` query param on TAP's
# credential-verification call.
TAP_APP_CLIENT_NAME = "RIP"

# ── Source upload defaults ────────────────────────────────────────────
SOURCE_DEFAULT_FILENAME = "upload"
SOURCE_DEFAULT_CONTENT_TYPE = "application/octet-stream"
SOURCE_DEFAULT_FORMAT = "UNKNOWN"

# ── Byte conversion ───────────────────────────────────────────────────
BYTES_PER_MB = 1024 * 1024

# ── Tenant code generation ────────────────────────────────────────────────
# Precheck retries against TenantRepository.get_by_code (cheap, no DB write)
# before giving up on generating a unique code for a name.
TENANT_CODE_MAX_GENERATION_ATTEMPTS = 25
# Bounded backstop for the rare race where two requests generate the same
# candidate code between the precheck and the flush.
TENANT_CODE_MAX_INSERT_RETRIES = 3

# ── Role names ────────────────────────────────────────────────────────
ROLE_ADMIN = "admin"
# Consolidates the former "pm"/"viewer" roles (see app/db/seed_db.py) — every
# non-admin tenant user gets this single role rather than picking between the
# two, which had grown to differ only by a handful of write permissions.
ROLE_MEMBER = "member"
# Cross-tenant, all-permissions role reserved for the seeded MVP bootstrap
# user (see app/db/seed_super_admin.py). Distinct from ROLE_ADMIN so future
# per-tenant admin scoping can be added without renaming the existing role.
ROLE_SUPER_ADMIN = "super_admin"
# ── Allowed MIME types ──────────────────────────────────────────────────────
SOURCE_ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/webp",
        "application/zip",
        "application/x-zip-compressed",
    }
)

SOURCE_ZIP_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
    }
)

# ── Incremental upload type ───────────────────────────────────────────────────
SOURCE_UPLOAD_INCREMENTAL = "bulk-incremental"

SOURCE_INCREMENTAL_PDF_MIME_TYPE = "application/pdf"

SOURCE_INCREMENTAL_IMAGE_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
    }
)

SOURCE_INCREMENTAL_ALLOWED_MIME_TYPES: frozenset[str] = SOURCE_INCREMENTAL_IMAGE_MIME_TYPES | {
    SOURCE_INCREMENTAL_PDF_MIME_TYPE
}

# ── SQS event types for sources ──────────────────────────────────────────
EVENT_SOURCE_UPLOADED = "source.uploaded"
EVENT_SOURCE_BULK_UPLOADED = "source.bulk_uploaded"

# ── Celery task retry policy ─────────────────────────────────────────────────
TASK_MAX_RETRIES = 3
TASK_RETRY_BASE_DELAY_SECONDS = 60  # Exponential back-off: 60 s, 120 s, 240 s
# Source-code pipeline tasks retry far less: each run can take up to 24 h, so a
# blanket TASK_MAX_RETRIES=3 would mean re-running an expensive pipeline
# multiple times over. Only one retry is allowed.
SOURCE_CODE_TASK_MAX_RETRIES = 1
# Countdown before that one retry fires, for the module-level tasks
# (process_single_module/persist_single_module) that retry transient
# infrastructure faults — see CELERY_RETRY_POLICY.docx Section A/D.
SOURCE_CODE_TASK_RETRY_COUNTDOWN_SECONDS = 30
# ConcurrentPipelineError means another project's pipeline currently holds
# LLMClient's process-wide single-flight lock, which can legitimately stay
# held for 10 minutes to 6 hours (see _process_single_module_task's
# docstring) — the 30s infra-blip countdown above would almost always retry
# straight back into the same contention and burn the one allowed retry for
# no reason. Longer countdown gives the other project's run a real chance to
# finish first.
SOURCE_CODE_CONCURRENT_PIPELINE_RETRY_COUNTDOWN_SECONDS = 300
# process_source_task is the lightweight fan-out dispatcher shared by every
# source mime type (document/image/code) — not source-code-specific — so it
# gets its own low-retry constant rather than borrowing SOURCE_CODE_TASK_MAX_RETRIES.
COMMON_MAX_RETRIES = 1

# ── Celery task time limits ──────────────────────────────────────────────────
# Parsing tasks (document, image, code) run for up to 3 hours.
TASK_PARSING_SOFT_TIME_LIMIT = 10800  # 3 h — triggers SoftTimeLimitExceeded
TASK_PARSING_TIME_LIMIT = 11100  # 3 h 5 min — hard kill after grace period
# process_source_task itself only batch-loads sources, flips their status to
# queued, and fires apply_async() at the per-mime-type leaf tasks — it never
# waits on those leaf tasks (document/image/code parsing, which can legitimately
# run for hours), so it needs its own short envelope rather than sharing
# TASK_PARSING_*_TIME_LIMIT with the tasks it merely dispatches.
TASK_SOURCE_DISPATCH_SOFT_TIME_LIMIT = 600  # 10 min — triggers SoftTimeLimitExceeded
TASK_SOURCE_DISPATCH_TIME_LIMIT = 900  # 15 min — hard kill after grace period
# Source-code orchestration parent task can block on module-group joins,
# so it needs a larger envelope than single-file parsing tasks.
TASK_SOURCE_CODE_SOFT_TIME_LIMIT = TASK_PARSING_SOFT_TIME_LIMIT * 4
TASK_SOURCE_CODE_TIME_LIMIT = TASK_PARSING_TIME_LIMIT * 4
# Explicit alias constants for source-code pipeline task.
TASK_SOURCE_CODE_PIPELINE_SOFT_TIME_LIMIT = 86_400  # 24 hours (outer orchestrator)
TASK_SOURCE_CODE_PIPELINE_TIME_LIMIT = 90_000  # 25 hours
# Single source-code module task (LLM-heavy) can run substantially longer.
TASK_SOURCE_CODE_MODULE_SOFT_TIME_LIMIT = 39_600  # 11 h
TASK_SOURCE_CODE_MODULE_TIME_LIMIT = 43_200  # 12 h
# Per-module persistence task limits.
TASK_PERSIST_SINGLE_MODULE_SOFT_TIME_LIMIT = 1_800  # 30 min
TASK_PERSIST_SINGLE_MODULE_TIME_LIMIT = 2_100  # 35 min
# Redis coordination TTLs for source-code module completion tracking.
# Sequential workflows (parsing → module/feature → user story) can total 48 h;
# both TTLs must exceed the worst-case full-pipeline wall-clock time.
COUNTER_TTL_SECONDS = 194_400  # 54 hours
LOCK_TTL_SECONDS = 194_400  # 54 hours
# Keep a guard window before parent soft limit while waiting on group join.
TASK_SOURCE_CODE_GROUP_WAIT_BUFFER_SECONDS = 300
# Minimum timeout floor for waiting on source-code module group completion.
TASK_SOURCE_CODE_GROUP_WAIT_MIN_SECONDS = 60
# AI generation tasks (module/feature + user story) can run up to 4 hours
# for large projects.
TASK_AI_SOFT_TIME_LIMIT = 14_400  # 4 h
TASK_AI_TIME_LIMIT = 14_700  # 4 h 5 min

# Visibility timeout must exceed the longest running task.  Without this,
# Redis re-enqueues messages after the default 1 h, causing duplicate
# execution while the original worker is still processing.
# 30 h = TASK_SOURCE_CODE_PIPELINE_TIME_LIMIT (25 h) + 5 h buffer.
CELERY_BROKER_VISIBILITY_TIMEOUT = TASK_SOURCE_CODE_PIPELINE_TIME_LIMIT + 5 * 3600

# How long a Redis cancellation flag (app/core/task_control.py) lives before
# expiring. Must outlast the longest task it could apply to, same reasoning
# as CELERY_BROKER_VISIBILITY_TIMEOUT above.
TASK_CANCEL_FLAG_TTL_SECONDS = TASK_SOURCE_CODE_PIPELINE_TIME_LIMIT + 5 * 3600

# ── Stale SourceIngestion detection ───────────────────────────────────────
# A worker process killed mid-task (OOM, spot interruption, node
# replacement, deploy) never raises a Python exception, so the normal
# failure path never runs and the row stays `running` forever — Redis alone
# won't redeliver the original message until CELERY_BROKER_VISIBILITY_TIMEOUT
# (up to 30h) elapses. tasks.maintenance.detect_stale_ingestions polls for
# this instead. Grace period added on top of a stage's own hard task
# time_limit before a `running` row is considered auto-fail-stale — a task
# that's genuinely still running never exceeds its own hard limit, so this
# only needs to cover clock skew / DB write latency, not real work.
#
# Applies to rfp/additional_rfp/meeting_notes/requirement_update ingestions
# ONLY (document parsing, module/feature gen, user-story gen) — each of those
# stages is one single Celery task with a real enforced hard time_limit, so
# elapsed-time-past-that-limit reliably means "worker died", not "still
# working." Deliberately NOT used for source_type == "source_code": see
# SOURCE_CODE_STALL_ADVISORY_THRESHOLD_SECONDS below for why that pipeline
# shape has no safe fixed auto-fail threshold.
STALE_INGESTION_GRACE_SECONDS = 1_800  # 30 min
# How often the sweep runs via Celery beat.
STALE_INGESTION_SWEEP_INTERVAL_SECONDS = 900  # 15 min

# source_code ingestions dispatch one sequential chain() of
# (process_single_module -> persist_single_module) task pairs per discovered
# module (app/workers/source_code_task.py's _run_pipeline_orchestrator) and
# return immediately — no single task's time_limit bounds the full run, and
# module count is uncapped ("Removed hard [:3] cap that silently dropped 18
# of 21 modules"). A project with 20+ modules at a realistic mix of ~2.5h
# average with occasional 10-11h outliers can legitimately run 50+ hours, so
# there is no fixed elapsed-time threshold that safely distinguishes "still
# working through many modules" from "worker died." Reusing COUNTER_TTL_SECONDS
# (the same 54h worst-case-pipeline-duration assumption the module-completion
# Redis counter above is already sized on) as an advisory-only tripwire: past
# this, tasks.maintenance.detect_stale_ingestions logs a WARNING for a human
# to check, but — unlike the rfp-family path above — never auto-fails the row.
SOURCE_CODE_STALL_ADVISORY_THRESHOLD_SECONDS = COUNTER_TTL_SECONDS  # 54 hours

# ── Celery queue names ─────────────────────────────────────────────────────
QUEUE_ROUTING = "routing"
QUEUE_DOCUMENT_PARSING = "document_parsing"
QUEUE_PARSING = "parsing"
QUEUE_SOURCE_CODE_PARSING = "source_code_parsing"
QUEUE_SOURCE_CODE_PROCESSING = "source_code_processing"
QUEUE_SOURCE_CODE_PERSISTENCE = "source_code_persistence"
# Split off from QUEUE_SOURCE_CODE_PROCESSING/PERSISTENCE so the "regenerate a
# single feature/MFU" and "cleanup a project's temp folder" workloads can be
# scaled/deployed on a different worker than the primary process/persist path.
QUEUE_SOURCE_CODE_FEATURE_REGENERATION = "source_code_feature_regeneration"
QUEUE_SOURCE_CODE_CLEANUP = "source_code_cleanup"
QUEUE_MODULE_FEATURE_GENERATION = "module_feature_generation"
# Split off from QUEUE_MODULE_FEATURE_GENERATION so a full regeneration run
# (typically slower/rarer) can't queue behind or compete with initial
# generation requests on the same worker.
QUEUE_MODULE_FEATURE_REGENERATION = "module_feature_regeneration"
QUEUE_USER_STORY_GENERATION = "user_story_generation"
# Split off from QUEUE_USER_STORY_GENERATION — feedback-driven regeneration is
# triggered from a different part of the product (review flow) than initial
# generation/regeneration and should scale independently.
QUEUE_USER_STORY_FEEDBACK_REGENERATION = "user_story_feedback_regeneration"
QUEUE_NEO4J_SYNC = "neo4j_sync"
QUEUE_INCREMENTAL_UPDATE = "incremental_update"
QUEUE_NOTIFICATIONS = "notifications"
QUEUE_MAINTENANCE = "maintenance"

# ── Incremental update task constants ─────────────────────────────────────────
INCREMENTAL_UPDATE_TASK_TYPE = "incremental_update"
INCREMENTAL_UPDATE_STATUS_QUEUED = "queued"

# ── Incremental update context modes ──────────────────────────────────────────
CONTEXT_MODE_FULL = ContextMode.FULL.value
CONTEXT_MODE_SUBSET = ContextMode.SUBSET.value

# Incremental update involves sequential LLM calls (parse + AI pipeline)
# and can run for 20-40 minutes per batch.
TASK_INCREMENTAL_SOFT_TIME_LIMIT = 7_200  # 2 h
TASK_INCREMENTAL_TIME_LIMIT = 7_500  # 2 h 5 min

# ── Source-code ZIP validation ────────────────────────────────────────────
# Extensions that identify source code files (covers common languages and
# frameworks).  Kept as a frozenset for O(1) membership tests.
SOURCE_CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Scripts / interpreted
        ".py",
        ".rb",
        ".php",
        ".pl",
        ".lua",
        ".r",
        ".sh",
        ".bash",
        # JVM
        ".java",
        ".kt",
        ".kts",
        ".groovy",
        ".scala",
        ".clj",
        # .NET
        ".cs",
        ".vb",
        ".fs",
        # C-family
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".cxx",
        ".hpp",
        ".hxx",
        # Systems
        ".go",
        ".rs",
        ".d",
        # Mobile
        ".swift",
        ".m",
        ".dart",
        # Web front-end
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".svelte",
        # Markup / template languages used in codebases
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sass",
        ".less",
        # Query / data languages
        ".sql",
        ".graphql",
        ".gql",
        # Functional
        ".hs",
        ".elm",
        ".ex",
        ".exs",
        ".erl",
        # Config / build manifests that signal a project root
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".xml",
        ".gradle",
        ".sra",
        ".srf",
        ".sru",
        ".srw",
        ".srm",
        ".srd",
        ".srs",
        ".srx",
        ".srj",
        ".srp",
        ".srq",
        ".srt",
        ".cls",
        ".vbp",
        ".frm",
        ".bas",
        ".ctl",
        ".vbproj",
        ".cbl",
        ".cob",
        ".cpy",
        ".pco",
        ".cblproj",
        ".makefile",
        ".cmake",
    }
)

# Project manifest file names (checked case-insensitively) that also
# confirm a ZIP contains a software project.
SOURCE_MANIFEST_NAMES: frozenset[str] = frozenset(
    {
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "requirements.txt",
        "pipfile",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "cargo.toml",
        "cargo.lock",
        "gemfile",
        "gemfile.lock",
        "composer.json",
        "composer.lock",
        "go.mod",
        "go.sum",
        "pubspec.yaml",
        "csproj",
        ".sln",
        "makefile",
        "cmake",
        "cmakelists.txt",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        ".gitignore",
        ".editorconfig",
    }
)
