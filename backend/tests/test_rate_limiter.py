"""
Unit tests for RateLimiterMiddleware (backend/main.py).

These tests are fully self-contained: they build a minimal FastAPI test app,
attach a fresh RateLimiterMiddleware instance, and exercise every behaviour
branch using httpx AsyncClient / TestClient – no database, no auth required.
"""
import asyncio
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from backend.main import RateLimiterMiddleware


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_app(
    max_requests: int = 3,
    window_seconds: int = 60,
    exempt_paths=("/exempt",),
) -> FastAPI:
    """Return a minimal FastAPI app with RateLimiterMiddleware pre-attached."""
    app = FastAPI()
    app.add_middleware(
        RateLimiterMiddleware,
        max_requests=max_requests,
        window_seconds=window_seconds,
        exempt_paths=exempt_paths,
    )

    @app.get("/ping")
    async def ping():
        return {"pong": True}

    @app.get("/exempt")
    async def exempt_route():
        return {"exempt": True}

    return app


# ─────────────────────────────────────────────────────────────────────────────
# 1. Requests within the limit succeed (200 OK + rate-limit headers present)
# ─────────────────────────────────────────────────────────────────────────────

def test_requests_within_limit_succeed():
    app = _make_app(max_requests=3)
    client = TestClient(app, raise_server_exceptions=True)

    for i in range(3):
        resp = client.get("/ping")
        assert resp.status_code == 200, f"Request #{i+1} should succeed"
        assert "x-ratelimit-limit" in resp.headers
        assert "x-ratelimit-remaining" in resp.headers
        assert "x-ratelimit-reset" in resp.headers
        assert int(resp.headers["x-ratelimit-limit"]) == 3


# ─────────────────────────────────────────────────────────────────────────────
# 2. The X-RateLimit-Remaining counter decrements correctly
# ─────────────────────────────────────────────────────────────────────────────

def test_remaining_header_decrements():
    app = _make_app(max_requests=5)
    client = TestClient(app)

    for expected_remaining in [4, 3, 2, 1, 0]:
        resp = client.get("/ping")
        assert resp.status_code == 200
        assert int(resp.headers["x-ratelimit-remaining"]) == expected_remaining


# ─────────────────────────────────────────────────────────────────────────────
# 3. Exceeding the limit returns HTTP 429 with correct headers
# ─────────────────────────────────────────────────────────────────────────────

def test_exceeding_limit_returns_429():
    app = _make_app(max_requests=2)
    client = TestClient(app)

    # Consume the quota
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200

    # Next request must be rejected
    resp = client.get("/ping")
    assert resp.status_code == 429
    body = resp.json()
    assert "detail" in body
    assert "retry_after_seconds" in body
    assert "retry-after" in resp.headers
    assert resp.headers["x-ratelimit-remaining"] == "0"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Exempt paths bypass rate limiting entirely
# ─────────────────────────────────────────────────────────────────────────────

def test_exempt_path_bypasses_rate_limit():
    app = _make_app(max_requests=1, exempt_paths=("/exempt",))
    client = TestClient(app)

    # Exhaust the rate limit on /ping
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 429

    # /exempt must remain accessible regardless
    for _ in range(5):
        resp = client.get("/exempt")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 5. Different client IPs have independent counters
# ─────────────────────────────────────────────────────────────────────────────

def test_different_ips_tracked_independently():
    app = _make_app(max_requests=1)
    client = TestClient(app)

    # First IP exhausts its quota
    resp_a = client.get("/ping", headers={"X-Forwarded-For": "10.0.0.1"})
    assert resp_a.status_code == 200
    resp_a2 = client.get("/ping", headers={"X-Forwarded-For": "10.0.0.1"})
    assert resp_a2.status_code == 429

    # Second IP still has its own fresh quota
    resp_b = client.get("/ping", headers={"X-Forwarded-For": "10.0.0.2"})
    assert resp_b.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 6. Window slides: after the window expires, the quota resets
# ─────────────────────────────────────────────────────────────────────────────

def test_quota_resets_after_window_expires():
    app = _make_app(max_requests=2, window_seconds=1)
    client = TestClient(app)

    # Exhaust quota
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 429

    # Advance time past the window so old timestamps are evicted
    with patch("backend.main.time.time", return_value=time.time() + 2):
        resp = client.get("/ping")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 7. X-Forwarded-For header is respected (proxy awareness)
# ─────────────────────────────────────────────────────────────────────────────

def test_x_forwarded_for_parsed_correctly():
    """First IP in a comma-separated X-Forwarded-For chain is used."""
    app = _make_app(max_requests=1)
    client = TestClient(app)

    # Use a multi-hop header; only 1.2.3.4 should be the key
    resp = client.get("/ping", headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
    assert resp.status_code == 200

    # Same real IP → should be rate-limited
    resp2 = client.get("/ping", headers={"X-Forwarded-For": "1.2.3.4, 9.9.9.9"})
    assert resp2.status_code == 429

    # Different IP → fresh quota
    resp3 = client.get("/ping", headers={"X-Forwarded-For": "2.2.2.2"})
    assert resp3.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 8. Multiple simultaneous clients (async concurrency smoke-test)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_requests_do_not_corrupt_state():
    """
    Fire many concurrent requests from distinct IPs and ensure each IP is
    limited to max_requests, never exceeds it, and sees consistent headers.
    """
    app = _make_app(max_requests=5, window_seconds=60)
    transport = ASGITransport(app=app)
    results: dict[str, list[int]] = {}

    async def fire_requests(ip: str, count: int):
        codes = []
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for _ in range(count):
                r = await ac.get("/ping", headers={"X-Forwarded-For": ip})
                codes.append(r.status_code)
        results[ip] = codes

    # 4 IPs, 8 requests each (5 allowed, 3 blocked)
    await asyncio.gather(*(fire_requests(f"192.168.1.{i}", 8) for i in range(1, 5)))

    for ip, codes in results.items():
        successes = codes.count(200)
        failures = codes.count(429)
        assert successes == 5, f"{ip}: expected 5 successes, got {successes}"
        assert failures == 3, f"{ip}: expected 3 rejections, got {failures}"


# ─────────────────────────────────────────────────────────────────────────────
# 9. RateLimiterMiddleware constructor defaults
# ─────────────────────────────────────────────────────────────────────────────

def test_middleware_default_configuration():
    """Verify the middleware stores the values it was initialised with."""
    dummy_app = FastAPI()
    mw = RateLimiterMiddleware(dummy_app, max_requests=50, window_seconds=30)
    assert mw.max_requests == 50
    assert mw.window_seconds == 30


# ─────────────────────────────────────────────────────────────────────────────
# 10. 429 response contains machine-readable JSON body
# ─────────────────────────────────────────────────────────────────────────────

def test_429_body_is_json():
    app = _make_app(max_requests=1)
    client = TestClient(app)

    client.get("/ping")                   # consume quota
    resp = client.get("/ping")            # trigger 429
    assert resp.status_code == 429
    data = resp.json()
    assert isinstance(data.get("detail"), str)
    assert isinstance(data.get("retry_after_seconds"), int)
    assert data["retry_after_seconds"] >= 0
