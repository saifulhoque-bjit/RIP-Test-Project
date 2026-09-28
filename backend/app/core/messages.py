"""Application-wide human-readable message strings.

All user-facing text that is hard-coded in services, routes, and utilities
should be defined here.  Using named constants avoids duplicating text and makes
future translation or copy-editing a single-file operation.

Parametric messages use ``str.format()`` placeholders (``{name}``) so that
callers supply dynamic values at the call site rather than building f-strings
inline.

Usage example::

    from app.core.messages import MSG_PROJECT_NOT_FOUND

    raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
"""

# ── Auth — Cognito error mapping ───────────────────────────────────────────

MSG_AUTH_EMAIL_ALREADY_EXISTS = "An account with this email already exists."
MSG_AUTH_REGISTER_NOT_AUTHORIZED = "Registration not authorized: {detail}"
MSG_AUTH_INVALID_CREDENTIALS = "Invalid credentials."
MSG_AUTH_PASSWORD_REQUIREMENTS = "Password does not meet Cognito requirements: {detail}"
MSG_AUTH_INVALID_PARAMETER = "Invalid parameter: {detail}"
MSG_AUTH_TOO_MANY_REQUESTS = "Too many requests. Please try again later."
MSG_AUTH_EMAIL_DELIVERY_FAILED = "Failed to deliver the verification email. Please try again."
MSG_AUTH_INVALID_CONFIRMATION_CODE = "Invalid confirmation code."
MSG_AUTH_CODE_EXPIRED = "Confirmation code has expired. Please request a new one."
MSG_AUTH_EMAIL_ALREADY_CONFIRMED = "This email is already associated with a confirmed account."
MSG_AUTH_SERVICE_ERROR = "Authentication service error: {detail}"
MSG_AUTH_AWS_UNAVAILABLE = "AWS service unavailable: {detail}"

# ── Auth — registration / confirmation ────────────────────────────────────

MSG_AUTH_REGISTRATION_SUCCESS = (
    "Registration successful. Please check your email to verify your account."
)
MSG_AUTH_CONFIRM_SUCCESS = "Email confirmed successfully. You can now log in."

# ── Auth — forgot / reset password ────────────────────────────────────────
# MSG_AUTH_FORGOT_PASSWORD_SUCCESS is deliberately identical whether or not an
# account exists for the given email (see CognitoAuthService.forgot_password) —
# never make this message conditional on account existence.
MSG_AUTH_FORGOT_PASSWORD_SUCCESS = (
    "If an account exists for this email, a password reset code has been sent."
)
MSG_AUTH_RESET_PASSWORD_SUCCESS = (
    "Password has been reset successfully. You can now log in with your new password."
)

# ── Auth — token / session flow ───────────────────────────────────────────

MSG_AUTH_CREDENTIALS_REQUIRED = "Authentication credentials are required."
MSG_AUTH_TOKEN_EXPIRED = "Token has expired."
MSG_AUTH_INVALID_TOKEN = "Invalid authentication credentials."
MSG_AUTH_PROFILE_LOAD_FAILED = "Could not load user profile."
MSG_AUTH_REFRESH_TOKEN_REQUIRED = "Refresh token is required."
MSG_AUTH_USERNAME_REQUIRED_FOR_REFRESH = (
    "Username (email) is required to refresh tokens. Provide it in the request body or re-login."
)
MSG_AUTH_ACCESS_TOKEN_REQUIRED = "Access token is required to log out."
MSG_AUTH_LOGIN_SUCCESS = "Login successful."
MSG_AUTH_REFRESH_SUCCESS = "Tokens refreshed successfully."
MSG_AUTH_LOGOUT_SUCCESS = "Logged out successfully."

# ── RBAC ──────────────────────────────────────────────────────────────────

MSG_RBAC_ROLES_REQUIRED = "This action requires one of these roles: {roles}."
MSG_RBAC_PERMISSIONS_MISSING = "Missing required permission(s): {permissions}."
MSG_RBAC_SUPER_ADMIN_ROLE_FORBIDDEN = "Only a super_admin can assign the super_admin role."
MSG_RBAC_ADMIN_ROLE_FORBIDDEN = "Only a super_admin can assign the admin (Client Admin) role."

# ── User ──────────────────────────────────────────────────────────────────

MSG_USER_NOT_FOUND = "User {user_id} not found."
MSG_USER_ACCOUNT_DEACTIVATED = "Your account has been deactivated."
MSG_USER_ROLE_NOT_FOUND = "Role '{role_name}' does not exist."
MSG_USER_DEACTIVATED_SUCCESS = "Account deactivated successfully."
MSG_USER_REMOVED = "User removed successfully."
MSG_USER_CANNOT_REMOVE_SELF = (
    "You cannot remove your own account via this endpoint; use DELETE /users/me instead."
)
MSG_USER_STATUS_UPDATED = "User status updated successfully."
MSG_USER_CANNOT_CHANGE_OWN_STATUS = "You cannot change your own active status via this endpoint."
MSG_USER_STATUS_CHANGE_FORBIDDEN_REMOVED = (
    "This user has been removed and can no longer be activated or deactivated."
)
MSG_USER_TOTAL = "{total} total user(s)."
MSG_USER_TENANT_NOT_ASSIGNED = "No tenant is assigned to the current user."
MSG_TENANT_NOT_FOUND = "Tenant {tenant_id} not found."
MSG_TENANT_ACCOUNT_DEACTIVATED = "Your organization's account has been deactivated."
MSG_TENANT_TOTAL = "{total} total tenant(s)."
MSG_TENANT_DEACTIVATED_SUCCESS = "Tenant deactivated successfully."
MSG_TENANT_STATS_FETCHED = "Tenant statistics fetched successfully."
MSG_TENANT_NAME_CONFLICT = "A tenant named '{name}' already exists."
MSG_TENANT_EMAIL_CONFLICT = "A tenant with contact email '{email}' already exists."
MSG_TENANT_CODE_GENERATION_FAILED = (
    "Unable to generate a unique tenant code for '{name}' after multiple attempts."
)
MSG_TENANT_FORBIDDEN_OTHER_TENANT = "You can only view your own tenant."
MSG_TENANT_LLM_PROVIDER_FORBIDDEN_OTHER_TENANT = (
    "You can only manage LLM provider configuration for your own tenant."
)
MSG_TENANT_LLM_PROVIDER_NO_API_KEY = (
    "No API key is configured for provider '{provider}'. Set one before testing it."
)
MSG_TENANT_LLM_PROVIDER_ACTIVATE_REQUIRES_API_KEY = (
    "Cannot activate provider '{provider}' without an API key. Set 'api_key' first."
)
MSG_TENANT_LLM_PROVIDER_UPDATED = "LLM provider configuration updated successfully."
MSG_TENANT_LLM_PROVIDER_NOT_FOUND = "No configuration found for provider '{provider}'."
MSG_TENANT_LLM_PROVIDER_DELETED = "LLM provider configuration deleted successfully."
MSG_TENANT_LLM_PROVIDER_BALANCE_NOT_SUPPORTED = (
    "Balance lookup is not supported for provider '{provider}'. Only providers "
    "with a documented balance API reachable via a plain API key are "
    "supported (currently: deepseek)."
)

SUMMARY_TENANT_LLM_PROVIDER_LIST = (
    "List this tenant's LLM provider configuration (enabled state, API key "
    "status, last test result)"
)
SUMMARY_TENANT_LLM_PROVIDER_UPDATE = (
    "Enable/disable a provider and/or set its API key for this tenant"
)
SUMMARY_TENANT_LLM_PROVIDER_TEST = (
    "Test the stored API key for a provider against the real provider API"
)
SUMMARY_TENANT_LLM_PROVIDER_DELETE = (
    "Delete a provider's configuration for this tenant (soft-delete)"
)
SUMMARY_TENANT_LLM_PROVIDER_BALANCE = (
    "Fetch the remaining account balance for a provider from the real provider API"
)

# ── Invitation ────────────────────────────────────────────────────────────

