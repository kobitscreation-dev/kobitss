from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    PROJECT_NAME: str = "Kobits API"
    DATABASE_URL: str = "sqlite+aiosqlite:///./kobits.db"
    # Secret key for JWT. In production, this MUST be secure and loaded from env.
    SECRET_KEY: str = "super-secret-key-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 1 week
    
    # LLM Provider Configuration
    LLM_PROVIDER: str = "mock"
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_BASE_URL: str = 'https://api.anthropic.com/v1'
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
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

    # OAuth Provider Configuration
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GITHUB_CLIENT_ID: Optional[str] = None
    GITHUB_CLIENT_SECRET: Optional[str] = None
    OAUTH_REDIRECT_BASE: str = "http://localhost:8000"
    FRONTEND_URL: str = "http://localhost:8081"

    # Billing Configuration
    STRIPE_SECRET_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None
    MISSION_CREDIT_COST: Optional[int] = None

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

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

settings = Settings()
