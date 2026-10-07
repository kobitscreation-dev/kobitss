import os
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# 1. Force the application to use a test database before any internal modules import config
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_kobits.db"

# Now we can safely import backend modules
from backend.core.database import engine, AsyncSessionLocal
from backend.models.base import Base
from backend.models.organization import User, Organization, OrganizationMember
from backend.models.project import Project, Task, Activity, ProjectMemory, Notification
from backend.models.agent import Agent, AgentRun
from backend.models.github import GitHubConnection, Repository, RepositoryBranch, RepositorySnapshot, PullRequest
from backend.models.mission import Mission, Milestone, MissionPlan
from backend.models.finding import Finding
from backend.models.debate_log import DebateLog
from backend.models.communication import AgentMessage, AgentArtifact, AgentDecision, AgentLesson
from backend.models.intelligence import TaskContract, ProcessMemory, ProcessOutcomeRecord, IntelligenceTrace, MemoryUsefulness, HumanFeedback, ContextEfficiencyRecord
from backend.models.memory import AgentMemory, MissionMemory

@pytest_asyncio.fixture(autouse=True, scope="function")
async def setup_db():
    """
    Global fixture for all tests to ensure they use a clean, isolated database.
    This prevents wiping the production/development database.
    """
    # Create all tables in the test database
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield
    
    # Drop all tables after the test completes to maintain isolation
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest_asyncio.fixture
async def db_session():
    """Provides an active database session for tests to use."""
    async with AsyncSessionLocal() as session:
        yield session
