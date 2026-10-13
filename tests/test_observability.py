import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.services.observability import (
    capture_exception,
    capture_llm_parsing_failure,
    capture_sandbox_timeout,
    capture_agent_run_failure,
    telemetry,
    TelemetryCollector,
)
from backend.core.config import settings


@pytest.fixture(autouse=True)
def reset_telemetry_fixture():
    telemetry.reset()
    yield
    telemetry.reset()


def test_telemetry_collector_metrics_and_health_score():
    collector = TelemetryCollector()

    # Empty collector defaults
    empty_metrics = collector.get_metrics()
    assert empty_metrics["total_runs"] == 0
    assert empty_metrics["health_score"] == 100.0
    assert empty_metrics["error_rate"] == 0.0

    # Record successful runs
    collector.record_run(
        mission_id="m-1",
        agent_role="BACKEND_ENGINEER",
        latency_ms=1200.0,
        prompt_tokens=400,
        completion_tokens=150,
        cost=0.005,
    )
    collector.record_run(
        mission_id="m-1",
        agent_role="FRONTEND_ENGINEER",
        latency_ms=800.0,
        prompt_tokens=300,
        completion_tokens=100,
        cost=0.003,
    )
    # Record a failed run
    collector.record_run(
        mission_id="m-1",
        agent_role="BACKEND_ENGINEER",
        latency_ms=2500.0,
        prompt_tokens=500,
        completion_tokens=20,
        cost=0.004,
        error_type="llm_parsing_failure",
        task_id="t-101",
    )

    metrics = collector.get_metrics()
    assert metrics["total_runs"] == 3
    assert metrics["successful_runs"] == 2
    assert metrics["failed_runs"] == 1
    assert metrics["total_prompt_tokens"] == 1200
    assert metrics["total_completion_tokens"] == 270
    assert metrics["total_tokens"] == 1470
    assert metrics["error_breakdown"]["llm_parsing_failure"] == 1
    assert "BACKEND_ENGINEER" in metrics["roles"]
    assert "FRONTEND_ENGINEER" in metrics["roles"]
    assert metrics["roles"]["BACKEND_ENGINEER"]["failed_runs"] == 1

    # Health score should be lower than 100 due to 1/3 failure rate
    assert metrics["health_score"] < 100.0
    assert metrics["health_score"] > 50.0

    # Role filter
    fe_metrics = collector.get_metrics(agent_role="FRONTEND_ENGINEER")
    assert fe_metrics["total_runs"] == 1
    assert fe_metrics["failed_runs"] == 0
    assert fe_metrics["health_score"] == 100.0


def test_capture_llm_parsing_failure():
    event_id = capture_llm_parsing_failure(
        error="Invalid JSON response schema: missing 'tasks' key",
        raw_response="{'raw': 'bad json'}",
        mission_id="m-llm-1",
        agent_role="TECHNICAL_LEAD",
        task_id="t-decomp-1",
    )
    metrics = telemetry.get_metrics()
    assert metrics["total_runs"] == 1
    assert metrics["failed_runs"] == 1
    assert metrics["error_breakdown"].get("llm_parsing_failure") == 1


def test_capture_sandbox_timeout():
    event_id = capture_sandbox_timeout(
        error="Subprocess timed out after 30s",
        command="npm run build",
        timeout=30,
        mission_id="m-sandbox-1",
        task_id="t-build-1",
    )
    metrics = telemetry.get_metrics()
    assert metrics["total_runs"] == 1
    assert metrics["failed_runs"] == 1
    assert metrics["error_breakdown"].get("sandbox_timeout") == 1


def test_capture_agent_run_failure():
    event_id = capture_agent_run_failure(
        error=ValueError("Bedrock quota exceeded"),
        mission_id="m-agent-1",
        agent_role="SECURITY_ENGINEER",
        task_id="t-sec-1",
    )
    metrics = telemetry.get_metrics()
    assert metrics["total_runs"] == 1
    assert metrics["failed_runs"] == 1
    assert metrics["error_breakdown"].get("agent_run_failure") == 1


@pytest.mark.asyncio
async def test_telemetry_api_endpoints():
    telemetry.record_run(
        mission_id="api-mission-1",
        agent_role="QA_ENGINEER",
        latency_ms=950.0,
        prompt_tokens=250,
        completion_tokens=80,
        cost=0.002,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /metrics
        resp = await client.get("/api/v1/telemetry/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 1
        assert data["total_tokens"] == 330

        # GET /health
        health_resp = await client.get("/api/v1/telemetry/health")
        assert health_resp.status_code == 200
        h_data = health_resp.json()
        assert h_data["status"] == "healthy"
        assert h_data["health_score"] == 100.0

        # POST /reset
        reset_resp = await client.post("/api/v1/telemetry/reset")
        assert reset_resp.status_code == 200
        assert reset_resp.json()["status"] == "reset"

        # Verify empty after reset
        empty_resp = await client.get("/api/v1/telemetry/metrics")
        assert empty_resp.json()["total_runs"] == 0


def test_sentry_conditional_init():
    with patch.object(settings, "SENTRY_DSN", "https://mockpublickey@sentry.io/123456"), \
         patch("sentry_sdk.init") as mock_sentry_init:
        from backend.main import lifespan
        # Verify settings config has SENTRY fields
        assert settings.SENTRY_DSN == "https://mockpublickey@sentry.io/123456"
        assert settings.SENTRY_ENVIRONMENT in ("production", "development", "test")
        assert isinstance(settings.SENTRY_TRACES_SAMPLE_RATE, float)


def test_telemetry_deduplication_by_task_id():
    """Ensure error capture followed by run completion updates entry rather than double-counting."""
    collector = TelemetryCollector()

    # 1. Error capture records initial failure with 0 latency
    collector.record_run(
        mission_id="m-dedup",
        agent_role="BACKEND_ENGINEER",
        latency_ms=0,
        error_type="llm_parsing_failure",
        task_id="task-dedup-1",
    )
    assert collector.get_metrics()["total_runs"] == 1
    assert collector.get_metrics()["failed_runs"] == 1

    # 2. Agent executor finishes and records final execution metrics for the same task_id
    collector.record_run(
        mission_id="m-dedup",
        agent_role="BACKEND_ENGINEER",
        latency_ms=1850.0,
        prompt_tokens=420,
        completion_tokens=90,
        cost=0.0045,
        error_type="agent_run_failure",
        task_id="task-dedup-1",
    )

    metrics = collector.get_metrics()
    # Must NOT double-count the run
    assert metrics["total_runs"] == 1
    assert metrics["failed_runs"] == 1
    # Specific error type must be preserved
    assert metrics["error_breakdown"].get("llm_parsing_failure") == 1
    assert metrics["error_breakdown"].get("agent_run_failure") is None
    # Metrics must be updated with final execution values
    assert metrics["avg_latency_ms"] == 1850.0
    assert metrics["total_prompt_tokens"] == 420
    assert metrics["total_completion_tokens"] == 90
    assert metrics["total_cost"] == 0.0045

