from datetime import datetime, timedelta, timezone
from typing import Any, Union
import hashlib
import hmac
import os
import jwt
import bcrypt
from backend.core.config import settings


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a stored hash.
    
    Supports both legacy SHA-256+salt hashes (format: salt$hash) and
    new bcrypt hashes (format: $2b$...).
    """
    if hashed_password.startswith("$2b$") or hashed_password.startswith("$2a$"):
        # bcrypt hash
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    
    # Legacy SHA-256 + salt format
    parts = hashed_password.split('$')
    if len(parts) != 2:
        return False
    salt = parts[0]
    stored_hash = parts[1]
    computed = hashlib.sha256((salt + plain_password).encode()).hexdigest()
    return hmac.compare_digest(computed, stored_hash)


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt (secure, adaptive).
    
    New passwords always use bcrypt. Legacy SHA-256 hashes remain readable
    for backward compatibility via verify_password().
    """
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def create_access_token(subject: Union[str, Any], expires_delta: timedelta = None) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt
