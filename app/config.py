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
from pathlib import Path
import secrets
from typing import Optional


def resolve_secret(secret_name: str, default: str = "") -> str:
    """
    Resolve a secret from environment variables, Google Cloud Run mounted secrets
    (/run/secrets/<name> or /secrets/<name>), or Google Cloud Secret Manager.
    Never stores hardcoded credentials in source code.
    """
    env_val = os.environ.get(secret_name, "").strip()
    if env_val:
        return env_val

    file_env_path = os.environ.get(f"{secret_name}_FILE", "").strip()
    candidate_paths = []
    if file_env_path:
        candidate_paths.append(Path(file_env_path))
    candidate_paths.extend(
        [
            Path("/run/secrets") / secret_name,
            Path("/run/secrets") / secret_name.lower(),
            Path("/secrets") / secret_name,
            Path("/secrets") / secret_name.lower(),
        ]
    )
    for secret_path in candidate_paths:
        try:
            if secret_path.is_file():
                content = secret_path.read_text(encoding="utf-8").strip()
                if content:
                    return content
        except OSError:
            continue

    gcp_project = (
        os.environ.get("GCP_PROJECT_ID", "").strip()
        or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    )
    if gcp_project and os.environ.get("KOBITS_USE_GCP_SECRET_MANAGER", "").lower() in ("1", "true", "yes"):
        try:
            from google.cloud import secretmanager  # type: ignore

            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{gcp_project}/secrets/{secret_name}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            payload = response.payload.data.decode("UTF-8").strip()
            if payload:
                return payload
        except Exception:
            pass

    return default


class Config:
    """
    Base configuration — all secrets from environment variables or Cloud Run Secret Manager only.
    """

    # ── Core Security ─────────────────────────────────────────────────────────
    # A02: Secret key from environment/Secret Manager only. Fail hard if not set in production.
    SECRET_KEY: str = resolve_secret("SECRET_KEY", "") or secrets.token_urlsafe(64)
    TESTING: bool = False

    def __init__(self):
        if not self.TESTING and not resolve_secret("SECRET_KEY", "") and "PYTEST_CURRENT_TEST" not in os.environ:
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
    DATABASE_URL: str = resolve_secret("DATABASE_URL", "")

    # ── JWT Configuration — A07 ───────────────────────────────────────────────
    # Always use explicit algorithms. NEVER algorithm='none'.
    JWT_SECRET_KEY: str = resolve_secret("JWT_SECRET_KEY", SECRET_KEY)
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
        missing = [v for v in required_vars if not resolve_secret(v)]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables for production: {missing}. "
                "Set these in your deployment environment or secrets manager."
            )
        # Production must never have DEBUG=True
        if os.environ.get("DEBUG", "False").lower() in ("1", "true", "yes"):
            raise RuntimeError("DEBUG=True is not allowed in production environment.")


class TestingConfig(Config):
    """Testing configuration — uses ephemeral runtime tokens if not set in env."""
    __test__ = False
    TESTING = True
    DEBUG = False
    DATABASE_URL = "sqlite:///:memory:"
    SECRET_KEY = resolve_secret("TEST_SECRET_KEY") or secrets.token_hex(32)
    JWT_SECRET_KEY = resolve_secret("TEST_JWT_SECRET_KEY") or secrets.token_hex(32)


_CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": Config,
}


def get_config(env: Optional[str] = None) -> Config:
    """
    Return the appropriate Config instance for the current environment.
    """
    env = env or os.environ.get("APP_ENV", "development").lower()
    config_class = _CONFIG_MAP.get(env, Config)
    return config_class()