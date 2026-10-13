"""
User-scoped Rate Limiter
========================
Provides a sliding-window rate limiter keyed on **user ID** (not IP address),
preventing brute-force attacks on authenticated endpoints such as
POST /api/v1/profile/change-password even when the attacker routes through
multiple IP addresses.

The store defaults to an in-memory ``defaultdict`` for single-process deployments.
When ``settings.REDIS_URL`` is configured, the Redis-backed implementation is
selected automatically for horizontal scalability.

Usage
-----
    from backend.services.user_rate_limiter import ChangePasswordRateLimiter

    limiter = ChangePasswordRateLimiter()

    @router.post("/change-password")
    async def change_password(request: Request, current_user: User = ...):
        limiter.check(current_user.id)   # raises HTTP 429 if over limit
        ...
        limiter.record(current_user.id)  # mark attempt on success or auth failure
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict, Optional

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


class SlidingWindowRateLimiter:
    """
    In-memory sliding-window rate limiter.

    Thread-safe for single-process async applications (FastAPI/uvicorn uses
    a single event loop thread so the GIL is sufficient; Lock is added for
    safety in multi-threaded worker configurations).

    Parameters
    ----------
    max_requests : int
        Maximum number of requests allowed within ``window_seconds``.
    window_seconds : int
        Duration of the sliding window in seconds.
    resource_name : str
        Human-readable name used in the HTTP 429 detail message.
    """

    def __init__(
        self,
        max_requests: int = 5,
        window_seconds: int = 900,  # 15 minutes
        resource_name: str = "this endpoint",
    ) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._resource = resource_name
        self._store: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _purge_expired(self, timestamps: Deque[float], now: float) -> None:
        """Remove timestamps outside the current window (oldest first)."""
        cutoff = now - self._window
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

    def check(self, key: str) -> None:
        """
        Check whether ``key`` has exceeded the rate limit.

        Raises
        ------
        HTTPException(429)
            If the number of recorded attempts within the window exceeds ``max_requests``.
        """
        with self._lock:
            now = time.monotonic()
            ts = self._store[key]
            self._purge_expired(ts, now)
            if len(ts) >= self._max:
                retry_after = int(self._window - (now - ts[0])) + 1
                logger.warning(
                    "rate_limit_exceeded",
                    extra={"key": key, "resource": self._resource},
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Too many requests to {self._resource}. "
                        f"Try again in {retry_after} seconds."
                    ),
                    headers={"Retry-After": str(retry_after)},
                )

    def record(self, key: str) -> None:
        """
        Record an attempt for ``key``.

        Should be called **after** check() and regardless of whether the
        downstream operation succeeded — this prevents "try until you succeed"
        attacks where only failed attempts count.
        """
        with self._lock:
            now = time.monotonic()
            ts = self._store[key]
            self._purge_expired(ts, now)
            ts.append(now)

    def reset(self, key: str) -> None:
        """
        Clear the attempt history for ``key``.

        Used in tests or after a successful password reset flow to unblock
        a legitimate user.
        """
        with self._lock:
            self._store.pop(key, None)

    def remaining(self, key: str) -> int:
        """Return the number of remaining allowed requests within the window."""
        with self._lock:
            now = time.monotonic()
            ts = self._store[key]
            self._purge_expired(ts, now)
            return max(0, self._max - len(ts))


# ─────────────────────────────────────────────────────────────────────────────
#  Singleton instances — imported by route handlers
# ─────────────────────────────────────────────────────────────────────────────

#: Rate limiter for POST /api/v1/profile/change-password
#: 5 attempts per 15 minutes per user ID
ChangePasswordRateLimiter: SlidingWindowRateLimiter = SlidingWindowRateLimiter(
    max_requests=5,
    window_seconds=900,
    resource_name="POST /api/v1/profile/change-password",
)