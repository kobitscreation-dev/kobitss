from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.models.project import Activity, ActivityType


async def log_activity(
    db: AsyncSession,
    organization_id: str,
    activity_type: ActivityType,
    title: str,
    description: str = None,
    project_id: str = None,
    user_id: str = None,
) -> Activity:
    """Create an activity log entry. Called by all services on significant actions."""
    activity = Activity(
        organization_id=organization_id,
        project_id=project_id,
        user_id=user_id,
        type=activity_type,
        title=title,
        description=description,
    )
    db.add(activity)
    await db.flush()
    return activity


async def get_activities(
    db: AsyncSession,
    organization_id: str,
    project_id: str = None,
    limit: int = 50,
):
    """Retrieve activities for an organization, optionally filtered by project."""
    query = select(Activity).where(Activity.organization_id == organization_id)
    if project_id:
        query = query.where(Activity.project_id == project_id)
    query = query.order_by(Activity.created_at.desc()).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()
