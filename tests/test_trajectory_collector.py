import os
import json
import pytest
from backend.services.trajectory_collector import TrajectoryCollector


def test_format_sharegpt_and_chatml_record():
    mission_id = "test-mission-123"
    title = "Implement rate limiter"
    objective = "Add sliding window rate limiter in backend/limiter.py"
    runs_data = [
        {
            "id": "run-1",
            "input_text": "Please write the rate limiter",
            "output_text": "Created the sliding window rate limiter class.",
            "tool_calls": [
                {
                    "type": "tool_call",
                    "name": "repository.write",
                    "arguments": {"path": "backend/limiter.py", "content": "class RateLimiter: pass"}
                },
                {
                    "type": "tool_result",
                    "name": "repository.write",
                    "result": {"success": True, "path": "backend/limiter.py"}
                }
            ]
        }
    ]
    deliverable = {
        "changeset": {"files_changed": 1, "lines_added": 20, "lines_removed": 0}
    }

    rec = TrajectoryCollector.format_sharegpt_record(
        mission_id=mission_id,
        title=title,
        objective=objective,
        runs_data=runs_data,
        deliverable=deliverable,
        model_source="test-model"
    )

    assert rec is not None
    assert rec["id"] == "kobits-mission-test-mission-123"
    assert rec["metadata"]["quality_grade"] == "GOLDEN_VERIFIED"
    assert len(rec["conversations"]) >= 4

    # Convert to ChatML
    chatml = TrajectoryCollector.format_chatml_record(rec)
    assert chatml["id"] == rec["id"]
    roles = [m["role"] for m in chatml["messages"]]
    assert "system" in roles
    assert "user" in roles
    assert "assistant" in roles
    assert "tool" in roles


def test_harvest_and_stats():
    stats = TrajectoryCollector.get_stats()
    assert "total_trajectories" in stats
    assert "total_tokens_approx" in stats
    assert "readiness_percentage_for_200k_sft" in stats
    assert stats["total_trajectories"] >= 22
    assert stats["total_tokens_approx"] > 1_000_000
