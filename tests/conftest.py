import os
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# 1. Force the application to use a test database and mock LLM provider
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_kobits.db"
os.environ["KOBITS_DEV_MODE"] = "true"
os.environ["REAL_AI_TEST"] = "0"
os.environ["LLM_PROVIDER"] = "mock"
os.environ.pop("USE_ONLY_CLAUDE", None)
os.environ.pop("AWS_BEDROCK_API_KEY", None)
os.environ.pop("E2B_API_KEY", None)

from backend.core.config import settings
settings.LLM_PROVIDER = "mock"
settings.AWS_BEDROCK_API_KEY = None

# Now we can safely import backend modules
from backend.core.database import engine, AsyncSessionLocal
import backend.models
from backend.models.base import Base
try:
    from backend.services.agent_executor import agent_executor
    from backend.services.llm import get_llm_provider
    agent_executor.provider = get_llm_provider()
    agent_executor._default_provider = agent_executor.provider
except Exception:
    pass


@pytest_asyncio.fixture(autouse=True, scope="function")
async def setup_db():
    """
    Global fixture for all tests to ensure they use a clean, isolated database.
    This prevents wiping the production/development database.
    """
    from sqlalchemy import text

    def _wipe_and_create(sync_conn):
        if "sqlite" in str(engine.url):
            sync_conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
            try:
                sync_conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            views = [r[0] for r in sync_conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='view'").fetchall()]
            for v in views:
                try:
                    sync_conn.exec_driver_sql(f'DROP VIEW IF EXISTS "{v}"')
                except Exception:
                    pass
            tables = [r[0] for r in sync_conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
            for t in tables:
                try:
                    sync_conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{t}"')
                except Exception:
                    pass
            try:
                sync_conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
        else:
            try:
                Base.metadata.drop_all(sync_conn, checkfirst=True)
            except Exception:
                pass
        Base.metadata.create_all(sync_conn, checkfirst=True)

    def _wipe_teardown(sync_conn):
        if "sqlite" in str(engine.url):
            sync_conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
            rows = sync_conn.exec_driver_sql(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for name, tbl_type in rows:
                try:
                    if tbl_type == "view":
                        sync_conn.exec_driver_sql(f'DROP VIEW IF EXISTS "{name}"')
                    else:
                        sync_conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{name}"')
                except Exception:
                    pass
        else:
            try:
                Base.metadata.drop_all(sync_conn, checkfirst=True)
            except Exception:
                pass

    await engine.dispose()
    try:
        for f in ["./test_kobits.db", "./test_kobits.db-shm", "./test_kobits.db-wal"]:
            if os.path.exists(f):
                os.remove(f)
    except Exception:
        pass

    async with engine.begin() as conn:
        await conn.run_sync(_wipe_and_create)
    
    yield
    
    # Drop all tables after the test completes to maintain isolation
    await engine.dispose()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_wipe_teardown)
    except Exception:
        pass

@pytest.fixture(autouse=True)
def isolate_test_state():
    """
    Ensures environment variables, settings, and sandbox session caches
    do not leak across test boundaries.
    """
    from backend.core.config import settings
    from backend.services.sandbox_manager import SandboxManager

    os.environ.pop("E2B_API_KEY", None)
    orig_env = dict(os.environ)
    orig_env.pop("E2B_API_KEY", None)
    orig_llm = settings.LLM_PROVIDER
    orig_anthropic_key = settings.ANTHROPIC_API_KEY
    orig_deepseek_key = settings.DEEPSEEK_API_KEY

    from backend.main import app
    orig_overrides = dict(app.dependency_overrides)

    yield
    os.environ.clear()
    for k, v in orig_env.items():
        if len(v) < 32760:
            os.environ[k] = v
        else:
            os.environ[k] = v[:32750]
    settings.LLM_PROVIDER = orig_llm
    settings.ANTHROPIC_API_KEY = orig_anthropic_key
    settings.DEEPSEEK_API_KEY = orig_deepseek_key
    SandboxManager._sessions.clear()
    app.dependency_overrides.clear()
    app.dependency_overrides.update(orig_overrides)

@pytest_asyncio.fixture
async def db_session():
    """Provides an active database session for tests to use."""
    async with AsyncSessionLocal() as session:
        yield session