MSG_INVITATION_ALREADY_EXISTS = (
    "An active user or pending invitation already exists for this email."
)
# Deliberately generic — used for missing, expired, revoked, and
# already-accepted tokens alike so a public caller can't distinguish which
# case applies (same rationale as MSG_AUTH_INVALID_CREDENTIALS).
MSG_INVITATION_NOT_FOUND_OR_EXPIRED = "This invitation link is invalid or has expired."
MSG_INVITATION_SENT = "Invitation sent successfully."
MSG_INVITATION_ACCEPTED = "Invitation accepted successfully. You can now log in."
MSG_INVITATION_FORBIDDEN_OTHER_TENANT = "You can only invite members into your own tenant."
MSG_INVITATION_ROLE_REQUIRED = "A Client Admin must specify 'role' (member) when inviting."
MSG_INVITATION_ROLE_FORBIDDEN_FOR_CLIENT_ADMIN = "A Client Admin may only invite as 'member'."
MSG_INVITATION_ROLE_FORBIDDEN_FOR_SUPER_ADMIN = (
    "A super_admin may only invite as 'admin' — inviting as 'member' or "
    "any other role is a Client Admin's responsibility within their own "
    "tenant."
)
MSG_INVITATION_NOT_FOUND = "Invitation not found."
MSG_INVITATION_FORBIDDEN_ADMIN_ROLE = "Only a super_admin can resend or revoke this invitation."
MSG_INVITATION_CANNOT_RESEND = "Only a pending or expired invitation can be resent."
MSG_INVITATION_CANNOT_REVOKE = "Only a pending or expired invitation can be revoked."
MSG_INVITATION_RESENT = "Invitation resent successfully."
MSG_INVITATION_REVOKED = "Invitation revoked successfully."

SUMMARY_INVITATION_CREATE = (
    "Invite a user into a tenant, choosing the role explicitly (super_admin "
    "invites a Client Admin into any tenant; a Client Admin invites a "
    "Member into their own tenant only)"
)
SUMMARY_INVITATION_VALIDATE = "Validate an invitation token and return prefill details"
SUMMARY_INVITATION_ACCEPT = "Accept an invitation by setting a password"
SUMMARY_INVITATION_LIST = "List a tenant's invitations, optionally filtered by status"
SUMMARY_INVITATION_RESEND = (
    "Resend an invitation — issues a fresh token and expiry, reusing the existing Cognito identity"
)
SUMMARY_INVITATION_REVOKE = "Revoke a pending or expired invitation"

# ── Project ───────────────────────────────────────────────────────────────

MSG_PROJECT_NAME_CONFLICT = "A project named '{name}' already exists."
MSG_PROJECT_NOT_FOUND = "Project {project_id} not found."
MSG_PROJECT_LLM_PROVIDER_NOT_ENABLED = (
    "LLM provider '{provider}' is not enabled for tenant {tenant_id}."
)
MSG_PROJECT_LLM_MODEL_REQUIRES_PROVIDER = "llm_model '{model}' requires an llm_provider to be set."
MSG_PROJECT_LLM_MODEL_NOT_SUPPORTED = (
    "llm_model '{model}' is not supported by llm_provider '{provider}'."
)
MSG_PROJECT_LLM_API_KEY_NOT_CONFIGURED = (
    "No active, verified API key is configured for LLM provider '{provider}' "
    "on this tenant. Ask an admin to configure and verify it before uploading sources."
)
MSG_PROJECT_UPDATE_BLOCKED_PIPELINE_RUNNING = (
    "Cannot update this project while a generation pipeline is still "
    "running for it (source ingestion {ingestion_id}). Wait for it to "
    "finish before making changes."
)
MSG_PROJECT_UPDATE_FORBIDDEN = "Only the project owner or an admin can update this project."
MSG_PROJECT_TENANT_ASSIGN_FORBIDDEN = "Only a super_admin can assign or change a project's tenant."
MSG_PROJECT_DELETE_FORBIDDEN = "Only the project owner or an admin can delete this project."
MSG_PROJECT_CREATED = "Project created successfully."
MSG_PROJECT_UPDATED = "Project updated successfully."
# Generic content-access denial (modules/features/user-stories/sources/
# incremental-updates) — distinct from the project-entity-specific messages
# above. Parametrized by the required level so the caller knows what tier of
# project membership would have been sufficient.
MSG_PROJECT_CONTENT_ACCESS_FORBIDDEN = "You do not have '{level}' access to this project."
MSG_PROJECT_MEMBERS_MANAGE_FORBIDDEN = (
    "Only a super_admin or the project's Client Admin can manage project members."
)
MSG_PROJECT_MEMBER_NOT_FOUND = "User {user_id} is not a member of this project."
MSG_PROJECT_MEMBER_TENANT_MISMATCH = (
    "The target user must belong to the same tenant as the project."
)
MSG_PROJECT_MEMBER_ASSIGNED = "Project member assigned successfully."
MSG_PROJECT_MEMBER_REMOVED = "Project member removed successfully."
MSG_USER_PROJECTS_ASSIGNED = "User assigned to project(s) successfully."
MSG_USER_ROLES_UPDATED = "User roles updated successfully."

SUMMARY_PROJECT_LIST_SUMMARY = (
    "List every project visible to the caller (id, name, file count), "
    "unpaginated — every tenant project for an admin/super_admin, only "
    "owned/assigned projects for a Member; optionally filtered by "
    "backlog-generation stage"
)
SUMMARY_PROJECT_MEMBER_ASSIGN = "Assign a Member role to a user for this project"
SUMMARY_PROJECT_MEMBER_LIST = "List members assigned to this project"
SUMMARY_PROJECT_MEMBER_REMOVE = "Remove a user's project-scoped membership"
SUMMARY_USER_ASSIGN_PROJECTS = (
    "Replace a user's full set of project assignments in a single call "
    "(within the requester's tenant scope), each with the member role"
)

# ── Source ────────────────────────────────────────────────────────────────

