from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
try:
    from fastapi import HTTPException
except ImportError:
    class HTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: str = ""):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail
from backend.models.organization import Organization
from backend.models.billing import LedgerTransaction, LedgerTransactionType
from backend.core.config import settings
import uuid

class CreditPolicy:
    """
    Central policy for determining credit costs and managing escrow holds.
    Requires server-side configuration. Does not hardcode prices.
    """

    @classmethod
    def _get_mission_cost(cls) -> int:
        if settings.MISSION_CREDIT_COST is None:
            raise HTTPException(status_code=500, detail="Billing policy not configured. MISSION_CREDIT_COST is not set.")
        return settings.MISSION_CREDIT_COST

    @classmethod
    async def get_balance(cls, db: AsyncSession, org_id: str) -> int:
        """
        Calculates the true balance from the ledger.
        """
        stmt = select(func.sum(LedgerTransaction.amount)).where(LedgerTransaction.organization_id == org_id)
        result = (await db.execute(stmt)).scalar()
        return result or 0

    @classmethod
    async def reserve_credits(cls, db: AsyncSession, org_id: str, mission_id: str) -> bool:
        """
        Attempts to reserve credits for a new mission.
        Raises HTTPException if insufficient balance.
        """
        cost = cls._get_mission_cost()
        
        # 1. Lock the organization to prevent race conditions during balance check
        stmt = select(Organization).where(Organization.id == org_id).with_for_update()
        org = (await db.execute(stmt)).scalars().first()
        
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        current_balance = await cls.get_balance(db, org_id)
            
        if current_balance < cost:
            raise HTTPException(
                status_code=402, 
                detail=f"Insufficient credits. Required: {cost}, Available: {current_balance}"
            )
            
        # 2. Update denormalized balance for legacy support
        org.credit_balance = current_balance - cost
        
        # 3. Create Ledger entry
        tx = LedgerTransaction(
            id=str(uuid.uuid4()),
            organization_id=org_id,
            type=LedgerTransactionType.MISSION_RESERVATION,
            amount=-cost,
            reference_id=mission_id,
            metadata_json='{"status": "reserved"}'
        )
        db.add(tx)
        
        # Note: caller is responsible for db.commit() to ensure it's atomic with mission creation
        return True

    @classmethod
    async def release_reservation(cls, db: AsyncSession, org_id: str, mission_id: str):
        """Releases the held credits (refund) if mission fails or cancels."""
        # 1. Lock organization to prevent double-refund race condition
        stmt = select(Organization).where(Organization.id == org_id).with_for_update()
        org = (await db.execute(stmt)).scalars().first()
        if not org:
            return

        # Find original reservation
        stmt = select(LedgerTransaction).where(
            LedgerTransaction.reference_id == mission_id,
            LedgerTransaction.type == LedgerTransactionType.MISSION_RESERVATION
        )
        original = (await db.execute(stmt)).scalars().first()
        if not original:
            return # No reservation found
            
        # Check if already released or settled
        stmt = select(LedgerTransaction).where(
            LedgerTransaction.reference_id == mission_id,
            LedgerTransaction.type.in_([LedgerTransactionType.MISSION_RELEASE, LedgerTransactionType.MISSION_SETTLEMENT])
        )
        existing = (await db.execute(stmt)).scalars().first()
        if existing:
            return # Already handled
            
        cost = abs(original.amount)
        org.credit_balance += cost
            
        tx = LedgerTransaction(
            id=str(uuid.uuid4()),
            organization_id=org_id,
            type=LedgerTransactionType.MISSION_RELEASE,
            amount=cost,
            reference_id=mission_id,
            metadata_json='{"status": "released"}'
        )
        db.add(tx)
        await db.commit()

    @classmethod
    async def settle_reservation(cls, db: AsyncSession, org_id: str, mission_id: str):
        """Finalizes the held credits (consumes them) when mission succeeds."""
        # 1. Lock organization to prevent race condition
        stmt = select(Organization).where(Organization.id == org_id).with_for_update()
        org = (await db.execute(stmt)).scalars().first()
        if not org:
            return

        # Find original reservation
        stmt = select(LedgerTransaction).where(
            LedgerTransaction.reference_id == mission_id,
            LedgerTransaction.type == LedgerTransactionType.MISSION_RESERVATION
        )
        original = (await db.execute(stmt)).scalars().first()
        if not original:
            return # No reservation found

        # Check if already handled
        stmt = select(LedgerTransaction).where(
            LedgerTransaction.reference_id == mission_id,
            LedgerTransaction.type.in_([LedgerTransactionType.MISSION_RELEASE, LedgerTransactionType.MISSION_SETTLEMENT])
        )
        existing = (await db.execute(stmt)).scalars().first()
        if existing:
            return

        tx = LedgerTransaction(
            id=str(uuid.uuid4()),
            organization_id=org_id,
            type=LedgerTransactionType.MISSION_SETTLEMENT,
            amount=0, # The actual deduction was the reservation
            reference_id=mission_id,
            metadata_json='{"status": "Settled"}'
        )
        db.add(tx)
        await db.commit()
