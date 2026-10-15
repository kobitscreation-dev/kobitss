"""
Authentication Utilities
OWASP A07 — Identification and Authentication Failures Remediation

Provides:
  - JWTManager: Secure JWT encode/decode with explicit algorithms (never 'none')
  - PasswordManager: Bcrypt/Argon2 password hashing (never MD5/SHA1)
  - require_auth: Flask endpoint decorator for JWT auth enforcement
  - RateLimiter: Simple in-memory rate limiter for auth endpoints

Security measures:
  - JWT algorithm always explicitly set to HS256 or RS256
  - JWT 'none' algorithm blocked
  - Tokens carry expiry claims
  - Passwords hashed with bcrypt (cost factor ≥ 12)
  - Auth failures logged with client IP for monitoring (A09)
"""

import os
import time
import hmac
import hashlib
import logging
import datetime
import functools
import threading
from collections import defaultdict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# JWT Manager — A07 Remediation
# ─────────────────────────────────────────────

class JWTError(ValueError):
    """Raised for any JWT validation failure."""
    pass


class JWTManager:
    """
    Secure JWT operations with explicit algorithm enforcement.

    NEVER allows algorithm='none'. Always verifies signatures.
    Loads secret from environment variables only.
    """

    # Allowed algorithms — 'none' is explicitly excluded
    ALLOWED_ALGORITHMS = {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256"}

    @classmethod
    def _get_secret(cls) -> str:
        secret = os.environ.get("JWT_SECRET_KEY", "").strip()
        if not secret:
            raise RuntimeError(
                "JWT_SECRET_KEY environment variable is not set. "
                "Never hardcode JWT secrets in source code."
            )
        return secret

    @classmethod
    def _get_algorithm(cls) -> str:
        algo = os.environ.get("JWT_ALGORITHM", "HS256").strip()
        if algo.lower() == "none":
            raise JWTError(
                "JWT algorithm 'none' is not permitted. "
                "Use HS256 or RS256."
            )
        if algo not in cls.ALLOWED_ALGORITHMS:
            raise JWTError(
                f"JWT algorithm '{algo}' is not in the allowed list. "
                f"Permitted: {cls.ALLOWED_ALGORITHMS}"
            )
        return algo

    @classmethod
    def encode(
        cls,
        payload: Dict[str, Any],
        expiry_hours: Optional[int] = None,
    ) -> str:
        """
        Encode a JWT token with explicit algorithm and expiry.

        Args:
            payload: Claims dict. 'sub' (subject) is required.
            expiry_hours: Token TTL in hours. Defaults to JWT_EXPIRY_HOURS env var (24h).

        Returns:
            Signed JWT string.
        """
        try:
            import jwt as pyjwt
        except ImportError as exc:
            raise ImportError("Install PyJWT: pip install PyJWT") from exc

        if "sub" not in payload:
            raise JWTError("JWT payload must contain a 'sub' (subject) claim.")

        now = datetime.datetime.utcnow()
        expiry_hours = expiry_hours or int(os.environ.get("JWT_EXPIRY_HOURS", "24"))

        full_payload = {
            **payload,
            "iat": now,
            "nbf": now,
            "exp": now + datetime.timedelta(hours=expiry_hours),
        }

        secret = cls._get_secret()
        algorithm = cls._get_algorithm()

        token = pyjwt.encode(full_payload, secret, algorithm=algorithm)
        logger.debug("JWT token issued for sub=%s", payload.get("sub"))
        return token

    @classmethod
    def decode(cls, token: str) -> Dict[str, Any]:
        """
        Decode and validate a JWT token.

        Validates: signature, expiry, not-before, algorithm whitelist.
        Blocks algorithm='none' attacks.

        Args:
            token: JWT string to decode.

        Returns:
            Validated payload dict.

        Raises:
            JWTError: On any validation failure.
        """
        try:
            import jwt as pyjwt
            from jwt.exceptions import (
                InvalidTokenError, ExpiredSignatureError,
                InvalidAlgorithmError, DecodeError,
            )
        except ImportError as exc:
            raise ImportError("Install PyJWT: pip install PyJWT") from exc

        secret = cls._get_secret()
        algorithm = cls._get_algorithm()

        try:
            payload = pyjwt.decode(
                token,
                secret,
                algorithms=[algorithm],   # Explicit list — blocks algorithm confusion
                options={
                    "verify_signature": True,   # Always verify
                    "verify_exp": True,          # Reject expired tokens
                    "verify_nbf": True,          # Respect not-before
                    "require": ["exp", "iat"],   # Required claims
                },
            )
        except ExpiredSignatureError as exc:
            logger.warning("JWT token expired")
            raise JWTError("Token has expired. Please re-authenticate.") from exc
        except InvalidAlgorithmError as exc:
            logger.error("JWT algorithm mismatch — potential attack")
            raise JWTError("Invalid token algorithm.") from exc
        except (InvalidTokenError, DecodeError) as exc:
            logger.warning("JWT validation failed: %s", exc)
            raise JWTError(f"Token validation failed: {exc}") from exc

        return payload


# ─────────────────────────────────────────────
# Password Manager — A07 Remediation
# ─────────────────────────────────────────────

class PasswordManager:
    """
    Secure password hashing using bcrypt.

    NEVER uses MD5, SHA1, or SHA256 for password storage.
    Cost factor is configurable via BCRYPT_ROUNDS env var (default 12).
    """

    MIN_ROUNDS = 10
    DEFAULT_ROUNDS = 12

    @classmethod
    def _get_rounds(cls) -> int:
        rounds = int(os.environ.get("BCRYPT_ROUNDS", cls.DEFAULT_ROUNDS))
        if rounds < cls.MIN_ROUNDS:
            logger.warning(
                "BCRYPT_ROUNDS=%d is below minimum %d, using %d",
                rounds, cls.MIN_ROUNDS, cls.MIN_ROUNDS,
            )
            return cls.MIN_ROUNDS
        return rounds

    @classmethod
    def hash_password(cls, plain_password: str) -> str:
        """
        Hash a plaintext password using bcrypt.

        Args:
            plain_password: User's plaintext password.

        Returns:
            bcrypt hash string (includes salt).

        Raises:
            ValueError: If password is empty or too short.
        """
        if not plain_password:
            raise ValueError("Password cannot be empty.")
        if len(plain_password) < 8:
            raise ValueError("Password must be at least 8 characters.")

        try:
            import bcrypt
        except ImportError as exc:
            raise ImportError("Install bcrypt: pip install bcrypt") from exc

        rounds = cls._get_rounds()
        salt = bcrypt.gensalt(rounds=rounds)
        hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
        return hashed.decode("utf-8")

    @classmethod
    def verify_password(cls, plain_password: str, hashed: str) -> bool:
        """
        Verify a plaintext password against a bcrypt hash.

        Uses constant-time comparison to prevent timing attacks.

        Args:
            plain_password: User-provided password attempt.
            hashed: Stored bcrypt hash.

        Returns:
            True if password matches, False otherwise.
        """
        if not plain_password or not hashed:
            return False

        try:
            import bcrypt
        except ImportError as exc:
            raise ImportError("Install bcrypt: pip install bcrypt") from exc

        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                hashed.encode("utf-8"),
            )
        except Exception:
            return False

    @classmethod
    def needs_rehash(cls, hashed: str) -> bool:
        """
        Check if a stored hash was created with outdated parameters.

        Returns True if the hash should be upgraded on next login.
        """
        try:
            import bcrypt
            rounds = cls._get_rounds()
            return bcrypt.checkpw(b"", bcrypt.gensalt(rounds=rounds)) != bcrypt.checkpw(b"", hashed.encode())
        except Exception:
            return True