MSG_SOURCE_UNSUPPORTED_TYPE = "Unsupported file type '{content_type}'. Allowed types: {allowed}."
MSG_SOURCE_FILE_TOO_LARGE = "File '{filename}' exceeds the maximum allowed size of {max_mb} MB."
MSG_SOURCE_NOT_FOUND = "Source {source_id} not found."
MSG_SOURCE_NO_STORAGE_KEY = "Source {source_id} has no associated file in storage."
MSG_SOURCE_DUPLICATE = "An identical file already exists in this project (source_id={source_id})."
MSG_SOURCE_BULK_FILES_REQUIRED = "At least one file is required for a bulk upload."
MSG_SOURCE_BULK_TOO_MANY = (
    "Bulk upload accepts at most {max_files} files per request (received {received})."
)
MSG_SOURCE_BULK_TOTAL_TOO_LARGE = (
    "Combined size of all files in this bulk upload exceeds the maximum"
    " allowed total of {max_mb} MB."
)
MSG_SOURCE_BULK_DUPLICATE = "A file with identical content already exists in this project."
MSG_SOURCE_BULK_DUPLICATE_FILENAME = (
    "A file named '{filename}' already exists in this project."
)
MSG_SOURCE_ZIP_NOT_SOURCE_CODE = (
    "'{filename}' does not appear to contain source code. "
    "ZIP files must include at least one source file "
    "(e.g. .py, .js, .ts, .java, .go, .rb, .php, .cs, .cpp, .c, .kt, .swift, "
    "or a recognised project manifest such as package.json, pom.xml, requirements.txt)."
)
MSG_SOURCE_DELETE_FORBIDDEN = "Only the uploader or an admin can delete this source."
MSG_SOURCE_BULK_DELETE_PROCESSED = "Bulk delete processed."
MSG_SOURCE_LINK_UPLOADED = "Source uploaded from link successfully."
MSG_SOURCE_BULK_PROCESSED = "Bulk upload processed."
MSG_SOURCE_INCREMENTAL_BULK_PROCESSED = "Incremental bulk upload processed."
MSG_SOURCE_INCREMENTAL_UNSUPPORTED_TYPE = (
    "Unsupported file type '{content_type}' for incremental upload. "
    "Only PDF and image files (JPEG, PNG, WebP) are allowed."
)
MSG_SOURCE_INCREMENTAL_REQUIRES_RFP_SOURCE = (
    "An incremental update requires at least one RFP source already uploaded "
    "for this project. Upload an initial RFP source first."
)
MSG_SOURCE_INCREMENTAL_MODULES_NOT_APPROVED = (
    "An incremental update requires all modules to be approved first. "
    "Please review and approve all modules before uploading new sources."
)
MSG_SOURCE_INCREMENTAL_FEATURES_NOT_APPROVED = (
    "An incremental update requires all features to be approved first. "
    "Please review and approve all features before uploading new sources."
)
MSG_SOURCE_INCREMENTAL_USER_STORIES_NOT_APPROVED = (
    "An incremental update requires all user stories to be approved first. "
    "Please review and approve all user stories before uploading new sources."
)
MSG_SOURCE_INCREMENTAL_NO_SOURCES_UPLOADED = (
    "No files in this batch could be uploaded (all were duplicates or failed validation)."
)
MSG_SOURCE_BULK_NO_SOURCES_UPLOADED = (
    "None of the files in this batch could be saved due to storage or database errors."
)
MSG_SOURCE_AI_DOWNLOAD_READY = "Source file downloaded locally for AI processing."
MSG_SOURCE_STORAGE_FAILED = (
    "'{filename}' could not be stored. The storage service returned an error. "
    "All other files in this batch were unaffected."
)
MSG_FRAGMENTS_LISTED = "Fragments listed successfully."
MSG_FRAGMENT_FETCHED = "Fragment fetched successfully."
MSG_FRAGMENT_BBOX_UPDATED = "Fragment bounding box updated successfully."
MSG_FRAGMENT_NOT_FOUND = "Fragment {fragment_id} not found for source {source_id}."
MSG_FRAGMENT_NOT_FOUND_FOR_PROJECT = "Fragment {fragment_id} not found for project {project_id}."
MSG_FRAGMENT_BY_ID_NOT_FOUND = "Fragment {fragment_id} not found."
MSG_FRAGMENT_GRAPH_SOURCE_NOT_FOUND = "Source node {source_id} not found in Neo4j graph."
MSG_MODULE_LISTED = "Modules listed successfully."
MSG_MODULE_FETCHED = "Module fetched successfully."
MSG_MODULE_REGENERATION_QUEUED = "Module-feature regeneration queued successfully."
MSG_MODULE_STATUS_CHANGED = "Module and feature status changed successfully."
MSG_MODULE_TREE_LISTED = "Module tree listed successfully."
MSG_PROJECT_SOURCES_NOT_FOUND = "No sources found for project {project_id}."
MSG_MODULE_NOT_FOUND = "Module {module_id} not found for source {source_id}."
MSG_FEATURE_FETCHED = "Feature fetched successfully."
MSG_FEATURE_NOT_FOUND = "Feature {feature_id} not found for module {module_id}."
MSG_MODULE_SYNC_FLAGS_UPDATED = "Module sync flags updated successfully."
MSG_FEATURE_SYNC_FLAGS_UPDATED = "Feature sync flags updated successfully."
MSG_MODULE_DELETED = "Module deleted successfully."
MSG_FEATURE_DELETED = "Feature deleted successfully."
MSG_MODULE_DELETE_REASON_REQUIRED = "reason is required when deleting an approved module."
MSG_FEATURE_DELETE_REASON_REQUIRED = "reason is required when deleting an approved feature."
MSG_USER_STORY_REGENERATION_FOR_SOURCE_CODE_QUEUED = "User story regeneration queued successfully."
MSG_SOURCE_CODE_METADATA_NOT_FOUND = (
    "No source-code metadata found for module {module_id} in project {project_id}."
)
MSG_MODULE_FEATURE_GENERATION_IN_PROGRESS = (
    "Module and feature generation is still running for this project (source ingestion "
    "{ingestion_id}). Wait for it to finish before requesting a regeneration."
)
MSG_FEATURE_REGENERATION_SOURCE_PROCESSING = (
    "Source-code module, feature, and user story generation is still running for this "
    "project (source ingestion {ingestion_id}). Wait for it to finish before requesting "
    "a feature regeneration."
)
MSG_SOURCE_CODE_PIPELINE_RUNNING_APPROVAL_BLOCKED = (
    "The source-code pipeline is still running for this project (source ingestion "
    "{ingestion_id}). Wait for it to finish before approving, giving feedback, or "
    "accepting/rejecting incremental updates."
)
MSG_RFP_MODULE_FEATURE_GENERATION_RUNNING_APPROVAL_BLOCKED = (
    "The RFP pipeline is still generating modules and features for this project "
    "(source ingestion {ingestion_id}). Wait for it to finish before approving, "
    "giving feedback, or accepting/rejecting incremental updates."
)
MSG_RFP_USER_STORY_GENERATION_RUNNING_APPROVAL_BLOCKED = (
    "The RFP pipeline is still generating user stories for this project (source "
    "ingestion {ingestion_id}). Wait for it to finish before approving, giving "
    "feedback, or accepting/rejecting incremental updates."
)
MSG_PIPELINE_RUNNING_APPROVAL_BLOCKED = (
    "A generation pipeline is still running for this project (source ingestion "
    "{ingestion_id}). Wait for it to finish before approving, giving feedback, or "
    "accepting/rejecting incremental updates."
)
MSG_MODULE_FEATURE_PENDING_FEEDBACK_CHANGE_APPROVAL_BLOCKED = (
    "This project has module(s) or feature(s) with a pending feedback-driven change "
    "(added, updated, or suggested for deletion) awaiting review. Please accept or "
    "reject the change first, then try approving again."
)
MSG_FEATURE_REGENERATION_TIME_LIMIT_EXCEEDED = "Feature regeneration exceeded its time limit."
MSG_SOURCE_CODE_MODULE_TIME_LIMIT_EXCEEDED = (
    "Increase TASK_SOURCE_CODE_MODULE_SOFT_TIME_LIMIT or investigate the LLM call."
)
MSG_SOURCE_CODE_MODULE_PERSIST_TIME_LIMIT_EXCEEDED = (
    "SoftTimeLimitExceeded while persisting module results."
)

# ── Stale SourceIngestion auto-recovery ────────────────────────────────────
MSG_SOURCE_INGESTION_STALE_AUTO_FAILED = (
    "Automatically marked as failed: this run exceeded the maximum expected "
    "processing time for its stage, indicating the worker crashed or was "
    "terminated before finishing. Please retry generation."
)
MSG_SOURCE_INGESTION_STALE_NOTIFICATION_TITLE = "Generation Failed"
MSG_SOURCE_INGESTION_STALE_NOTIFICATION_MESSAGE = (
    "Run {run_code} stalled and was automatically marked as failed after "
    "exceeding its expected processing time. You can retry generation for "
    "this project."
)
# Advisory-only: source_code ingestions are never auto-failed (see
# SourceIngestionService.is_advisory_stale_source_code) — this is a log
# message for a human to go check, not a user-facing notification.
MSG_SOURCE_CODE_INGESTION_ADVISORY_STALE = (
    "source_code ingestion running implausibly long — may be a dead worker, "
    "or may just be a large multi-module project; check queue depth/worker "
    "activity for this project before assuming either."
)
# ── Link downloader ───────────────────────────────────────────────────────

MSG_LINK_HTTPS_ONLY = "Only HTTPS URLs are supported for link uploads."
MSG_LINK_MISSING_HOSTNAME = "Invalid URL: missing hostname."
MSG_LINK_PRIVATE_ADDRESS = "Requests to private or internal network addresses are not allowed."
MSG_LINK_FILE_TOO_LARGE = (
    "Remote file size ({size_mb} MB) exceeds the {limit_mb} MB limit for link uploads."
)
MSG_LINK_CONTENT_TOO_LARGE = (
    "Downloaded content exceeded the {limit_mb} MB limit for link uploads. Download aborted."
)
MSG_LINK_HTTP_ERROR = "Failed to download from URL (HTTP {status_code}): {url}"
MSG_LINK_NETWORK_ERROR = "Could not reach URL '{url}': {detail}"

# ── Rate limiter ──────────────────────────────────────────────────────────

MSG_RATE_LIMIT_EXCEEDED = "Too many requests. Please retry after {retry_after} seconds."

# ── LLM provider errors ───────────────────────────────────────────────────

