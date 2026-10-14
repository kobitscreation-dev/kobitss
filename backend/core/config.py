from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional
import os
import logging
import warnings

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    PROJECT_NAME: str = "Kobits API"
    DATABASE_URL: str = "sqlite+aiosqlite:///./kobits.db"
    # Secret key for JWT. In production, this MUST be ≥32 cryptographically random bytes.
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY: str = "super-secret-key-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 1 week

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key_strength(cls, v: str) -> str:
        """
        Enforce ≥32-byte SECRET_KEY in PostgreSQL (production) environments.
        Emit a security warning for shorter keys in SQLite/dev environments.
        """
        import os
        key_bytes = len(v.encode("utf-8"))
        db_url = os.environ.get("DATABASE_URL", "")
        is_production = "postgresql" in db_url.lower() or "postgres" in db_url.lower()

        if key_bytes < 32:
            if is_production:
                raise ValueError(
                    f"SECRET_KEY must be ≥32 bytes in production (got {key_bytes} bytes). "
                    "Generate a secure key with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            else:
                warnings.warn(
                    f"SecurityWarning: SECRET_KEY is only {key_bytes} bytes "
                    f"(RFC 7518 §3.2 recommends ≥32 bytes for HS256). "
                    "Set a stronger key via the SECRET_KEY environment variable before deploying to production.",
                    stacklevel=2,
                )
        return v
    
    # LLM Provider Configuration
    LLM_PROVIDER: str = "mock"
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_BASE_URL: str = 'https://api.anthropic.com/v1'
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o"
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.5-pro"
    LLM_BASE_URL: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-flash"
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: str = "ap-southeast-2"
    AWS_BEDROCK_API_KEY: Optional[str] = None
    BEDROCK_MODEL: str = "anthropic.claude-3-opus-20240229-v1:0"
    REAL_AI_TEST: bool = False
    KOBITS_DEV_MODE: bool = False
    ALLOW_DEV_POSTGRES: bool = False

    # Big Think / Extended Reasoning Configuration
    BIG_THINK: bool = False
    THINKING_BUDGET: int = 4096
    REASONING_EFFORT: str = "medium"  # low | medium | high

    # OAuth Provider Configuration
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GITHUB_CLIENT_ID: Optional[str] = None
    GITHUB_CLIENT_SECRET: Optional[str] = None
    GITHUB_APP_ID: Optional[str] = None
    GITHUB_APP_PRIVATE_KEY_PATH: Optional[str] = None
    GITHUB_WEBHOOK_SECRET: Optional[str] = None
    OAUTH_REDIRECT_BASE: str = "http://localhost:8000"
    FRONTEND_URL: str = "http://localhost:8081"

    # Billing Configuration
    STRIPE_SECRET_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None
    MISSION_CREDIT_COST: int = 10

    # Distributed Worker & Concurrency Configuration
    REDIS_URL: Optional[str] = None
    WORKER_CONCURRENCY: int = 2
    WORKER_HEARTBEAT_INTERVAL: float = 5.0
    WORKER_LEASE_TIMEOUT_SECONDS: float = 60.0
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10

    # Session cookie settings
    SESSION_COOKIE_NAME: str = "kobits_session"
    SESSION_COOKIE_SECURE: bool = False  # Set True in production (HTTPS)
    SESSION_COOKIE_SAMESITE: str = "lax"

    # MicroVM & Sandbox Driver Configuration
    E2B_API_KEY: Optional[str] = None
    KOBITS_MICROVM_ENDPOINT: Optional[str] = None
    KOBITS_SANDBOX_DRIVER: str = "auto"  # auto | cloud_microvm | docker | virtual_rootfs_jail
    KOBITS_EXECUTION_MODE: str = "cloud_native"  # cloud_native | local_dev
    KOBITS_CLOUD_PREVIEW_BASE_URL: Optional[str] = None
    KOBITS_DEFAULT_VM_TIMEOUT: int = 900  # 15 minutes
    KOBITS_MAX_FLUID_ITERATIONS: int = 15

    # Remote Docker Daemon Configuration (For Docker-in-Cloud execution)
    DOCKER_HOST: Optional[str] = None
    DOCKER_TLS_VERIFY: Optional[str] = None
    DOCKER_CERT_PATH: Optional[str] = None

    # Production Observability & Error Tracing (Sentry / Datadog)
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # Kyros Parity / Parallel Speculative Compute & Proactive Integrations
    KOBITS_TURBO_MODE: bool = False
    KOBITS_SPECULATIVE_EXEC: bool = False
    KOBITS_PARALLEL_ROLLOUT_LIMIT: int = 4
    JIRA_API_TOKEN: Optional[str] = None
    LINEAR_API_KEY: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

settings = Settings()
if settings.E2B_API_KEY and not os.environ.get("E2B_API_KEY"):
    import os
    os.environ["E2B_API_KEY"] = settings.E2B_API_KEY
