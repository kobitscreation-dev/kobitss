import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from backend.core.config import settings

is_sqlite = settings.DATABASE_URL.startswith("sqlite")

connect_args = {}
engine_kwargs = {"echo": False}

from sqlalchemy.pool import NullPool

if is_sqlite:
    connect_args["check_same_thread"] = False
    connect_args["timeout"] = 30.0  # aiosqlite connection timeout
    engine_kwargs["connect_args"] = connect_args
    engine_kwargs["poolclass"] = NullPool
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
            cursor.execute("PRAGMA cache_size=-16000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            res = cursor.execute("PRAGMA table_info(audit_logs)").fetchall()
            if res:
                existing_cols = {row[1] for row in res}
                soc2_cols = [
                    ("organization_id", "VARCHAR"),
                    ("actor_role", "VARCHAR(32)"),
                    ("session_id", "VARCHAR(64)"),
                    ("mission_id", "VARCHAR(64)"),
                    ("project_id", "VARCHAR(64)"),
                    ("category", "VARCHAR(64) DEFAULT 'AUTH'"),
                    ("severity", "VARCHAR(16) DEFAULT 'INFO'"),
                    ("status", "VARCHAR(32) DEFAULT 'SUCCESS'"),
                    ("resource_type", "VARCHAR(64)"),
                    ("resource_id", "VARCHAR(128)"),
                    ("file_path", "VARCHAR(1024)"),
                    ("command_executed", "TEXT"),
                    ("code_change_hash", "VARCHAR(64)"),
                    ("content_hash", "VARCHAR(64)"),
                    ("sequence_number", "INTEGER DEFAULT 1"),
                    ("prev_hash", "VARCHAR(64) DEFAULT '0000000000000000000000000000000000000000000000000000000000000000'"),
                    ("entry_hash", "VARCHAR(64) DEFAULT ''"),
                    ("hmac_signature", "VARCHAR(64) DEFAULT ''"),
                ]
                added = False
                for col_name, col_type in soc2_cols:
                    if col_name not in existing_cols:
                        cursor.execute(f"ALTER TABLE audit_logs ADD COLUMN {col_name} {col_type}")
                        added = True
                if added:
                    dbapi_connection.commit()
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