MSG_LLM_CREDIT_EXHAUSTED = (
    "The AI provider account has run out of credits or quota. Please contact your "
    "administrator to top up billing before retrying this operation."
)
MSG_LLM_AUTHENTICATION = (
    "The AI provider rejected the request due to an invalid or expired API key. "
    "Please contact your administrator to update the provider credentials."
)
MSG_LLM_PERMISSION_DENIED = (
    "The AI provider denied access for this operation. Please contact your "
    "administrator to check the provider account's permissions."
)
MSG_LLM_INVALID_MODEL = (
    "The configured AI model is invalid or unsupported by the provider. Please "
    "contact your administrator to review the model configuration."
)
MSG_LLM_INVALID_REQUEST = (
    "The AI provider rejected the request as invalid. Please contact your "
    "administrator — retrying will not resolve this."
)
MSG_LLM_CONTEXT_LENGTH_EXCEEDED = (
    "The input is too large for the AI model's context limit. Please reduce the "
    "input size or split it into smaller parts before retrying."
)
MSG_LLM_CONTENT_POLICY = (
    "The AI provider rejected the request due to its content policy. Please "
    "review the input content before retrying."
)
MSG_LLM_GENERIC_NONRETRYABLE = (
    "The AI provider returned an error that cannot be resolved by retrying. "
    "Please contact your administrator."
)

# ── Generic ───────────────────────────────────────────────────────────────

MSG_GENERIC_ERROR = "An error occurred."
MSG_GENERIC_UNEXPECTED_ERROR = "An unexpected error occurred."

# ── Route summaries — Auth ────────────────────────────────────────────────

SUMMARY_AUTH_REGISTER = "Register a new user account via Cognito"
SUMMARY_AUTH_CONFIRM = "Confirm email address with the verification code sent by Cognito"
SUMMARY_AUTH_LOGIN = "Authenticate with email and password"
SUMMARY_AUTH_REFRESH = "Refresh access and ID tokens using a refresh token"
SUMMARY_AUTH_LOGOUT = "Globally invalidate all tokens for the authenticated user"
SUMMARY_AUTH_ME = "Return the authenticated user's profile from JWT claims"
SUMMARY_AUTH_FORGOT_PASSWORD = "Request a password reset code via email"
SUMMARY_AUTH_RESET_PASSWORD = "Reset password using the emailed verification code"

# ── Route summaries — Project ──────────────────────────────────────────────

SUMMARY_PROJECT_CREATE = "Create a new project"
SUMMARY_PROJECT_LIST = "List projects owned by the authenticated user"
SUMMARY_PROJECT_LIST_ALL = "List all projects in the platform (admin only)"
SUMMARY_PROJECT_GET = "Get a project by ID"
SUMMARY_PROJECT_UPDATE = "Update a project (owner or admin)"
SUMMARY_PROJECT_DELETE = "Delete a project (owner or admin)"

# ── Route summaries — User ────────────────────────────────────────────────

SUMMARY_USER_GET_ME = "Return the authenticated user's full profile"
SUMMARY_USER_UPDATE_ME = "Update the authenticated user's display name"
SUMMARY_USER_DEACTIVATE_ME = "Soft-deactivate the authenticated user's account"
SUMMARY_USER_LIST = "List all users (admin only)"
SUMMARY_USER_GET = "Get any user by ID (admin only)"
SUMMARY_USER_ASSIGN_ROLE = "Assign a role to a user (admin only)"
SUMMARY_USER_REVOKE_ROLE = "Revoke a role from a user (admin only)"
SUMMARY_USER_UPDATE_ROLES = "Replace a user's full role set in one call (admin only)"
SUMMARY_USER_REMOVE = (
    "Permanently remove a user — deletes their Cognito identity and frees "
    "their email for reuse (admin only)"
)
SUMMARY_USER_UPDATE_STATUS = "Activate or deactivate a user (admin only)"

# ── Route summaries — Tenant ───────────────────────────────────────────────

SUMMARY_TENANT_GET_ME = "Return the tenant assigned to the authenticated user"
SUMMARY_TENANT_LIST = "List tenants (super_admin: all; Client Admin: own tenant only)"
SUMMARY_TENANT_CREATE = "Create a new tenant (super_admin only)"
SUMMARY_TENANT_GET = "Get a tenant by ID (super_admin: any; Client Admin: own tenant only)"
SUMMARY_TENANT_UPDATE = "Update a tenant (admin only)"
SUMMARY_TENANT_REPLACE = (
    "Fully replace a tenant (PUT — every field reflects the complete desired "
    "state; omitted address/providers are cleared; admin only)"
)
SUMMARY_TENANT_DEACTIVATE = "Deactivate a tenant (admin only)"
SUMMARY_TENANT_STATS = (
    "Platform-wide tenant/project counts: total, active, pending-invitation, "
    "deactivated tenants, and total projects (super_admin only)"
)

# ── Route summaries — Source ──────────────────────────────────────────────

SUMMARY_SOURCE_DOWNLOAD = "Download a single source file from S3 by source ID"
SUMMARY_SOURCE_BULK_UPLOAD = "Upload multiple source files in one request"
SUMMARY_SOURCE_INCREMENTAL_BULK_UPLOAD = (
    "Upload PDF or image files and run an incremental backlog update"
)
SUMMARY_SOURCE_LINK_UPLOAD = (
    "Download and store a source from a URL (GitHub, SharePoint, Nextcloud)"
)
SUMMARY_SOURCE_LIST = "List sources for a project (paginated, filterable)"
SUMMARY_SOURCE_GET = "Get a single source by ID"
SUMMARY_SOURCE_DELETE = "Soft-delete a source (uploader or admin)"
SUMMARY_SOURCE_BULK_DELETE = "Soft-delete multiple sources in one request (uploader or admin)"
SUMMARY_SOURCE_AI_DOWNLOAD = "Download a source file to local storage for AI processing"
SUMMARY_SOURCE_INGESTION_LIST = "List source ingestions for a project (paginated, filterable)"
SUMMARY_FRAGMENT_LIST_BY_PROJECT = "List all fragments for a project"
SUMMARY_FRAGMENT_GET = "Get a single fragment by ID for a project"
SUMMARY_FRAGMENT_UPDATE_BBOX = "Update bounding-box coordinates of a fragment"
SUMMARY_MODULE_LIST = "List all modules for a source"
SUMMARY_MODULE_GET = "Get a single module by ID for a source"
SUMMARY_FEATURE_GET = "Get a single feature by ID for a module"
SUMMARY_MODULE_REGENERATE = "Regenerate modules and features using human feedback"
SUMMARY_MODULE_CHANGE_STATUS = "Change status for a module and its features"
SUMMARY_MODULE_UPDATE_SYNC_FLAGS = "Update Jira/TAP sync flags for a module"
SUMMARY_FEATURE_UPDATE_SYNC_FLAGS = "Update Jira/TAP sync flags for a feature"
SUMMARY_MODULE_TREE_LIST = "List modules with features and functions as a tree"
SUMMARY_MODULE_DELETE = "Delete a module and its features"
SUMMARY_FEATURE_DELETE = "Delete a single feature"
SUMMARY_USER_STORY_REGENERATE_FOR_SOURCE_CODE = (
    "Regenerate source-code-derived features and their user stories using human feedback"
)
DESC_MODULE_REGENERATE = (
    "Enqueue an AI re-generation of modules and features for a project using optional human feedback. "
    "The response includes a `task_id`; connect to "
    "`/ws/projects/{project_id}` to receive real-time progress updates."
)
DESC_MODULE_FILTER_SOURCE_INGESTION_ID = (
    "Filter by the SourceIngestion that created or last regenerated a module (exact match)"
)
DESC_USER_STORY_REGENERATE_FOR_SOURCE_CODE = (
    "Enqueue an AI regeneration of one or more source-code-derived features (and their user stories) "
    "using human feedback. Each feedback item carries its own mod_code/mfu_id, so one request can "
    "target several MFUs. Runs the Stage 5 MFU-regeneration pipeline against each MFU's stored "
    "source-code specs. The response includes a `task_id`; connect to "
    "`/ws/projects/{project_id}` to receive real-time progress updates."
)

# ── User Story ───────────────────────────────────────────────────────────

