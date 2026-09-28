"""OpenAPI schema customisation utilities.

Provides ``build_custom_openapi`` which returns a callable suitable for
assigning to ``app.openapi``.  The returned function:

- Replaces FastAPI's default ``HTTPValidationError`` 422 response schema
  with the project-wide ``ErrorResponse`` envelope so Swagger always renders
  a consistent error shape.
- Removes stale FastAPI validation-error component schemas that would
  otherwise appear as dead entries in the spec.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi.openapi.utils import get_openapi

from app.core.enums.source_type import SOURCE_TYPE_VALUES
from app.core.error_response import ErrorResponse
from app.core.messages import (
    DESC_SOURCE_DESCRIPTION,
    DESC_SOURCE_FILES,
    DESC_SOURCE_PROJECT_ID,
    DESC_SOURCE_TYPE,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

_ERROR_SCHEMA_NAME = "ErrorResponse"
_CONTENT_JSON = "application/json"
_WS_EVENT_TASKS_CURRENT = "tasks.current"
_WS_EVENT_TASK_UPDATE = "task.update"
_WS_EVENT_HEARTBEAT = "heartbeat"
_WS_EVENT_NOTIFICATIONS_CURRENT = "notifications.current"
_WS_EVENT_NOTIFICATION_NEW = "notification.new"
_WS_EXAMPLE_NOTIFICATION_ID = "b2c3d4e5-f6a7-8901-bcde-f01234567890"
_WS_EXAMPLE_USER_ID = "c3d4e5f6-a7b8-9012-cdef-012345678901"
_WS_EXAMPLE_NOTIF_TITLE = "Source Processing Complete"
_WS_EXAMPLE_NOTIF_MSG = "3 files were processed successfully."
_WS_STAGE_SOURCE_QUEUED = "source.process.queued"
_WS_STAGE_PARSING_STARTED = "documents.parsing.started"
_WS_STAGE_PARSING_COMPLETED = "documents.parsing.completed"
_WS_EXAMPLE_TIMESTAMP = "2026-05-08T04:14:39.008148+00:00"
_WS_EXAMPLE_TS_START = "2026-05-08T04:00:00.000000+00:00"
_WS_EXAMPLE_TS_MID = "2026-05-08T04:01:00.000000+00:00"
_WS_EXAMPLE_TS_END = "2026-05-08T04:05:00.000000+00:00"
_WS_EXAMPLE_TASK_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_WS_EXAMPLE_CELERY_ID = "d4e5f6a7-b8c9-0123-def0-456789abcdef"
_WS_PARAM_TOKEN_DESC = "JWT access token (alternative to access_token cookie)"
_WS_DESC_UNAUTHORIZED = "Unauthorized — missing or invalid JWT"
_WS_HEARTBEAT_SUMMARY = "Keepalive heartbeat"
_REF_WS_HEARTBEAT = "#/components/schemas/WsHeartbeat"
_WS_EXAMPLE_PROJECT_ID = "35fb2f8c-53de-4fd8-82d3-3de2412474a7"
_BULK_DEFAULT_DESCRIPTION = "The test description"
_BULK_DEFAULT_SOURCE_LANGUAGE = "pb"
_BULK_DEFAULT_FRONTEND_STACK = "React/TypeScript"
_BULK_DEFAULT_BACKEND_STACK = "Java Spring Boot"
_BULK_DEFAULT_INFRA_STACK = "AWS Cloud"
_BULK_DEFAULT_ARCHITECTURE_STACK = "Monolithic"
_BULK_DEFAULT_DATABASE_STACK = "PostgreSQL"

# ---------------------------------------------------------------------------
# Route-level OpenAPI overrides
# ---------------------------------------------------------------------------

#: Explicit ``multipart/form-data`` schema for ``POST /sources/upload/bulk``.
#: FastAPI cannot infer the array-of-binary shape from ``list[UploadFile]``
#: when the handler is registered via ``add_api_route``, so we provide the
#: full OpenAPI request-body object here.
BULK_UPLOAD_OPENAPI_EXTRA: dict = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "example": {
                    "project_id": "00000000-0000-0000-0000-000000000000",
                    "source_type": "rfp",
                    "description": _BULK_DEFAULT_DESCRIPTION,
                    "source_language": _BULK_DEFAULT_SOURCE_LANGUAGE,
                    "frontend_stack": _BULK_DEFAULT_FRONTEND_STACK,
                    "backend_stack": _BULK_DEFAULT_BACKEND_STACK,
                    "infrastructure_stack": _BULK_DEFAULT_INFRA_STACK,
                    "architecture_stack": _BULK_DEFAULT_ARCHITECTURE_STACK,
                    "database_stack": _BULK_DEFAULT_DATABASE_STACK,
                    "skip_processing": "False",
                },
                "schema": {
                    "type": "object",
                    "required": ["files", "project_id", "source_type"],
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": DESC_SOURCE_FILES,
                        },
                        "project_id": {
                            "type": "string",
                            "format": "uuid",
                            "description": DESC_SOURCE_PROJECT_ID,
                        },
                        "source_type": {
                            "type": "string",
                            "enum": list(SOURCE_TYPE_VALUES),
                            "example": "rfp",
                            "description": DESC_SOURCE_TYPE,
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 2000,
                            "default": _BULK_DEFAULT_DESCRIPTION,
                            "example": _BULK_DEFAULT_DESCRIPTION,
                            "description": DESC_SOURCE_DESCRIPTION,
                        },
                        "source_language": {
                            "type": "string",
                            "maxLength": 255,
                            "default": _BULK_DEFAULT_SOURCE_LANGUAGE,
                            "example": _BULK_DEFAULT_SOURCE_LANGUAGE,
                            "description": "Primary source language (e.g. Power Builder(pb), Visual Basic 6(vb6), Cobol(cobol), Java, Python, TypeScript)",
                        },
                        "frontend_stack": {
                            "type": "string",
                            "maxLength": 255,
                            "default": _BULK_DEFAULT_FRONTEND_STACK,
                            "example": _BULK_DEFAULT_FRONTEND_STACK,
                            "description": "Frontend technology stack (e.g. React/TypeScript, Angular/TypeScript, Vue/JavaScript)",
                        },
                        "backend_stack": {
                            "type": "string",
                            "maxLength": 255,
                            "default": _BULK_DEFAULT_BACKEND_STACK,
                            "example": _BULK_DEFAULT_BACKEND_STACK,
                            "description": "Backend technology stack (e.g. Java Spring Boot, Node.js, Python Django, Ruby on Rails, PHP Laravel, C# .NET Core)",
                        },
                        "infrastructure_stack": {
                            "type": "string",
                            "maxLength": 255,
                            "default": _BULK_DEFAULT_INFRA_STACK,
                            "example": _BULK_DEFAULT_INFRA_STACK,
                            "description": "Infrastructure / cloud platform (e.g. AWS Cloud, Azure, Google Cloud)",
                        },
                        "architecture_stack": {
                            "type": "string",
                            "maxLength": 255,
                            "default": _BULK_DEFAULT_ARCHITECTURE_STACK,
                            "example": _BULK_DEFAULT_ARCHITECTURE_STACK,
                            "description": "Architecture pattern (e.g. Monolithic, Modular Monolithic, Microservices)",
                        },
                        "database_stack": {
                            "type": "string",
                            "maxLength": 255,
                            "default": _BULK_DEFAULT_DATABASE_STACK,
                            "example": _BULK_DEFAULT_DATABASE_STACK,
                            "description": "Database technology (e.g. PostgreSQL, MySQL, MongoDB, Oracle, SQL Server)",
                        },
                        "coding_standard": {
                            "type": "string",
                            "maxLength": 255,
                            "description": "Coding standard / style guide applied to the source",
                        },
                        "database_strategy": {
                            "type": "string",
                            "maxLength": 255,
                            "description": "Database access / replication strategy",
                        },
                        "architecture": {
                            "type": "string",
                            "maxLength": 255,
                            "description": "Architecture approach / guidance (e.g. Hexagonal Architecture)",
                        },
                        "security": {
                            "type": "string",
                            "maxLength": 255,
                            "description": "Security mechanism (e.g. OAuth2 + JWT)",
                        },
                        "source_layout_type": {
                            "type": "string",
                            "enum": ["modular", "flat", "auto"],
                            "example": "modular",
                            "description": (
                                "Layout shape of the analyzed source code. Send the value "
                                "shown after '=>': Modular => modular, Non Modular => flat, "
                                "Unknown => auto. Only applies when source_type is "
                                "'source_code'; stored as NULL for any other source_type."
                            ),
                        },
                        "skip_processing": {
                            "type": "string",
                            "enum": ["True", "False"],
                            "default": "False",
                            "example": "False",
                            "examples": ["True", "False"],
                            "description": (
                                "Skip full pipeline processing and use sample artifacts/module results when available. "
                                "Send as string: True or False. Default is False."
                            ),
                        },
                        "is_incremental": {
                            "type": "string",
                            "enum": ["True", "False"],
                            "default": "False",
                            "example": "False",
                            "description": (
                                "When True, routes the upload through the incremental update pipeline "
                                "(same behaviour as POST /sources/upload/bulk/incremental). "
                                "Send as string: True or False. Default is False."
                            ),
                        },
                        "user_message": {
                            "type": "string",
                            "maxLength": 10000,
                            "default": "",
                            "example": "",
                            "description": (
                                "Optional user message providing additional context for the incremental update. "
                                "Only used when is_incremental is True."
                            ),
                        },
                        "context_mode": {
                            "type": "string",
                            "enum": ["full", "subset"],
                            "default": "full",
                            "example": "full",
                            "description": (
                                "Backlog context given to the incremental update LLM. 'full' sends the "
                                "complete backlog; 'subset' sends a selector-filtered slice. Only used "
                                "when is_incremental is True. Default is 'full'."
                            ),
                        },
                    },
                },
            }
        },
    }
}
#: Response example for ``GET /projects/{project_id}/modules/list``.
#: Provides a concrete tree-shaped example in Swagger UI.
#: Explicit ``multipart/form-data`` schema for ``POST /sources/upload/bulk/incremental``.
#: FastAPI cannot infer the array-of-binary shape from ``list[UploadFile]``
#: when the handler is registered via ``add_api_route``, so we provide the
#: full OpenAPI request-body object here.
INCREMENTAL_BULK_UPLOAD_OPENAPI_EXTRA: dict = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["files", "project_id", "source_type"],
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "PDF and/or image files (JPEG, PNG, WebP) to parse and use for the incremental backlog update.",
                        },
                        "project_id": {
                            "type": "string",
                            "format": "uuid",
                            "description": DESC_SOURCE_PROJECT_ID,
                        },
                        "source_type": {
                            "type": "string",
                            "enum": list(SOURCE_TYPE_VALUES),
                            "example": "rfp",
                            "description": DESC_SOURCE_TYPE,
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 2000,
                            "description": DESC_SOURCE_DESCRIPTION,
                        },
                        "user_message": {
                            "type": "string",
                            "maxLength": 10000,
                            "default": "",
                            "description": "Optional user message providing additional context for the incremental update.",
                        },
                        "skip_processing": {
                            "type": "string",
                            "enum": ["True", "False"],
                            "default": "False",
                            "example": "False",
                            "description": (
                                "Skip the AI pipeline and return cached output when available. "
                                "Send as string: True or False. Default is False."
                            ),
                        },
                        "context_mode": {
                            "type": "string",
                            "enum": ["full", "subset"],
                            "default": "full",
                            "example": "full",
                            "description": (
                                "Backlog context given to the incremental update LLM. 'full' sends the "
                                "complete backlog; 'subset' sends a selector-filtered slice. Default is 'full'."
                            ),
                        },
                    },
                }
            }
        },
    }
}

MODULE_TREE_LIST_OPENAPI_EXTRA: dict = {
    "responses": {
        "200": {
            "content": {
                _CONTENT_JSON: {
                    "example": {
                        "success": True,
                        "message": "Module tree listed successfully",
                        "data": {
                            "total": 2,
                            "items": [
                                {
                                    "id": "mod-001",
                                    "mod_code": "MOD-001",
                                    "name": "Authentication",
                                    "description": "Handles user authentication and identity management",
                                    "children": [
                                        {
                                            "id": "fea-001",
                                            "fea_code": "FEA-001",
                                            "name": "User Login",
                                            "description": "Allows registered users to authenticate with credentials",
                                            "children": [
                                                {
                                                    "fun_code": "FUN-001",
                                                    "name": "Validate credentials",
                                                    "description": "Check username/password against the database",
                                                }
                                            ],
                                        },
                                        {
                                            "id": "fea-002",
                                            "fea_code": "FEA-002",
                                            "name": "User Registration",
                                            "description": "Enables new users to create an account",
                                            "children": [],
                                        },
                                    ],
                                },
                                {
                                    "id": "mod-002",
                                    "mod_code": "MOD-002",
                                    "name": "Project Management",
                                    "description": "Manages projects and their lifecycle",
                                    "children": [
                                        {
                                            "id": "fea-003",
                                            "fea_code": "FEA-003",
                                            "name": "Create Project",
                                            "description": "Allows users to create a new project with metadata",
                                            "children": [],
                                        }
                                    ],
                                },
                            ],
                        },
                    }
                }
            }
        }
    }
}

# ---------------------------------------------------------------------------
# Shared example data — reused across all openapi_extra dicts below
# ---------------------------------------------------------------------------

_EX_PROJECT_ID = _WS_EXAMPLE_PROJECT_ID  # "35fb2f8c-..."
_EX_OWNER_ID = "c3d4e5f6-a7b8-9012-cdef-012345678901"
_EX_SOURCE_ID = "7a3f91b2-4c5d-4e6f-8a9b-0c1d2e3f4a5b"
_EX_FRAGMENT_ID = "frag-001"
_EX_MODULE_ID = "mod-001"
_EX_FEATURE_ID = "fea-001"
_EX_TASK_ID_STR = _WS_EXAMPLE_TASK_ID  # "a1b2c3d4-..."

_EX_PROJECT: dict = {
    "id": _EX_PROJECT_ID,
    "name": "RFP Analysis Q4-2025",
    "description": "Extract requirements from the Q4 2025 tender document.",
    "status": "active",
    "owner_id": _EX_OWNER_ID,
    "created_at": _WS_EXAMPLE_TS_START,
    "updated_at": _WS_EXAMPLE_TS_START,
    "files": 3,
    "user_stories": 42,
    "approved_user_stories": 15,
    "team_members": None,
}

_EX_FRAGMENT_BBOX: list = [
    {"page": 1, "bbox": {"x": 72.0, "y": 100.5, "w": 400.0, "h": 20.0}, "confidence": 0.98}
]

_EX_FRAGMENT: dict = {
    "id": _EX_FRAGMENT_ID,
    "source_id": _EX_SOURCE_ID,
    "frag_type": "text",
    "content": "The system shall support multi-factor authentication for all users.",
    "content_hash": "a3f8d2c1b0e9f7a6d5c4b3a2e1f0d9c8b7a6f5e4d3c2b1a0",
    "bbox": _EX_FRAGMENT_BBOX,
    "created_at": _WS_EXAMPLE_TS_START,
    "updated_at": _WS_EXAMPLE_TS_START,
}

_EX_FEATURE: dict = {
    "id": _EX_FEATURE_ID,
    "fea_code": "FEA-001",
    "name": "User Login",
    "description": "Allows registered users to authenticate with their credentials.",
    "status": "draft",
    "total_user_stories": 5,
    "functions": [
        {
            "fun_code": "FUN-001",
            "name": "Validate credentials",
            "description": "Check username/password against the database.",
        }
    ],
    "created_at": _WS_EXAMPLE_TS_START,
    "updated_at": _WS_EXAMPLE_TS_START,
}

_EX_MODULE: dict = {
    "id": _EX_MODULE_ID,
    "project_id": _EX_PROJECT_ID,
    "mod_code": "MOD-001",
    "name": "Authentication",
    "description": "Handles user authentication and identity management.",
    "status": "draft",
    "features": [_EX_FEATURE],
    "created_at": _WS_EXAMPLE_TS_START,
    "updated_at": _WS_EXAMPLE_TS_START,
}

_EX_MODULE_LIST_ITEM: dict = {
    "id": _EX_MODULE_ID,
    "mod_code": "MOD-001",
    "name": "Authentication",
    "description": "Handles user authentication and identity management.",
    "status": "draft",
    "total_features": 2,
    "total_user_stories": 10,
    "features": [_EX_FEATURE],
    "created_at": _WS_EXAMPLE_TS_START,
    "updated_at": _WS_EXAMPLE_TS_START,
}


def _ok_example(message: str, data: object) -> dict:
    """Wrap *data* in the standard ``ApiResponse`` envelope for use in examples."""
    return {"success": True, "message": message, "data": data}


def _response_extra(status_code: str | int, message: str, data: object) -> dict:
    """Build an ``openapi_extra`` dict that adds a concrete example to a response."""
    return {
        "responses": {
            str(status_code): {"content": {_CONTENT_JSON: {"example": _ok_example(message, data)}}}
        }
    }


# ---------------------------------------------------------------------------
# Project endpoint openapi_extra dicts
# ---------------------------------------------------------------------------

PROJECT_CREATE_OPENAPI_EXTRA: dict = _response_extra(
    201,
    "Project created successfully.",
    _EX_PROJECT,
)

PROJECT_LIST_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Success",
    {"items": [_EX_PROJECT], "total": 1, "skip": 0, "limit": 20},
)

PROJECT_LIST_ALL_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Success",
    {"items": [_EX_PROJECT], "total": 1, "skip": 0, "limit": 20},
)

PROJECT_GET_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Success",
    _EX_PROJECT,
)

PROJECT_UPDATE_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Project updated successfully.",
    {**_EX_PROJECT, "name": "RFP Analysis Q4-2025 (Final)", "status": "archived"},
)

PROJECT_DELETE_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Success",
    None,
)

# ---------------------------------------------------------------------------
# Fragment endpoint openapi_extra dicts
# ---------------------------------------------------------------------------

FRAGMENT_LIST_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Fragments listed successfully.",
    {
        "project_id": _EX_PROJECT_ID,
        "total": 1,
        "skip": 0,
        "limit": 20,
        "items": [_EX_FRAGMENT],
    },
)

FRAGMENT_GET_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Fragment fetched successfully.",
    {"source_id": _EX_SOURCE_ID, "fragment": _EX_FRAGMENT},
)

FRAGMENT_UPDATE_BBOX_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Fragment bounding box updated successfully.",
    {
        "fragment_id": _EX_FRAGMENT_ID,
        "source_id": _EX_SOURCE_ID,
        "bbox": _EX_FRAGMENT_BBOX,
        "created_at": _WS_EXAMPLE_TS_START,
        "updated_at": _WS_EXAMPLE_TS_END,
    },
)

# ---------------------------------------------------------------------------
# Module / Feature endpoint openapi_extra dicts
# ---------------------------------------------------------------------------

MODULE_LIST_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Modules listed successfully.",
    {"total": 1, "skip": 0, "limit": 20, "items": [_EX_MODULE_LIST_ITEM]},
)

MODULE_GET_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Module fetched successfully.",
    {"project_id": _EX_PROJECT_ID, "module": _EX_MODULE},
)

MODULE_STATUS_CHANGE_OPENAPI_EXTRA: dict = _response_extra(
    200,
    "Module and feature status changed successfully.",
    {"project_id": None, "task_id": None, "status": "approved"},
)

MODULE_REGENERATE_OPENAPI_EXTRA: dict = _response_extra(
    202,
    "Module-feature regeneration queued successfully.",
    {
        "task_id": _EX_TASK_ID_STR,
        "project_id": _EX_PROJECT_ID,
        "source_ids": [_EX_SOURCE_ID],
        "status": "queued",
    },
)

# ---------------------------------------------------------------------------
# User Story — regenerate-by-feedback endpoint
# ---------------------------------------------------------------------------

_EX_USER_STORY_ID_1 = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_EX_USER_STORY_ID_2 = "7c9e6679-7425-40de-944b-e07fc1f90ae7"

_EX_STORY_FEEDBACK_REQUEST: list = [
    {
        "user_story_id": _EX_USER_STORY_ID_1,
        "overall_feedback": (
            "The acceptance criteria are too basic. Add an edge case for when the resident tries "
            "to bind to an apartment number already bound to another account. Also, the "
            "technical_notes should mention the apartment master registry lookup endpoint explicitly."
        ),
        "specific_feedback": None,
    },
    {
        "user_story_id": _EX_USER_STORY_ID_2,
        "overall_feedback": None,
        "specific_feedback": [
            {
                "selected_text": (
                    "eliminating reconciliation delays caused by the absence of "
                    "real-time linkage between payment and card write"
                ),
                "selected_feedback": (
                    "This is good but needs a concrete metric. Change to target "
                    "reconciliation delay reduction to under 2 minutes per transaction."
                ),
            },
            {
                "selected_text": "automatically recorded in the system ledger",
                "selected_feedback": (
                    "Clarify what fields are recorded: apartment number, resident ID, "
                    "recharge amount, bKash transaction ID, timestamp, and card write status."
                ),
            },
        ],
    },
]

USER_STORY_REGENERATE_BY_FEEDBACK_OPENAPI_EXTRA: dict = {
    "requestBody": {
        "required": True,
        "content": {
            _CONTENT_JSON: {
                "example": _EX_STORY_FEEDBACK_REQUEST,
            }
        },
    },
    "responses": {
        "202": {
            "content": {
                _CONTENT_JSON: {
                    "example": _ok_example(
                        "User story feedback-based regeneration queued successfully.",
                        {
                            "task_id": _EX_TASK_ID_STR,
                            "project_id": _EX_PROJECT_ID,
                            "source_ids": [_EX_SOURCE_ID],
                            "status": "queued",
                        },
                    )
                }
            }
        }
    },
}

_STALE_SCHEMAS = ("HTTPValidationError", "ValidationError")


def build_custom_openapi(application: FastAPI) -> Callable[[], dict]:
    """Return a ``custom_openapi()`` callable bound to *application*.

    Usage::

        app.openapi = build_custom_openapi(app)
    """

    def custom_openapi() -> dict:
        if application.openapi_schema:
            return application.openapi_schema

        schema = get_openapi(
            title=application.title,
            version=application.version,
            routes=application.routes,
        )

        error_ref = {"$ref": f"#/components/schemas/{_ERROR_SCHEMA_NAME}"}

        # Ensure ErrorResponse is present in components/schemas.
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        if _ERROR_SCHEMA_NAME not in schemas:
            schemas[_ERROR_SCHEMA_NAME] = ErrorResponse.model_json_schema()

        # Rewrite every 422 response body to use ErrorResponse instead of
        # the default HTTPValidationError produced by FastAPI.
        for _path, path_item in schema.get("paths", {}).items():
            for _method, operation in path_item.items():
                responses = operation.get("responses", {})
                if "422" in responses:
                    responses["422"]["content"] = {_CONTENT_JSON: {"schema": error_ref}}

        # Remove stale FastAPI validation-error schemas.
        for stale in _STALE_SCHEMAS:
            schemas.pop(stale, None)

        # ── Project-task list schemas ──────────────────────────────────────
        # The project task list route handler has no typed
        # response_model, so FastAPI emits no schema for its 200 response.
        # We inject the full schema here and patch the path entry below.
        task_schemas: dict = {
            "TaskEventOut": {
                "type": "object",
                "description": (
                    "A single status-transition snapshot for a task. "
                    "One entry is stored per unique status the task passes through. "
                    "Source-processing tasks: queued → running → ready_for_review/failed. "
                    "Module-regeneration tasks: queued → running → ready_for_review/failed. "
                    "Story-generation, story-regeneration, and story-feedback-patch tasks: "
                    "queued → running → completed/failed."
                ),
                "required": ["status", "progress"],
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "queued",
                            "running",
                            "ready_for_review",
                            "completed",
                            "failed",
                        ],
                        "example": "running",
                    },
                    "progress": {"type": "integer", "minimum": 0, "maximum": 100, "example": 60},
                    "stage": {
                        "type": "string",
                        "nullable": True,
                        "example": _WS_STAGE_PARSING_STARTED,
                    },
                    "meta": {"type": "object", "nullable": True, "additionalProperties": True},
                    "error": {"type": "string", "nullable": True},
                    "created_at": {"type": "string", "format": "date-time", "nullable": True},
                },
                "example": {
                    "status": "running",
                    "progress": 60,
                    "stage": _WS_STAGE_PARSING_STARTED,
                    "meta": None,
                    "error": None,
                    "created_at": _WS_EXAMPLE_TS_MID,
                },
            },
            "ProjectTaskOut": {
                "type": "object",
                "description": (
                    "A background task row with its full status-event history. "
                    "Returned by `GET /projects/{project_id}/tasks`."
                ),
                "required": ["task_id", "task_type", "project_id", "status", "progress", "events"],
                "properties": {
                    "task_id": {"type": "string", "format": "uuid", "example": _WS_EXAMPLE_TASK_ID},
                    "celery_task_id": {
                        "type": "string",
                        "nullable": True,
                        "example": _WS_EXAMPLE_CELERY_ID,
                    },
                    "task_type": {
                        "type": "string",
                        "enum": [
                            "source_process",
                            "module_regeneration",
                            "story_generation",
                            "story_regeneration",
                        ],
                        "example": "source_process",
                    },
                    "project_id": {
                        "type": "string",
                        "format": "uuid",
                        "example": _WS_EXAMPLE_PROJECT_ID,
                    },
                    "user_id": {"type": "string", "format": "uuid", "nullable": True},
                    "status": {
                        "type": "string",
                        "enum": [
                            "queued",
                            "running",
                            "ready_for_review",
                            "completed",
                            "failed",
                        ],
                        "example": "completed",
                    },
                    "progress": {"type": "integer", "minimum": 0, "maximum": 100, "example": 100},
                    "stage": {
                        "type": "string",
                        "nullable": True,
                        "example": _WS_STAGE_PARSING_COMPLETED,
                    },
                    "meta": {"type": "object", "nullable": True, "additionalProperties": True},
                    "error": {"type": "string", "nullable": True},
                    "created_at": {"type": "string", "format": "date-time", "nullable": True},
                    "updated_at": {"type": "string", "format": "date-time", "nullable": True},
                    "events": {
                        "type": "array",
                        "description": "One entry per unique status the task has passed through, in chronological order.",
                        "items": {"$ref": "#/components/schemas/TaskEventOut"},
                    },
                },
            },
            "ProjectTaskListData": {
                "type": "object",
                "description": "Payload returned inside the ApiResponse envelope for the task list endpoint.",
                "required": ["tasks", "total"],
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/ProjectTaskOut"},
                    },
                    "total": {"type": "integer", "example": 3},
                },
            },
            "ProjectTaskListResponse": {
                "type": "object",
                "description": "ApiResponse envelope for `GET /projects/{project_id}/tasks`.",
                "required": ["success", "message", "data"],
                "properties": {
                    "success": {"type": "boolean", "example": True},
                    "message": {"type": "string", "example": "Success"},
                    "data": {"$ref": "#/components/schemas/ProjectTaskListData"},
                },
            },
        }
        schemas.update(task_schemas)

        # Patch the untyped 200 response for the project-tasks list endpoint.
        _tasks_path = schema.get("paths", {}).get("/api/v1/projects/{project_id}/tasks", {})
        _tasks_get = _tasks_path.get("get", {})
        if _tasks_get:
            _tasks_get.setdefault("responses", {})["200"] = {
                "description": "Successful Response",
                "content": {
                    _CONTENT_JSON: {
                        "schema": {"$ref": "#/components/schemas/ProjectTaskListResponse"},
                        "example": {
                            "success": True,
                            "message": "Success",
                            "data": {
                                "tasks": [
                                    {
                                        "task_id": _WS_EXAMPLE_TASK_ID,
                                        "celery_task_id": _WS_EXAMPLE_CELERY_ID,
                                        "task_type": "source_process",
                                        "project_id": _WS_EXAMPLE_PROJECT_ID,
                                        "user_id": "c3d4e5f6-a7b8-9012-cdef-012345678901",
                                        "status": "completed",
                                        "progress": 100,
                                        "stage": _WS_STAGE_PARSING_COMPLETED,
                                        "meta": None,
                                        "error": None,
                                        "created_at": _WS_EXAMPLE_TS_START,
                                        "updated_at": _WS_EXAMPLE_TS_END,
                                        "events": [
                                            {
                                                "status": "queued",
                                                "progress": 0,
                                                "stage": _WS_STAGE_SOURCE_QUEUED,
                                                "meta": None,
                                                "error": None,
                                                "created_at": _WS_EXAMPLE_TS_START,
                                            },
                                            {
                                                "status": "running",
                                                "progress": 50,
                                                "stage": _WS_STAGE_PARSING_STARTED,
                                                "meta": None,
                                                "error": None,
                                                "created_at": _WS_EXAMPLE_TS_MID,
                                            },
                                            {
                                                "status": "completed",
                                                "progress": 100,
                                                "stage": _WS_STAGE_PARSING_COMPLETED,
                                                "meta": None,
                                                "error": None,
                                                "created_at": _WS_EXAMPLE_TS_END,
                                            },
                                        ],
                                    }
                                ],
                                "total": 1,
                            },
                        },
                    }
                },
            }

        # ── WebSocket endpoint documentation ───────────────────────────────
        # OpenAPI 3.0 has no native WebSocket type; we document it as a GET
        # with Upgrade semantics so it appears in Swagger UI.
        #
        # Reusable message schemas are also added to components/schemas so
        # they render as named models in the Swagger UI sidebar.
        ws_schemas: dict = {
            "WsProjectTask": {
                "type": "object",
                "description": "A single project task row included in the `tasks.current` envelope.",
                "required": ["task_id", "task_type", "project_id", "status"],
                "properties": {
                    "task_id": {"type": "string", "format": "uuid", "example": _WS_EXAMPLE_TASK_ID},
                    "celery_task_id": {
                        "type": "string",
                        "nullable": True,
                        "example": _WS_EXAMPLE_CELERY_ID,
                    },
                    "task_type": {
                        "type": "string",
                        "enum": [
                            "source_process",
                            "module_regeneration",
                            "story_generation",
                            "story_regeneration",
                        ],
                        "example": "source_process",
                    },
                    "project_id": {
                        "type": "string",
                        "format": "uuid",
                        "example": _WS_EXAMPLE_PROJECT_ID,
                    },
                    "status": {
                        "type": "string",
                        "enum": [
                            "queued",
                            "running",
                            "ready_for_review",
                            "completed",
                            "failed",
                        ],
                        "example": "running",
                    },
                    "progress": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "nullable": True,
                        "example": 40,
                    },
                    "stage": {
                        "type": "string",
                        "nullable": True,
                        "example": _WS_STAGE_SOURCE_QUEUED,
                    },
                    "meta": {"type": "object", "nullable": True, "additionalProperties": True},
                    "error": {"type": "string", "nullable": True},
                    "created_at": {"type": "string", "format": "date-time", "nullable": True},
                    "updated_at": {"type": "string", "format": "date-time", "nullable": True},
                },
            },
            "WsTasksCurrentEnvelope": {
                "type": "object",
                "description": (
                    "Sent immediately on WebSocket connect. Contains all non-terminal "
                    "tasks for the project so the client can restore UI state without "
                    "an extra REST poll."
                ),
                "required": ["event", "project_id", "tasks"],
                "properties": {
                    "event": {
                        "type": "string",
                        "enum": [_WS_EVENT_TASKS_CURRENT],
                        "example": _WS_EVENT_TASKS_CURRENT,
                    },
                    "project_id": {
                        "type": "string",
                        "format": "uuid",
                        "example": _WS_EXAMPLE_PROJECT_ID,
                    },
                    "tasks": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/WsProjectTask"},
                    },
                },
                "example": {
                    "event": _WS_EVENT_TASKS_CURRENT,
                    "project_id": _WS_EXAMPLE_PROJECT_ID,
                    "tasks": [
                        {
                            "task_id": _WS_EXAMPLE_TASK_ID,
                            "celery_task_id": _WS_EXAMPLE_CELERY_ID,
                            "task_type": "source_process",
                            "project_id": _WS_EXAMPLE_PROJECT_ID,
                            "status": "running",
                            "progress": 40,
                            "stage": _WS_STAGE_SOURCE_QUEUED,
                            "meta": None,
                            "error": None,
                            "created_at": _WS_EXAMPLE_TS_START,
                            "updated_at": _WS_EXAMPLE_TS_MID,
                        }
                    ],
                },
            },
            "WsDashboardTasksCurrentEnvelope": {
                "type": "object",
                "description": (
                    "Sent immediately on WebSocket connect to `/ws/projects/pipelines`. "
                    "Contains all non-terminal tasks across every project owned by the "
                    "authenticated user (each item carries its own `project_id`) so a "
                    "dashboard listing all projects can hydrate status without an extra "
                    "REST poll or one connection per project."
                ),
                "required": ["event", "tasks"],
                "properties": {
                    "event": {
                        "type": "string",
                        "enum": [_WS_EVENT_TASKS_CURRENT],
                        "example": _WS_EVENT_TASKS_CURRENT,
                    },
                    "tasks": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/WsProjectTask"},
                    },
                },
                "example": {
                    "event": _WS_EVENT_TASKS_CURRENT,
                    "tasks": [
                        {
                            "task_id": _WS_EXAMPLE_TASK_ID,
                            "celery_task_id": _WS_EXAMPLE_CELERY_ID,
                            "task_type": "source_process",
                            "project_id": _WS_EXAMPLE_PROJECT_ID,
                            "status": "running",
                            "progress": 40,
                            "stage": _WS_STAGE_SOURCE_QUEUED,
                            "meta": None,
                            "error": None,
                            "created_at": _WS_EXAMPLE_TS_START,
                            "updated_at": _WS_EXAMPLE_TS_MID,
                        }
                    ],
                },
            },
            "WsTaskUpdate": {
                "type": "object",
                "description": (
                    "Pushed to the client whenever a task transitions to a new status. "
                    "Covers all task types: source processing, module regeneration, "
                    "story generation, and story regeneration."
                ),
                "required": ["event", "task_id", "task_type", "project_id", "status", "timestamp"],
                "properties": {
                    "event": {
                        "type": "string",
                        "enum": [_WS_EVENT_TASK_UPDATE],
                        "example": _WS_EVENT_TASK_UPDATE,
                    },
                    "task_id": {"type": "string", "format": "uuid", "example": _WS_EXAMPLE_TASK_ID},
                    "celery_task_id": {
                        "type": "string",
                        "nullable": True,
                        "example": _WS_EXAMPLE_CELERY_ID,
                    },
                    "task_type": {
                        "type": "string",
                        "enum": [
                            "source_process",
                            "module_regeneration",
                            "story_generation",
                            "story_regeneration",
                        ],
                        "example": "source_process",
                    },
                    "project_id": {
                        "type": "string",
                        "format": "uuid",
                        "example": _WS_EXAMPLE_PROJECT_ID,
                    },
                    "status": {
                        "type": "string",
                        "enum": [
                            "queued",
                            "running",
                            "ready_for_review",
                            "completed",
                            "failed",
                        ],
                        "example": "running",
                    },
                    "progress": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "nullable": True,
                        "example": 60,
                    },
                    "stage": {
                        "type": "string",
                        "nullable": True,
                        "example": _WS_STAGE_PARSING_STARTED,
                    },
                    "meta": {"type": "object", "nullable": True, "additionalProperties": True},
                    "error": {"type": "string", "nullable": True},
                    "timestamp": {
                        "type": "string",
                        "format": "date-time",
                        "example": _WS_EXAMPLE_TIMESTAMP,
                    },
                },
                "example": {
                    "event": _WS_EVENT_TASK_UPDATE,
                    "task_id": _WS_EXAMPLE_TASK_ID,
                    "celery_task_id": _WS_EXAMPLE_CELERY_ID,
                    "task_type": "source_process",
                    "project_id": _WS_EXAMPLE_PROJECT_ID,
                    "status": "running",
                    "progress": 60,
                    "stage": _WS_STAGE_PARSING_STARTED,
                    "meta": None,
                    "error": None,
                    "timestamp": _WS_EXAMPLE_TIMESTAMP,
                },
            },
            "WsHeartbeat": {
                "type": "object",
                "description": "Keepalive heartbeat sent by the server every 30 seconds.",
                "required": ["event", "timestamp"],
                "properties": {
                    "event": {
                        "type": "string",
                        "enum": [_WS_EVENT_HEARTBEAT],
                        "example": _WS_EVENT_HEARTBEAT,
                    },
                    "timestamp": {
                        "type": "string",
                        "format": "date-time",
                        "example": _WS_EXAMPLE_TIMESTAMP,
                    },
                },
                "example": {
                    "event": _WS_EVENT_HEARTBEAT,
                    "timestamp": _WS_EXAMPLE_TIMESTAMP,
                },
            },
            "WsNotificationItem": {
                "type": "object",
                "description": "A single in-app notification as returned in WebSocket frames.",
                "required": [
                    "id",
                    "user_id",
                    "title",
                    "message",
                    "notification_type",
                    "is_read",
                    "created_at",
                ],
                "properties": {
                    "id": {
                        "type": "string",
                        "format": "uuid",
                        "example": _WS_EXAMPLE_NOTIFICATION_ID,
                    },
                    "user_id": {"type": "string", "format": "uuid", "example": _WS_EXAMPLE_USER_ID},
                    "title": {"type": "string", "example": _WS_EXAMPLE_NOTIF_TITLE},
                    "message": {"type": "string", "example": _WS_EXAMPLE_NOTIF_MSG},
                    "notification_type": {
                        "type": "string",
                        "enum": ["info", "success", "warning", "error"],
                        "example": "success",
                    },
                    "is_read": {"type": "boolean", "example": False},
                    "data": {
                        "type": "object",
                        "nullable": True,
                        "additionalProperties": True,
                        "example": {"project_id": _WS_EXAMPLE_PROJECT_ID},
                    },
                    "created_at": {
                        "type": "string",
                        "format": "date-time",
                        "example": _WS_EXAMPLE_TS_START,
                    },
                    "updated_at": {
                        "type": "string",
                        "format": "date-time",
                        "nullable": True,
                        "example": _WS_EXAMPLE_TS_START,
                    },
                },
            },
            "WsNotificationsCurrentEnvelope": {
                "type": "object",
                "description": (
                    "Sent immediately on WebSocket connect. Contains the 50 most recent "
                    "notifications and the current unread count so the client can hydrate "
                    "the bell badge and dropdown without an extra REST poll."
                ),
                "required": ["event", "notifications", "unread_count"],
                "properties": {
                    "event": {
                        "type": "string",
                        "enum": [_WS_EVENT_NOTIFICATIONS_CURRENT],
                        "example": _WS_EVENT_NOTIFICATIONS_CURRENT,
                    },
                    "notifications": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/WsNotificationItem"},
                    },
                    "unread_count": {"type": "integer", "minimum": 0, "example": 3},
                },
                "example": {
                    "event": _WS_EVENT_NOTIFICATIONS_CURRENT,
                    "unread_count": 1,
                    "notifications": [
                        {
                            "id": _WS_EXAMPLE_NOTIFICATION_ID,
                            "user_id": _WS_EXAMPLE_USER_ID,
                            "title": _WS_EXAMPLE_NOTIF_TITLE,
                            "message": _WS_EXAMPLE_NOTIF_MSG,
                            "notification_type": "success",
                            "is_read": False,
                            "data": {"project_id": _WS_EXAMPLE_PROJECT_ID},
                            "created_at": _WS_EXAMPLE_TS_START,
                            "updated_at": _WS_EXAMPLE_TS_START,
                        }
                    ],
                },
            },
            "WsNotificationNewEnvelope": {
                "type": "object",
                "description": (
                    "Pushed to the client in real-time whenever a new notification is "
                    "created for the authenticated user. Increment the badge counter by 1 "
                    "and prepend the notification to the dropdown list."
                ),
                "required": ["event", "notification"],
                "properties": {
                    "event": {
                        "type": "string",
                        "enum": [_WS_EVENT_NOTIFICATION_NEW],
                        "example": _WS_EVENT_NOTIFICATION_NEW,
                    },
                    "notification": {"$ref": "#/components/schemas/WsNotificationItem"},
                },
                "example": {
                    "event": _WS_EVENT_NOTIFICATION_NEW,
                    "notification": {
                        "id": _WS_EXAMPLE_NOTIFICATION_ID,
                        "user_id": _WS_EXAMPLE_USER_ID,
                        "title": _WS_EXAMPLE_NOTIF_TITLE,
                        "message": _WS_EXAMPLE_NOTIF_MSG,
                        "notification_type": "success",
                        "is_read": False,
                        "data": {"project_id": _WS_EXAMPLE_PROJECT_ID},
                        "created_at": _WS_EXAMPLE_TS_END,
                        "updated_at": _WS_EXAMPLE_TS_END,
                    },
                },
            },
        }
        schemas.update(ws_schemas)

        schema.setdefault("paths", {})["/ws/projects/{project_id}"] = {
            "get": {
                "summary": "Stream project task events (WebSocket)",
                "description": (
                    "Upgrade to a WebSocket to receive real-time JSON frames for **all** "
                    "task types in a project (source processing, module regeneration, "
                    "story generation, story regeneration).\n\n"
                    "**Authentication**: JWT via `?token=<jwt>` query param or "
                    "`access_token` cookie.\n\n"
                    "**Authorization**: connection is accepted for any authenticated "
                    "user when the target project exists.\n\n"
                    "---\n\n"
                    "### Server → Client message frames\n\n"
                    "**1. `tasks.current`** — sent immediately on connect with all active tasks:\n"
                    "```json\n"
                    "{\n"
                    '  "event": "tasks.current",\n'
                    '  "project_id": "35fb2f8c-53de-4fd8-82d3-3de2412474a7",\n'
                    '  "tasks": [\n'
                    "    {\n"
                    '      "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",\n'
                    '      "task_type": "source_process",\n'
                    '      "status": "running",\n'
                    '      "progress": 40,\n'
                    '      "stage": "source.process.queued"\n'
                    "    }\n"
                    "  ]\n"
                    "}\n"
                    "```\n\n"
                    "**2. `task.update`** — pushed on every task status / progress change:\n"
                    "```json\n"
                    "{\n"
                    '  "event": "task.update",\n'
                    '  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",\n'
                    '  "task_type": "source_process",\n'
                    '  "project_id": "35fb2f8c-53de-4fd8-82d3-3de2412474a7",\n'
                    '  "status": "completed",\n'
                    '  "progress": 100,\n'
                    '  "stage": "documents.parsing.completed",\n'
                    '  "meta": null,\n'
                    '  "error": null,\n'
                    '  "timestamp": "2026-05-08T04:15:02.123456+00:00"\n'
                    "}\n"
                    "```\n\n"
                    "**3. `heartbeat`** — sent every 30 s to keep the connection alive:\n"
                    "```json\n"
                    "{\n"
                    '  "event": "heartbeat",\n'
                    '  "timestamp": "2026-05-08T04:14:39.008148+00:00"\n'
                    "}\n"
                    "```\n\n"
                    "---\n\n"
                    "### Task types\n"
                    "| `task_type` | Triggered by |\n"
                    "|---|---|\n"
                    "| `source_process` | `POST /api/v1/sources/upload/bulk` or `POST /api/v1/sources/upload/link` |\n"
                    "| `module_regeneration` | `POST /api/v1/projects/{project_id}/modules/regenerate` |\n"
                    "| `story_generation` | `PATCH /api/v1/projects/{project_id}/modules/status` (status=`approved`) |\n"
                    "| `story_regeneration` | `POST /api/v1/projects/{project_id}/user-stories/regenerate` |\n"
                    "\n"
                    "### Task status values\n"
                    "`source_process` tasks:\n"
                    "| Value | Meaning |\n"
                    "|---|---|\n"
                    "| `queued` | Task queued for processing |\n"
                    "| `processing` | Task actively parsing (image / incremental-update pipelines) |\n"
                    "| `running` | Task actively parsing / generating modules & features (document & source-code pipelines) |\n"
                    "| `ready_for_review` | Parsing and module+feature generation finished — all source_process pipelines (document, source-code, image, incremental-update) converge here |\n"
                    "| `failed` | Task failed after retries |\n"
                    "\n"
                    "`module_regeneration` tasks:\n"
                    "| Value | Meaning |\n"
                    "|---|---|\n"
                    "| `queued` | Task queued for processing |\n"
                    "| `running` | AI generation actively in progress |\n"
                    "| `ready_for_review` | Regeneration finished successfully, awaiting review |\n"
                    "| `failed` | Task failed after retries |\n"
                    "\n"
                    "`story_generation` / `story_regeneration` / `story_feedback_patch` tasks:\n"
                    "| Value | Meaning |\n"
                    "|---|---|\n"
                    "| `queued` | Task queued for processing |\n"
                    "| `running` | AI generation actively in progress |\n"
                    "| `completed` | Task finished successfully |\n"
                    "| `failed` | Task failed after retries |\n"
                    "\n"
                    "### `stage`\n"
                    "Free-text pipeline stage label emitted by workers. Examples: "
                    "`source.process.queued`, `documents.parsing.started`, "
                    "`modules_and_features.regeneration.completed`, "
                    "`user_story.generation.failed`.\n"
                    "\n"
                    "---\n\n"
                    "### Close codes\n"
                    "| Code | Reason |\n"
                    "|---|---|\n"
                    "| `1000` | Normal closure |\n"
                    "| `4001` | Unauthorized — missing or invalid JWT |\n"
                    "| `4004` | Project not found or invalid `project_id` |\n"
                ),
                "tags": ["WebSocket"],
                "operationId": "project_tasks_websocket",
                "parameters": [
                    {
                        "name": "project_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                        "description": "UUID of the project to subscribe to",
                        "example": "35fb2f8c-53de-4fd8-82d3-3de2412474a7",
                    },
                    {
                        "name": "token",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": _WS_PARAM_TOKEN_DESC,
                    },
                ],
                "responses": {
                    "101": {
                        "description": (
                            "Switching Protocols — WebSocket upgrade successful. "
                            "The server will stream JSON text frames. "
                            "See message schemas: "
                            "`WsTasksCurrentEnvelope`, `WsTaskUpdate`, `WsHeartbeat`."
                        ),
                        "content": {
                            _CONTENT_JSON: {
                                "schema": {
                                    "oneOf": [
                                        {"$ref": "#/components/schemas/WsTasksCurrentEnvelope"},
                                        {"$ref": "#/components/schemas/WsTaskUpdate"},
                                        {"$ref": _REF_WS_HEARTBEAT},
                                    ]
                                },
                                "examples": {
                                    "tasks.current": {
                                        "summary": "Active tasks on connect",
                                        "value": {
                                            "event": _WS_EVENT_TASKS_CURRENT,
                                            "project_id": _WS_EXAMPLE_PROJECT_ID,
                                            "tasks": [
                                                {
                                                    "task_id": _WS_EXAMPLE_TASK_ID,
                                                    "celery_task_id": _WS_EXAMPLE_CELERY_ID,
                                                    "task_type": "source_process",
                                                    "project_id": _WS_EXAMPLE_PROJECT_ID,
                                                    "status": "running",
                                                    "progress": 40,
                                                    "stage": _WS_STAGE_SOURCE_QUEUED,
                                                    "meta": None,
                                                    "error": None,
                                                    "created_at": _WS_EXAMPLE_TS_START,
                                                    "updated_at": _WS_EXAMPLE_TS_MID,
                                                }
                                            ],
                                        },
                                    },
                                    "task.update": {
                                        "summary": "Task status transition",
                                        "value": {
                                            "event": _WS_EVENT_TASK_UPDATE,
                                            "task_id": _WS_EXAMPLE_TASK_ID,
                                            "celery_task_id": _WS_EXAMPLE_CELERY_ID,
                                            "task_type": "source_process",
                                            "project_id": _WS_EXAMPLE_PROJECT_ID,
                                            "status": "completed",
                                            "progress": 100,
                                            "stage": _WS_STAGE_PARSING_COMPLETED,
                                            "meta": None,
                                            "error": None,
                                            "timestamp": _WS_EXAMPLE_TS_END,
                                        },
                                    },
                                    "task.update_module_regeneration": {
                                        "summary": "Module regeneration update",
                                        "value": {
                                            "event": _WS_EVENT_TASK_UPDATE,
                                            "task_id": "b2c3d4e5-f6a7-8901-bcde-f01234567890",
                                            "celery_task_id": "e5f6a7b8-c9d0-1234-ef01-234567890abc",
                                            "task_type": "module_regeneration",
                                            "project_id": _WS_EXAMPLE_PROJECT_ID,
                                            "status": "running",
                                            "progress": 50,
                                            "stage": "modules_and_features.regeneration.started",
                                            "meta": {"project_id": _WS_EXAMPLE_PROJECT_ID},
                                            "error": None,
                                            "timestamp": "2026-05-21T13:00:10.000000+00:00",
                                        },
                                    },
                                    "heartbeat": {
                                        "summary": _WS_HEARTBEAT_SUMMARY,
                                        "value": {
                                            "event": _WS_EVENT_HEARTBEAT,
                                            "timestamp": _WS_EXAMPLE_TIMESTAMP,
                                        },
                                    },
                                },
                            }
                        },
                    },
                    "401": {"description": _WS_DESC_UNAUTHORIZED},
                    "404": {"description": "Project not found or invalid project_id"},
                },
            }
        }

        schema.setdefault("paths", {})["/ws/projects/pipelines"] = {
            "get": {
                "summary": "Stream cross-project task status for the authenticated user (WebSocket)",
                "description": (
                    "Upgrade to a WebSocket to receive real-time task-status JSON frames "
                    "for **every project owned by the authenticated user**, from a single "
                    "connection — a dashboard/project-list counterpart to "
                    "`/ws/projects/{project_id}`, which is scoped to one project.\n\n"
                    "**Authentication**: JWT via `?token=<jwt>` query param or "
                    "`access_token` cookie.\n\n"
                    "**Scope**: covers only projects owned by the connected user "
                    "(not admin-wide) — both the on-connect snapshot and the Redis "
                    "channel are scoped by owner id, not by a client-supplied project id.\n\n"
                    "---\n\n"
                    "### Server → Client message frames\n\n"
                    "**1. `tasks.current`** — sent immediately on connect with all active "
                    "tasks across every owned project:\n"
                    "```json\n"
                    "{\n"
                    '  "event": "tasks.current",\n'
                    '  "tasks": [\n'
                    "    {\n"
                    '      "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",\n'
                    '      "task_type": "source_process",\n'
                    '      "project_id": "35fb2f8c-53de-4fd8-82d3-3de2412474a7",\n'
                    '      "status": "running",\n'
                    '      "progress": 40,\n'
                    '      "stage": "source.process.queued"\n'
                    "    }\n"
                    "  ]\n"
                    "}\n"
                    "```\n\n"
                    "**2. `task.update`** — pushed on every task status / progress change "
                    "for any project this user owns. Identical event shape to "
                    "`/ws/projects/{project_id}`'s `task.update` — the client buckets by "
                    "the `project_id` field on each event:\n"
                    "```json\n"
                    "{\n"
                    '  "event": "task.update",\n'
                    '  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",\n'
                    '  "task_type": "source_process",\n'
                    '  "project_id": "35fb2f8c-53de-4fd8-82d3-3de2412474a7",\n'
                    '  "status": "completed",\n'
                    '  "progress": 100,\n'
                    '  "stage": "documents.parsing.completed",\n'
                    '  "meta": null,\n'
                    '  "error": null,\n'
                    '  "timestamp": "2026-05-08T04:15:02.123456+00:00"\n'
                    "}\n"
                    "```\n\n"
                    "**3. `heartbeat`** — sent every 30 s to keep the connection alive:\n"
                    "```json\n"
                    "{\n"
                    '  "event": "heartbeat",\n'
                    '  "timestamp": "2026-05-08T04:14:39.008148+00:00"\n'
                    "}\n"
                    "```\n\n"
                    "---\n\n"
                    "See `/ws/projects/{project_id}` for the full task type / status "
                    "value reference — this endpoint pushes the exact same `task.update` "
                    "events, just fanned out to every project this user owns instead of "
                    "one.\n\n"
                    "---\n\n"
                    "### Close codes\n"
                    "| Code | Reason |\n"
                    "|---|---|\n"
                    "| `1000` | Normal closure |\n"
                    "| `4001` | Unauthorized — missing or invalid JWT |\n"
                    "| `4004` | User not found — no DB record for this Cognito sub |\n"
                ),
                "tags": ["WebSocket"],
                "operationId": "project_pipelines_status_websocket",
                "parameters": [
                    {
                        "name": "token",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": _WS_PARAM_TOKEN_DESC,
                    },
                ],
                "responses": {
                    "101": {
                        "description": (
                            "Switching Protocols — WebSocket upgrade successful. "
                            "The server will stream JSON text frames. "
                            "See message schemas: "
                            "`WsDashboardTasksCurrentEnvelope`, `WsTaskUpdate`, `WsHeartbeat`."
                        ),
                        "content": {
                            _CONTENT_JSON: {
                                "schema": {
                                    "oneOf": [
                                        {
                                            "$ref": "#/components/schemas/WsDashboardTasksCurrentEnvelope"
                                        },
                                        {"$ref": "#/components/schemas/WsTaskUpdate"},
                                        {"$ref": _REF_WS_HEARTBEAT},
                                    ]
                                },
                                "examples": {
                                    "tasks.current": {
                                        "summary": "Active tasks across all owned projects, on connect",
                                        "value": {
                                            "event": _WS_EVENT_TASKS_CURRENT,
                                            "tasks": [
                                                {
                                                    "task_id": _WS_EXAMPLE_TASK_ID,
                                                    "celery_task_id": _WS_EXAMPLE_CELERY_ID,
                                                    "task_type": "source_process",
                                                    "project_id": _WS_EXAMPLE_PROJECT_ID,
                                                    "status": "running",
                                                    "progress": 40,
                                                    "stage": _WS_STAGE_SOURCE_QUEUED,
                                                    "meta": None,
                                                    "error": None,
                                                    "created_at": _WS_EXAMPLE_TS_START,
                                                    "updated_at": _WS_EXAMPLE_TS_MID,
                                                }
                                            ],
                                        },
                                    },
                                    "task.update": {
                                        "summary": "Task status transition for one of the user's projects",
                                        "value": {
                                            "event": _WS_EVENT_TASK_UPDATE,
                                            "task_id": _WS_EXAMPLE_TASK_ID,
                                            "celery_task_id": _WS_EXAMPLE_CELERY_ID,
                                            "task_type": "source_process",
                                            "project_id": _WS_EXAMPLE_PROJECT_ID,
                                            "status": "completed",
                                            "progress": 100,
                                            "stage": _WS_STAGE_PARSING_COMPLETED,
                                            "meta": None,
                                            "error": None,
                                            "timestamp": _WS_EXAMPLE_TS_END,
                                        },
                                    },
                                    "heartbeat": {
                                        "summary": _WS_HEARTBEAT_SUMMARY,
                                        "value": {
                                            "event": _WS_EVENT_HEARTBEAT,
                                            "timestamp": _WS_EXAMPLE_TIMESTAMP,
                                        },
                                    },
                                },
                            }
                        },
                    },
                    "401": {"description": _WS_DESC_UNAUTHORIZED},
                    "404": {"description": "User not found — no DB record for this Cognito sub"},
                },
            }
        }

        schema.setdefault("paths", {})["/ws/notifications"] = {
            "get": {
                "summary": "Stream per-user in-app notification events (WebSocket)",
                "description": (
                    "Upgrade to a WebSocket to receive real-time in-app notification frames "
                    "for the authenticated user (notification bell).\n\n"
                    "**Authentication**: JWT via `?token=<jwt>` query param or "
                    "`access_token` cookie.\n\n"
                    "---\n\n"
                    "### Server → Client message frames\n\n"
                    "**1. `notifications.current`** — sent immediately on connect with the 50 most "
                    "recent notifications and current unread count:\n"
                    "```json\n"
                    "{\n"
                    '  "event": "notifications.current",\n'
                    '  "unread_count": 3,\n'
                    '  "notifications": [\n'
                    "    {\n"
                    '      "id": "b2c3d4e5-f6a7-8901-bcde-f01234567890",\n'
                    '      "title": "Source Processing Complete",\n'
                    '      "message": "3 files were processed successfully.",\n'
                    '      "notification_type": "success",\n'
                    '      "is_read": false\n'
                    "    }\n"
                    "  ]\n"
                    "}\n"
                    "```\n\n"
                    "**2. `notification.new`** — pushed in real-time when a new notification "
                    "is created for this user. Increment badge by 1 and prepend to list:\n"
                    "```json\n"
                    "{\n"
                    '  "event": "notification.new",\n'
                    '  "notification": {\n'
                    '    "id": "b2c3d4e5-f6a7-8901-bcde-f01234567890",\n'
                    '    "title": "Source Processing Complete",\n'
                    '    "message": "3 files were processed successfully.",\n'
                    '    "notification_type": "success",\n'
                    '    "is_read": false\n'
                    "  }\n"
                    "}\n"
                    "```\n\n"
                    "**3. `heartbeat`** — sent every 30 s to keep the connection alive:\n"
                    "```json\n"
                    "{\n"
                    '  "event": "heartbeat",\n'
                    '  "timestamp": "2026-07-02T10:14:39.008148+00:00"\n'
                    "}\n"
                    "```\n\n"
                    "---\n\n"
                    "### Notification types\n"
                    "| `notification_type` | Meaning |\n"
                    "|---|---|\n"
                    "| `info` | Informational message |\n"
                    "| `success` | Operation completed successfully |\n"
                    "| `warning` | Non-critical warning |\n"
                    "| `error` | Operation failed |\n"
                    "\n"
                    "---\n\n"
                    "### Close codes\n"
                    "| Code | Reason |\n"
                    "|---|---|\n"
                    "| `1000` | Normal closure |\n"
                    "| `4001` | Unauthorized — missing or invalid JWT |\n"
                    "| `4004` | User not found — no DB record for this Cognito sub |\n"
                ),
                "tags": ["WebSocket"],
                "operationId": "notifications_websocket",
                "parameters": [
                    {
                        "name": "token",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": _WS_PARAM_TOKEN_DESC,
                    },
                ],
                "responses": {
                    "101": {
                        "description": (
                            "Switching Protocols — WebSocket upgrade successful. "
                            "The server will stream JSON text frames. "
                            "See message schemas: "
                            "`WsNotificationsCurrentEnvelope`, `WsNotificationNewEnvelope`, `WsHeartbeat`."
                        ),
                        "content": {
                            _CONTENT_JSON: {
                                "schema": {
                                    "oneOf": [
                                        {
                                            "$ref": "#/components/schemas/WsNotificationsCurrentEnvelope"
                                        },
                                        {"$ref": "#/components/schemas/WsNotificationNewEnvelope"},
                                        {"$ref": _REF_WS_HEARTBEAT},
                                    ]
                                },
                                "examples": {
                                    "notifications.current": {
                                        "summary": "Recent notifications on connect",
                                        "value": {
                                            "event": _WS_EVENT_NOTIFICATIONS_CURRENT,
                                            "unread_count": 1,
                                            "notifications": [
                                                {
                                                    "id": _WS_EXAMPLE_NOTIFICATION_ID,
                                                    "user_id": _WS_EXAMPLE_USER_ID,
                                                    "title": _WS_EXAMPLE_NOTIF_TITLE,
                                                    "message": _WS_EXAMPLE_NOTIF_MSG,
                                                    "notification_type": "success",
                                                    "is_read": False,
                                                    "data": {"project_id": _WS_EXAMPLE_PROJECT_ID},
                                                    "created_at": _WS_EXAMPLE_TS_START,
                                                    "updated_at": _WS_EXAMPLE_TS_START,
                                                }
                                            ],
                                        },
                                    },
                                    "notification.new": {
                                        "summary": "New notification pushed in real-time",
                                        "value": {
                                            "event": _WS_EVENT_NOTIFICATION_NEW,
                                            "notification": {
                                                "id": _WS_EXAMPLE_NOTIFICATION_ID,
                                                "user_id": _WS_EXAMPLE_USER_ID,
                                                "title": _WS_EXAMPLE_NOTIF_TITLE,
                                                "message": _WS_EXAMPLE_NOTIF_MSG,
                                                "notification_type": "success",
                                                "is_read": False,
                                                "data": {"project_id": _WS_EXAMPLE_PROJECT_ID},
                                                "created_at": _WS_EXAMPLE_TS_END,
                                                "updated_at": _WS_EXAMPLE_TS_END,
                                            },
                                        },
                                    },
                                    "heartbeat": {
                                        "summary": _WS_HEARTBEAT_SUMMARY,
                                        "value": {
                                            "event": _WS_EVENT_HEARTBEAT,
                                            "timestamp": _WS_EXAMPLE_TIMESTAMP,
                                        },
                                    },
                                },
                            }
                        },
                    },
                    "401": {"description": _WS_DESC_UNAUTHORIZED},
                    "404": {"description": "User not found — no DB record for this Cognito sub"},
                },
            }
        }

        application.openapi_schema = schema
        return application.openapi_schema

    return custom_openapi
