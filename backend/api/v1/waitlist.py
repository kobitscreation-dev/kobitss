"""
Waitlist & Early Access API
Allows prospective users to apply for early access from landing.html and founders to view/approve leads.
"""

from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from backend.core.database import get_db
from backend.models.waitlist import WaitlistLead
from backend.api.deps import get_current_user
from backend.models.organization import User

router = APIRouter(prefix="/waitlist", tags=["Waitlist"])


class WaitlistApplyRequest(BaseModel):
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=120)
    company: Optional[str] = Field(None, max_length=120)
    use_case: Optional[str] = Field(None, max_length=1000)
    github_handle: Optional[str] = Field(None, max_length=100)


class WaitlistApplyResponse(BaseModel):
    status: str
    message: str
    email: str


class WaitlistLeadResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str]
    company: Optional[str]
    use_case: Optional[str]
    github_handle: Optional[str]
    status: str
    created_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


@router.post("/apply", response_model=WaitlistApplyResponse, status_code=status.HTTP_201_CREATED)
async def apply_for_waitlist(
    payload: WaitlistApplyRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Public endpoint: prospective users submit their email and details for Early Access.
    """
    norm_email = payload.email.strip().lower()

    # Check for existing application
    stmt = select(WaitlistLead).where(WaitlistLead.email == norm_email)
    res = await db.execute(stmt)
    existing = res.scalars().first()

    if existing:
        # Idempotent response: don't reveal internal state, confirm receipt
        return WaitlistApplyResponse(
            status="received",
            message="Your early access application is on file. We will notify you as batches open.",
            email=norm_email
        )

    lead = WaitlistLead(
        email=norm_email,
        full_name=payload.full_name.strip() if payload.full_name else None,
        company=payload.company.strip() if payload.company else None,
        use_case=payload.use_case.strip() if payload.use_case else None,
        github_handle=payload.github_handle.strip() if payload.github_handle else None,
        status="PENDING"
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    return WaitlistApplyResponse(
        status="success",
        message="Application received. You have been placed on the Kobits early access waitlist.",
        email=norm_email
    )


@router.get("/leads", response_model=List[WaitlistLeadResponse])
async def list_waitlist_leads(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Authenticated endpoint: view waitlist applicants (Founder / Admin only).
    """
    stmt = (
        select(WaitlistLead)
        .order_by(desc(WaitlistLead.created_at))
        .offset(offset)
        .limit(limit)
    )
    res = await db.execute(stmt)
    leads = res.scalars().all()
    return leads


@router.post("/leads/{lead_id}/approve")
async def approve_waitlist_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Authenticated endpoint: approve a waitlist applicant.
    """
    stmt = select(WaitlistLead).where(WaitlistLead.id == lead_id)
    res = await db.execute(stmt)
    lead = res.scalars().first()

    if not lead:
        raise HTTPException(status_code=404, detail="Waitlist lead not found")

    lead.status = "APPROVED"
    await db.commit()
    return {"status": "approved", "lead_id": lead_id, "email": lead.email}
