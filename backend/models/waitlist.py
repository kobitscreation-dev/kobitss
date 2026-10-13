from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String

from backend.models.base import Base


class WaitlistLead(Base):
    """
    Tracks visitors applying for early access to the Kobits autonomous engineering platform.
    """
    __tablename__ = "waitlist_leads"

    email: Mapped[str] = mapped_column(String, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    company: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    use_case: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    github_handle: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="PENDING")  # PENDING | APPROVED | INVITED
