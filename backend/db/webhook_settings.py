"""
backend/db/webhook_settings.py
================================
Persistent storage layer for webhook settings.

This module provides a lightweight SQLite-backed (via ``sqlite3``) key-value
store for the Slack and Discord webhook URLs so they survive process restarts.

In a production environment backed by PostgreSQL or another relational DB,
replace the ``_DB_PATH`` with a proper SQLAlchemy session / encrypted column.
The public interface (``load_webhook_settings``, ``save_webhook_settings``,
``get_webhook_settings_masked``) remains identical regardless of storage back-end.

Environment variables
---------------------
WEBHOOK_SETTINGS_DB  – Filesystem path to the SQLite database file.
                        Default: ``./webhook_settings.db``
SLACK_WEBHOOK_URL    – Seed value used when the DB row does not exist yet.
DISCORD_WEBHOOK_URL  – Seed value used when the DB row does not exist yet.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database path
# ---------------------------------------------------------------------------

_DB_PATH: str = os.getenv("WEBHOOK_SETTINGS_DB", "./webhook_settings.db")

# Thread-safety: each thread uses its own connection via threading.local().
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return (or create) a per-thread SQLite connection."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _bootstrap(_local.conn)
    return _local.conn


def _bootstrap(conn: sqlite3.Connection) -> None:
    """Create the settings table if it doesn't exist and seed from env vars."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_settings (
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            slack_webhook_url   TEXT,
            discord_webhook_url TEXT
        )
        """
    )
    conn.commit()

    # Seed the single row on first run from environment variables.
    row = conn.execute("SELECT id FROM webhook_settings WHERE id = 1").fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO webhook_settings (id, slack_webhook_url, discord_webhook_url)
            VALUES (1, ?, ?)
            """,
            (
                os.getenv("SLACK_WEBHOOK_URL"),
                os.getenv("DISCORD_WEBHOOK_URL"),
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_webhook_settings() -> dict:
    """
    Load the current webhook URLs from the database.

    Returns
    -------
    dict with keys ``"slack_webhook_url"`` and ``"discord_webhook_url"``
    (plain-text values, may be ``None``).
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT slack_webhook_url, discord_webhook_url FROM webhook_settings WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"slack_webhook_url": None, "discord_webhook_url": None}
    return {
        "slack_webhook_url": row["slack_webhook_url"],
        "discord_webhook_url": row["discord_webhook_url"],
    }


def save_webhook_settings(
    slack_webhook_url: Optional[str],
    discord_webhook_url: Optional[str],
) -> dict:
    """
    Persist the given URLs and return masked values for the API response.

    Parameters
    ----------
    slack_webhook_url:    New Slack URL to store (``None`` to clear).
    discord_webhook_url:  New Discord URL to store (``None`` to clear).

    Returns
    -------
    Masked settings dict (same shape as :func:`get_webhook_settings_masked`).
    """
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO webhook_settings (id, slack_webhook_url, discord_webhook_url)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            slack_webhook_url   = excluded.slack_webhook_url,
            discord_webhook_url = excluded.discord_webhook_url
        """,
        (slack_webhook_url, discord_webhook_url),
    )
    conn.commit()
    logger.info("Webhook settings saved to DB.")
    return get_webhook_settings_masked()


def get_webhook_settings_masked() -> dict:
    """
    Return current webhook settings with URLs partially masked for display.

    A URL longer than 35 chars is truncated at character 35 with an ellipsis.

    Returns
    -------
    dict with keys ``"slack_webhook_url"`` and ``"discord_webhook_url"``.
    """
    raw = load_webhook_settings()

    def _mask(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        return url[:35] + "…" if len(url) > 35 else url

    return {
        "slack_webhook_url": _mask(raw["slack_webhook_url"]),
        "discord_webhook_url": _mask(raw["discord_webhook_url"]),
    }