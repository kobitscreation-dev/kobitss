"""
Unit tests for backend/services/notifications.py

Uses unittest.mock to patch requests.post so no real HTTP calls are made.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Ensure the workspace root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.services.notifications import (
    DiffStats,
    MissionPayload,
    SprintTask,
    _build_discord_payload,
    _build_slack_payload,
    notify_mission_event,
    post_discord_notification,
    post_slack_notification,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_mission(status: str = "SUCCESS") -> MissionPayload:
    return MissionPayload(
        mission_id="test-mission-001",
        mission_title="Implement OAuth2 Login",
        status=status,
        diff_stats=DiffStats(files_changed=4, insertions=120, deletions=30),
        sprint_tasks=[
            SprintTask("Design DB schema", "DONE", "DATABASE_ENGINEER"),
            SprintTask("Implement endpoints", "DONE", "BACKEND_ENGINEER"),
            SprintTask("Add login UI", "DONE", "FRONTEND_ENGINEER"),
        ],
        extra_notes="Merged cleanly, no conflicts.",
    )


# ---------------------------------------------------------------------------
# MissionPayload property tests
# ---------------------------------------------------------------------------

class TestMissionPayloadProperties(unittest.TestCase):

    def test_view_diff_url_format(self):
        m = _make_mission()
        assert "/missions/test-mission-001/diff" in m.view_diff_url

    def test_status_emoji_success(self):
        assert _make_mission("SUCCESS").status_emoji == "✅"

    def test_status_emoji_failed(self):
        assert _make_mission("FAILED").status_emoji == "❌"

    def test_color_hex_success(self):
        assert _make_mission("SUCCESS").color_hex == 0x2ECC71

    def test_color_hex_failed(self):
        assert _make_mission("FAILED").color_hex == 0xE74C3C


# ---------------------------------------------------------------------------
# Payload builder tests
# ---------------------------------------------------------------------------

class TestSlackPayloadBuilder(unittest.TestCase):

    def setUp(self):
        self.mission = _make_mission()
        self.payload = _build_slack_payload(self.mission)

    def test_payload_is_dict(self):
        self.assertIsInstance(self.payload, dict)

    def test_has_blocks(self):
        self.assertIn("blocks", self.payload)
        self.assertIsInstance(self.payload["blocks"], list)
        self.assertGreater(len(self.payload["blocks"]), 0)

    def test_contains_view_diff_url(self):
        import json
        serialised = json.dumps(self.payload)
        self.assertIn("/missions/test-mission-001/diff", serialised)

    def test_contains_diff_stats(self):
        import json
        serialised = json.dumps(self.payload)
        self.assertIn("120", serialised)  # insertions

    def test_contains_sprint_tasks(self):
        import json
        serialised = json.dumps(self.payload)
        self.assertIn("Implement endpoints", serialised)

    def test_failed_mission_label(self):
        failed_payload = _build_slack_payload(_make_mission("FAILED"))
        import json
        serialised = json.dumps(failed_payload)
        self.assertIn("FAILED", serialised)


class TestDiscordPayloadBuilder(unittest.TestCase):

    def setUp(self):
        self.mission = _make_mission()
        self.payload = _build_discord_payload(self.mission)

    def test_payload_is_dict(self):
        self.assertIsInstance(self.payload, dict)

    def test_has_embeds(self):
        self.assertIn("embeds", self.payload)
        self.assertIsInstance(self.payload["embeds"], list)
        self.assertEqual(len(self.payload["embeds"]), 1)

    def test_embed_color_success(self):
        self.assertEqual(self.payload["embeds"][0]["color"], 0x2ECC71)

    def test_embed_color_failed(self):
        failed_payload = _build_discord_payload(_make_mission("FAILED"))
        self.assertEqual(failed_payload["embeds"][0]["color"], 0xE74C3C)

    def test_contains_view_diff_url(self):
        import json
        serialised = json.dumps(self.payload)
        self.assertIn("/missions/test-mission-001/diff", serialised)

    def test_contains_diff_stats_code_block(self):
        embed = self.payload["embeds"][0]
        diff_field = next(f for f in embed["fields"] if "Diff" in f["name"])
        self.assertIn("```diff", diff_field["value"])
        self.assertIn("+", diff_field["value"])

    def test_contains_sprint_tasks(self):
        import json
        serialised = json.dumps(self.payload)
        self.assertIn("Implement endpoints", serialised)

    def test_no_tasks_fallback(self):
        empty_mission = MissionPayload(
            mission_id="empty-001",
            mission_title="Empty Mission",
            status="SUCCESS",
        )
        payload = _build_discord_payload(empty_mission)
        tasks_field = next(f for f in payload["embeds"][0]["fields"] if "Sprint" in f["name"])
        self.assertIn("No tasks recorded", tasks_field["value"])


# ---------------------------------------------------------------------------
# HTTP dispatch tests (mocked)
# ---------------------------------------------------------------------------

class TestPostSlackNotification(unittest.TestCase):

    @patch("backend.services.notifications.requests.post")
    def test_posts_to_configured_url(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        result = post_slack_notification(_make_mission(), webhook_url="https://hooks.slack.com/fake")
        self.assertTrue(result)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertEqual(call_kwargs[0][0], "https://hooks.slack.com/fake")

    @patch("backend.services.notifications.requests.post")
    def test_returns_false_on_http_error(self, mock_post):
        import requests as req
        mock_post.side_effect = req.exceptions.RequestException("timeout")
        result = post_slack_notification(_make_mission(), webhook_url="https://hooks.slack.com/fake")
        self.assertFalse(result)

    def test_returns_false_when_no_url(self):
        import backend.services.notifications as n
        original = n.SLACK_WEBHOOK_URL
        n.SLACK_WEBHOOK_URL = None
        result = post_slack_notification(_make_mission())
        n.SLACK_WEBHOOK_URL = original
        self.assertFalse(result)


class TestPostDiscordNotification(unittest.TestCase):

    @patch("backend.services.notifications.requests.post")
    def test_posts_to_configured_url(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        result = post_discord_notification(_make_mission(), webhook_url="https://discord.com/api/webhooks/fake")
        self.assertTrue(result)
        mock_post.assert_called_once()

    @patch("backend.services.notifications.requests.post")
    def test_returns_false_on_http_error(self, mock_post):
        import requests as req
        mock_post.side_effect = req.exceptions.ConnectionError("refused")
        result = post_discord_notification(_make_mission(), webhook_url="https://discord.com/api/webhooks/fake")
        self.assertFalse(result)

    def test_returns_false_when_no_url(self):
        import backend.services.notifications as n
        original = n.DISCORD_WEBHOOK_URL
        n.DISCORD_WEBHOOK_URL = None
        result = post_discord_notification(_make_mission())
        n.DISCORD_WEBHOOK_URL = original
        self.assertFalse(result)


class TestNotifyMissionEvent(unittest.TestCase):

    @patch("backend.services.notifications.requests.post")
    def test_dispatches_to_both_targets(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        results = notify_mission_event(
            _make_mission(),
            slack_webhook_url="https://hooks.slack.com/fake",
            discord_webhook_url="https://discord.com/api/webhooks/fake",
        )
        self.assertTrue(results["slack"])
        self.assertTrue(results["discord"])
        self.assertEqual(mock_post.call_count, 2)

    @patch("backend.services.notifications.requests.post")
    def test_returns_dict_with_slack_discord_keys(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        results = notify_mission_event(
            _make_mission(),
            slack_webhook_url="https://hooks.slack.com/fake",
            discord_webhook_url="https://discord.com/api/webhooks/fake",
        )
        self.assertIn("slack", results)
        self.assertIn("discord", results)

    def test_no_urls_returns_false_for_both(self):
        import backend.services.notifications as n
        orig_s, orig_d = n.SLACK_WEBHOOK_URL, n.DISCORD_WEBHOOK_URL
        n.SLACK_WEBHOOK_URL = None
        n.DISCORD_WEBHOOK_URL = None

        results = notify_mission_event(_make_mission())
        self.assertFalse(results["slack"])
        self.assertFalse(results["discord"])

        n.SLACK_WEBHOOK_URL = orig_s
        n.DISCORD_WEBHOOK_URL = orig_d


if __name__ == "__main__":
    unittest.main()