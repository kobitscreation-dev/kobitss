import pytest
from backend.services.github_app import GitHubAppService


def test_github_app_service_missing_config():
    # When no APP_ID is set, JWT generation should safely return None without throwing
    jwt_token = GitHubAppService.generate_app_jwt()
    assert jwt_token is None


def test_webhook_signature_verification():
    secret = "test_webhook_secret_123"
    payload = b'{"action": "created", "issue": {"number": 42}}'

    import hmac, hashlib
    mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
    valid_sig = f"sha256={mac.hexdigest()}"

    # Temporarily set secret
    import os
    os.environ["GITHUB_WEBHOOK_SECRET"] = secret

    assert GitHubAppService.verify_webhook_signature(payload, valid_sig) is True
    assert GitHubAppService.verify_webhook_signature(payload, "sha256=invalid") is False
    assert GitHubAppService.verify_webhook_signature(payload, None) is False
