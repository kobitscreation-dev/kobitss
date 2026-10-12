"""
Official GitHub App Authentication & Webhook Service for Kobits.
Implements enterprise JWT RS256 token minting, ephemeral installation token generation,
and secure HMAC webhook verification for 1-click GitHub organization integration.
"""

import hmac
import hashlib
import time
import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import jwt
import httpx
from backend.core.config import settings

logger = logging.getLogger(__name__)


class GitHubAppService:
    """
    Manages authenticating as an official GitHub App.
    """
    BASE_URL = "https://api.github.com"

    @classmethod
    def get_app_id(cls) -> Optional[str]:
        return getattr(settings, "GITHUB_APP_ID", None) or os.environ.get("GITHUB_APP_ID")

    @classmethod
    def get_private_key(cls) -> Optional[str]:
        key_content = os.environ.get("GITHUB_APP_PRIVATE_KEY")
        if key_content:
            return key_content.strip()

        key_path = (
            getattr(settings, "GITHUB_APP_PRIVATE_KEY_PATH", None)
            or os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")
            or "kobits-app.pem"
        )
        p = Path(key_path)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        return None

    @classmethod
    def generate_app_jwt(cls) -> Optional[str]:
        """
        Signs an RS256 JWT using the GitHub App's private key valid for 10 minutes.
        """
        app_id = cls.get_app_id()
        private_key = cls.get_private_key()

        if not app_id or not private_key:
            return None

        now = int(time.time())
        payload = {
            "iat": now - 60,       # Issued 60s in the past to prevent clock drift issues
            "exp": now + (10 * 60),# Valid for 10 minutes
            "iss": str(app_id),
        }
        try:
            return jwt.encode(payload, private_key, algorithm="RS256")
        except Exception as e:
            logger.error(f"Failed to sign GitHub App JWT: {e}")
            return None

    @classmethod
    async def get_installation_access_token(cls, installation_id: str) -> Optional[str]:
        """
        Exchanges the App JWT for an ephemeral installation access token (ghs_...).
        Valid for 60 minutes.
        """
        jwt_token = cls.generate_app_jwt()
        if not jwt_token:
            return None

        url = f"{cls.BASE_URL}/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(url, headers=headers)
            if res.status_code == 201:
                data = res.json()
                return data.get("token")
            else:
                logger.error(f"Failed to generate installation token for {installation_id}: {res.status_code} {res.text}")
                return None

    @classmethod
    def verify_webhook_signature(cls, payload_bytes: bytes, signature_header: Optional[str]) -> bool:
        """
        Verifies the HMAC SHA-256 signature from incoming GitHub webhooks.
        """
        secret = (
            getattr(settings, "GITHUB_WEBHOOK_SECRET", None)
            or os.environ.get("GITHUB_WEBHOOK_SECRET")
        )
        if not secret:
            # If secret not configured in dev mode, skip verification
            return True

        if not signature_header or not signature_header.startswith("sha256="):
            return False

        expected_sig = signature_header.split("sha256=")[1]
        mac = hmac.new(secret.encode("utf-8"), msg=payload_bytes, digestmod=hashlib.sha256)
        computed_sig = mac.hexdigest()

        return hmac.compare_digest(computed_sig, expected_sig)
