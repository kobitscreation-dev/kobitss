import os
import time
import json
import logging
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

logger = logging.getLogger("kobits.observability")


def is_sentry_available() -> bool:
    """Check if sentry_sdk is installed and initialized."""
    try:
        import sentry_sdk
        hub = getattr(sentry_sdk, "Hub", None)
        if hub and hasattr(hub, "current") and hub.current.client:
            return True
        # Modern sentry_sdk (v2+)
        client = getattr(sentry_sdk, "get_client", None)
        if callable(client) and client().is_active():
            return True
        return False
    except Exception:
        return False


def capture_exception(
    error: Exception | str,
    tags: Optional[Dict[str, str]] = None,
    extra: Optional[Dict[str, Any]] = None,
    level: str = "error",
) -> Optional[str]:
    """
    Centralized exception capture helper.
    Logs structured exception locally and ships to Sentry if configured.
    """
    tags = tags or {}
    extra = extra or {}
    event_id = None

    # Structured local logging
    log_msg = f"[Observability] {level.upper()}: {error} | tags={tags} | extra={extra}"
    if level == "warning":
        logger.warning(log_msg)
    elif level == "info":
        logger.info(log_msg)
    else:
        logger.error(log_msg, exc_info=isinstance(error, Exception))

    try:
        import sentry_sdk
        scope_cm = getattr(sentry_sdk, "new_scope", getattr(sentry_sdk, "push_scope", None))
        if scope_cm:
            with scope_cm() as scope:
                for k, v in tags.items():
                    if v is not None:
                        scope.set_tag(str(k), str(v))
                for k, v in extra.items():
                    if v is not None:
                        scope.set_extra(str(k), v)
                scope.set_level(level)

                if isinstance(error, Exception):
                    event_id = sentry_sdk.capture_exception(error)
                else:
                    event_id = sentry_sdk.capture_message(str(error), level=level)
        else:
            if isinstance(error, Exception):
                event_id = sentry_sdk.capture_exception(error)
            else:
                event_id = sentry_sdk.capture_message(str(error), level=level)
    except Exception as e:
        logger.debug(f"Failed to ship event to Sentry: {e}")

    return event_id


def capture_llm_parsing_failure(
    error: Exception | str,
    raw_response: Any = None,
    mission_id: Optional[str] = None,
    agent_role: Optional[str] = None,
    task_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Capture silent LLM output parsing failures or schema validation errors."""
    tags = {
        "subsystem": "llm",
        "error_type": "llm_parsing_failure",
        "mission_id": mission_id,
        "agent_role": agent_role,
        "task_id": task_id,
    }
    merged_extra = extra.copy() if extra else {}
    if raw_response is not None:
        raw_str = str(raw_response)
        merged_extra["raw_response_snippet"] = raw_str[:1000]

    try:
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category="llm.parsing",
            message=f"LLM Parsing Failure: {str(error)[:200]}",
            level="error",
            data={
                "mission_id": mission_id,
                "agent_role": agent_role,
                "task_id": task_id,
            },
        )
    except Exception:
        pass

    telemetry.record_run(
        mission_id=mission_id,
        agent_role=agent_role or "unknown",
        latency_ms=0,
        error_type="llm_parsing_failure",
        task_id=task_id,
    )

    return capture_exception(error, tags=tags, extra=merged_extra, level="error")


def capture_sandbox_timeout(
    error: Exception | str,
    command: str,
    timeout: int | float,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Capture sandbox command execution timeouts."""
    tags = {
        "subsystem": "sandbox",
        "error_type": "sandbox_timeout",
        "mission_id": mission_id,
        "task_id": task_id,
    }
    merged_extra = extra.copy() if extra else {}
    merged_extra["command"] = command[:500]
    merged_extra["timeout_seconds"] = timeout

    try:
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category="sandbox.timeout",
            message=f"Sandbox command timed out after {timeout}s: {command[:100]}",
            level="error",
            data={
                "command": command[:200],
                "timeout": timeout,
                "mission_id": mission_id,
                "task_id": task_id,
            },
        )
    except Exception:
        pass

    telemetry.record_run(
        mission_id=mission_id,
        agent_role="sandbox_runner",
        latency_ms=float(timeout) * 1000,
        error_type="sandbox_timeout",
        task_id=task_id,
    )

    return capture_exception(error, tags=tags, extra=merged_extra, level="error")


