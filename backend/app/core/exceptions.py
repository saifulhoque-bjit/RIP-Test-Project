"""Custom application exceptions.

Every exception class carries its own HTTP status code and machine-readable
error code so the global exception handler in ``app/core/exception_handlers.py``
can convert them to a standardized JSON response without any per-route
mapping logic.

Usage (raise from any service or dependency):
    raise NotFoundError("Test abc123 not found.")
    raise ForbiddenError("You do not have permission to delete this resource.")
"""

from __future__ import annotations


class RIPBaseException(Exception):
    """Base exception for all application-defined errors.

    Subclasses declare ``status_code`` and ``error_code`` as class-level
    attributes.  The human-readable message is passed at raise-site.
    """

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message: str = message


class NotFoundError(RIPBaseException):
    """Raised when a requested resource does not exist."""

    status_code = 404
    error_code = "NOT_FOUND"


class ConflictError(RIPBaseException):
    """Raised when an operation conflicts with current state (e.g. duplicate)."""

    status_code = 409
    error_code = "CONFLICT"


class ValidationError(RIPBaseException):
    """Raised when business-level validation fails."""

    status_code = 400
    error_code = "VALIDATION_ERROR"


class UnauthorizedError(RIPBaseException):
    """Raised when the caller is not authenticated or credentials are invalid."""

    status_code = 401
    error_code = "UNAUTHORIZED"


class ForbiddenError(RIPBaseException):
    """Raised when the caller is authenticated but lacks the required
    role or permission for the requested operation."""

    status_code = 403
    error_code = "FORBIDDEN"


class CognitoError(RIPBaseException):
    """Raised when AWS Cognito returns an unexpected, rate-limiting, or
    infrastructure-level error that cannot be mapped to a more specific
    application exception (e.g. TooManyRequestsException)."""

    status_code = 502
    error_code = "AUTH_SERVICE_ERROR"


class AIServiceError(RIPBaseException):
    """Raised when the external AI service returns an unexpected response."""

    status_code = 502
    error_code = "AI_SERVICE_ERROR"


class OmniParserClientError(RIPBaseException):
    """Raised when the OmniParser REST service returns an error, times out,
    or returns a response that cannot be validated against the expected schema."""

    status_code = 502
    error_code = "OMNIPARSER_ERROR"


class ServiceError(RIPBaseException):
    """Raised when a service-level orchestration step fails in a way that
    cannot be mapped to a more specific application exception."""

    status_code = 500
    error_code = "SERVICE_ERROR"


class ServiceUnavailableError(RIPBaseException):
    """Raised when a downstream dependency (AI service, queue, etc.) is
    temporarily unavailable and the request cannot be fulfilled."""

    status_code = 503
    error_code = "SERVICE_UNAVAILABLE"


class StorageError(RIPBaseException):
    """Raised when an external file-storage operation (S3 upload, download,
    delete, or pre-signed URL generation) fails.

    Wraps raw infrastructure/boto3 exceptions so callers only need to
    handle a single, well-typed domain error rather than AWS SDK internals.
    """

    status_code = 502
    error_code = "STORAGE_ERROR"


class AIWorkflowError(RIPBaseException):
    """Raised when an AI workflow step (LangGraph pass, LLM call, schema
    validation of AI output) fails in an unrecoverable way."""

    status_code = 500
    error_code = "AI_WORKFLOW_ERROR"


class JiraClientError(RIPBaseException):
    """Raised when the Jira REST API returns an error, times out, or returns
    an unexpected response."""

    status_code = 502
    error_code = "JIRA_CLIENT_ERROR"


class JiraSyncError(RIPBaseException):
    """Raised when the Jira sync orchestration fails in a way that cannot
    be recovered by retrying individual API calls."""

    status_code = 500
    error_code = "JIRA_SYNC_ERROR"


class TapClientError(RIPBaseException):
    """Raised when the TAP REST API returns an error, times out, or returns
    an unexpected response."""

    status_code = 502
    error_code = "TAP_CLIENT_ERROR"