MSG_USER_STORY_LISTED = "User Stories listed successfully."
MSG_USER_STORY_NOT_FOUND_BY_ID = "User Story {user_story_id} not found."
MSG_USER_STORY_REGENERATION_QUEUED = "User Story regeneration queued successfully."
MSG_USER_STORY_DELETED = "User Stories deleted successfully."
MSG_USER_STORY_SINGLE_DELETED = "User Story deleted successfully."
MSG_USER_STORY_DELETE_REASON_REQUIRED = "reason is required when deleting an approved user story."
MSG_USER_STORY_SYNC_FLAGS_UPDATED = "User story sync flags updated successfully."

# ── Route summaries — User Story ─────────────────────────────────────────

SUMMARY_USER_STORY_LIST_BY_PROJECT = "List all user stories for a project (paginated)"
SUMMARY_USER_STORY_CHANGE_STATUS = "Change the status of a user story"
SUMMARY_USER_STORY_UPDATE_SYNC_FLAGS = "Update Jira/TAP sync flags for a user story"
SUMMARY_USER_STORY_CHANGE_STATUS_BY_PROJECT = "Change the status of all user stories for a project"
MSG_PROJECT_USER_STORIES_STATUS_CHANGED = "All project user story statuses changed successfully."
SUMMARY_USER_STORY_BULK_CHANGE_STATUS_BY_IDS = (
    "Bulk-change the status of specific user stories by IDs within a project"
)
MSG_USER_STORY_BULK_STATUS_CHANGED = "User story statuses updated successfully."
SUMMARY_USER_STORY_GET_BY_PROJECT = "Get a single user story with sources and fragments by project"
SUMMARY_USER_STORY_REGENERATE = "Regenerate user stories for a project using human feedback"
SUMMARY_USER_STORY_REGENERATE_BY_FEEDBACK = (
    "Regenerate selected user stories using targeted per-story feedback"
)
SUMMARY_USER_STORY_DELETE_BY_PROJECT = "Delete all user stories for a project"
SUMMARY_USER_STORY_DELETE_BY_ID = "Delete a single user story by ID"
DESC_USER_STORY_REGENERATE = (
    "Enqueue an AI re-generation of user stories for a project using optional human feedback. "
    "The response includes a `task_id`; connect to "
    "`/ws/projects/{project_id}` to receive real-time progress updates."
)
MSG_USER_STORY_DETAIL_FETCHED = "User Story detail fetched successfully."
MSG_USER_STORY_REGENERATION_BY_FEEDBACK_QUEUED = (
    "User story feedback-based regeneration queued successfully."
)
DESC_USER_STORY_REGENERATE_BY_FEEDBACK = (
    "Enqueue a targeted AI patch of selected user stories driven by per-story feedback. "
    "The request body is a **JSON array** where each element identifies one user story by its ID "
    "and carries optional whole-story feedback and/or inline selection-level comments.\n\n"
    "The pipeline fetches each targeted story from Neo4j, builds feature contexts with sibling "
    "stories, retrieves the project persona glossary, and calls the patch AI graph. "
    "Only the targeted stories are revised — siblings are preserved unchanged.\n\n"
    "The response includes a `task_id`; connect to "
    "`/ws/projects/{project_id}` to receive real-time progress updates."
)
MSG_USER_STORY_FEEDBACK_ALREADY_IN_PROGRESS = (
    "User stories {user_story_ids} are already being patched by a running source "
    "ingestion ({ingestion_id}). Wait for that ingestion to finish before submitting "
    "new feedback for the same stories."
)
MSG_USER_STORY_GENERATION_IN_PROGRESS = (
    "User story generation is still running for this project (source ingestion "
    "{ingestion_id}). Wait for it to finish before requesting a regeneration or "
    "submitting feedback."
)

# ── Query parameter descriptions — User Story ────────────────────────────

DESC_USER_STORY_FILTER_STATUS = "Filter by status (draft, in-review, conflict, approved, rejected)"
DESC_USER_STORY_FILTER_VERSION = "Filter by version number e.g. 1"
DESC_USER_STORY_FILTER_MODULE_ID = "Filter by module ID (exact match)"
DESC_USER_STORY_FILTER_FEATURE_ID = "Filter by feature ID (exact match)"
DESC_USER_STORY_FILTER_SOURCE_ID = "Filter by source ID (exact match)"
DESC_USER_STORY_FILTER_SEARCH_TEXT = "Full-text search across user story title, code, module name, feature name, and source name (case-insensitive partial match)"
DESC_USER_STORY_FILTER_CODE = "Filter by user story code (case-insensitive partial match)"
DESC_USER_STORY_FILTER_CONSENSUS_MIN = "Minimum consensus score (0.0–10.0)"
DESC_USER_STORY_FILTER_CONSENSUS_MAX = "Maximum consensus score (0.0–10.0)"
DESC_USER_STORY_FILTER_SOURCE_INGESTION_ID = (
    "Filter by the SourceIngestion that created or last regenerated a module, feature, or "
    "user story (exact match, matched at whichever level carries it)"
)
DESC_USER_STORY_FILTER_SYNC_TARGET = (
    "Which sync target to check for unsynced approved user stories (jira or tap)"
)
SUMMARY_PROJECT_USER_STORY_SUMMARY = "Summarise user stories across a project"
MSG_PROJECT_USER_STORY_SUMMARY_FETCHED = "Project user story summary fetched successfully."
MSG_USER_STORY_STATUS_CHANGED = "User story status changed successfully."
MSG_USER_STORY_BBOXES_UPDATED = "User story bboxes updated successfully."
SUMMARY_USER_STORY_UPDATE_BBOXES = "Update bboxes for a user story by ID"
SUMMARY_USER_STORY_TREE_BY_PROJECT = "Get the full module → feature → user story tree for a project"
MSG_USER_STORY_TREE_FETCHED = "User story tree fetched successfully."

SUMMARY_USER_STORY_SYNC_CANDIDATES = (
    "Get the module → feature → user story tree pruned to approved, not-yet-synced stories"
)
MSG_USER_STORY_SYNC_CANDIDATES_FETCHED = "Sync candidate user stories fetched successfully."

SUMMARY_INCREMENTAL_UPDATES_TREE = "Get the module → feature → user story tree overlaid with the latest pending incremental proposal"
MSG_INCREMENTAL_UPDATES_TREE_FETCHED = "Incremental updates tree fetched successfully."

SUMMARY_INCREMENTAL_UPDATES_LIST = "Get the module → feature → user story tree pruned to only incremental_change_type-flagged nodes"
MSG_INCREMENTAL_UPDATES_LIST_FETCHED = "Incremental updates list fetched successfully."

SUMMARY_UPDATES_ACCEPT = "Accept a pending incremental change for a module, feature, or user story"
MSG_UPDATES_ACCEPTED = "Update accepted successfully."
SUMMARY_UPDATES_REJECT = "Reject a pending incremental change for a module, feature, or user story"
MSG_UPDATES_REJECTED = "Update rejected successfully."
MSG_UPDATE_ENTITY_NOT_FOUND = "{entity_type} {entity_id} not found for project {project_id}."

SUMMARY_UNIFIED_UPDATES_ACCEPT = (
    "Accept a pending incremental or feedback-driven change for a module, feature, or user story, "
    "regardless of which review flow flagged it"
)
SUMMARY_UNIFIED_UPDATES_REJECT = (
    "Reject a pending incremental or feedback-driven change for a module, feature, or user story, "
    "regardless of which review flow flagged it"
)
MSG_UPDATE_NO_PENDING_CHANGE = (
    "{entity_type} {entity_id} has no pending incremental or feedback change for project "
    "{project_id}."
)

SUMMARY_FEEDBACK_UPDATES_ACCEPT = (
    "Accept a pending feedback-driven regeneration change for a module or feature"
)
MSG_FEEDBACK_UPDATES_ACCEPTED = "Feedback change accepted successfully."
SUMMARY_FEEDBACK_UPDATES_REJECT = (
    "Reject a pending feedback-driven regeneration change for a module or feature"
)
MSG_FEEDBACK_UPDATES_REJECTED = "Feedback change rejected successfully."
MSG_FEEDBACK_DELETE_SUGGESTED_NOT_SUPPORTED = (
    "DELETE_SUGGESTED feedback changes are not yet supported."
)

