"""
backend/api/webhooks.py
========================
REST API endpoints for webhook settings management.

Routes
------
GET  /api/settings/webhooks        -> Returns current Slack/Discord URLs (masked)
PATCH /api/settings/webhooks       -> Saves Slack/Discord URLs
POST /api/settings/webhooks/test   -> Sends a test notification to the given platform

NOTE: In production, webhook URLs should be stored in a secrets manager or
encrypted column. This module uses environment-variable overrides as a
reference implementation; adapt the storage layer to your ORM/DB.
"""

from __future__ import annotations

import os
from typing import Optional

try:
    from fastapi import APIRouter, HTTPException, status
    from pydantic import BaseModel, HttpUrl, field_validator
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

from backend.services.notifications import (
    DiffStats,
    MissionPayload,
    SprintTask,
    post_discord_notification,
    post_slack_notification,
)

# ---------------------------------------------------------------------------
# In-memory settings store (replace with DB / secrets manager in production)
# ---------------------------------------------------------------------------

_webhook_settings: dict[str, Optional[str]] = {
    "slack_webhook_url": os.getenv("SLACK_WEBHOOK_URL"),
    "discord_webhook_url": os.getenv("DISCORD_WEBHOOK_URL"),
}


def get_webhook_settings() -> dict:
    """Return current webhook settings (URLs masked for security)."""
    def _mask(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        # Keep scheme + first 12 chars + trailing dots for display
        if len(url) > 35:
            return url[:35] + "…"
        return url

    return {
        "slack_webhook_url": _mask(_webhook_settings["slack_webhook_url"]),
        "discord_webhook_url": _mask(_webhook_settings["discord_webhook_url"]),
    }


def update_webhook_settings(
    slack_webhook_url: Optional[str],
    discord_webhook_url: Optional[str],
) -> dict:
    """Persist (in-memory) the webhook URLs and return masked values."""
    _webhook_settings["slack_webhook_url"] = slack_webhook_url
    _webhook_settings["discord_webhook_url"] = discord_webhook_url
    return get_webhook_settings()


def send_test_notification(platform: str) -> bool:
    """
    Fire a test notification to the given platform using current settings.

    Returns True on success, raises ValueError if platform is unknown or
    URL is not configured.
    """
    test_mission = MissionPayload(
        mission_id="test-000",
        mission_title="🧪 Test Notification",
        status="SUCCESS",
        diff_stats=DiffStats(files_changed=1, insertions=10, deletions=2),
        sprint_tasks=[SprintTask("Verify webhook connectivity", "DONE", "DEVOPS_ENGINEER")],
        extra_notes="This is a test notification from Kobits.",
    )

    if platform == "slack":
        url = _webhook_settings.get("slack_webhook_url")
        if not url:
            raise ValueError("Slack webhook URL is not configured.")
        return post_slack_notification(test_mission, webhook_url=url)

    elif platform == "discord":
        url = _webhook_settings.get("discord_webhook_url")
        if not url:
            raise ValueError("Discord webhook URL is not configured.")
        return post_discord_notification(test_mission, webhook_url=url)

    else:
        raise ValueError(f"Unknown platform: {platform!r}. Expected 'slack' or 'discord'.")


# ---------------------------------------------------------------------------
# FastAPI router (only registered when FastAPI is available)
# ---------------------------------------------------------------------------

if _FASTAPI_AVAILABLE:
    router = APIRouter(prefix="/api/settings/webhooks", tags=["webhooks"])

    class WebhookSettingsRead(BaseModel):
        slack_webhook_url: Optional[str] = None
        discord_webhook_url: Optional[str] = None

    class WebhookSettingsUpdate(BaseModel):
        slack_webhook_url: Optional[HttpUrl] = None
        discord_webhook_url: Optional[HttpUrl] = None

        @field_validator("slack_webhook_url", mode="before")
        @classmethod
        def validate_slack(cls, v):
            if v and not str(v).startswith("https://hooks.slack.com/"):
                raise ValueError("Slack webhook URL must start with https://hooks.slack.com/")
            return v

        @field_validator("discord_webhook_url", mode="before")
        @classmethod
        def validate_discord(cls, v):
            if v and not str(v).startswith("https://discord.com/api/webhooks/"):
                raise ValueError("Discord webhook URL must start with https://discord.com/api/webhooks/")
            return v

    class TestRequest(BaseModel):
        platform: str  # "slack" | "discord"

    @router.get("", response_model=WebhookSettingsRead, summary="Get webhook settings")
    async def api_get_webhook_settings():
        """Return the currently configured webhook URLs (masked)."""
        return get_webhook_settings()

    @router.patch("", response_model=WebhookSettingsRead, summary="Update webhook settings")
    async def api_update_webhook_settings(body: WebhookSettingsUpdate):
        """Save new Slack / Discord webhook URLs."""
        updated = update_webhook_settings(
            slack_webhook_url=str(body.slack_webhook_url) if body.slack_webhook_url else None,
            discord_webhook_url=str(body.discord_webhook_url) if body.discord_webhook_url else None,
        )
        return updated

    @router.post("/test", summary="Send a test notification")
    async def api_test_webhook(body: TestRequest):
        """
        Fire a test notification to the specified platform.
        Returns 200 on success, 400 if the URL is missing, 502 if delivery fails.
        """
        try:
            ok = send_test_notification(body.platform)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

        if not ok:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Test notification to {body.platform!r} was dispatched but the remote server rejected it.",
            )
        return {"detail": f"Test notification sent to {body.platform!r} successfully."}