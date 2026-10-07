import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from backend.core.config import settings

is_sqlite = settings.DATABASE_URL.startswith("sqlite")

connect_args = {}
engine_kwargs = {"echo": False}

if is_sqlite:
    connect_args["check_same_thread"] = False
    connect_args["timeout"] = 30.0  # aiosqlite connection timeout
    engine_kwargs["connect_args"] = connect_args
else:
    # Production PostgreSQL connection pool configuration
    engine_kwargs["pool_size"] = getattr(settings, "DB_POOL_SIZE", 20)
    engine_kwargs["max_overflow"] = getattr(settings, "DB_MAX_OVERFLOW", 10)
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 3600
    engine_kwargs["pool_timeout"] = 30

# Create async engine
engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

if is_sqlite:
    # High-concurrency SQLite WAL pragma tuning:
    # 1. WAL (Write-Ahead Logging) allows concurrent readers and writers without blocking.
    # 2. synchronous=NORMAL gives safe durability with maximum write throughput.
    # 3. busy_timeout=30000 prevents 'database is locked' errors under parallel multi-agent load.
    # 4. foreign_keys=ON enforces relational integrity.
    # 5. cache_size=-64000 reserves 64MB of in-memory page cache.
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA cache_size=-64000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.close()
        except Exception:
            pass

# Create async session maker
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

async def get_db():
    """Dependency for FastAPI to get a database session."""
    async with AsyncSessionLocal() as session:
        yield session