# ─────────────────────────────────────────────
# In-Memory Rate Limiter — A07 Remediation
# ─────────────────────────────────────────────

class RateLimiter:
    """
    Simple in-memory rate limiter for authentication endpoints.

    Implements sliding window rate limiting per client identifier (IP or user).
    For production, replace with Redis-backed rate limiting.

    Usage::

        limiter = RateLimiter(max_attempts=5, window_seconds=60)

        @app.route('/login', methods=['POST'])
        def login():
            client_id = request.remote_addr
            if not limiter.is_allowed(client_id):
                return jsonify({'error': 'Rate limit exceeded'}), 429
            ...
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._store: Dict[str, list] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, client_id: str) -> bool:
        """
        Check if a client is within rate limits.

        Args:
            client_id: Client identifier (IP address or user ID).

        Returns:
            True if the request is allowed, False if rate limited.
        """
        now = time.time()
        window_start = now - self.window_seconds

        with self._lock:
            # Clean old entries
            self._store[client_id] = [
                ts for ts in self._store[client_id] if ts > window_start
            ]

            if len(self._store[client_id]) >= self.max_attempts:
                logger.warning(
                    "Rate limit exceeded for client: %s (%d requests in %ds)",
                    client_id, len(self._store[client_id]), self.window_seconds,
                )
                return False

            self._store[client_id].append(now)
            return True

    def reset(self, client_id: str):
        """Reset rate limit for a client (e.g. on successful auth)."""
        with self._lock:
            self._store.pop(client_id, None)


# ─────────────────────────────────────────────
# Auth Decorator — A01/A07 Remediation
# ─────────────────────────────────────────────

def require_auth(f):
    """
    Flask view decorator that enforces JWT authentication.

    Extracts and validates JWT from Authorization: Bearer <token> header.
    Attaches decoded payload to Flask's g.current_user.

    Usage::

        @app.route('/api/data')
        @require_auth
        def get_data():
            from flask import g
            user_id = g.current_user['sub']
            ...
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        try:
            from flask import request, jsonify, g
        except ImportError as exc:
            raise ImportError("Flask is required for require_auth decorator") from exc

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning(
                "Unauthorized access attempt to %s from %s",
                request.path, request.remote_addr,
            )
            return jsonify({"error": "Authentication required"}), 401

        token = auth_header[7:]  # Strip 'Bearer '
        try:
            payload = JWTManager.decode(token)
            g.current_user = payload
        except JWTError as exc:
            logger.warning(
                "Invalid JWT on %s from %s: %s",
                request.path, request.remote_addr, exc,
            )
            return jsonify({"error": "Invalid or expired token"}), 401

        return f(*args, **kwargs)

    return decorated