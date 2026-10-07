import json
import uuid
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from backend.core.database import get_db
from backend.api.deps import get_current_active_user
from backend.models.organization import User, Organization
from backend.models.billing import CreditPackage, LedgerTransaction, LedgerTransactionType
from backend.services.billing_policy import CreditPolicy
from backend.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# --- Models ---
class PackageResponse(BaseModel):
    id: str
    name: str
    credits: int
    currency: str
    price_cents: int
    stripe_price_id: str

class CheckoutRequest(BaseModel):
    package_id: str

class CheckoutResponse(BaseModel):
    checkout_url: str

class BalanceResponse(BaseModel):
    balance: int


# --- Endpoints ---

@router.get("/packages", response_model=List[PackageResponse])
async def get_packages(db: AsyncSession = Depends(get_db)):
    """Returns available credit packages. Actual data is configured in the database."""
    stmt = select(CreditPackage).where(CreditPackage.is_active == True)
    packages = (await db.execute(stmt)).scalars().all()
    
    return [
        PackageResponse(
            id=p.id,
            name=p.name,
            credits=p.credits,
            currency=p.currency,
            price_cents=p.price_cents,
            stripe_price_id=p.stripe_price_id
        ) for p in packages
    ]

@router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Returns the user's organizational credit balance from the ledger."""
    # Note: get_current_active_user ensures user is valid, but we need their org.
    # In Kobits, organization_id is sometimes on user or in memberships.
    # Let's get it safely.
    from backend.models.organization import OrganizationMember
    stmt = select(OrganizationMember).where(OrganizationMember.user_id == current_user.id)
    membership = (await db.execute(stmt)).scalars().first()
    if not membership:
        raise HTTPException(status_code=403, detail="User has no organization")
        
    org_id = membership.organization_id
    balance = await CreditPolicy.get_balance(db, org_id)
    return BalanceResponse(balance=balance)

@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout_session(
    req: CheckoutRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Creates a Stripe Checkout Session using the server-side Stripe secret."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe configuration is missing")
        
    # Get the package
    package = await db.get(CreditPackage, req.package_id)
    if not package or not package.is_active:
        raise HTTPException(status_code=404, detail="Credit package not found or inactive")

    # Get org
    from backend.models.organization import OrganizationMember
    stmt = select(OrganizationMember).where(OrganizationMember.user_id == current_user.id)
    membership = (await db.execute(stmt)).scalars().first()
    if not membership:
        raise HTTPException(status_code=403, detail="User has no organization")
    org_id = membership.organization_id

    # Use HTTP client to call Stripe API directly (avoiding full stripe-python dependency if not installed)
    async with httpx.AsyncClient() as client:
        auth = (settings.STRIPE_SECRET_KEY, "")
        data = {
            "payment_method_types[0]": "card",
            "line_items[0][price]": package.stripe_price_id,
            "line_items[0][quantity]": "1",
            "mode": "payment",
            "success_url": f"{settings.FRONTEND_URL}/portal.html#billing?success=true",
            "cancel_url": f"{settings.FRONTEND_URL}/portal.html#billing?canceled=true",
            "client_reference_id": org_id,
            "metadata[organization_id]": org_id,
            "metadata[credits]": str(package.credits)
        }
        resp = await client.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=auth,
            data=data
        )
        if resp.status_code != 200:
            logger.error(f"Stripe error: {resp.text}")
            raise HTTPException(status_code=500, detail="Failed to create checkout session")
            
        session = resp.json()
        return CheckoutResponse(checkout_url=session["url"])

@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handles Stripe webhooks idempotently."""
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Stripe webhook secret not configured")
        
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        import stripe
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        logger.error(f"Webhook signature error: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid signature")
        
    if event.get("type") == "checkout.session.completed":
        session = event["data"]["object"]
        event_id = event.get("id")
        
        # Idempotency check: see if we already processed this event
        stmt = select(LedgerTransaction).where(LedgerTransaction.idempotency_key == event_id)
        existing = (await db.execute(stmt)).scalars().first()
        if existing:
            return Response(content="Already processed", status_code=200)
            
        metadata = session.get("metadata", {})
        org_id = metadata.get("organization_id")
        credits_str = metadata.get("credits")
        
        if org_id and credits_str:
            credits_amount = int(credits_str)
            tx = LedgerTransaction(
                id=str(uuid.uuid4()),
                organization_id=org_id,
                type=LedgerTransactionType.TOPUP,
                amount=credits_amount,
                reference_id=session.get("id"),
                idempotency_key=event_id,
                metadata_json=json.dumps({"stripe_event": event_id})
            )
            db.add(tx)
            
            # Also update legacy credit_balance for existing components
            org = await db.get(Organization, org_id)
            if org:
                org.credit_balance += credits_amount
                
            await db.commit()
            
    return Response(content="OK", status_code=200)