SUMMARY_PROJECT_DASHBOARD_STATS = (
    "Get dashboard statistics (active projects, modules, features, stories, pipelines), "
    "scoped to what the caller may see"
)
MSG_PROJECT_DASHBOARD_STATS_FETCHED = "Dashboard statistics fetched successfully."

# ── Query / form parameter descriptions — Project ─────────────────────────

DESC_PROJECT_SEARCH = (
    "Case-insensitive partial match on project name. "
    "Returns all projects whose name contains the given string."
)
DESC_PROJECT_SEARCH_TITLE = "Search"

DESC_PROJECT_ALL_TENANT_FILTER = (
    "Filter by tenant. Only a super_admin can use this to view another "
    "tenant's projects, or omit it to see every tenant; for a plain admin "
    "it is ignored and their own tenant is always used instead."
)
DESC_PROJECT_ALL_TENANT_FILTER_TITLE = "Tenant"

DESC_PROJECT_STAGE_FILTER = (
    "Filter by backlog-generation progress: 'fresh' returns projects with "
    "no modules/features or user stories yet; 'module_feature_only' "
    "returns projects that have modules/features but no user stories yet; "
    "'user_story_created' returns projects that have at least one user "
    "story. Omit to return every project regardless of progress."
)
DESC_PROJECT_STAGE_FILTER_TITLE = "Stage"

# ── Query / form parameter descriptions — User ────────────────────────────

DESC_USER_LIST_TENANT_FILTER = (
    "Filter by tenant. Only a super_admin can use this to view another "
    "tenant's users, or omit it to see every tenant; for a plain admin it "
    "is ignored and their own tenant is always used instead."
)
DESC_USER_LIST_TENANT_FILTER_TITLE = "Tenant"

# ── Query / form parameter descriptions — Source ─────────────────────────

DESC_SOURCE_FILES = (
    "Files or folder contents to upload (PDF, DOC, DOCX, XLS, XLSX, CSV, images, ZIP; max 20). "
    "When uploading a folder, relative paths are preserved in metadata."
)
DESC_SOURCE_PROJECT_ID = "Project these sources belong to"
DESC_SOURCE_DESCRIPTION = "Optional note applied to all files in this batch"
DESC_SOURCE_TYPE = "Client-supplied source category: rfp | additional_rfp | source_code | meeting_notes | requirement_update"
DESC_SOURCE_FILTER_PROJECT = "Filter by project"
DESC_SOURCE_FILTER_STATUS = (
    "Filter by status (uploaded, queued, processing, ready_for_review, failed)"
)
DESC_SOURCE_FILTER_FORMAT = "Filter by type e.g. PDF, DOCX, PNG"
DESC_SOURCE_FILTER_UPLOAD_TYPE = "Filter by upload type: single | bulk"
DESC_SOURCE_INGESTION_FILTER_PROJECT = "Filter ingestions by project"
DESC_SOURCE_INGESTION_FILTER_STATUS = (
    "Filter ingestions by status (running, ready_for_review, completed, failed)"
)
DESC_SOURCE_INGESTION_FILTER_SOURCE_TYPE = "Filter ingestions by source type (rfp, additional_rfp, source_code, meeting_notes, requirement_update)"

# ── Schema field descriptions ─────────────────────────────────────────────

DESC_AUTH_CONFIRM_CODE = "6-digit verification code"
DESC_LINK_URL = (
    "Publicly accessible HTTPS URL to download from. "
    "Supported sources: GitHub repository, GitHub file, SharePoint, Nextcloud. "
    "Maximum download size: 500 MB."
)


# ── Notification ─────────────────────────────────────────────────────────

MSG_NOTIFICATION_NOT_FOUND = "Notification {notification_id} not found."
MSG_NOTIFICATION_MARKED_READ = "Notification marked as read."
MSG_NOTIFICATION_ALL_MARKED_READ = "All notifications marked as read."

DESC_NOTIFICATION_FILTER_IS_READ = "Filter by read state (true = read only, false = unread only)"
DESC_NOTIFICATION_FILTER_TYPE = "Filter by notification type (info, success, warning, error)"

SUMMARY_NOTIFICATION_LIST = "List in-app notifications for the authenticated user"
SUMMARY_NOTIFICATION_UNREAD_COUNT = "Return the unread notification count (bell-icon badge)"
SUMMARY_NOTIFICATION_MARK_READ = "Mark a single notification as read"
SUMMARY_NOTIFICATION_MARK_ALL_READ = "Mark all notifications as read"

# ── Activity Log ──────────────────────────────────────────────────────────

MSG_ACTIVITY_LOG_LIST_FETCHED = "Activity logs fetched successfully."
DESC_ACTIVITY_LOG_FILTER_TYPE = "Filter by activity type"
SUMMARY_ACTIVITY_LOG_LIST = "List activity-log entries for a project"

SUMMARY_ACTIVITY_PROJECT_CREATED = "Project Created"
MSG_ACTIVITY_PROJECT_CREATED = (
    'Created project "{project_name}" (type: {project_type}, '
    "provider: {llm_provider}, model: {llm_model})"
)

SUMMARY_ACTIVITY_PROJECT_UPDATED = "Project Updated"
MSG_ACTIVITY_PROJECT_UPDATED = (
    'Updated project "{project_name}" (type: {project_type}, '
    "provider: {llm_provider}, model: {llm_model})"
)

SUMMARY_ACTIVITY_RFP_MODULES_GENERATION_STARTED = "Modules & Features Generation Started"
MSG_ACTIVITY_RFP_MODULES_GENERATION_STARTED = "Started generating modules & features"

SUMMARY_ACTIVITY_RFP_MODULES_GENERATED = "Modules & Features Generated"
MSG_ACTIVITY_RFP_MODULES_GENERATED = "Generated {total_modules} Modules, {total_features} Features"

SUMMARY_ACTIVITY_RFP_MODULES_GENERATION_FAILED = "Modules & Features Generation Failed"
MSG_ACTIVITY_RFP_MODULES_GENERATION_FAILED = "Failed to generate modules & features: {error}"

SUMMARY_ACTIVITY_RFP_MODULES_GENERATION_CANCELLED = "Modules & Features Generation Cancelled"
MSG_ACTIVITY_RFP_MODULES_GENERATION_CANCELLED = "Cancelled generating modules & features"

SUMMARY_ACTIVITY_RFP_MODULES_REGENERATION_STARTED = "Modules & Features Regeneration Started"
MSG_ACTIVITY_RFP_MODULES_REGENERATION_STARTED = (
    "Started regenerating modules & features from feedback"
)

SUMMARY_ACTIVITY_RFP_MODULES_REGENERATED = "Modules & Features Regenerated"
MSG_ACTIVITY_RFP_MODULES_REGENERATED = (
    "Regenerated: {tot_modules} module(s) added, {tot_modules_updated} module(s) updated, "
    "{tot_features} feature(s) added, {tot_features_updated} feature(s) updated"
)

SUMMARY_ACTIVITY_RFP_MODULES_REGENERATION_FAILED = "Modules & Features Regeneration Failed"
MSG_ACTIVITY_RFP_MODULES_REGENERATION_FAILED = "Failed to regenerate modules & features: {error}"

SUMMARY_ACTIVITY_RFP_MODULES_REGENERATION_CANCELLED = "Modules & Features Regeneration Cancelled"
MSG_ACTIVITY_RFP_MODULES_REGENERATION_CANCELLED = (
    "Cancelled regenerating modules & features from feedback"
)

SUMMARY_ACTIVITY_MODULE_FEATURE_APPROVED = "Modules & Features Approved"
MSG_ACTIVITY_MODULE_FEATURE_APPROVED = "Approved {total_modules} Modules, {total_features} Features"

SUMMARY_ACTIVITY_USER_STORIES_APPROVED = "User Stories Approved"
MSG_ACTIVITY_USER_STORIES_APPROVED = "Approved {total_user_stories} User Stories"

SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATION_STARTED = "User Stories Generation Started"
MSG_ACTIVITY_RFP_USER_STORIES_GENERATION_STARTED = "Started generating user stories"

SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATED = "User Stories Generated"
MSG_ACTIVITY_RFP_USER_STORIES_GENERATED = "Generated {total_items} User Stories"

SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATION_FAILED = "User Stories Generation Failed"
MSG_ACTIVITY_RFP_USER_STORIES_GENERATION_FAILED = "Failed to generate user stories: {error}"

SUMMARY_ACTIVITY_RFP_USER_STORIES_GENERATION_CANCELLED = "User Stories Generation Cancelled"
MSG_ACTIVITY_RFP_USER_STORIES_GENERATION_CANCELLED = "Cancelled generating user stories"

SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATION_STARTED = "User Stories Regeneration Started"
MSG_ACTIVITY_RFP_USER_STORIES_REGENERATION_STARTED = "Started regenerating user stories"

SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATED = "User Stories Regenerated"
MSG_ACTIVITY_RFP_USER_STORIES_REGENERATED = "Regenerated {total_items} User Stories"

SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATION_FAILED = "User Stories Regeneration Failed"
MSG_ACTIVITY_RFP_USER_STORIES_REGENERATION_FAILED = "Failed to regenerate user stories: {error}"

SUMMARY_ACTIVITY_RFP_USER_STORIES_REGENERATION_CANCELLED = "User Stories Regeneration Cancelled"
MSG_ACTIVITY_RFP_USER_STORIES_REGENERATION_CANCELLED = "Cancelled regenerating user stories"

SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATION_STARTED = (
    "User Stories Regeneration from Feedback Started"
)
MSG_ACTIVITY_RFP_FEEDBACK_REGENERATION_STARTED = "Started regenerating user stories from feedback"

SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATED = "User Stories Regenerated from Feedback"
MSG_ACTIVITY_RFP_FEEDBACK_REGENERATED = (
    "Regenerated from feedback: {tot_user_stories} user story(ies) added, "
    "{tot_user_stories_updated} user story(ies) updated"
)

SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATION_FAILED = "User Stories Regeneration from Feedback Failed"
MSG_ACTIVITY_RFP_FEEDBACK_REGENERATION_FAILED = (
    "Failed to regenerate user stories from feedback: {error}"
)

SUMMARY_ACTIVITY_RFP_FEEDBACK_REGENERATION_CANCELLED = (
    "User Stories Regeneration from Feedback Cancelled"
)
MSG_ACTIVITY_RFP_FEEDBACK_REGENERATION_CANCELLED = (
    "Cancelled regenerating user stories from feedback"
)

SUMMARY_ACTIVITY_SOURCE_CODE_PIPELINE_STARTED = "Source Code Pipeline Started"
MSG_ACTIVITY_SOURCE_CODE_PIPELINE_STARTED = "Started processing source code"

SUMMARY_ACTIVITY_SOURCE_CODE_PIPELINE_COMPLETED = "Source Code Pipeline Completed"
MSG_ACTIVITY_SOURCE_CODE_PIPELINE_COMPLETED = (
    "Processed source code: {total_modules} Modules, {total_features} Features, "
    "{total_user_stories} User Stories"
)

SUMMARY_ACTIVITY_SOURCE_CODE_PIPELINE_FAILED = "Source Code Pipeline Failed"
MSG_ACTIVITY_SOURCE_CODE_PIPELINE_FAILED = "Failed to process source code: {error}"

SUMMARY_ACTIVITY_SOURCE_CODE_GLOBAL_ARTIFACTS_COMPLETED = "Global Artifacts Generated"
MSG_ACTIVITY_SOURCE_CODE_GLOBAL_ARTIFACTS_COMPLETED = (
    "Generated global artifacts — {modules_detected} modules detected"
)

SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_STARTED = "Module Processing Started"
MSG_ACTIVITY_SOURCE_CODE_MODULE_STARTED = "Started processing module {module_name}"

SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_COMPLETED = "Module Processing Completed"
MSG_ACTIVITY_SOURCE_CODE_MODULE_COMPLETED = (
    "Processed module {module_name}: {total_features} Feature(s), "
    "{total_user_stories} User Story(ies)"
)

SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_FAILED = "Module Processing Failed"
MSG_ACTIVITY_SOURCE_CODE_MODULE_FAILED = "Failed to process module {module_name}: {error}"

SUMMARY_ACTIVITY_SOURCE_CODE_MODULE_CANCELLED = "Module Processing Cancelled"
MSG_ACTIVITY_SOURCE_CODE_MODULE_CANCELLED = "Cancelled processing module {module_name}"

SUMMARY_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_COMPLETED = "Domain Knowledge Generated"
MSG_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_COMPLETED = "Generated the domain-knowledge document"

SUMMARY_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_FAILED = "Domain Knowledge Generation Failed"
MSG_ACTIVITY_SOURCE_CODE_DOMAIN_KNOWLEDGE_FAILED = (
    "Failed to generate the domain-knowledge document: {error}"
)

SUMMARY_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_COMPLETED = "Architecture Document Generated"
MSG_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_COMPLETED = (
    "Generated the architecture document"
)

SUMMARY_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_FAILED = (
    "Architecture Document Generation Failed"
)
MSG_ACTIVITY_SOURCE_CODE_ARCHITECTURE_DOCUMENT_FAILED = (
    "Failed to generate the architecture document: {error}"
)

SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_STARTED = (
    "Feature Regeneration from Feedback Started"
)
MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_STARTED = (
    "Started regenerating {feature_count} feature(s) from feedback"
)

SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATED = "Features Regenerated from Feedback"
MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATED = (
    "Regenerated {feature_count} feature(s) from feedback"
)

SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED = (
    "Feature Regeneration from Feedback Failed"
)
MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_FAILED = (
    "Failed to regenerate features from feedback: {error}"
)

SUMMARY_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED = (
    "Feature Regeneration from Feedback Cancelled"
)
MSG_ACTIVITY_SOURCE_CODE_FEEDBACK_REGENERATION_CANCELLED = (
    "Cancelled regenerating {feature_count} feature(s) from feedback"
)

SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_STARTED = "Incremental Change-Set Started"
MSG_ACTIVITY_INCREMENTAL_CHANGESET_STARTED = "Started ingesting incremental change-set"

SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_INGESTED = "Incremental Change-Set Ingested"
MSG_ACTIVITY_INCREMENTAL_CHANGESET_INGESTED = (
    "Ingested change-set: {adds} added, {updates} updated, {deletes} deleted"
)

SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_FAILED = "Incremental Change-Set Failed"
MSG_ACTIVITY_INCREMENTAL_CHANGESET_FAILED = "Failed to ingest incremental change-set: {error}"

SUMMARY_ACTIVITY_INCREMENTAL_CHANGESET_CANCELLED = "Incremental Change-Set Cancelled"
MSG_ACTIVITY_INCREMENTAL_CHANGESET_CANCELLED = "Cancelled ingesting incremental change-set"

SUMMARY_ACTIVITY_INCREMENTAL_CHANGE_ACCEPTED = "Change Accepted"
MSG_ACTIVITY_INCREMENTAL_CHANGE_ACCEPTED = "Approved change: {entity_type} {change_type}"

SUMMARY_ACTIVITY_INCREMENTAL_CHANGE_REJECTED = "Change Rejected"
MSG_ACTIVITY_INCREMENTAL_CHANGE_REJECTED = "Rejected change: {entity_type} {change_type}"

SUMMARY_ACTIVITY_FEEDBACK_CHANGE_ACCEPTED = "Feedback Change Accepted"
MSG_ACTIVITY_FEEDBACK_CHANGE_ACCEPTED = "Approved feedback change: {entity_type} {change_type}"

SUMMARY_ACTIVITY_FEEDBACK_CHANGE_REJECTED = "Feedback Change Rejected"
MSG_ACTIVITY_FEEDBACK_CHANGE_REJECTED = "Rejected feedback change: {entity_type} {change_type}"

SUMMARY_ACTIVITY_REQUIREMENT_UPDATE_REVIEW_COMPLETED = "Requirement Update Review Completed"
MSG_ACTIVITY_REQUIREMENT_UPDATE_REVIEW_COMPLETED = (
    "Review completed: {total_accepted} item(s) accepted, {total_rejected} item(s) rejected"
)

