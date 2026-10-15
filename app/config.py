"""
Application Configuration
OWASP A02, A05, A07 Remediation

All sensitive configuration is loaded EXCLUSIVELY from environment variables.
Never hardcode secrets, API keys, or passwords in this file.

Usage::

    from app.config import get_config, Config
    config = get_config()
    app.config.from_object(config)
"""

import os
import secrets
from typing import Optional


class Config:
    """
    Base configuration — all secrets from environment variables only.

    Environment variables required in production:
      SECRET_KEY            — Django/Flask secret key
      DATABASE_URL          — Database connection string
      ALLOWED_HOSTS         — Comma-separated allowed hostnames
      JWT_SECRET_KEY        — JWT signing secret

    Optional AI service keys:
      OPENAI_API_KEY
      ANTHROPIC_API_KEY
      HUGGINGFACE_API_KEY

    Never set any of these in source code.
    """

    # ── Core Security ─────────────────────────────────────────────────────────
    # A02: Secret key from environment only. Fail hard if not set in production.
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "")
    if not SECRET_KEY:
        # Generate an ephemeral key for development — warn loudly
        SECRET_KEY = secrets.token_urlsafe(64)
        import warnings
        warnings.warn(
            "SECRET_KEY environment variable not set. "
            "Using an ephemeral key — DO NOT use in production!",
            stacklevel=2,
        )

    # ── Debug Mode — A05 ──────────────────────────────────────────────────────
    # NEVER hardcode DEBUG=True. Must be env-controlled.
    DEBUG: bool = os.environ.get("DEBUG", "False").strip().lower() in ("1", "true", "yes")

    # ── Allowed Hosts — A05 ───────────────────────────────────────────────────
    # Never use wildcard '*' in production
    ALLOWED_HOSTS_RAW: str = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1")
    ALLOWED_HOSTS: list = [h.strip() for h in ALLOWED_HOSTS_RAW.split(",") if h.strip()]

    # ── Database — A03 ────────────────────────────────────────────────────────
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

    # ── JWT Configuration — A07 ───────────────────────────────────────────────
    # Always use explicit algorithms. NEVER algorithm='none'.
    JWT_SECRET_KEY: str = os.environ.get("JWT_SECRET_KEY", SECRET_KEY)
    JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")  # HS256 or RS256 only
    JWT_ALGORITHMS: list = [JWT_ALGORITHM]  # For decode — always a list
    JWT_EXPIRY_HOURS: int = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))

    # ── CORS Configuration — A05 ──────────────────────────────────────────────
    CORS_ORIGIN_ALLOW_ALL: bool = False  # Always False
    CORS_ALLOWED_ORIGINS_RAW: str = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    CORS_ALLOWED_ORIGINS: list = [
        o.strip() for o in CORS_ALLOWED_ORIGINS_RAW.split(",") if o.strip()
    ]

    # ── Session Security ──────────────────────────────────────────────────────
    SESSION_COOKIE_SECURE: bool = not DEBUG
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    CSRF_COOKIE_SECURE: bool = not DEBUG

    # ── Password Hashing — A07 ────────────────────────────────────────────────
    # Use bcrypt or argon2. Never MD5 or SHA1.
    PASSWORD_HASHERS: list = [
        "django.contrib.auth.hashers.Argon2PasswordHasher",
        "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    ]

    # ── Rate Limiting — A07 ───────────────────────────────────────────────────
    AUTH_RATE_LIMIT: str = os.environ.get("AUTH_RATE_LIMIT", "5/minute")
    API_RATE_LIMIT: str = os.environ.get("API_RATE_LIMIT", "100/minute")

    # ── AI/ML API Keys — A02 ─────────────────────────────────────────────────
    # These are loaded at runtime via AIKeyManager — do not reference directly.
    # Present here as documentation only (always empty defaults).
    OPENAI_API_KEY: Optional[str] = None      # Set via OPENAI_API_KEY env var
    ANTHROPIC_API_KEY: Optional[str] = None   # Set via ANTHROPIC_API_KEY env var
    HUGGINGFACE_API_KEY: Optional[str] = None # Set via HUGGINGFACE_API_KEY env var

    # ── RAG/SSRF Protection — A10 ─────────────────────────────────────────────
    RAG_URL_ALLOWLIST: str = os.environ.get("RAG_URL_ALLOWLIST", "")

    # ── Logging — A09 ─────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
    LOG_SECURITY_EVENTS: bool = True  # Always log security events

    # ── TLS/HTTPS — A02 ───────────────────────────────────────────────────────
    SECURE_SSL_REDIRECT: bool = os.environ.get("SECURE_SSL_REDIRECT", "True").lower() in ("1", "true", "yes")
    SECURE_HSTS_SECONDS: int = int(os.environ.get("SECURE_HSTS_SECONDS", "63072000"))  # 2 years
    SECURE_HSTS_INCLUDE_SUBDOMAINS: bool = True
    SECURE_HSTS_PRELOAD: bool = True
    SECURE_CONTENT_TYPE_NOSNIFF: bool = True


class DevelopmentConfig(Config):
    """Development overrides — NEVER use in production."""
    DEBUG = True
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False


class ProductionConfig(Config):
    """Production configuration — validates required env vars."""

    def __init__(self):
        required_vars = ["SECRET_KEY", "DATABASE_URL", "JWT_SECRET_KEY"]
        missing = [v for v in required_vars if not os.environ.get(v)]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables for production: {missing}. "
                "Set these in your deployment environment or secrets manager."
            )
        # Production must never have DEBUG=True
        if os.environ.get("DEBUG", "False").lower() in ("1", "true", "yes"):
            raise RuntimeError("DEBUG=True is not allowed in production environment.")


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DEBUG = False
    DATABASE_URL = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-not-for-production"
    JWT_SECRET_KEY = "test-jwt-secret-not-for-production"


_CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": Config,
}


def get_config(env: Optional[str] = None) -> Config:
    """
    Return the appropriate Config instance for the current environment.

    Args:
        env: Override environment name. Defaults to APP_ENV env var or 'development'.

    Returns:
        Config instance.
    """
    env = env or os.environ.get("APP_ENV", "development").lower()
    config_class = _CONFIG_MAP.get(env, Config)
    return config_class()