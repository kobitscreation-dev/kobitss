from typing import List, Optional
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, ForeignKey, Enum, DateTime, Integer
from datetime import datetime, timezone
import enum
import uuid


from backend.models.base import Base


class OrgRole(str, enum.Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"


class User(Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # OAuth identity fields (nullable — a user may have zero, one, or multiple linked providers)
    google_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)
    github_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    memberships: Mapped[List["OrganizationMember"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Organization(Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String)
    credit_balance: Mapped[int] = mapped_column(Integer, default=0)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    members: Mapped[List["OrganizationMember"]] = relationship(back_populates="organization", cascade="all, delete-orphan")


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    role: Mapped[OrgRole] = mapped_column(Enum(OrgRole), default=OrgRole.MEMBER)

    user: Mapped["User"] = relationship(back_populates="memberships")
    organization: Mapped["Organization"] = relationship(back_populates="members")
