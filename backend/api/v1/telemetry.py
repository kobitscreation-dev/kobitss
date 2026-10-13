from fastapi import APIRouter, Query
from typing import Optional, Dict, Any
from backend.services.observability import telemetry

router = APIRouter(tags=["telemetry"])


@router.get("/metrics")
async def get_telemetry_metrics(
    agent_role: Optional[str] = Query(None, description="Filter metrics by agent role"),
    mission_id: Optional[str] = Query(None, description="Filter metrics by mission ID"),
) -> Dict[str, Any]:
    """Retrieve aggregated agent telemetry metrics."""
    return telemetry.get_metrics(agent_role=agent_role, mission_id=mission_id)


@router.get("/health")
async def get_telemetry_health(
    agent_role: Optional[str] = Query(None, description="Filter health score by agent role"),
    mission_id: Optional[str] = Query(None, description="Filter health score by mission ID"),
) -> Dict[str, Any]:
    """Retrieve current health score (0-100) and high-level health status."""
    score = telemetry.get_health_score(agent_role=agent_role, mission_id=mission_id)
    status = "healthy" if score >= 85 else "degraded" if score >= 60 else "critical"
    return {
        "health_score": score,
        "status": status,
        "agent_role": agent_role,
        "mission_id": mission_id,
    }


@router.post("/reset")
async def reset_telemetry_metrics() -> Dict[str, str]:
    """Reset telemetry history (for test teardown or administrative maintenance)."""
    telemetry.reset()
    return {"status": "reset", "message": "Telemetry metrics cleared"}
