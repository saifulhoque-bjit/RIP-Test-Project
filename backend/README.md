# RIP — Requirement Intelligence Platform

![Python](https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?logo=fastapi&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-5.6-37814A?logo=celery&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-0.4+-1C3C3C?logo=langchain&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-5.26-008CC1?logo=neo4j&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7.4-DC382D?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-856%2F891%20passing-yellow)
![License](https://img.shields.io/badge/license-Proprietary-lightgrey)

> AI-powered backend platform for extracting, managing, and reviewing software requirements — modules, features, and user stories — from source documents and source code.

RIP ingests PDFs, DOCX, spreadsheets, images, and source-code archives; runs LangGraph generate-and-critique pipelines to derive a Module → Feature → User Story backlog; persists the graph in Neo4j; and delivers real-time processing progress and in-app notifications to clients over WebSockets. Multi-tenant RBAC (Super Admin / Client Admin / Member), per-project membership, and outbound sync to Jira and TAP round out the platform.

---

## Features

- **Multi-format ingestion** — PDF, DOC/DOCX, CSV, XLS/XLSX, PNG/JPEG/WEBP images, and ZIP source-code archives, uploaded individually or as a batch (`SourceIngestion`)
- **Module/Feature and Agile Backlog generation** — LangGraph generator → critic loops turn parsed fragments into a Module → Feature hierarchy, then an Agile Backlog of User Stories
- **Incremental updates (3-stage pipeline)** — `IncrementalSourceService` (intake/upload) → Celery worker running the selector → generator → critic LangGraph via `IncrementalUpdateProcessorService` (proposal written to Postgres `incremental_histories` + Neo4j) → `IncrementalUpdatesService` (human review: merge live tree with the pending proposal, accept/reject per module/feature/user-story)
- **Entity version snapshots** — every incremental update that changes an existing Module/Feature/UserStory writes an immutable `*Version` node (`ModuleVersion`/`FeatureVersion`/`UserStoryVersion`) in Neo4j before applying the change, giving each entity a point-in-time history
- **Feedback-driven story patching** — reviewers can request AI-assisted rewrites of specific user stories (or source-code-derived features) from inline feedback
- **Source-code ingestion** — a separate Tree-sitter-based pipeline parses ZIP archives (COBOL, PowerBuilder, VB6, …) into the same Module/Feature/User-Story graph, plus `GroupSpec`/`ConfigSpec`/`SourceCodeMetadata` Neo4j records for spec/config artifacts and raw per-module pipeline output
- **Provider-agnostic LLM routing** — Architect/Critic (and image-extraction) agents are independently configurable across OpenAI, Anthropic, Google, and DeepSeek; tenants can additionally bring their own encrypted API keys per provider
- **Graph-native requirement storage** — Neo4j stores Project → Source → Fragment and Project → Module → Feature → UserStory hierarchies (plus evidence/spec side-graphs) with MERGE-safe idempotent writes
- **Real-time progress & notifications** — Redis-backed WebSocket pub/sub delivers live task events (per-project and cross-project dashboard) and an in-app notification feed
- **Cooperative task cancellation** — a Redis-backed flag keyed by `request_id` lets long-running pipelines check for cancellation at their own checkpoints, since the `-P threads` worker pool can't hard-kill an in-flight task
- **Celery task pipeline** — dedicated queues for routing, parsing, source-code processing, AI generation, and Neo4j sync, with exponential back-off retry
- **Full audit trail** — append-only `project_task_events` table records every status transition
- **JWT authentication & RBAC** — AWS Cognito User Pool with access/refresh token rotation; three built-in roles (Super Admin / Client Admin / Member) plus custom roles backed by a `Permission` catalogue, and per-project `ProjectMember` assignment scoping which projects a Member can access
- **Multi-tenancy** — `Tenant` records group users under a Client Admin, with tenant-scoped invitations (admin-issued, token-based accept flow) and per-tenant, Fernet-encrypted LLM provider configuration with live connection testing
- **Jira integration** — per-project sync pushing Features as Epics and User Stories as Jira issues, with a preview/diff step, content-hash change detection, and full sync history/mapping audit trail
- **TAP integration** — a decoupled stage → notify → pull → ack handoff of the module/feature/user-story hierarchy to an external TAP platform, with its own sync/ack history tables
- **Backlog & SRS export** — on-demand ZIP export of the requirements backlog (JSON or generated PDF) and/or already-generated SRS Markdown specs from S3, with a manifest recording who/when/what was exported
- **Rate limiting** — per-endpoint SlowAPI rate limits (auth, source upload/download, AI regeneration, Jira/TAP sync, invitations, export, …) configurable via environment variables
- **Vector RAG** — pgvector embeddings on fragment content for context-aware requirement retrieval

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Project Structure](#project-structure)
- [Why `routes/v1` Instead of Controllers](#why-routesv1-instead-of-controllers)
- [Architecture Overview](#architecture-overview)
- [Neo4j Graph Schema](#neo4j-graph-schema)
- [AI Generation Pipelines](#ai-generation-pipelines)
- [Celery Worker Architecture](#celery-worker-architecture)
- [Environment Setup](#environment-setup)
- [Running the Application](#running-the-application)
  - [Option 1 — Docker Compose (recommended)](#option-1--docker-compose-recommended)
  - [Option 2 — Local development](#option-2--local-development)
- [Database Migrations](#database-migrations)
- [Running Tests](#running-tests)
- [API Documentation](#api-documentation)
- [Key Environment Variables](#key-environment-variables)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Prerequisites

| Tool | Minimum Version |
|------|----------------|
| Python | 3.13 |
| Docker + Docker Compose | 24+ |
| PostgreSQL | 17 with pgvector extension |
| Neo4j | 5.26 with APOC plugin |
| Redis | 7.4 |
| AWS account | Cognito User Pool, S3 bucket, SQS queue |

---

## Project Structure

```
p1821.2_rip_backend/
├── app/
│   ├── main.py                            # FastAPI application factory
│   ├── router.py                          # Top-level versioned router (/api/v1)
│   ├── deps.py                            # FastAPI dependency injectors
│   ├── version.py                         # Application version / sprint / release string
│   │
│   ├── routes/                            # Function-based versioned route modules
│   │   └── v1/
│   │       ├── auth.py                    # Register / confirm / login / refresh / logout / forgot & reset password / me
│   │       ├── users.py                   # User profile, admin role/status management, project assignment
│   │       ├── tenants.py                 # Tenant CRUD + tenant-scoped LLM provider config/test
│   │       ├── tenant_invitations.py      # Admin-issued tenant invitations (create/list/resend/revoke)
│   │       ├── invitations.py             # Public invitation-token validation + accept
│   │       ├── projects.py                # Project CRUD + dashboard stats
│   │       ├── project_members.py         # Per-project Member assignment
│   │       ├── project_tasks.py           # Background task tracking + cancellation (project/task/request scoped)
│   │       ├── roles.py                   # Custom role + permission-catalogue management (super_admin)
│   │       ├── source_ingestion_pipelines.py # Cross-project SourceIngestion pipeline listing
│   │       ├── settings.py                # Shared application settings
│   │       ├── sources.py                 # Source upload / download / delete / ingestion batches
│   │       ├── fragments.py               # Fragment endpoints (list, get, update bbox)
│   │       ├── module_features.py         # Module / Feature endpoints + Jira/TAP sync-status flags
│   │       ├── observability.py           # Celery queue depth / dead-letter inspection
│   │       ├── user_stories.py            # User story endpoints (list, status, regenerate, feedback, sync-status)
│   │       ├── incremental_updates.py     # Incremental-update review: latest tree, list, accept/reject
│   │       ├── notifications.py           # In-app notification feed
│   │       ├── jira_integrations.py       # Per-project Jira config + sync preview/execute/history
│   │       ├── tap_integrations.py        # Per-project TAP sync + inbound pull/ack callbacks
│   │       └── export.py                  # Backlog (JSON/PDF) + SRS spec ZIP export
│   │
│   ├── services/                          # Business logic layer
│   │   ├── auth_service.py
│   │   ├── config_spec_service.py         # Source-code pipeline config-spec persistence (S3 + Neo4j)
│   │   ├── document_parser_service.py
│   │   ├── export_service.py              # Backlog/SRS ZIP export assembly
│   │   ├── fragment_embedding_service.py  # Vector embedding for RAG retrieval
│   │   ├── fragment_service.py            # Document fragment management
│   │   ├── group_spec_service.py          # Source-code group/spec metadata
│   │   ├── incremental_source_service.py  # Incremental-update upload intake (route-facing)
│   │   ├── incremental_update_processor_service.py # Applies AI proposal to Neo4j/Postgres (worker-facing)
│   │   ├── incremental_updates_service.py # Human review layer: merge tree + proposal, accept/reject
│   │   ├── invitation_service.py          # Tenant invitation issue/validate/accept/resend/revoke
│   │   ├── jira_integration_service.py    # Jira integration CRUD + live credential validation
│   │   ├── jira_sync_service.py           # Jira sync engine: preview/diff, push, history
│   │   ├── module_feature_service.py      # Module / Feature hierarchy
│   │   ├── notification_service.py        # In-app notification create/list/read
│   │   ├── project_graph_service.py       # Neo4j project graph orchestration
│   │   ├── project_member_service.py      # Per-project Member assignment (tenant-scoped)
│   │   ├── project_service.py
│   │   ├── project_task_service.py        # Project background-task tracking + cancellation
│   │   ├── role_service.py                # Role/permission CRUD (super_admin)
│   │   ├── setting_service.py
│   │   ├── source_code_metadata_service.py # Source-code pipeline per-module output persistence (Neo4j)
│   │   ├── source_ingestion_service.py    # Upload-batch (SourceIngestion) rollup
│   │   ├── source_service.py
│   │   ├── srs_evidence_service.py
│   │   ├── tap_sync_service.py            # TAP stage → notify → pull → ack sync engine
│   │   ├── tenant_llm_provider_service.py # Per-tenant encrypted LLM provider config + connection test
│   │   ├── tenant_service.py              # Tenant CRUD + visibility scoping
│   │   ├── ui_parser_service.py
│   │   ├── user_service.py
│   │   ├── user_story_service.py          # User story / agile backlog management
│   │   │
│   │   ├── rfp_pipeline_v2_graph_service/ # LangGraph generator/critic pipelines
│   │   │   ├── graph_module_feature.py    # Module/feature generation graph
│   │   │   ├── graph_agile_backlog.py     # Agile backlog (user story) generation graph
│   │   │   ├── graph_agile_backlog_patch.py # Feedback-driven user-story patch graph
│   │   │   ├── graph_incremental.py       # Incremental backlog update graph
│   │   │   ├── graph_incremental_selector.py # Pre-filter: selects features an update affects
│   │   │   ├── image_extraction_service.py # Single-pass image → fragment extraction
│   │   │   └── prompts/                   # Versioned markdown prompts for each graph
│   │   │
│   │   └── source_code_pipeline/          # Tree-sitter-based source-code ingestion pipeline
│   │       ├── pipeline_orchestrator.py
│   │       ├── spec_extractor.py
│   │       ├── flatten_source.py, stage_business_source.py, feedback_grouping.py
│   │       ├── source_code_pipeline_service.py
│   │       ├── src/                       # Language-specific parsers (COBOL, PowerBuilder, VB6, …)
│   │       ├── config/                    # Per-language pipeline configs
│   │       ├── schemas/                   # Pipeline-internal Pydantic schemas
│   │       └── prompts/
│   │
│   ├── repositories/                      # Data access layer
│   │   ├── postgres/                      # PostgreSQL repositories
│   │   │   ├── base_repository.py         # Generic CRUD base
│   │   │   ├── fragment_embedding_repository.py
│   │   │   ├── incremental_history_repository.py
│   │   │   ├── invitation_repository.py
│   │   │   ├── jira_integration_repository.py, jira_sync_history_repository.py, jira_sync_mapping_repository.py
│   │   │   ├── notification_repository.py
│   │   │   ├── permission_repository.py, role_repository.py
│   │   │   ├── project_member_repository.py
│   │   │   ├── project_repository.py / project_repository_async.py
│   │   │   ├── project_stats_repository.py
│   │   │   ├── project_task_event_repository.py # Append-only task event log
│   │   │   ├── project_task_repository.py
│   │   │   ├── source_ingestion_repository.py
│   │   │   ├── source_repository.py
│   │   │   ├── story_feedback_history_repository.py
│   │   │   ├── tap_ack_history_repository.py, tap_sync_history_repository.py, tap_sync_mapping_repository.py
│   │   │   ├── tenant_repository.py
│   │   │   └── user_repository.py
│   │   └── neo4j/                         # Neo4j repositories
│   │       ├── config_spec_repository.py
│   │       ├── fragment_repository.py
│   │       ├── group_spec_repository.py
│   │       ├── module_feature_repository.py # Also owns ModuleVersion/FeatureVersion snapshot writes
│   │       ├── project_metadata_repository.py
│   │       ├── project_repository.py
│   │       ├── setting_repository.py
│   │       ├── source_code_metadata_repository.py
│   │       ├── source_repository.py
│   │       ├── srs_evidence_repository.py
│   │       └── user_story_repository.py    # Also owns UserStoryVersion snapshot writes
│   │
│   ├── models/                            # ORM / graph models
│   │   ├── postgres/                      # SQLAlchemy ORM models
│   │   │   ├── fragment_embedding_model.py
│   │   │   ├── incremental_history_model.py
│   │   │   ├── invitation_model.py        # Tenant invitation (token hash, status, expiry)
│   │   │   ├── jira_integration_model.py, jira_sync_history_model.py, jira_sync_mapping_model.py
│   │   │   ├── notification_model.py
│   │   │   ├── permission_model.py, role_model.py # role_permissions association
│   │   │   ├── project_member_model.py    # Per-project role assignment (composite PK)
│   │   │   ├── project_model.py           # incl. tenant_id (nullable FK) and version counter
│   │   │   ├── project_task_event_model.py # Append-only task state-change history
│   │   │   ├── project_task_model.py      # incl. request_id (cancellation grouping); run_id/ProjectRun FK dropped (migration 0002)
│   │   │   ├── source_ingestion_model.py  # Upload-batch rollup (run_code, stages, generation counters)
│   │   │   ├── source_model.py
│   │   │   ├── story_feedback_history_model.py
│   │   │   ├── tap_ack_history_model.py, tap_sync_history_model.py, tap_sync_mapping_model.py
│   │   │   ├── tenant_llm_provider_model.py # Encrypted per-tenant provider API key + test state
│   │   │   ├── tenant_model.py
│   │   │   └── user_model.py              # user_roles association (tenant-wide roles)
│   │   └── neo4j/                         # Neo4j node/relationship models
│   │       ├── config_spec_model.py       # Source-code pipeline config-spec artifact pointer
│   │       ├── fragment_model.py
│   │       ├── group_spec_model.py
│   │       ├── module_feature_model.py
│   │       ├── project_metadata_model.py
│   │       ├── project_model.py
│   │       ├── source_code_metadata_model.py # Raw per-module source-code pipeline output
│   │       ├── source_model.py
│   │       ├── srs_evidence_model.py
│   │       ├── user_story_model.py
│   │       └── version_model.py           # ModuleVersion / FeatureVersion / UserStoryVersion snapshots
│   │
│   ├── schemas/                           # Pydantic request / response schemas
│   │   ├── auth_schema.py, user_schema.py
│   │   ├── config_spec_schema.py, source_code_metadata_schema.py
│   │   ├── document_schema.py, fragment_schema.py
│   │   ├── export_schema.py
│   │   ├── group_spec_schema.py, srs_evidence_schema.py
│   │   ├── image_extraction_schema.py
│   │   ├── incremental_updates_schema.py
│   │   ├── invitation_schema.py
│   │   ├── jira_integration_schema.py, tap_integration_schema.py
│   │   ├── module_feature_schema.py, user_story_schema.py
│   │   ├── notification_schema.py
│   │   ├── project_member_schema.py, project_pipeline_schema.py
│   │   ├── project_schema.py
│   │   ├── rfp_pipeline_v2_graph_schema.py # Output schemas for the LangGraph pipelines
│   │   ├── setting_schema.py
│   │   ├── source_schema.py, source_ingestion_schema.py
│   │   ├── tenant_schema.py
│   │   └── observability_schema.py
│   │
│   ├── core/                              # Application-wide infrastructure
│   │   ├── celery_app.py                  # Celery application factory + task_routes
│   │   ├── config.py                      # Settings via Pydantic BaseSettings
│   │   ├── constants.py                   # Shared string / numeric constants (role names, etc.)
│   │   ├── redis_client.py                # Shared sync/async Redis client factory (VPC-safe keepalive settings)
│   │   ├── task_control.py                # Redis-backed cooperative cancellation flag (keyed by request_id)
│   │   ├── enums/                         # All domain status/type enums, one file each
│   │   │   ├── context_mode.py, source_layout_type.py
│   │   │   ├── invitation_status.py, tenant_status.py, project_member_role.py, llm_provider.py
│   │   │   ├── notification_type.py
│   │   │   ├── project_status.py, project_type.py
│   │   │   ├── run_stage.py               # module_feature / user_story — tags SourceIngestion.stages
│   │   │   ├── source_status.py, source_type.py
│   │   │   └── source_ingestion_status.py
│   │   ├── error_response.py              # Standardised error response builder
│   │   ├── exception_handlers.py          # Global FastAPI exception handlers
│   │   ├── exceptions.py                  # Custom exception hierarchy
│   │   ├── health.py                      # /health route
│   │   ├── lifespan.py                    # Startup / shutdown lifecycle hooks (DB, WS managers)
│   │   ├── messages.py                    # Shared user-facing string constants
│   │   ├── middleware.py                  # CORS, correlation ID, rate limiter wiring
│   │   ├── openapi_examples.py            # Shared OpenAPI example payloads
│   │   ├── protocols.py                   # Shared protocol / interface definitions
│   │   ├── rate_limiter.py                # SlowAPI rate limiter setup
│   │   └── security.py                    # Cognito JWT verification
│   │
│   ├── db/                                # Database layer
│   │   ├── base.py                        # SQLAlchemy declarative base
│   │   ├── init_db.py                     # Table creation helpers
│   │   ├── init_and_migrate.py            # Startup migration runner (alembic upgrade head)
│   │   ├── neo4j.py                       # Neo4j async driver factory
│   │   ├── seed_db.py                     # Idempotent seed data (roles, permissions)
│   │   ├── seed_super_admin.py            # Idempotent Super Admin bootstrap (no tenant assigned)
│   │   ├── session.py                     # SQLAlchemy sync session factory
│   │   ├── unit_of_work.py                # Unit of Work pattern — owns all repositories
│   │   └── async_unit_of_work.py          # Async variant for async-only call sites
│   │
│   ├── clients/                           # External service clients
│   │   ├── aws_session.py                 # Shared aioboto3 session singleton
│   │   ├── jira_client.py                 # Jira Cloud REST client
│   │   ├── tap_client.py                  # TAP platform client (notify/pull/ack)
│   │   ├── llamaparser_client.py          # LlamaParse document parsing client
│   │   ├── llm_factory.py                 # Multi-provider LLM factory (OpenAI / Anthropic / Google / DeepSeek)
│   │   ├── omniparser_client.py           # OmniParser image parsing client
│   │   └── s3_client.py                   # S3 upload / download / presign helpers
│   │
│   ├── messaging/                         # AWS SQS integration
│   │   ├── project_publisher.py           # Publishes project-level events to SQS
│   │   ├── sqs_consumer.py                # Long-poll consumer with handler registry
│   │   └── sqs_producer.py                # Message producer
│   │
│   ├── workers/                           # Celery background tasks
│   │   ├── worker.py                      # Minimal entry point (app = celery_app)
│   │   ├── tasks.py                       # Notification email task
│   │   ├── process_source_tasks.py        # Root dispatcher — routes by MIME type
│   │   ├── document_task.py               # PDF/DOCX/CSV/XLSX parsing + module/story task entry points
│   │   ├── document_task_stages.py        # Shared stage helpers used by document_task.py
│   │   ├── image_task.py                  # PNG / JPEG / WEBP via OmniParser
│   │   ├── source_code_task.py            # ZIP source archives via Tree-sitter
│   │   ├── incremental_task.py            # Incremental backlog-update pipeline task
│   │   ├── jira_sync_task.py              # RIP → Jira sync (registered, not yet in task_routes — see below)
│   │   ├── project_tasks.py               # Project-level background task handlers
│   │   └── _task_helpers.py               # Shared helpers (_mark_status, emit_task_event, _run_async)
│   │
│   ├── websockets/                        # WebSocket layer
│   │   ├── manager.py                     # Redis-backed pub/sub manager for per-project task events
│   │   ├── source_ws.py                   # WS /ws/projects/{project_id}
│   │   ├── notification_manager.py        # Redis-backed pub/sub manager for per-user notifications
│   │   ├── notification_ws.py             # WS /ws/notifications
│   │   ├── project_status_manager.py      # Redis-backed pub/sub manager for the cross-project dashboard
│   │   └── project_status_ws.py           # WS /ws/projects/pipelines
│   │
│   └── utils/                             # Shared utilities
│       ├── auth.py, common.py, correlation.py, cache.py, log_context.py
│       ├── cancellable_llm.py             # Wraps LLM calls with task_control cancellation checks
│       ├── document_fragment_formatter.py # Fragment → prompt payload serialiser
│       ├── embedder.py                    # Fragment embedding helpers
│       ├── encryption.py                  # Fernet helpers for Jira token / tenant LLM key encryption
│       ├── http_client.py, link_downloader.py
│       ├── logger.py                      # Structured logger factory
│       ├── openapi.py                     # OpenAPI schema customisation helpers
│       ├── pagination.py, response.py
│       └── queue_monitoring.py            # Celery queue depth / worker / dead-letter helpers
│
├── alembic/                               # Alembic migration environment
│   ├── env.py
│   ├── script.py.mako
│   └── versions/                          # 25+ revisions (0002 drops the old project_runs table)
│
├── tests/                                 # pytest test suite (fully mocked, no external services)
│   ├── conftest.py
│   └── test_*.py                          # ~70 files, one per module under test (891 tests collected)
│
├── docs/                                  # Architecture and API documentation
├── scripts/                               # Standalone utility / debug scripts
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```

---

## Why `routes/v1` Instead of Controllers

Earlier versions of this codebase used a class-based **controller** pattern where each resource had a class (`AuthController`, `SourceController`, …) whose `__init__` registered routes via `add_api_route(...)`. That pattern was replaced with **function-based route modules** under `app/routes/v1/` for the following reasons:

| Concern | Class-based controllers | `routes/v1` modules |
|---------|------------------------|---------------------|
| **FastAPI idiom** | Non-idiomatic — FastAPI is designed around `APIRouter` + standalone `async def` handlers | First-class FastAPI pattern; consistent with official docs and community conventions |
| **Dependency injection** | Dependencies injected manually in `__init__`, bypassing FastAPI's `Depends` resolution tree | `Annotated` type aliases + `Depends` let FastAPI resolve, validate, and document dependencies automatically |
| **OpenAPI docs** | Route metadata had to be threaded through `add_api_route` kwargs, making Swagger maintenance fragile | `openapi_extra`, `response_model`, `status_code`, and `summary` sit directly on each `@router.get/post/…` decorator — easy to read and update |
| **Testability** | Tests had to instantiate the controller class and wire a mock app around it | Handlers are plain async functions — importable and callable directly; `TestClient` app construction is a one-liner |
| **`Annotated` + `Query`/`Cookie`** | Class-level aliases were scattered and sometimes conflicted with Pydantic field defaults | Module-level `Annotated` aliases centralise parameter metadata; `default=` lives only at the call site, avoiding FastAPI assertion errors |
| **Rate limiting** | `@limiter.limit()` could not decorate class methods without extra scaffolding | Decorating standalone `async def` handlers works out of the box with SlowAPI |
| **Dead code** | Each controller lived in its own file even when it wrapped a single service call | Route modules group related endpoints naturally; no empty classes or unused `__init__` boilerplate |

---

## Architecture Overview

```
┌─────────────┐    HTTPS     ┌──────────────────────────────────────────────┐
│   Frontend  │ ──────────▶  │             FastAPI (port 8000)               │
└─────────────┘   WS  ▲      │  routes/v1 → services → repositories          │
                       │      │  UnitOfWork owns SQLAlchemy sync session      │
                       │      └────────────┬─────────────────────────────────┘
                       │                   │
        ┌──────────────┴───────────────────┼──────────────────────────┐
        │                                  │                           │
 ┌──────▼──────┐                  ┌────────▼────────┐         ┌────────▼───────┐
 │ PostgreSQL  │                  │     Neo4j        │         │    Redis        │
 │  (pgvector) │                  │ (source/backlog  │         │ broker / cache /│
 │             │                  │  graph + version │         │ WS pub-sub /    │
 │             │                  │  snapshots)      │         │ cancel flags    │
 └─────────────┘                  └──────────────────┘         └────────────────┘
                                                                       │
                                                             ┌─────────▼──────────┐
                                                             │  Celery Worker(s)   │
                                                             │  process_source_tasks│
                                                             │  document_task       │
                                                             │  image_task          │
                                                             │  source_code_task    │
                                                             │  incremental_task    │
                                                             │  jira_sync_task      │
                                                             │  project_tasks       │
                                                             └────────────────────┘

AWS services (accessed via aioboto3):        External integrations:
  Cognito → authentication / JWT verification  Jira Cloud → per-project Epic/Story sync (outbound)
  S3      → source file storage                TAP        → hierarchy handoff (stage/notify/pull/ack)
  SQS     → async event messaging
```

RBAC layers on top of this: a tenant-wide `UserRole` (super_admin / admin / member) gates platform-level capability, while per-project `ProjectMember` rows scope which specific projects a Member can access. A `Tenant` groups users under a Client Admin and can hold its own encrypted LLM provider keys.

**Request flow (source upload → backlog generation)**

1. Client uploads via `POST /api/v1/sources/upload/bulk` (or `/upload/link`, `/upload/bulk/incremental`) → `sources` route handler
2. `SourceService`/`SourceIngestionService` validates, uploads to S3, creates DB rows (`Source`, `SourceIngestion` batch rollup), and enqueues `process_source_tasks`
3. Celery's routing worker dispatches by MIME type to `document_task`, `image_task`, or `source_code_task`
4. The document pipeline runs the Module/Feature LangGraph (generator → critic) and persists the result via `ModuleFeatureService` to Neo4j
5. Once modules/features are approved (`PATCH /modules/status`), the Agile Backlog LangGraph generates `UserStory` nodes under each `Feature`
6. Every status transition is written to `project_tasks`/`project_task_events` in PostgreSQL and published to Redis → delivered to the client over `/ws/projects/{project_id}` (or the cross-project `/ws/projects/pipelines` dashboard feed) and, where relevant, as an in-app notification over `/ws/notifications`
7. Optionally: a reviewer triggers `POST /projects/{id}/integrations/jira/sync` or `/integrations/tap/sync` to push the approved backlog outward, or `POST /projects/{id}/export` to pull a ZIP of the backlog/SRS specs

---

## Neo4j Graph Schema

Current graph relationships in code:

```text
(:Project)-[:HAS_MODULE]->(:Module)
(:Project)-[:HAS_SOURCE]->(:Source)
(:Source)-[:HAS_FRAGMENT]->(:Fragment)
(:Module)-[:HAS_FEATURE]->(:Feature)
(:Feature)-[:HAS_USER_STORY]->(:UserStory)

(:Module)-[:HAS_VERSION]->(:ModuleVersion)
(:Feature)-[:HAS_VERSION]->(:FeatureVersion)
(:UserStory)-[:HAS_VERSION]->(:UserStoryVersion)

(:Project)-[:HAS_GROUP_SPEC]->(:GroupSpec)
(:Module)-[:HAS_GROUP_SPEC]->(:GroupSpec)
(:Project)-[:HAS_CONFIG_SPEC]->(:ConfigSpec)
(:Module)-[:HAS_CONFIG_SPEC]->(:ConfigSpec)
(:Project)-[:HAS_SOURCE_CODE_METADATA]->(:SourceCodeMetadata)
(:Module)-[:HAS_SOURCE_CODE_METADATA]->(:SourceCodeMetadata)
(:Project)-[:HAS_SRS_EVIDENCE]->(:SRSEvidence)
(:Feature)-[:HAS_SRS_EVIDENCE]->(:SRSEvidence)
(:GroupSpec)-[:HAS_SRS_EVIDENCE]->(:SRSEvidence)
(:UserStory)-[:HAS_SRS_EVIDENCE]->(:SRSEvidence)
```

> `ProjectMetadata` is keyed by a `project_id` property (looked up directly, not via a graph edge).

> **Functions** are stored as a JSON string property directly on the `Feature` node (`f.functions`) rather than as separate graph nodes. Each element has the shape `{fun_code, name, description}`.

### Version snapshots

`ModuleVersion`/`FeatureVersion`/`UserStoryVersion` are point-in-time snapshot nodes, written (`CREATE`, never `MERGE`) whenever an incremental update changes an existing entity — one accumulates per entity per change, each carrying a `snapshotted_at` timestamp plus a copy of the entity's fields at that moment.

### Incremental-update / sync tracking fields

`Module`, `Feature`, and `UserStory` nodes additionally carry:

| Field | Purpose |
|---|---|
| `incremental_change_type` / `feedback_change_type` | `added` / `updated` / `delete_suggested` — tags how the last incremental-update or feedback run affected the node |
| `text_diffs` | LLM-emitted semantic diff spans, nested per field (and per `fun_code` for functions) |
| `is_jira_synced` / `is_tap_synced` | Whether the node has been pushed to Jira / acknowledged by TAP |
| `rfp_flagged_item` | Quality-gate flag (`entity_id`, `entity_type`, `issue`, `suggested_fix`) from a failed incremental-update correction loop |

Source-code-pipeline-only fields: `mfu_id`/`l2_sources` (links back to the Tree-sitter pipeline's Feature-unit and granular source references) and, on `Feature`, `is_infrastructure`/`condensation_note`.

### Source-code pipeline side-graph

`GroupSpec` and `ConfigSpec` (per-module/feature spec and config artifacts — filename + S3 `storage_key` pointer) and `SourceCodeMetadata` (raw per-module pipeline output: `module_response`, `module_manifest`) are populated by the Tree-sitter source-code pipeline (`app/workers/source_code_task.py` via `GroupSpecService`/`ConfigSpecService`/`SourceCodeMetadataService`) alongside the standard Module/Feature/UserStory graph.

---

## AI Generation Pipelines

All pipelines are built with LangGraph and share the same **generator → critic** shape: a generator node drafts output, a critic node reviews it against the source fragments, and a conditional edge routes back to the generator (up to `ALT_MAX_CRITIC_ITERATIONS` retries) or forward on approval.

```
Source fragments (Neo4j)
    │
    ▼
① Module/Feature graph (graph_module_feature.py)
    ├─ generate → drafts a Module → Feature hierarchy
    ├─ critic   → reviews against source fragments, requests revisions or approves
    └─ persists via ModuleFeatureService (Neo4j MERGE)
    │
    ▼  (once modules/features are approved via PATCH /modules/status)
② Agile Backlog graph (graph_agile_backlog.py)
    ├─ generate → drafts User Stories per Feature
    ├─ critic   → reviews acceptance criteria / technical notes
    └─ persists via UserStoryService (Neo4j MERGE)
```

**Related pipelines**, each following the same generate → critic pattern:

| Pipeline | Module | Triggered by |
|---|---|---|
| Incremental selector | `graph_incremental_selector.py` | Pre-filters which existing features a new meeting-notes/change document affects |
| Incremental update | `graph_incremental.py` | `POST /sources/upload/bulk/incremental` — derives the backlog impact of the new document without a full regeneration |
| Story feedback patch | `graph_agile_backlog_patch.py` | `POST /projects/{project_id}/user-stories/regenerate-by-feedback` — rewrites specific stories from reviewer feedback |
| Image extraction | `image_extraction_service.py` | Single-pass (no critic loop) fragment extraction for image sources via OmniParser |

**Incremental update — 3-stage pipeline**, split across intake / worker / review so the AI proposal is never applied directly without a review step:

```
IncrementalSourceService (route-facing intake)
    ├─ upload to S3, persist Source rows, create ProjectTask
    └─ enqueue Celery incremental_task
    │
    ▼
Celery worker → IncrementalUpdateProcessorService
    ├─ runs graph_incremental_selector.py → graph_incremental.py
    ├─ snapshots any changed Module/Feature/UserStory to a *Version node first
    ├─ writes the adds/updates/deletes/flags proposal to Neo4j
    └─ persists the raw proposal to Postgres incremental_histories
    │
    ▼  (reviewer calls GET /incremental-updates/latest or /updates/list)
IncrementalUpdatesService (human review)
    ├─ merges the live tree with the pending incremental_histories proposal
    └─ POST /incremental-updates/accept or /incremental-updates/reject finalizes each changed node
```

**Source-code ingestion** is a separate, non-LangGraph pipeline (`app/services/source_code_pipeline/`): a Tree-sitter-based static analyzer parses ZIP archives per source language (COBOL, PowerBuilder, VB6, …) directly into the same Module/Feature/UserStory graph, without an LLM generate/critique loop — plus `GroupSpecService`/`ConfigSpecService`/`SourceCodeMetadataService` persisting spec/config artifacts and raw per-module output alongside it. Feedback-driven regeneration of source-code-derived features is a separate entry point, `POST /user-stories/regenerate-for-source-code`.

### LLM provider configuration

The Architect (generator) and Critic agents share one provider/model pair, configured in `app/core/config.py` — uncomment one provider block (or override the corresponding fields via environment variables):

| Setting | Default | Description |
|---|---|---|
| `ALT_ARCHITECT_MODEL_PROVIDER` / `ALT_ARCHITECT_MODEL_NAME` | `anthropic` / `claude-sonnet-4-6` | Generator agent, used by all pipelines above |
| `ALT_CRITIC_MODEL_PROVIDER` / `ALT_CRITIC_MODEL_NAME` | `anthropic` / `claude-sonnet-4-6` | Critic agent, used by all pipelines above |
| `ALT_MAX_CRITIC_ITERATIONS` | `1` | Max generate↔critic retry cycles per phase (module/feature, backlog) |
| `IMAGE_EXTRACTION_PROVIDER` / `IMAGE_EXTRACTION_MODEL_NAME` | `google` / `gemini-pro-latest` | Single-pass image fragment extraction |

Supported providers: `openai`, `anthropic`, `google`, `deepseek`. Set the corresponding API key — only the keys for active providers are required.

A tenant can additionally enable its own LLM provider(s) via `PATCH /tenants/{tenant_id}/llm-providers/{provider}` — the API key is Fernet-encrypted at rest (`LLM_ENCRYPTION_KEY`) and can be live-tested via `POST .../test` before use.

---

## Celery Worker Architecture

Celery is configured in `app/core/celery_app.py` with auto-discovery across the following task modules:

| Module | Purpose |
|--------|---------|
| `app.workers.tasks` | Notification email delivery |
| `app.workers.process_source_tasks` | Root dispatcher — routes a source to the correct parser by MIME type |
| `app.workers.document_task` | PDF / DOCX / CSV / XLSX parsing via LlamaParse; module/feature and user-story generation task entry points |
| `app.workers.image_task` | PNG / JPEG / WEBP parsing via OmniParser |
| `app.workers.source_code_task` | ZIP source-code archive parsing via Tree-sitter |
| `app.workers.incremental_task` | Incremental backlog-update pipeline (meeting notes / change documents) |
| `app.workers.jira_sync_task` | RIP → Jira sync (`tasks.sync_to_jira`) |
| `app.workers.project_tasks` | Project-level background task handlers (status updates, Neo4j sync, notifications) |
| `app.workers.maintenance_task` | Periodic sweep (`tasks.maintenance.detect_stale_ingestions`) that auto-fails an rfp-family `SourceIngestion` stuck `running` past its stage's max plausible duration — e.g. a worker killed mid-task by OOM/spot-interruption/deploy, which never raises an exception and so never hits the normal failure path. `source_code` ingestions are log-only (WARNING, no auto-fail) since their total duration is unbounded by module count — see `SourceIngestionService.is_stale`'s docstring |

### Queue routing

Tasks are routed to dedicated queues (`app/core/celery_app.py`'s `task_routes`):

| Queue | Tasks | Recommended concurrency |
|-------|-------|--------------------------|
| `routing` | `tasks.process_source` | 10 |
| `document_parsing` | `tasks.parse_document` | 4 (instance 1) + 4 (instance 2) |
| `parsing` | `tasks.parse_image` | 10 |
| `source_code_parsing` | `tasks.parse_code` | 5 |
| `source_code_processing` | `tasks.parse_code.process_single_module` | 5 |
| `source_code_persistence` | `tasks.parse_code.persist_single_module` | 5 |
| `source_code_feature_regeneration` | `tasks.parse_code.regenerate_feature_mfu` | 3 |
| `source_code_cleanup` | `tasks.parse_code.cleanup_project_folder` | 3 |
| `module_feature_generation` | `tasks.modules_and_features.generate_modules_and_features` | 4 |
| `module_feature_regeneration` | `tasks.modules_and_features.regenerate_modules_and_features` | 3 |
| `user_story_generation` | `tasks.user_stories.generate_user_story`, `...regenerate_user_story` | 4 |
| `user_story_feedback_regeneration` | `tasks.user_stories.regenerate_by_feedback` | 3 |
| `incremental_update` | `tasks.incremental_update` | 4 |
| `neo4j_sync` | `tasks.project.sync_project_to_neo4j`, `tasks.project.delete_project_graph` | 10 |
| `notifications` | `tasks.send_notification_email` | 10 |
| `maintenance` | `tasks.maintenance.detect_stale_ingestions` (fired every `STALE_INGESTION_SWEEP_INTERVAL_SECONDS` via Celery beat — requires a `celery beat` process, not yet part of the production instance table below) | shares instance 2 worker2 |
| `celery` (default) | any unrouted task — currently **`tasks.sync_to_jira`** (see note) | 2 |

> `tasks.sync_to_jira` (`app/workers/jira_sync_task.py`) is registered via Celery's `include=[...]` list but has no entry in `task_routes`, so it falls through to the default `celery` queue instead of a dedicated one — worth revisiting before Jira sync sees production volume.

Recommended production worker startup — 4 instances (instances 1-3 run 2 workers each, instance 4 runs 3 — see below) from `app/core/celery_app.py`'s own comment. Every queue is consumed by exactly one worker except `document_parsing`, split across `i1_worker1` (instance 1) and `i2_worker1` (instance 2) for extra capacity — cross-instance queue sharing works the same way as same-instance sharing (competing consumers via Redis), it just spans two machines instead of one. Concurrency is sized from each instance's actual cpu/ram, not a flat guess:

| Instance | cpu | ram | Notes |
|---|---|---|---|
| 1 | 2 | 4 | LLM generation + document parsing — mostly I/O-wait, some real CPU (LLM stream accumulation) |
| 2 | 1 | 2 | Smallest box; worker2's tasks are quick/cheap so tolerate more threads than worker1's parsing/LLM calls |
| 3 | 1 | 2 | Low-volume regeneration paths |
| 4 | 2 | 8 | Source-code pipeline runs `-P prefork` (each concurrent task is its own OS process, not a thread) — sidesteps GIL contention on the CPU-bound segments (tree-sitter, zip extraction); `--max-tasks-per-child=25` recycles children periodically. `source_code_parsing` (the per-project orchestrator, `tasks.parse_code`) runs on its own dedicated `i4_worker3` pool, never sharing a pool with `source_code_processing`/`source_code_persistence` — that task blocks inside its own body waiting on the module chain those queues run, so sharing a pool self-deadlocks once enough concurrent projects fill every child with blocked orchestrators and nothing left to run the module tasks they're waiting on |

```bash
# Instance 1 (2 cpu / 4 ram) — module/feature generation + document parsing + user story generation
celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=4 \
  -n i1_worker1@%h -Q document_parsing,module_feature_generation
celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=4 \
  -n i1_worker2@%h -Q user_story_generation

# Instance 2 (1 cpu / 2 ram) — document parsing + incremental update + interactive/fast tasks
celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=4 \
  -n i2_worker1@%h -Q document_parsing,incremental_update
celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=10 \
  -n i2_worker2@%h -Q routing,parsing,neo4j_sync,notifications

# Instance 3 (1 cpu / 2 ram) — regeneration paths, isolated so a regen backlog can't starve
# first-time generation on instances 1/2
celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=3 \
  -n i3_worker1@%h -Q module_feature_regeneration
celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=3 \
  -n i3_worker2@%h -Q user_story_feedback_regeneration

# Instance 4 (2 cpu / 8 ram) — source-code pipeline (own deploy cadence)
celery -A app.core.celery_app.celery_app worker --loglevel=info -P prefork --concurrency=5 --max-tasks-per-child=25 --max-memory-per-child=614400 \
  -n i4_worker1@%h -Q source_code_processing,source_code_persistence
celery -A app.core.celery_app.celery_app worker --loglevel=info -P prefork --concurrency=5 --max-tasks-per-child=25 --max-memory-per-child=614400 \
  -n i4_worker2@%h -Q source_code_feature_regeneration,source_code_cleanup
# i4_worker3 is a dedicated pool for tasks.parse_code (source_code_parsing) only —
# never merge it back onto i4_worker1's pool, see the self-deadlock note above.
celery -A app.core.celery_app.celery_app worker --loglevel=info -P prefork --concurrency=5 \
  -n i4_worker3@%h -Q source_code_parsing
```

`docker-compose.yml` runs the same 9-worker topology above as named services (`i1_worker1` … `i4_worker3`) so local status output matches production 1:1.

`app/workers/worker.py` is the **minimal Celery entry point** — it exists solely to expose the `app` name used by the `celery` CLI:

```python
from app.core.celery_app import celery_app
app = celery_app
```

Shared helpers (`_mark_status`, `emit_task_event`, `_run_async`) live in `_task_helpers.py` and are imported by each leaf task module to avoid code duplication and circular imports.

### Source processing state machine

```
uploaded → queued → running → ready_for_review
                        │            └──▶ failed
                        └─────────────────▶ failed
```

Every pipeline that processes an uploaded source (RFP document, source-code, image, incremental-update) converges on `ready_for_review` as its success terminal — including the RFP document pipeline's module/feature-generation checkpoint (`document_task_stages.py`'s `generate_modules_and_features_task`), which used to report a distinct `ready_for_module_feature_approval` status. That distinct status is gone: a `Source`'s and its `SourceIngestion`'s status flip together at every checkpoint (`_mark_sources_status`/`_update_source_ingestion_fields`), and which generation phase actually completed (module/feature vs. user-story) is now recorded in `SourceIngestion.stages` (`RunStage.MODULE_FEATURE_READY_FOR_REVIEW` vs. `RunStage.USER_STORY_READY_FOR_REVIEW`) instead of a separate status value. User-story/backlog generation tasks (triggered separately, after review) follow their own status set — see the [WebSocket endpoints](#websocket-endpoints) task-status tables in Swagger for the full per-`task_type` breakdown.

### Task event history

Every status transition for any background task (source processing, module regeneration, user-story generation) is recorded as an immutable row in the `project_task_events` PostgreSQL table. The table is append-only — rows are never updated or deleted directly. This provides a full audit trail of when a task first entered each status.

> If the underlying `project_tasks` row is gone by the time a stale/cancelled retry reports back (e.g. wiped or reset out from under it), `publish_task_event_sync` skips the status/history write instead of raising a foreign-key error — it logs a warning and still publishes the event to Redis for any live dashboard.

`ProjectTaskEventRepository` (owned by `UnitOfWork`) exposes:

| Method | Description |
|--------|-------------|
| `record(...)` | Insert one event row for a status transition |
| `list_by_task(task_id)` | Full timeline for a single task, oldest first |
| `list_first_events_per_status_by_tasks(task_ids)` | First occurrence of each status per task (ordered by `task_id → created_at → status`) |
| `list_by_project(project_id, *, status, limit)` | Recent events for a project, optionally filtered by status |

### Task cancellation

Because production workers run with the `-P threads` pool, there's no separate OS process per task for Celery's `revoke(terminate=True)` to signal — so cancellation is cooperative instead. `app/core/task_control.py` exposes a Redis-backed flag keyed by `request_id` (a whole request's task subtree shares one cancellation unit, mirroring `ProjectTask.request_id`); long-running pipelines poll `is_request_cancelled` at their own checkpoints (task entry, per-module, per-MFU, per generate/critic cycle — see `app/utils/cancellable_llm.py` for the LLM-call wrapper) and exit early once flagged. `DELETE /projects/{project_id}/tasks`, `DELETE /tasks/{task_id}`, and `DELETE /tasks/requests/{request_id}` (`app/routes/v1/project_tasks.py`) set this flag; a Redis outage fails open (`is_request_cancelled` returns `False`) rather than blocking task progress.

### Retry strategy

All tasks use exponential backoff with a maximum of 3 retries:

| Retry | Delay |
|-------|-------|
| 1st | 60 s |
| 2nd | 120 s |
| 3rd | 240 s |

After the final retry, tasks transition to `failed` and record the last error in PostgreSQL.

---

## Environment Setup

Copy the example env file and populate your values:

```bash
cp .env.example .env
```

See [Key Environment Variables](#key-environment-variables) for the full reference.

---

## Running the Application

### Option 1 — Docker Compose (recommended)

Starts all services — PostgreSQL, Neo4j, Redis, the API server, and the Celery worker — in a single command.

```bash
# First run — build images and start all services in the foreground
docker compose up --build

# First run — build images and start all services in detached mode
docker compose up --build -d

# Subsequent runs — start without rebuilding
docker compose up -d

# Tail logs from all services
docker compose logs -f

# Tail logs from a specific service
docker compose logs -f api

# Stop all services (data volumes preserved)
docker compose down

# Stop and remove persistent volumes (wipes all data)
docker compose down -v
```

The API is available at **http://localhost:8000** once the `api` service is healthy.

**Services started by Docker Compose:**

| Service | Port(s) | Description |
|---------|---------|-------------|
| `api` | 8000 | FastAPI application server |
| `celery_worker` | — | Celery background worker, all queues, concurrency 4 |
| `postgres` | 5432 | PostgreSQL 17 (pgvector image) |
| `neo4j` | 7474, 7687 | Neo4j 5.26 Community with APOC |
| `redis` | 6379 | Redis 7.4 — WS pub/sub (DB 0), Celery broker (DB 1), result backend (DB 2) |

> The `api` service automatically runs `python -m app.db.init_and_migrate` (alembic upgrade head) before starting Uvicorn, so no manual migration step is needed on first run.

---

### Option 2 — Local development

#### 1. Create and activate a virtual environment

```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

#### 2. Install dependencies

```bash
pip install -r requirements.txt
```

#### 3. Start required backing services

Use Docker Compose to spin up infrastructure only:

```bash
docker compose up postgres neo4j redis -d
```

Or point your `.env` at existing local instances.

#### 4. Apply database migrations

```bash
alembic upgrade head
```

#### 5. Start the FastAPI server

```bash
# Development mode with auto-reload
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### 6. Start the Celery worker

```bash
celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=4
```

The worker auto-discovers tasks from all modules listed in the [Celery Worker Architecture](#celery-worker-architecture) table. Add `-Q <queue,queue,...>` to consume only specific queues.

#### 7. (Optional) Start the SQS consumer

```bash
python -m app.messaging.sqs_consumer
```

---

## Database Migrations

Schema tables are created automatically at startup via `init_db()` in non-production environments. In production, schema changes **must** be applied via Alembic before restarting the service.

```bash
# Generate a migration from current model changes
alembic revision --autogenerate -m "short description"

# Apply all pending migrations
alembic upgrade head

# Roll back one migration
alembic downgrade -1

# Show current applied revision
alembic current

# Show full migration history
alembic history --verbose
```

> PostgreSQL enum columns (e.g. `sources.status`) cannot have values dropped once added — only appended (`ALTER TYPE ... ADD VALUE`). Plan enum changes as additive; retire unused values in application code rather than removing DB labels.

---

## Running Tests

The test suite requires no external services — all database, AWS, and third-party calls are mocked via `unittest.mock`.

```bash
# Run the full suite
python3.13 -m pytest

# With coverage report
python3.13 -m pytest --cov=app --cov-report=term-missing

# Run a specific test file
python3.13 -m pytest tests/test_source_service.py

# Short traceback, quiet output
python3.13 -m pytest --tb=short -q
```

---

## API Documentation

Interactive documentation is available **in non-production environments only**:

| Interface | URL |
|-----------|-----|
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| Health check | http://localhost:8000/health |

> Swagger UI and ReDoc are disabled when `APP_ENV=production`.

### Endpoint overview

#### Authentication (`/api/v1/auth`) — all public except `/me`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/auth/register` | Register a new user account |
| POST | `/api/v1/auth/confirm` | Confirm email with Cognito verification code |
| POST | `/api/v1/auth/login` | Login — sets HttpOnly access/refresh cookies |
| POST | `/api/v1/auth/refresh` | Refresh access and ID tokens |
| POST | `/api/v1/auth/logout` | Globally invalidate all tokens for the user |
| POST | `/api/v1/auth/forgot-password` | Request a password-reset code via email |
| POST | `/api/v1/auth/reset-password` | Reset password using the emailed code |
| GET | `/api/v1/auth/me` | Get current authenticated user profile |

#### Users (`/api/v1/users`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/users/me` | Get own profile (roles + permissions) |
| PUT | `/api/v1/users/me` | Update own display name |
| DELETE | `/api/v1/users/me` | Soft-deactivate own account |
| GET | `/api/v1/users/` | List users, paginated (**admin**, own tenant unless super_admin) |
| GET | `/api/v1/users/{user_id}` | Get a specific user (**admin**) |
| POST | `/api/v1/users/{user_id}/roles` | Assign a role to a user (**admin**) |
| DELETE | `/api/v1/users/{user_id}/roles/{role_name}` | Revoke a role from a user (**admin**) |
| PATCH | `/api/v1/users/{user_id}/status` | Activate/deactivate a user (**admin**) |
| PATCH | `/api/v1/users/{user_id}/roles` | Replace a user's full role set (**admin**; only super_admin grants super_admin/admin) |
| POST | `/api/v1/users/{user_id}/project-assignments` | Assign a user as Member to one or more projects (**admin**) |
| DELETE | `/api/v1/users/{user_id}` | Permanently remove a user, incl. Cognito identity (**admin**) |

#### Tenants (`/api/v1/tenants`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/tenants/me` | Get the tenant assigned to the current user |
| GET | `/api/v1/tenants/` | List tenants (**super_admin**: all; **admin**: own only) |
| POST | `/api/v1/tenants/` | Create a tenant (**super_admin**) |
| GET | `/api/v1/tenants/{tenant_id}` | Get a tenant by ID (**super_admin**: any; **admin**: own) |
| PATCH | `/api/v1/tenants/{tenant_id}` | Update a tenant (**super_admin**) |
| DELETE | `/api/v1/tenants/{tenant_id}` | Soft-deactivate a tenant (**super_admin**) |
| GET | `/api/v1/tenants/{tenant_id}/llm-providers` | List tenant LLM provider config (**admin/super_admin**) |
| PATCH | `/api/v1/tenants/{tenant_id}/llm-providers/{provider}` | Enable/disable a provider and/or set its API key (**admin/super_admin**) |
| POST | `/api/v1/tenants/{tenant_id}/llm-providers/{provider}/test` | Test the stored API key against the live provider (**admin/super_admin**) |

#### Tenant Invitations (`/api/v1/tenants/{tenant_id}/invitations`, `/api/v1/invitations`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/tenants/{tenant_id}/invitations` | Invite a user into a tenant with a role (**admin/super_admin**) |
| GET | `/api/v1/tenants/{tenant_id}/invitations` | List a tenant's invitations, filterable by status (**admin/super_admin**) |
| POST | `/api/v1/tenants/{tenant_id}/invitations/{invitation_id}/resend` | Resend an invitation with a fresh token/expiry (**admin/super_admin**) |
| POST | `/api/v1/tenants/{tenant_id}/invitations/{invitation_id}/revoke` | Revoke a pending/expired invitation (**admin/super_admin**) |
| GET | `/api/v1/invitations/{token}` | Validate an invitation token, return prefill details (public) |
| POST | `/api/v1/invitations/{token}/accept` | Accept an invitation by setting a password (public) |

#### Roles & Permissions (`/api/v1/roles`, `/api/v1/permissions`) — all super_admin only

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/roles` | List all roles with their permissions |
| POST | `/api/v1/roles` | Create a custom role with chosen permissions |
| GET | `/api/v1/roles/{role_id}` | Get a role by ID |
| PATCH | `/api/v1/roles/{role_id}` | Update a role's description/permissions |
| DELETE | `/api/v1/roles/{role_id}` | Delete a custom role (built-in roles are immutable) |
| GET | `/api/v1/permissions` | List the fixed permission catalogue |

#### Projects (`/api/v1/projects`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/projects` | Create a new project |
| GET | `/api/v1/projects` | List projects owned by the current user |
| GET | `/api/v1/projects/dashboard/stats` | Aggregate dashboard statistics |
| GET | `/api/v1/projects/all` | List all projects (**admin**) |
| GET | `/api/v1/projects/{project_id}` | Get project details |
| PATCH | `/api/v1/projects/{project_id}` | Update project metadata (owner or **admin**) |
| DELETE | `/api/v1/projects/{project_id}` | Delete a project (owner or **admin**) |

#### Project Members, Tasks & Pipelines

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/projects/{project_id}/members` | Assign a Member to a project (**admin/super_admin**) |
| GET | `/api/v1/projects/{project_id}/members` | List a project's members (**admin/super_admin**) |
| DELETE | `/api/v1/projects/{project_id}/members/{user_id}` | Remove a user's project membership (**admin/super_admin**) |
| GET | `/api/v1/projects/{project_id}/tasks` | List background tasks for a project, with event history |
| DELETE | `/api/v1/projects/{project_id}/tasks` | Cancel every active background task for a project |
| DELETE | `/api/v1/tasks/{task_id}` | Cancel a single background task |
| DELETE | `/api/v1/tasks/requests/{request_id}` | Cancel a task by its request ID |
| GET | `/api/v1/projects/me/pipelines` | List `SourceIngestion` pipelines across the user's own projects (status/type/search filters) |

#### Sources (`/api/v1/sources`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/sources/upload/bulk` | Upload multiple source files (creates a `SourceIngestion` batch, returns `batch_id` + per-file results + `task_id`) |
| POST | `/api/v1/sources/upload/bulk/incremental` | Upload files to trigger an incremental backlog update |
| POST | `/api/v1/sources/upload/link` | Fetch and store a file from a URL (GitHub, SharePoint, Nextcloud) |
| GET | `/api/v1/sources` | List sources for a project |
| GET | `/api/v1/sources/ingestion` | List upload-batch (`SourceIngestion`) rollups for a project |
| GET | `/api/v1/sources/download/{source_id}` | Download source file from S3 |
| GET | `/api/v1/sources/download/local/{source_id}` | Download source file from local storage |
| DELETE | `/api/v1/sources/delete/bulk` | Bulk soft-delete multiple sources |
| GET | `/api/v1/sources/{source_id}` | Get source details |
| DELETE | `/api/v1/sources/{source_id}` | Soft-delete a source |

#### Fragments

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/projects/{project_id}/fragments` | List all fragments in a project |
| GET | `/api/v1/projects/{project_id}/fragments/{fragment_id}` | Get a specific fragment |
| PATCH | `/api/v1/sources/{source_id}/fragments/{fragment_id}/bbox` | Update fragment bounding box |

#### Modules & Features

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/projects/{project_id}/modules` | Paginated module hierarchy with features |
| GET | `/api/v1/projects/{project_id}/modules/list` | Module → feature → function tree view |
| GET | `/api/v1/projects/{project_id}/modules/{module_id}` | Get a specific module |
| GET | `/api/v1/projects/{project_id}/modules/{module_id}/features/{feature_id}` | Get a specific feature |
| PATCH | `/api/v1/projects/{project_id}/modules/{module_id}/sync-status` | Update Jira/TAP sync flags for a module |
| PATCH | `/api/v1/projects/{project_id}/modules/{module_id}/features/{feature_id}/sync-status` | Update Jira/TAP sync flags for a feature |
| PATCH | `/api/v1/projects/{project_id}/modules/status` | Change module/feature status — approving auto-triggers user-story generation |
| POST | `/api/v1/projects/{project_id}/modules/regenerate` | Enqueue module/feature regeneration (returns `task_id`) |

#### User Stories

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/projects/{project_id}/user-stories` | List user stories for a project |
| DELETE | `/api/v1/projects/{project_id}/user-stories` | Delete all user stories for a project |
| GET | `/api/v1/projects/{project_id}/user-stories/list/tree` | Tree view of user stories |
| GET | `/api/v1/projects/{project_id}/user-stories/summary` | User story count summary |
| GET | `/api/v1/projects/{project_id}/user-stories/{user_story_id}` | Get a specific user story |
| DELETE | `/api/v1/projects/{project_id}/user-stories/{user_story_id}` | Delete a user story |
| PATCH | `/api/v1/projects/{project_id}/user-stories/{user_story_id}/bboxes` | Update user story bounding boxes |
| PATCH | `/api/v1/user-stories/{user_story_id}/status` | Update a single user story's status |
| PATCH | `/api/v1/user-stories/{user_story_id}/sync-status` | Update Jira/TAP sync flags for a user story |
| PATCH | `/api/v1/projects/{project_id}/user-stories/status` | Update status by project (**approve** access) |
| PATCH | `/api/v1/projects/{project_id}/user-stories/bulk-status` | Bulk-update status for specific user stories (**approve** access) |
| POST | `/api/v1/projects/{project_id}/user-stories/regenerate` | Enqueue user-story regeneration (returns `task_id`) |
| POST | `/api/v1/projects/{project_id}/user-stories/regenerate-by-feedback` | Enqueue AI-assisted patching of specific stories from reviewer feedback |
| POST | `/api/v1/projects/{project_id}/user-stories/regenerate-for-source-code` | Regenerate source-code-derived features and their user stories from feedback |

#### Incremental Updates

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/projects/{project_id}/incremental-updates/latest` | Module→feature→user story tree overlaid with the latest pending incremental proposal |
| GET | `/api/v1/projects/{project_id}/updates/list` | Tree pruned to only `incremental_change_type`-flagged nodes |
| POST | `/api/v1/projects/{project_id}/incremental-updates/accept` | Accept a pending incremental change for a node |
| POST | `/api/v1/projects/{project_id}/incremental-updates/reject` | Reject a pending incremental change for a node |
| POST | `/api/v1/projects/{project_id}/updates/accept` | Unified accept — dispatches to the incremental or feedback-driven flow, whichever flagged the node |
| POST | `/api/v1/projects/{project_id}/updates/reject` | Unified reject — same dispatch as above |

#### Notifications (`/api/v1/notifications`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/notifications` | List the authenticated user's notification feed |
| GET | `/api/v1/notifications/unread-count` | Get the bell-icon unread badge count |
| PATCH | `/api/v1/notifications/read-all` | Mark every unread notification as read |
| PATCH | `/api/v1/notifications/{notification_id}/read` | Mark a single notification as read |

#### Jira Integration (`/api/v1/projects/{project_id}/integrations/jira`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/projects/{project_id}/integrations/jira` | Create the project's Jira integration config |
| GET | `/api/v1/projects/{project_id}/integrations/jira` | Get the Jira integration config |
| PATCH | `/api/v1/projects/{project_id}/integrations/jira` | Update the Jira integration config |
| DELETE | `/api/v1/projects/{project_id}/integrations/jira` | Deactivate the Jira integration |
| POST | `/api/v1/projects/{project_id}/integrations/jira/test-existing` | Test the stored Jira connection |
| GET | `/api/v1/projects/{project_id}/integrations/jira/issue-types` | List Jira issue types |
| GET | `/api/v1/projects/{project_id}/integrations/jira/sync/preview` | Preview the Jira sync diff (new/changed/unchanged) |
| POST | `/api/v1/projects/{project_id}/integrations/jira/sync` | Execute the Jira sync (push Features as Epics, User Stories as issues) |
| GET | `/api/v1/projects/{project_id}/integrations/jira/sync/history` | List Jira sync history |
| GET | `/api/v1/projects/{project_id}/integrations/jira/sync/history/{sync_id}` | Get a Jira sync run's detail |

#### TAP Integration (`/api/v1/projects/{project_id}/integrations/tap`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/projects/{project_id}/integrations/tap/sync` | Stage a TAP sync and best-effort notify TAP to pull it |
| GET | `/api/v1/projects/{project_id}/integrations/tap/sync/{sync_id}/data` | *Inbound* — TAP pulls the staged payload (shared-secret `X-TAP-Signature`, not user auth) |
| POST | `/api/v1/projects/{project_id}/integrations/tap/sync/{sync_id}/ack` | *Inbound* — TAP acknowledges processing, flips `is_tap_synced` flags (shared-secret, not user auth) |

#### Export

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/projects/{project_id}/export` | Stream a ZIP of the requirements backlog (JSON/PDF) and/or SRS spec files, plus a manifest |

#### Settings & Observability

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/settings` | Get shared application settings |
| GET | `/api/v1/queue/status` | Celery queue depth / worker status (**admin**) |
| GET | `/api/v1/queue/dashboard` | Condensed queue/worker dashboard with stalled-queue alerts (**super_admin**) |
| GET | `/api/v1/queue/dead-letter` | List failed/rejected tasks from the dead-letter queue (**admin**) |
| POST | `/api/v1/queue/dead-letter/replay` | Re-queue a failed task for retry (**admin**) |

### WebSocket endpoints

| Path | Description |
|------|-------------|
| `/ws/projects/{project_id}?token=<jwt>` | Real-time task events (source processing, module/feature generation, user-story generation/regeneration) for **one** project |
| `/ws/projects/pipelines?token=<jwt>` | Same `task.update` events, fanned out across **every** project the connected user owns — dashboard/project-list view, one connection instead of one-per-project |
| `/ws/notifications?token=<jwt>` | In-app notification bell feed for the authenticated user |

**WebSocket real-time flow (per-project or cross-project task events):**

```
# Any regeneration or processing task
1. Trigger an action (e.g. POST /api/v1/projects/{project_id}/modules/regenerate)
   → Response: { "task_id": "abc-123", ... }
2. Connect: wss://api/ws/projects/{project_id}?token=<jwt>
  (or wss://api/ws/projects/pipelines?token=<jwt> for the cross-project feed,
   or with an `access_token` cookie instead of the query param)
3. First frame:
  { "event": "tasks.current", "tasks": [ ...active tasks... ] }
4. Progress/status frames:
  { "event": "task.update", "task_id": "abc-123", "project_id": "...", "status": "...", "progress": 0-100, "stage": "..." }
5. Keepalive every 30s:
  { "event": "heartbeat", "timestamp": "ISO-8601" }

Close codes:
- 4001 unauthorized (missing/invalid/expired token)
- 4004 project/user not found or invalid project_id
```

`/ws/notifications` follows the same auth/heartbeat/close-code shape, with `notifications.current` / `notification.new` / `notification.read` / `notification.read_all` frames instead of `tasks.current` / `task.update`. See Swagger's WebSocket path docs for full per-`task_type` status-value tables and message schemas.

---

## Key Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `APP_ENV` | ✅ | — | `development` \| `staging` \| `production` |
| `SECRET_KEY` | ✅ | — | Min 32-character secret for internal signing |
| `DEBUG` | | `false` | Enable debug mode |
| `CORS_ORIGINS` | | `["http://localhost:5173"]` | JSON array of allowed CORS origins |
| `COOKIE_SAMESITE` | | `none` | Cookie `SameSite` policy |
| `COOKIE_SECURE` | | `true` | Require `Secure` flag on cookies |
| `POSTGRES_HOST` | ✅ | — | PostgreSQL hostname |
| `POSTGRES_PORT` | | `5432` | PostgreSQL port |
| `POSTGRES_DB` | ✅ | — | Database name |
| `POSTGRES_USER` | ✅ | — | Database username |
| `POSTGRES_PASSWORD` | ✅ | — | Database password |
| `DB_AUTO_CREATE` | | `false` | Create tables directly instead of via migrations — local bootstrap only |
| `NEO4J_URI` | ✅ | — | Neo4j Bolt URI e.g. `bolt://localhost:7687` |
| `NEO4J_USER` | ✅ | — | Neo4j username |
| `NEO4J_PASSWORD` | ✅ | — | Neo4j password |
| `REDIS_URL` | ✅ | — | Redis URL e.g. `redis://localhost:6379/0` (WS pub/sub) |
| `CELERY_BROKER_URL` | ✅ | — | Celery broker URL (Redis DB 1 recommended) |
| `CELERY_RESULT_BACKEND` | ✅ | — | Celery result backend URL (Redis DB 2 recommended) |
| `CELERY_BROKER_SOCKET_TIMEOUT` | | `None` | Optional broker socket read timeout in seconds; empty/`0` disables |
| `CELERY_RESULT_SOCKET_TIMEOUT` | | `None` | Optional result-backend socket read timeout in seconds; empty/`0` disables |
| `CELERY_BROKER_SOCKET_CONNECT_TIMEOUT` | | `30` | Broker socket connect timeout in seconds |
| `CELERY_BROKER_HEALTH_CHECK_INTERVAL` | | `30` | Broker Redis health-check interval in seconds |
| `SEED_SUPER_ADMIN_ENABLED` | | `true` | Idempotent Super Admin bootstrap on startup (no tenant assigned) |
| `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` / `SUPER_ADMIN_NAME` | | — | Super Admin bootstrap credentials — leave empty to disable seeding |
| `AWS_REGION` | ✅ | `us-east-1` | AWS region |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | | — | AWS credentials (omit on AWS infra — IAM role used) |
| `AWS_COGNITO_USER_POOL_ID` | ✅ | — | Cognito User Pool ID |
| `AWS_COGNITO_CLIENT_ID` | ✅ | — | Cognito App Client ID |
| `AWS_COGNITO_CLIENT_SECRET` | | — | Cognito App Client Secret (if app client has a secret) |
| `AWS_SQS_QUEUE_URL` | ✅ | — | SQS main queue URL |
| `AWS_SQS_DLQ_URL` | ✅ | — | SQS dead-letter queue URL |
| `AWS_SES_SENDER_EMAIL` | | — | SES sender address for invitation/notification emails |
| `FRONTEND_BASE_URL` | ✅ | — | Frontend origin used to build invitation-accept links |
| `INVITATION_EXPIRY_DAYS` | | `7` | Days a tenant invitation stays valid before expiring |
| `AWS_S3_SOURCES_BUCKET` | | `rip-development-s3-static` | S3 bucket for source files |
| `AWS_S3_PRESIGNED_URL_EXPIRY` | | `3600` | Presigned URL TTL in seconds |
| `S3_DOWNLOAD_CHUNK_SIZE_MB` | | `5` | Streaming chunk size for S3 downloads (MB) |
| `SOURCE_MAX_FILE_SIZE_MB` | | `500` | Maximum upload size per file (MB) |
| `SOURCE_LINK_MAX_FILE_SIZE_MB` | | `500` | Maximum file size for link-based uploads (MB) |
| `SOURCE_BULK_MAX_FILES` | | `20` | Maximum number of files per bulk upload |
| `SOURCE_BULK_MAX_TOTAL_SIZE_MB` | | `2000` | Maximum combined size of one bulk upload (MB) |
| `SOURCE_LOCAL_DOWNLOAD_DIR` | | `temp/sources` | Local staging directory for link downloads |
| `LLAMA_CLOUD_API_KEY` | ✅ | — | LlamaParse Cloud API key |
| `OMNIPARSER_BASE_URL` | ✅ | — | OmniParser service base URL |
| `OMNIPARSER_TIMEOUT_SECONDS` | | `300` | OmniParser request timeout (seconds) |
| `OMNIPARSER_MAX_INPUT_LENGTH` | | `8000` | Maximum input length for OmniParser |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / `DEEPSEEK_API_KEY` | | — | LLM provider keys — only the key(s) for active providers are required |
| `OPENAI_EMBEDDING_MODEL` | | `text-embedding-3-small` | OpenAI model used for fragment embeddings |
| `EMBEDDING_VECTOR_DIM` | | `1536` | pgvector column dimension — must match the embedding model's output size |
| `ALT_ARCHITECT_MODEL_PROVIDER` / `ALT_ARCHITECT_MODEL_NAME` | | `anthropic` / `claude-sonnet-4-6` | Generator agent for module/feature & backlog pipelines |
| `ALT_CRITIC_MODEL_PROVIDER` / `ALT_CRITIC_MODEL_NAME` | | `anthropic` / `claude-sonnet-4-6` | Critic agent for module/feature & backlog pipelines |
| `ALT_ARCHITECT_TEMPERATURE` / `ALT_CRITIC_TEMPERATURE` | | `0.0` / `0.0` | Sampling temperature for the generator/critic agents |
| `ALT_MAX_TOKENS` | | `384000` | Max output tokens per generator/critic LLM call |
| `ALT_MAX_CRITIC_ITERATIONS` | | `1` | Max generate↔critic retries for module/feature & backlog graphs |
| `ALT_MAX_PATCH_CRITIC_ITERATIONS` | | `2` | Max generate↔critic retries for the story-feedback-patch graph |
| `ALT_MAX_INCREMENTAL_CRITIC_ITERATIONS` | | `2` | Max generate↔critic retries for the incremental-update graph |
| `ALT_MAX_SELECTOR_CRITIC_ITERATIONS` | | `2` | Max generate↔critic retries for the incremental-selector graph |
| `IMAGE_EXTRACTION_PROVIDER` / `IMAGE_EXTRACTION_MODEL_NAME` | | `google` / `gemini-pro-latest` | Single-pass image fragment extraction |
| `IMAGE_EXTRACTION_MAX_TOKENS` | | `64000` | Max output tokens for image extraction |
| `JIRA_ENCRYPTION_KEY` | ✅* | — | Fernet key (44-char) encrypting stored Jira API tokens — required once any project enables Jira sync |
| `LLM_ENCRYPTION_KEY` | ✅* | — | Fernet key (44-char) encrypting tenant LLM provider API keys — required once any tenant enables its own provider |
| `TAP_BASE_URL` | | — | TAP platform base URL — empty disables TAP sync |
| `TAP_API_KEY` / `TAP_APP_CLIENT_ID` | | — | TAP platform credentials |
| `RIP_PUBLIC_BASE_URL` | | — | Publicly reachable RIP base URL, included in the pull-back URL sent to TAP; omitted if empty |
| `TAP_ACK_SECRET` | | — | Shared secret validated via `X-TAP-Signature` on TAP's inbound pull/ack callbacks — empty disables the check |
| `JIRA_SYNC_REQUEST_DELAY_MS` / `TAP_SYNC_REQUEST_DELAY_MS` | | `100` / `100` | Throttle delay between per-entity requests during sync |
| `RATE_LIMIT_AUTH_REGISTER` | | `10/minute` | Rate limit for registration endpoint |
| `RATE_LIMIT_AUTH_CONFIRM` | | `10/minute` | Rate limit for confirmation endpoint |
| `RATE_LIMIT_AUTH_LOGIN` | | `20/minute` | Rate limit for login endpoint |
| `RATE_LIMIT_AUTH_REFRESH` | | `30/minute` | Rate limit for token refresh endpoint |
| `RATE_LIMIT_AUTH_FORGOT_PASSWORD` / `RATE_LIMIT_AUTH_RESET_PASSWORD` | | `5/minute` / `10/minute` | Rate limits for the password-reset flow |
| `RATE_LIMIT_SOURCE_UPLOAD` | | `20/minute` | Rate limit for source upload endpoints |
| `RATE_LIMIT_SOURCE_DOWNLOAD` | | `60/minute` | Rate limit for source download endpoints |
| `RATE_LIMIT_SOURCE_DELETE` | | `30/minute` | Rate limit for single/bulk source delete endpoints |
| `RATE_LIMIT_AI_REGENERATE` | | `10/minute` | Rate limit for module/user-story regeneration endpoints |
| `RATE_LIMIT_JIRA_SYNC` / `RATE_LIMIT_JIRA_CONFIG` | | `5/minute` / `10/minute` | Rate limits for Jira sync execution / config mutation |
| `RATE_LIMIT_TAP_SYNC` | | `5/minute` | Rate limit for TAP sync execution |
| `RATE_LIMIT_TENANT_LLM_PROVIDER_TEST` / `RATE_LIMIT_TENANT_LLM_PROVIDER_CONFIG` | | `10/minute` / `10/minute` | Rate limits for tenant LLM provider test / config mutation |
| `RATE_LIMIT_EXPORT` | | `10/minute` | Rate limit for the backlog/SRS export endpoint |
| `RATE_LIMIT_INVITATION_RESEND` | | `5/minute` | Rate limit for resending a tenant invitation |

\* Only required once the corresponding feature (Jira sync / tenant-managed LLM keys) is actually used — leaving it unset raises a `ServiceError` at the point of use, not at startup.

See `.env.example` in the repo root for the complete, copy-pasteable reference — it is the authoritative source and is kept in sync with `app/core/config.py`.

---

## Troubleshooting

### Docker Compose — port already in use

```bash
# Find the conflicting process
sudo lsof -i :5432   # or :7474, :7687, :6379, :8000
# Kill it or change the host port mapping in docker-compose.yml
```

### Neo4j fails to start — APOC plugin missing

Ensure `NEO4J_PLUGINS=["apoc"]` is set in the `neo4j` service environment inside `docker-compose.yml`. The plugin is downloaded automatically on first start; an internet connection is required.

### Alembic — "Target database is not up to date"

```bash
alembic upgrade head
```

If the revision chain is broken after a branch merge, run:

```bash
alembic history --verbose   # identify the divergence
alembic upgrade head        # resolves merge heads automatically
```

### Celery worker not picking up tasks

1. Confirm the broker URL matches your Redis instance (`CELERY_BROKER_URL`).
2. Ensure the worker was started with the correct `-Q` queue name(s) — see [Queue routing](#queue-routing).
3. Check for import errors on worker startup — they silently prevent task registration:
   ```bash
   celery -A app.core.celery_app.celery_app inspect registered
   ```

### Celery Redis timeout/reconnect loop

If logs show `consumer: Connection to broker lost` with `redis.exceptions.TimeoutError: Timeout reading from socket`:

1. Disable broker/result read timeouts (recommended for managed Redis / lower traffic):
  ```env
  CELERY_BROKER_SOCKET_TIMEOUT=
  CELERY_RESULT_SOCKET_TIMEOUT=
  ```
  (`0` also disables it.)
2. Keep connect timeout and health check enabled:
  ```env
  CELERY_BROKER_SOCKET_CONNECT_TIMEOUT=30
  CELERY_BROKER_HEALTH_CHECK_INTERVAL=30
  ```
3. Restart the worker deployment so the new settings take effect.

### LLM API errors (rate limits / quota)

- Verify the correct API key is set for the active provider (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`).
- Tasks automatically retry up to 3 times with exponential back-off (60 s → 120 s → 240 s).
- Check the source row's `status` column and `project_task_events` table for the recorded error message.

### Cancelling a task doesn't stop it immediately

`DELETE /projects/{project_id}/tasks`, `/tasks/{task_id}`, and `/tasks/requests/{request_id}` only set a Redis cancellation flag (`app/core/task_control.py`) — the worker pool (`-P threads`) can't be hard-killed, so a task only stops once it reaches its next checkpoint (task entry, per-module, per-MFU, per generate/critic cycle). Long single LLM calls in progress will finish that call before the next check. If cancellation appears to do nothing at all, confirm `REDIS_URL` is reachable from the worker — `is_request_cancelled` fails open (returns `False`) on a Redis error.

### TAP inbound callback returns 401/403

`GET /integrations/tap/sync/{sync_id}/data` and `POST /integrations/tap/sync/{sync_id}/ack` are called by TAP itself, not by an authenticated RIP user — they're guarded by a shared secret in the `X-TAP-Signature` header instead of a JWT. Confirm `TAP_ACK_SECRET` is set identically on both sides; leaving it empty on the RIP side disables the check entirely (fine for local testing, not for production).

### Jira/TAP sync fails with a decryption error

`JIRA_ENCRYPTION_KEY` / `LLM_ENCRYPTION_KEY` must be a valid 44-character Fernet key and must not change after integrations/tenant provider keys have been saved — rotating the key without re-encrypting existing rows makes `app/utils/encryption.py` raise on decrypt. Generate a new key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

### pgvector extension missing

Connect to PostgreSQL and run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

The Docker Compose `postgres` service uses `pgvector/pgvector:pg17`, which ships the extension pre-installed.

### Tests failing with import errors

Ensure the virtual environment is activated and all dependencies are installed:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

---

## Contributing

This is an internal BJIT project. Contributions follow the standard feature-branch workflow:

1. **Branch** — create a branch from `develop`: `git checkout -b feature/<short-description>`
2. **Code** — implement your changes; keep commits atomic and use [Conventional Commits](https://www.conventionalcommits.org/) prefixes (`feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`)
3. **Tests** — add or update unit tests; run the suite before raising a PR:
   ```bash
   python -m pytest -q
   ```
4. **Pull Request** — open a PR against `develop`; request at least one reviewer
5. **No force-push** to `main` or `develop`; use merge commits only

### Coding conventions

| Topic | Convention |
|-------|-----------|
| Formatter | `ruff format` |
| Linter | `ruff check` |
| Type hints | Required on all public function signatures |
| Async | Prefer `async/await`; use `asyncio.to_thread` for blocking I/O |
| Imports | Absolute imports only; no star imports |
| Secrets | Never commit secrets; use `.env` (git-ignored) |

---

## License

Copyright © 2024–2026 BJIT Limited. All rights reserved.

This software is proprietary and confidential. Unauthorised copying, distribution, or use of any part of this software, via any medium, is strictly prohibited without the express written permission of BJIT Limited.
