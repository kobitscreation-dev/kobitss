import enum
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Text, ForeignKey, Enum, Integer, Boolean, DateTime
from backend.models.base import Base

class GitHubConnectionStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"
    DISCONNECTED = "DISCONNECTED"

class GitHubConnection(Base):
    __tablename__ = "github_connections"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    github_account_id: Mapped[Optional[str]] = mapped_column(String)
    github_username: Mapped[Optional[str]] = mapped_column(String)
    installation_id: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[GitHubConnectionStatus] = mapped_column(Enum(GitHubConnectionStatus), default=GitHubConnectionStatus.PENDING)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text)
    
    repositories: Mapped[List["Repository"]] = relationship(back_populates="connection", cascade="all, delete-orphan")

class RepositoryStatus(str, enum.Enum):
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    SYNCING = "SYNCING"
    INDEXING = "INDEXING"
    READY = "READY"
    OUT_OF_DATE = "OUT_OF_DATE"
    ERROR = "ERROR"
    DISCONNECTED = "DISCONNECTED"

class Repository(Base):
    __tablename__ = "repositories"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    github_connection_id: Mapped[Optional[str]] = mapped_column(ForeignKey("github_connections.id", ondelete="CASCADE"), nullable=True, index=True)
    github_repo_id: Mapped[str] = mapped_column(String, default="0")
    
    owner: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    full_name: Mapped[str] = mapped_column(String)
    default_branch: Mapped[str] = mapped_column(String, default="main")
    selected_branch: Mapped[str] = mapped_column(String, default="main")
    clone_url: Mapped[str] = mapped_column(String)
    web_url: Mapped[Optional[str]] = mapped_column(String, nullable=True, default="")
    private: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[RepositoryStatus] = mapped_column(Enum(RepositoryStatus), default=RepositoryStatus.CONNECTING)
    current_commit_sha: Mapped[Optional[str]] = mapped_column(String)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    connection: Mapped[Optional["GitHubConnection"]] = relationship(back_populates="repositories")
    branches: Mapped[List["RepositoryBranch"]] = relationship(back_populates="repository", cascade="all, delete-orphan")
    snapshots: Mapped[List["RepositorySnapshot"]] = relationship(back_populates="repository", cascade="all, delete-orphan")
    pull_requests: Mapped[List["PullRequest"]] = relationship(back_populates="repository", cascade="all, delete-orphan")

class RepositoryBranch(Base):
    __tablename__ = "repository_branches"
    
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    latest_commit_sha: Mapped[Optional[str]] = mapped_column(String)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    repository: Mapped["Repository"] = relationship(back_populates="branches")

class RepositorySnapshot(Base):
    __tablename__ = "repository_snapshots"
    
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    branch: Mapped[str] = mapped_column(String)
    commit_sha: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    files_count: Mapped[int] = mapped_column(Integer, default=0)
    symbols_count: Mapped[int] = mapped_column(Integer, default=0)
    chunks_count: Mapped[int] = mapped_column(Integer, default=0)
    
    repository: Mapped["Repository"] = relationship(back_populates="snapshots")

class PullRequestStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    MERGEABLE = "MERGEABLE"
    CONFLICT = "CONFLICT"
    CHECKS_PENDING = "CHECKS_PENDING"
    CHECKS_FAILED = "CHECKS_FAILED"
    MERGED = "MERGED"
    CLOSED = "CLOSED"

class PullRequest(Base):
    __tablename__ = "pull_requests"
    
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    github_pr_id: Mapped[Optional[str]] = mapped_column(String)
    number: Mapped[Optional[int]] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(String)
    source_branch: Mapped[str] = mapped_column(String)
    target_branch: Mapped[str] = mapped_column(String)
    base_sha: Mapped[Optional[str]] = mapped_column(String)
    head_sha: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[PullRequestStatus] = mapped_column(Enum(PullRequestStatus), default=PullRequestStatus.OPEN)
    review_status: Mapped[Optional[str]] = mapped_column(String)
    checks_status: Mapped[Optional[str]] = mapped_column(String)
    merged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    # Gate 16 fields
    kobits_approved_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    kobits_approved_sha: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    repository: Mapped["Repository"] = relationship(back_populates="pull_requests")

class ChangesetStatus(str, enum.Enum):
    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMMITTED = "COMMITTED"
    PUSHED = "PUSHED"
    SUPERSEDED = "SUPERSEDED"

class Changeset(Base):
    __tablename__ = "changesets"

    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    repository_id: Mapped[Optional[str]] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True, index=True)
    branch: Mapped[str] = mapped_column(String)
    base_commit_sha: Mapped[Optional[str]] = mapped_column(String)
    head_commit_sha: Mapped[Optional[str]] = mapped_column(String)
    files_changed: Mapped[Optional[int]] = mapped_column(Integer)
    lines_added: Mapped[Optional[int]] = mapped_column(Integer)
    lines_removed: Mapped[Optional[int]] = mapped_column(Integer)
    diff_summary: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[ChangesetStatus] = mapped_column(Enum(ChangesetStatus), default=ChangesetStatus.PENDING)