SUMMARY_ACTIVITY_JIRA_SYNC_STARTED = "Jira Sync Started"
MSG_ACTIVITY_JIRA_SYNC_STARTED = "Started syncing to Jira"

SUMMARY_ACTIVITY_JIRA_SYNC_COMPLETED = "Jira Sync Completed"
MSG_ACTIVITY_JIRA_SYNC_COMPLETED = (
    "Synced to Jira: {created} created, {updated} updated, {deprecated} deprecated, "
    "{errors} error(s)"
)

SUMMARY_ACTIVITY_JIRA_SYNC_FAILED = "Jira Sync Failed"
MSG_ACTIVITY_JIRA_SYNC_FAILED = "Jira sync failed: {error}"

SUMMARY_ACTIVITY_TAP_SYNC_STARTED = "TAP Sync Started"
MSG_ACTIVITY_TAP_SYNC_STARTED = "Started syncing to TAP"

SUMMARY_ACTIVITY_TAP_SYNC_COMPLETED = "TAP Sync Completed"
MSG_ACTIVITY_TAP_SYNC_COMPLETED = "TAP acknowledged sync: {entities_synced} item(s) marked synced"

SUMMARY_ACTIVITY_TAP_SYNC_FAILED = "TAP Sync Failed"
MSG_ACTIVITY_TAP_SYNC_FAILED = "TAP sync failed: {error}"

# ── App metadata ──────────────────────────────────────────────────────────

APP_TITLE = "RIP \u2014 AI-Powered Requirement Intelligence Platform"

# ── Schema validation error messages ─────────────────────────────────────

MSG_LINK_URL_TOO_LONG = "URL must not exceed 2 048 characters."
MSG_LINK_URL_HTTPS_REQUIRED = (
    "Only HTTPS URLs are supported for link uploads. "
    "Please provide a URL starting with 'https://'. "
    "For example: https://github.com/owner/repo"
)
MSG_LINK_URL_MISSING_HOST = "Invalid URL: hostname is missing after 'https://'."

# ── Jira Integration ──────────────────────────────────────────────────────

MSG_JIRA_INTEGRATION_CREATED = "Jira integration configured successfully."
MSG_JIRA_INTEGRATION_UPDATED = "Jira integration updated successfully."
MSG_JIRA_INTEGRATION_DELETED = "Jira integration deactivated successfully."
MSG_JIRA_INTEGRATION_NOT_FOUND = "No active Jira integration found for project {project_id}."
MSG_JIRA_INTEGRATION_CONFLICT = "A Jira integration already exists for project {project_id}."
MSG_JIRA_CONNECTION_OK = "Jira connection verified successfully."
MSG_JIRA_CONNECTION_FAILED = "Jira connection failed: {detail}."
MSG_JIRA_TOKEN_ENV_VAR_NOT_SET = "Server environment variable '{env_var}' is not set."
MSG_JIRA_SYNC_QUEUED = "Jira sync queued successfully."
MSG_JIRA_SYNC_NOTHING_TO_SYNC = "No new, changed, or deprecated items to sync."
MSG_JIRA_SYNC_COMPLETED = (
    "Jira sync completed: {created} created, {updated} updated, {deprecated} deprecated."
)
MSG_JIRA_SYNC_FAILED = "Jira sync failed: {detail}."
SUMMARY_JIRA_INTEGRATION_CREATE = "Create Jira integration"
SUMMARY_JIRA_INTEGRATION_GET = "Get Jira integration"
SUMMARY_JIRA_INTEGRATION_UPDATE = "Update Jira integration"
SUMMARY_JIRA_INTEGRATION_DELETE = "Deactivate Jira integration"
SUMMARY_JIRA_SYNC_PREVIEW = "Preview Jira sync diff"
SUMMARY_JIRA_SYNC_EXECUTE = "Execute Jira sync"
SUMMARY_JIRA_SYNC_HISTORY_LIST = "List Jira sync history"
SUMMARY_JIRA_SYNC_HISTORY_DETAIL = "Get Jira sync run detail"
SUMMARY_JIRA_ISSUE_TYPES = "List Jira issue types"


# ── Export ─────────────────────────────────────────────────────────────────

SUMMARY_PROJECT_EXPORT = (
    "Export project requirements backlog, SRS specifications, and/or pipeline documents"
)


# ── TAP Integration ───────────────────────────────────────────────────────

MSG_TAP_NOT_CONFIGURED = (
    "TAP is not connected for this project. Add the TAP credentials in project "
    "settings before syncing."
)
MSG_TAP_SYNC_NOTHING_TO_SYNC = "No new, changed, or deprecated items to sync."
MSG_TAP_SYNC_ALREADY_RUNNING = (
    "A TAP sync is already in progress for this project (sync {sync_id}). "
    "Wait for TAP to finish with it before starting another."
)
MSG_TAP_SYNC_COMPLETED = "TAP sync completed: {created} created, {updated} updated."
MSG_TAP_ACK_RECEIVED = "TAP acknowledgement recorded for job {job_id} ({status})."
MSG_TAP_ACK_UNAUTHORIZED = "Invalid or missing TAP acknowledgement signature."
MSG_TAP_ACK_SECRET_MISSING = (
    "TAP callbacks are not available: this deployment has no TAP_ACK_SECRET configured."
)
SUMMARY_TAP_SYNC_EXECUTE = "Stage TAP sync and notify TAP to pull it"
SUMMARY_TAP_SYNC_DATA = "Pull staged sync data (inbound TAP callback)"
SUMMARY_TAP_ACK_RECEIVE = "Receive TAP acknowledgement (inbound callback)"

# ── TAP integration config ─────────────────────────────────────────────────
MSG_TAP_INTEGRATION_CREATED = "TAP integration configured successfully."
MSG_TAP_INTEGRATION_UPDATED = "TAP integration updated successfully."
MSG_TAP_INTEGRATION_DELETED = "TAP integration deactivated successfully."
MSG_TAP_INTEGRATION_NOT_FOUND = "No active TAP integration found for project {project_id}."
MSG_TAP_INTEGRATION_CONFLICT = "A TAP integration already exists for project {project_id}."
MSG_TAP_CLIENT_VERIFIED = "TAP app client credentials verified successfully."
MSG_TAP_CLIENT_VERIFY_FAILED = "TAP app client verification failed: {detail}."
SUMMARY_TAP_INTEGRATION_CREATE = "Create TAP integration"
SUMMARY_TAP_INTEGRATION_GET = "Get TAP integration"
SUMMARY_TAP_INTEGRATION_UPDATE = "Update TAP integration"
SUMMARY_TAP_INTEGRATION_DELETE = "Deactivate TAP integration"
SUMMARY_TAP_CLIENT_VERIFY = "Verify TAP app client credentials"

# ── Settings — enum catalog ────────────────────────────────────────────────

MSG_SETTINGS_ENUM_CATALOG_FETCHED = "Enum catalog fetched successfully."
SUMMARY_SETTINGS_ENUM_CATALOG = "Get the full catalog of app-wide enums"

# ── Role management (dynamic RBAC, super_admin only) ──────────────────────

MSG_ROLE_NOT_FOUND = "Role {role_id} not found."
MSG_ROLE_NAME_CONFLICT = "A role named '{name}' already exists."
MSG_ROLE_BUILTIN_IMMUTABLE = (
    "'{name}' is a built-in role and cannot be renamed or deleted. "
    "Its description and permissions can still be updated."
)
MSG_ROLE_IN_USE = (
    "Role '{name}' is still referenced by {count} invitation(s) and cannot be deleted."
)
MSG_PERMISSION_NOT_FOUND = "Permission '{name}' does not exist."
MSG_ROLE_CREATED = "Role created successfully."
MSG_ROLE_UPDATED = "Role updated successfully."
MSG_ROLE_DELETED = "Role deleted successfully."

SUMMARY_ROLE_LIST = "List all roles with their permissions"
SUMMARY_ROLE_CREATE = "Create a custom role with a chosen set of permissions"
SUMMARY_ROLE_GET = "Get a role by ID"
SUMMARY_ROLE_UPDATE = "Update a role's description and/or permission set"
SUMMARY_ROLE_DELETE = "Delete a custom role"
SUMMARY_PERMISSION_LIST = "List the fixed permission catalogue"
