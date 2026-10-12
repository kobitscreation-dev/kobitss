from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any
import os

from backend.core.database import get_db
from backend.api.deps import get_current_active_user
from backend.models.organization import User
from backend.services.trajectory_collector import (
    TrajectoryCollector,
    SHAREGPT_FILE,
    CHATML_FILE,
)

router = APIRouter()


@router.get("/stats")
async def get_training_stats(
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Returns telemetry on the collected fine-tuning dataset:
    Total trajectories, approximate tokens harvested, average turn depth,
    and readiness progress towards a custom fine-tuned model.
    """
    return TrajectoryCollector.get_stats()


@router.post("/harvest")
async def harvest_trajectories(
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Scans the database for all COMPLETED missions and agent runs,
    scrubs sensitive credentials, and appends them to the golden
    ShareGPT and ChatML training datasets.
    """
    result = TrajectoryCollector.harvest_from_db()
    return result


@router.get("/download")
async def download_dataset(
    format: str = Query("chatml", description="Format: 'chatml' (OpenAI/Axolotl) or 'sharegpt'"),
    current_user: User = Depends(get_current_active_user),
):
    """
    Downloads the harvested training dataset in JSONL format
    for fine-tuning with Unsloth, Axolotl, Llama-Factory, or OpenAI API.
    """
    target_file = CHATML_FILE if format.lower() == "chatml" else SHAREGPT_FILE

    if not target_file.exists():
        # Trigger harvesting if file does not exist yet
        TrajectoryCollector.harvest_from_db()

    if not target_file.exists():
        raise HTTPException(
            status_code=404,
            detail="No training trajectories collected yet. Complete missions first to build the training dataset.",
        )

    filename = f"kobits_golden_dataset_{format.lower()}.jsonl"
    return FileResponse(
        path=str(target_file.resolve()),
        filename=filename,
        media_type="application/x-jsonlines",
    )
