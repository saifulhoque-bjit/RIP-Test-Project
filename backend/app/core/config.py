"""Application configuration via Pydantic BaseSettings.

Loaded once at startup via ``get_settings()`` (lru_cache).  All secrets and
environment-specific values must be supplied through environment variables or
an .env file — never hardcoded here.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ─────────────────────────────────────────────────────────────────
    APP_ENV: str = Field(default="development")
    DEBUG: bool = Field(default=False)
    SECRET_KEY: str = Field(min_length=32)
    CORS_ORIGINS: list[str] = Field(default=["http://localhost:5173"])

    # ── Cookie ───────────────────────────────────────────────────────────────
    COOKIE_SAMESITE: str = Field(
        default="none"
    )  # "none" for cross-origin, "lax"/"strict" for same-site
    COOKIE_SECURE: bool = Field(default=True)  # must be True when samesite="none"

    # ── PostgreSQL ───────────────────────────────────────────────────────────
    POSTGRES_HOST: str
    POSTGRES_PORT: int = Field(default=5432)
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_ASYNC_DRIVER: str = Field(default="asyncpg")
    # If true, init_db() may run ORM create_all for local/bootstrap scenarios.
    # Keep false for migration-first startup.
    DB_AUTO_CREATE: bool = Field(default=False)

    # ── Neo4j ────────────────────────────────────────────────────────────────
    NEO4J_URI: str
    NEO4J_USER: str
    NEO4J_PASSWORD: str

    # ── Redis / Celery ───────────────────────────────────────────────────────
    REDIS_URL: str
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    CELERY_REDIS_SOCKET_KEEPALIVE: bool = Field(default=True)
    # TCP keepalive timing for the broker's client-side socket. socket_keepalive=True
    # alone just flips on SO_KEEPALIVE and inherits the OS default idle time (7200s on
    # Linux) before the first probe — far longer than AWS's ~350s idle-connection
    # eviction inside a VPC, so a quiet broker connection gets silently dropped by the
    # network fabric long before the kernel would ever notice. These three values are
    # passed as socket_keepalive_options so probing starts well inside that window:
    # first probe after IDLE seconds, a probe every INTVL seconds, give up after CNT
    # missed probes (30 + 10*3 = 60s worst case to detect a truly dead peer).
    CELERY_REDIS_SOCKET_KEEPALIVE_IDLE: int = Field(default=30)
    CELERY_REDIS_SOCKET_KEEPALIVE_INTVL: int = Field(default=10)
    CELERY_REDIS_SOCKET_KEEPALIVE_CNT: int = Field(default=3)
    # Keep broker socket reads unbounded by default. Managed Redis + low traffic
    # can otherwise raise periodic "Timeout reading from socket" and force
    # reconnect loops in Celery consumers.
    CELERY_BROKER_SOCKET_TIMEOUT: float | None = Field(default=None)
    CELERY_BROKER_SOCKET_CONNECT_TIMEOUT: float = Field(default=30.0)
    CELERY_BROKER_HEALTH_CHECK_INTERVAL: int = Field(default=30)
    # None means "retry forever" on broker reconnect.
    CELERY_BROKER_CONNECTION_MAX_RETRIES: int | None = Field(default=None)
    # Keep result-backend socket reads unbounded by default for the same
    # reason as broker reads: finite read timeouts can surface as noisy
    # redis.exceptions.TimeoutError in low-traffic managed networks.
    CELERY_RESULT_SOCKET_TIMEOUT: float | None = Field(default=None)
    CELERY_RESULT_SOCKET_CONNECT_TIMEOUT: float = Field(default=30.0)
    CELERY_RESULT_HEALTH_CHECK_INTERVAL: int = Field(default=30)
    # Global fallback time limits for lightweight tasks (routing, notifications).
    # Heavy tasks (parsing, AI generation) override these per-task decorator.
    CELERY_TASK_SOFT_TIME_LIMIT: int = Field(default=600)  # 10 min
    CELERY_TASK_TIME_LIMIT: int = Field(default=780)  # 13 min — grace period

    # ── AWS ──────────────────────────────────────────────────────────────────
    AWS_REGION: str = Field(default="us-east-1")
    # Leave empty on AWS infrastructure — boto3 will use the instance-profile /
    # ECS task role credential chain automatically.  Set explicitly for local
    # development and CI only.
    AWS_ACCESS_KEY_ID: str = Field(default="")
    AWS_SECRET_ACCESS_KEY: str = Field(default="")
    AWS_COGNITO_USER_POOL_ID: str
    AWS_COGNITO_CLIENT_ID: str
    AWS_COGNITO_CLIENT_SECRET: str = Field(default="")  # Required when app client has a secret
    AWS_SQS_QUEUE_URL: str
    AWS_SQS_DLQ_URL: str
    # Verified SES sending identity used for invitation emails.
    AWS_SES_SENDER_EMAIL: str = Field(default="")

    # ── Invitations ──────────────────────────────────────────────────────────
    # Base URL of the frontend app; invite-accept links are built as
    # "{FRONTEND_BASE_URL}/invitations/accept?token=...".
    FRONTEND_BASE_URL: str = Field(default="http://localhost:5173")
    INVITATION_EXPIRY_DAYS: int = Field(default=7)

    # ── Super Admin bootstrap (MVP) ─────────────────────────────────────────
    # Idempotent seed run at every startup (app/db/seed_super_admin.py). Leave
    # SUPER_ADMIN_EMAIL/PASSWORD empty to skip seeding entirely (e.g. once a
    # real admin has been provisioned and the bootstrap account is retired).
    # SECURITY: These MUST be set via environment variables or secrets manager.
    # No defaults provided to prevent accidental deployment with test credentials.
    SEED_SUPER_ADMIN_ENABLED: bool = Field(default=True)
    SUPER_ADMIN_EMAIL: str = Field(default="")
    SUPER_ADMIN_PASSWORD: str = Field(default="")
    SUPER_ADMIN_NAME: str = Field(default="Super Admin")

    # ── S3 Source Storage ───────────────────────────────────────────────────
    AWS_S3_SOURCES_BUCKET: str = Field(default="rip-development-s3-static")
    AWS_S3_PRESIGNED_URL_EXPIRY: int = Field(default=3600)  # seconds
    S3_DOWNLOAD_CHUNK_SIZE_MB: int = Field(default=5)  # streaming chunk size in MB
    SOURCE_MAX_FILE_SIZE_MB: int = Field(default=500)  # single / bulk upload cap
    SOURCE_LINK_MAX_FILE_SIZE_MB: int = Field(default=500)  # link (URL) upload cap
    SOURCE_BULK_MAX_FILES: int = Field(default=20)
    # Cumulative cap across every file in one bulk/incremental upload request —
    # independent of the per-file cap above, to bound total request size/time
    # (e.g. 20 files x 500MB would otherwise allow a single 10GB request).
    SOURCE_BULK_MAX_TOTAL_SIZE_MB: int = Field(default=2000)
    SOURCE_LOCAL_DOWNLOAD_DIR: str = Field(default="temp/sources")

    # ── LlamaParse Service ────────────────────────────────────────────────
    LLAMA_CLOUD_API_KEY: str

    # ── OmniParser ───────────────────────────────────────────────────────────
    OMNIPARSER_BASE_URL: str
    OMNIPARSER_TIMEOUT_SECONDS: int = Field(default=300)
    OMNIPARSER_MAX_INPUT_LENGTH: int = Field(default=8000)

    # ── LLM Provider API Keys ─────────────────────────────────────────────
    # At least one provider key must be set depending on which agents are configured.
    OPENAI_API_KEY: str = Field(default="")
    ANTHROPIC_API_KEY: str = Field(default="")
    GOOGLE_API_KEY: str = Field(default="")
    DEEPSEEK_API_KEY: str = Field(default="")

    # ── Embedding ────────────────────────────────────────────────────────────
    OPENAI_EMBEDDING_MODEL: str = Field(default="text-embedding-3-small")
    # Vector dimension must match the embedding model output size.
    # Changing this value also requires a new Alembic schema migration.
    EMBEDDING_VECTOR_DIM: int = Field(default=1536)

    # ── Alternate Pipeline Graphs ───────────────────────────────────────────────
    ALT_PIPELINE_PROMPT_BASE_PATH: str = Field(
        default="app/services/rfp_pipeline_v2_graph_service/prompts/"
    )
    ALT_ARCHITECT_TEMPERATURE: float = Field(default=0.0)
    ALT_CRITIC_TEMPERATURE: float = Field(default=0.0)
    ALT_MAX_CRITIC_ITERATIONS: int = Field(
        default=1
    )  # max number of critic review cycles per phase
    ALT_MAX_TOKENS: int = Field(default=384000)
    ALT_MAX_PATCH_CRITIC_ITERATIONS: int = Field(default=2)
    ALT_MAX_INCREMENTAL_CRITIC_ITERATIONS: int = Field(default=2)
    ALT_MAX_SELECTOR_CRITIC_ITERATIONS: int = Field(default=2)

    # ── Image extraction ──────────────────────────────────────────────────
    IMAGE_EXTRACTION_PROVIDER: str = Field(default="google")
    IMAGE_EXTRACTION_MODEL_NAME: str = Field(default="gemini-pro-latest")
    IMAGE_EXTRACTION_MAX_TOKENS: int = Field(default=64000)

    # ╔═════════════════════════════════════════════════════════════════════════╗
    # ║                        HOW TO SWITCH MODELS                             ║
    # ╠═════════════════════════════════════════════════════════════════════════╣
    # ║  Uncomment ONE provider block below for both ARCHITECT and CRITIC.      ║
    # ║  Make sure all other provider blocks remain commented out to avoid      ║
    # ║  duplicate field definition errors.                                     ║
    # ║                                                                         ║
    # ║  Supported providers:  "anthropic"  |  "google"  |  "openai"            ║
    # ║  After switching, restart the application for changes to take effect.   ║
    # ╚═════════════════════════════════════════════════════════════════════════╝

    # ── Anthorpic model variable ───────────────────────────────────────────────
    ALT_ARCHITECT_MODEL_PROVIDER: str = Field(default="anthropic")  # uncomment for claude
    ALT_ARCHITECT_MODEL_NAME: str = Field(default="claude-sonnet-4-6")  # uncomment for claude
    ALT_CRITIC_MODEL_PROVIDER: str = Field(default="anthropic")  # uncomment for claude
    ALT_CRITIC_MODEL_NAME: str = Field(default="claude-sonnet-4-6")  # uncomment for claude

    # ── Google model variable ───────────────────────────────────────────────
    # ALT_ARCHITECT_MODEL_PROVIDER: str = Field(default="google") #uncomment for gemini
    # ALT_ARCHITECT_MODEL_NAME: str = Field(default="gemini-pro-latest") #uncomment for gemini
    # ALT_CRITIC_MODEL_PROVIDER: str = Field(default="google") #uncomment for gemini
    # ALT_CRITIC_MODEL_NAME: str = Field(default="gemini-pro-latest") #uncomment for gemini

    # ── OpenAI model variable ───────────────────────────────────────────────
    # ALT_ARCHITECT_MODEL_PROVIDER: str = Field(default="openai") #uncomment for gpt
    # ALT_ARCHITECT_MODEL_NAME: str = Field(default="gpt-5.2-pro") #uncomment for gpt
    # ALT_CRITIC_MODEL_PROVIDER: str = Field(default="openai") #uncomment for gpt
    # ALT_CRITIC_MODEL_NAME: str = Field(default="gpt-5.2-pro") #uncomment for gpt

    # # ── DeepSeek model variable ───────────────────────────────────────────────
    # ALT_ARCHITECT_MODEL_PROVIDER: str = Field(default="deepseek") #uncomment for deepseek
    # ALT_ARCHITECT_MODEL_NAME: str = Field(default="deepseek-v4-pro") #uncomment for deepseek
    # ALT_CRITIC_MODEL_PROVIDER: str = Field(default="deepseek") #uncomment for deepseek
    # ALT_CRITIC_MODEL_NAME: str = Field(default="deepseek-v4-pro") #uncomment for deepseek

    # ── Rate limiting ────────────────────────────────────────────────────────
    # Format: "{count}/{period}" — period is second | minute | hour | day.
    # Override via environment variables to tune for your traffic profile without
    # redeploying.  Tighter limits (e.g. "5/minute") are recommended for production.
    RATE_LIMIT_AUTH_REGISTER: str = Field(default="10/minute")
    RATE_LIMIT_AUTH_CONFIRM: str = Field(default="10/minute")
    RATE_LIMIT_AUTH_LOGIN: str = Field(default="20/minute")
    RATE_LIMIT_AUTH_REFRESH: str = Field(default="30/minute")
    # Forgot-password sends a real email per call and is a user-enumeration/
    # abuse surface — kept tighter than login/refresh.
    RATE_LIMIT_AUTH_FORGOT_PASSWORD: str = Field(default="5/minute")
    RATE_LIMIT_AUTH_RESET_PASSWORD: str = Field(default="10/minute")
    RATE_LIMIT_SOURCE_UPLOAD: str = Field(default="20/minute")
    RATE_LIMIT_SOURCE_DOWNLOAD: str = Field(default="60/minute")
    RATE_LIMIT_SOURCE_DELETE: str = Field(default="30/minute")
    # AI-generation-triggering endpoints (regenerate/generate) enqueue an
    # LLM-backed Celery job per call — throttle harder than a plain read/write
    # endpoint since each call has real inference cost.
    RATE_LIMIT_AI_REGENERATE: str = Field(default="10/minute")
    RATE_LIMIT_JIRA_SYNC: str = Field(default="5/minute")
    RATE_LIMIT_JIRA_CONFIG: str = Field(default="10/minute")
    RATE_LIMIT_TAP_SYNC: str = Field(default="5/minute")
    RATE_LIMIT_TAP_CONFIG: str = Field(default="10/minute")
    # Testing a tenant's LLM provider API key makes a real outbound call to
    # that provider — throttle like the other config/test endpoints.
    RATE_LIMIT_TENANT_LLM_PROVIDER_TEST: str = Field(default="10/minute")
    # Writing a tenant's LLM provider config stores a secret — throttle like
    # the other config-mutation endpoints (e.g. RATE_LIMIT_JIRA_CONFIG).
    RATE_LIMIT_TENANT_LLM_PROVIDER_CONFIG: str = Field(default="10/minute")
    # Fetching a tenant's LLM provider balance makes a real outbound call to
    # that provider — throttle like the other config/test endpoints.
    RATE_LIMIT_TENANT_LLM_PROVIDER_BALANCE: str = Field(default="10/minute")
    # Export renders a PDF and/or pulls SRS files from S3 per request — costlier
    # than a plain download, throttle closer to the sync endpoints.
    RATE_LIMIT_EXPORT: str = Field(default="10/minute")
    # Resending an invitation triggers a real outbound email — throttle to
    # prevent an admin (or a compromised admin session) from email-bombing
    # an invitee.
    RATE_LIMIT_INVITATION_RESEND: str = Field(default="5/minute")

    # ── Jira sync ────────────────────────────────────────────────────────────
    # Delay between Jira API calls during sync (ms) to avoid rate limiting.
    JIRA_SYNC_REQUEST_DELAY_MS: int = Field(default=100)
    # Fernet key for encrypting Jira API tokens stored in the DB.
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    JIRA_ENCRYPTION_KEY: str = Field(default="")

    # Fernet key for encrypting tenant LLM provider API keys stored in the DB.
    # Kept separate from JIRA_ENCRYPTION_KEY so the two secret domains can be
    # rotated independently. Generate the same way as JIRA_ENCRYPTION_KEY.
    LLM_ENCRYPTION_KEY: str = Field(default="")

    # ── TAP (Test Automation Platform) sync ──────────────────────────────────
    # TAP's base URL and credentials are per-project, stored encrypted in
    # tap_integrations — deliberately NOT settings. They are per-tenant, so a
    # deployment-wide fallback would let one project push its requirements
    # into TAP under another's identity. Only these three are deployment-level.
    #
    # Shared secret expected in the X-TAP-Signature header on inbound TAP
    # callbacks (/ack and the pull-data endpoint). Required: those two routes
    # are not user-authenticated, so leaving this empty would expose a
    # project's whole requirements payload and let anyone forge an ack. They
    # fail closed (503) when it is unset.
    TAP_ACK_SECRET: str = Field(default="")
    # Externally-reachable base URL of this RIP backend (scheme + host, no
    # trailing slash, e.g. "https://rip.example.com") — used to build the
    # pull-back link RIP sends TAP in the sync-ready notification. Left empty
    # the sync still stages data (nothing is lost) but cannot tell TAP where
    # to pull it from.
    RIP_PUBLIC_BASE_URL: str = Field(default="")
    # Fernet key for encrypting per-project TAP API keys stored in the DB.
    TAP_ENCRYPTION_KEY: str = Field(default="")

    # ── Derived (built, not from env) ────────────────────────────────────────
    DATABASE_URL: str = ""
    DATABASE_ASYNC_URL: str = ""

    @field_validator("APP_ENV")
    @classmethod
    def validate_app_env(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"APP_ENV must be one of {allowed}, got '{v}'")
        return v

    @field_validator("CELERY_BROKER_SOCKET_TIMEOUT", mode="before")
    @classmethod
    def normalize_optional_broker_socket_timeout(cls, v: object) -> float | None:
        return cls._normalize_optional_timeout(v)

    @field_validator("CELERY_RESULT_SOCKET_TIMEOUT", mode="before")
    @classmethod
    def normalize_optional_result_socket_timeout(cls, v: object) -> float | None:
        return cls._normalize_optional_timeout(v)

    @staticmethod
    def _normalize_optional_timeout(v: object) -> float | None:
        # Accept empty/none/zero-ish values as "unset" for env-file ergonomics.
        if v is None:
            return None
        if isinstance(v, str):
            normalized = v.strip().lower()
            if normalized in {"", "none", "null"}:
                return None
            parsed = float(normalized)
        else:
            parsed = float(v)
        if parsed <= 0:
            return None
        return parsed

    @model_validator(mode="after")
    def build_database_url(self) -> Settings:
        user = quote_plus(self.POSTGRES_USER)
        password = quote_plus(self.POSTGRES_PASSWORD)
        self.DATABASE_URL = (
            f"postgresql+psycopg2://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )
        self.DATABASE_ASYNC_URL = (
            f"postgresql+{self.POSTGRES_ASYNC_DRIVER}://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