def capture_agent_run_failure(
    error: Exception | str,
    mission_id: Optional[str] = None,
    agent_role: Optional[str] = None,
    task_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Capture general agent execution failures."""
    tags = {
        "subsystem": "agent_executor",
        "error_type": "agent_run_failure",
        "mission_id": mission_id,
        "agent_role": agent_role,
        "task_id": task_id,
    }
    merged_extra = extra.copy() if extra else {}

    try:
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category="agent.run",
            message=f"Agent Run Failure: {str(error)[:200]}",
            level="error",
            data={
                "mission_id": mission_id,
                "agent_role": agent_role,
                "task_id": task_id,
            },
        )
    except Exception:
        pass

    telemetry.record_run(
        mission_id=mission_id,
        agent_role=agent_role or "unknown",
        latency_ms=0,
        error_type="agent_run_failure",
        task_id=task_id,
    )

    return capture_exception(error, tags=tags, extra=merged_extra, level="error")


class TelemetryCollector:
    """
    Centralized agent telemetry metrics collector.
    Tracks latency, prompt/completion tokens, cost, error types, and health score.
    Thread-safe and fast in-memory aggregation.
    """

    def __init__(self, max_history: int = 1000):
        self._lock = threading.Lock()
        self.max_history = max_history
        self._runs: List[Dict[str, Any]] = []

    def record_run(
        self,
        mission_id: Optional[str],
        agent_role: str,
        latency_ms: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost: float = 0.0,
        error_type: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> None:
        entry = {
            "mission_id": mission_id,
            "agent_role": agent_role,
            "latency_ms": max(0.0, float(latency_ms)),
            "prompt_tokens": max(0, int(prompt_tokens)),
            "completion_tokens": max(0, int(completion_tokens)),
            "total_tokens": max(0, int(prompt_tokens) + int(completion_tokens)),
            "cost": max(0.0, float(cost)),
            "error_type": error_type,
            "success": error_type is None,
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            # Check if an existing run with this task_id was already recorded
            if task_id:
                for existing in reversed(self._runs):
                    if existing.get("task_id") == task_id:
                        if latency_ms > 0:
                            existing["latency_ms"] = float(latency_ms)
                        if prompt_tokens > 0:
                            existing["prompt_tokens"] = int(prompt_tokens)
                        if completion_tokens > 0:
                            existing["completion_tokens"] = int(completion_tokens)
                        existing["total_tokens"] = existing["prompt_tokens"] + existing["completion_tokens"]
                        if cost > 0:
                            existing["cost"] = float(cost)
                        if error_type and not existing.get("error_type"):
                            existing["error_type"] = error_type
                            existing["success"] = False
                        if mission_id and not existing.get("mission_id"):
                            existing["mission_id"] = mission_id
                        if agent_role and existing.get("agent_role") in ("unknown", "sandbox_runner", ""):
                            existing["agent_role"] = agent_role
                        return

            self._runs.append(entry)
            if len(self._runs) > self.max_history:
                self._runs = self._runs[-self.max_history:]

    def get_metrics(
        self,
        agent_role: Optional[str] = None,
        mission_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            runs = [
                r for r in self._runs
                if (agent_role is None or r["agent_role"] == agent_role)
                and (mission_id is None or r["mission_id"] == mission_id)
            ]

        total_runs = len(runs)
        if total_runs == 0:
            return {
                "total_runs": 0,
                "successful_runs": 0,
                "failed_runs": 0,
                "success_rate": 1.0,
                "error_rate": 0.0,
                "avg_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "health_score": 100.0,
                "error_breakdown": {},
                "roles": {},
            }

        successful = [r for r in runs if r["success"]]
        failed = [r for r in runs if not r["success"]]
        successful_runs = len(successful)
        failed_runs = len(failed)

        latencies = sorted(r["latency_ms"] for r in runs if r["latency_ms"] > 0)
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        p95_idx = int(len(latencies) * 0.95)
        p95_latency = latencies[p95_idx] if latencies else 0.0

        total_prompt_tokens = sum(r["prompt_tokens"] for r in runs)
        total_completion_tokens = sum(r["completion_tokens"] for r in runs)
        total_cost = sum(r["cost"] for r in runs)

        error_breakdown: Dict[str, int] = {}
        for r in failed:
            etype = r.get("error_type") or "unknown_error"
            error_breakdown[etype] = error_breakdown.get(etype, 0) + 1

        # Per-role metrics breakdown
        roles_summary: Dict[str, Dict[str, Any]] = {}
        role_names = set(r["agent_role"] for r in runs)
        for rname in role_names:
            role_runs = [r for r in runs if r["agent_role"] == rname]
            role_failed = [r for r in role_runs if not r["success"]]
            role_lat = [r["latency_ms"] for r in role_runs if r["latency_ms"] > 0]
            roles_summary[rname] = {
                "total_runs": len(role_runs),
                "failed_runs": len(role_failed),
                "error_rate": round(len(role_failed) / len(role_runs), 4) if role_runs else 0.0,
                "avg_latency_ms": round(sum(role_lat) / len(role_lat), 2) if role_lat else 0.0,
                "total_cost": round(sum(r["cost"] for r in role_runs), 5),
            }

        error_rate = failed_runs / total_runs
        health_score = self._compute_health_score(error_rate, p95_latency)

        return {
            "total_runs": total_runs,
            "successful_runs": successful_runs,
            "failed_runs": failed_runs,
            "success_rate": round(successful_runs / total_runs, 4),
            "error_rate": round(error_rate, 4),
            "avg_latency_ms": round(avg_latency, 2),
            "p95_latency_ms": round(p95_latency, 2),
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "total_cost": round(total_cost, 5),
            "health_score": round(health_score, 1),
            "error_breakdown": error_breakdown,
            "roles": roles_summary,
        }

    def _compute_health_score(self, error_rate: float, p95_latency_ms: float) -> float:
        """
        Calculate health score (0.0 to 100.0).
        Penalized primarily for high error rates and severe latency degradation.
        """
        score = 100.0
        # Error penalty (up to 70 points deduction for 100% errors)
        score -= error_rate * 70.0
        # Latency penalty: if p95 exceeds 15 seconds, deduct up to 30 points
        if p95_latency_ms > 15000:
            excess_sec = (p95_latency_ms - 15000) / 1000.0
            latency_penalty = min(30.0, excess_sec * 2.0)
            score -= latency_penalty
        return max(0.0, min(100.0, score))

    def get_health_score(self, agent_role: Optional[str] = None, mission_id: Optional[str] = None) -> float:
        metrics = self.get_metrics(agent_role=agent_role, mission_id=mission_id)
        return metrics["health_score"]

    def reset(self) -> None:
        """Reset all recorded telemetry (used for test teardown)."""
        with self._lock:
            self._runs.clear()


telemetry = TelemetryCollector()
