import os
import contextlib
import time
import asyncio
from collections import defaultdict
from typing import Dict, Tuple
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from pydantic import BaseModel
from backend.core.config import settings

from backend.core.database import engine
from backend.models.base import Base

# Import ALL models so SQLAlchemy registers them for table creation
from backend.models.organization import User, Organization, OrganizationMember
from backend.models.project import Project, Task, Activity, ProjectMemory, Notification
from backend.models.agent import Agent, AgentRun
from backend.models.github import GitHubConnection, Repository, RepositoryBranch, RepositorySnapshot, PullRequest
from backend.models.mission import Mission, Milestone, MissionPlan
from backend.models.finding import Finding
from backend.models.debate_log import DebateLog
from backend.models.communication import *
from backend.models.intelligence import *
from backend.models.memory import *
from backend.models.audit_log import AuditLog
from backend.models.password_reset import PasswordResetToken
from backend.models.waitlist import WaitlistLead
from backend.models.durable_job import DurableJob, JobStatus


from backend.api.v1 import auth, projects, agents, github, missions, memory, tools


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # ── P0 SECURITY: Startup assertions ────────────────────────────────────────
    from backend.core.config import settings
    import logging as _logging
    _startup_logger = _logging.getLogger("kobits.startup")

    # Guard 0: Initialize Sentry Observability SDK if configured
    if getattr(settings, "SENTRY_DSN", None):
        try:
            import sentry_sdk
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.starlette import StarletteIntegration

            sentry_sdk.init(
                dsn=settings.SENTRY_DSN,
                environment=settings.SENTRY_ENVIRONMENT,
                traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
                integrations=[
                    FastApiIntegration(),
                    StarletteIntegration(),
                ],
                send_default_pii=False,
            )
            _startup_logger.info(f"Sentry SDK initialized successfully (env={settings.SENTRY_ENVIRONMENT})")
        except Exception as _sentry_err:
            _startup_logger.error(f"Failed to initialize Sentry: {_sentry_err}")

    # Guard 1: DEV_MODE must NEVER be enabled in a PostgreSQL (production) environment
    if settings.KOBITS_DEV_MODE and "postgresql" in settings.DATABASE_URL.lower():
        if not settings.ALLOW_DEV_POSTGRES and os.environ.get("ALLOW_DEV_POSTGRES") != "1":
            raise RuntimeError(
                "SECURITY VIOLATION: KOBITS_DEV_MODE=True is NOT permitted when connecting to PostgreSQL. "
                "DEV_MODE bypasses ALL authentication. Disable it before running in production, or set ALLOW_DEV_POSTGRES=true in .env for development against cloud DB."
            )

    # Guard 2: Warn loudly if SECRET_KEY is still the insecure default
    if settings.SECRET_KEY in ("super-secret-key-change-me", ""):
        _startup_logger.critical(
            "SECURITY WARNING: SECRET_KEY is set to the insecure default value. "
            "All JWTs signed with this key can be forged. "
            "Set a cryptographically random 32+ byte key in the SECRET_KEY environment variable."
        )

    # Guard 3: Warn if SECRET_KEY is shorter than the HS256 minimum (32 bytes)
    if len(settings.SECRET_KEY.encode()) < 32:
        _startup_logger.warning(
            f"SECRET_KEY length ({len(settings.SECRET_KEY.encode())} bytes) is below the HS256 "
            "minimum of 32 bytes. Run: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    # Guard 4: Warn if SESSION_COOKIE_SECURE is disabled in a non-SQLite env (likely production)
    if not settings.SESSION_COOKIE_SECURE and "postgresql" in settings.DATABASE_URL.lower():
        _startup_logger.warning(
            "SESSION_COOKIE_SECURE=False in a PostgreSQL environment. "
            "Set SESSION_COOKIE_SECURE=True in all HTTPS deployments."
        )

    # Validate provider configuration on startup
    from backend.services.llm import get_llm_provider
    try:
        get_llm_provider()
    except Exception as e:
        if settings.REAL_AI_TEST:
            # Hard crash if REAL_AI_TEST is enabled and provider is misconfigured
            raise e
        print(f"Warning: Provider misconfigured: {e}")

    # Ensure database tables exist (idempotent, safe for SQLite on cloud containers)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # Ensure changesets.repository_id is nullable in SQLite (prevent NOT NULL constraint failures)
            if "sqlite" in settings.DATABASE_URL.lower():
                def _check_and_migrate_changesets(sync_conn):
                    res = sync_conn.exec_driver_sql("PRAGMA table_info(changesets)").fetchall()
                    repo_col = next((c for c in res if c[1] == "repository_id"), None)
                    if repo_col and repo_col[3] == 1:  # notnull == 1
                        _startup_logger.info("Migrating SQLite changesets table: making repository_id nullable...")
                        sync_conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                        sync_conn.exec_driver_sql("""
                            CREATE TABLE changesets_migrated (
                                mission_id VARCHAR NOT NULL, 
                                task_id VARCHAR NOT NULL, 
                                agent_id VARCHAR, 
                                repository_id VARCHAR, 
                                branch VARCHAR NOT NULL, 
                                base_commit_sha VARCHAR, 
                                head_commit_sha VARCHAR, 
                                files_changed INTEGER, 
                                lines_added INTEGER, 
                                lines_removed INTEGER, 
                                diff_summary TEXT, 
                                status VARCHAR(10) NOT NULL, 
                                id VARCHAR NOT NULL, 
                                created_at DATETIME NOT NULL, 
                                updated_at DATETIME NOT NULL, 
                                PRIMARY KEY (id), 
                                FOREIGN KEY(mission_id) REFERENCES missions (id) ON DELETE CASCADE, 
                                FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE, 
                                FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE SET NULL, 
                                FOREIGN KEY(repository_id) REFERENCES repositories (id) ON DELETE CASCADE
                            )
                        """)
                        sync_conn.exec_driver_sql("INSERT INTO changesets_migrated SELECT * FROM changesets")
                        sync_conn.exec_driver_sql("DROP TABLE changesets")
                        sync_conn.exec_driver_sql("ALTER TABLE changesets_migrated RENAME TO changesets")
                        sync_conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_changesets_repository_id ON changesets (repository_id)")
                        sync_conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_changesets_task_id ON changesets (task_id)")
                        sync_conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_changesets_mission_id ON changesets (mission_id)")
                        sync_conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                        _startup_logger.info("SQLite changesets table migration complete.")
                await conn.run_sync(_check_and_migrate_changesets)
    except Exception as _schema_err:
        _startup_logger.warning(f"Schema init notice (likely initialized by peer worker): {_schema_err}")

    # Auto-seed default user if database is fresh
    from backend.core.database import AsyncSessionLocal
    from backend.models.organization import User, Organization, OrganizationMember, OrgRole
    from backend.core.security import get_password_hash
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        try:
            admin_user = (await db.execute(select(User).where(User.email == "realuser@kobits.space"))).scalar_one_or_none()
            if not admin_user:
                new_u = User(
                    email="realuser@kobits.space",
                    hashed_password=get_password_hash("MyPass12345!"),
                    full_name="Kobits Founder",
                    is_active=True
                )
                db.add(new_u)
                new_org = Organization(name="Kobits Core Workspace", credit_balance=500)
                db.add(new_org)
                await db.flush()
                db.add(OrganizationMember(user_id=new_u.id, organization_id=new_org.id, role=OrgRole.OWNER))
                await db.commit()
                print("Auto-seeded default user realuser@kobits.space with 500 credits")
        except Exception as _seed_err:
            await db.rollback()
            _startup_logger.info(f"Default user already seeded by peer worker: {_seed_err}")

        try:
            # Auto-seed default SaaS credit packages if empty
            from backend.models.billing import CreditPackage
            existing_pkgs = (await db.execute(select(CreditPackage))).scalars().all()
            if not existing_pkgs:
                starter = CreditPackage(
                    id="pkg_starter",
                    name="Starter Plan",
                    credits=250,
                    currency="usd",
                    price_cents=2900,
                    stripe_price_id="price_starter",
                    is_active=True,
                )
                growth = CreditPackage(
                    id="pkg_growth",
                    name="Growth Plan",
                    credits=800,
                    currency="usd",
                    price_cents=7900,
                    stripe_price_id="price_growth",
                    is_active=True,
                )
                scale = CreditPackage(
                    id="pkg_scale",
                    name="Scale Plan",
                    credits=2500,
                    currency="usd",
                    price_cents=19900,
                    stripe_price_id="price_scale",
                    is_active=True,
                )
                db.add_all([starter, growth, scale])
                await db.commit()
                print("Auto-seeded default SaaS credit packages (Starter, Growth, Scale)")
        except Exception as _pkg_err:
            await db.rollback()
            _startup_logger.info(f"Credit packages already seeded by peer worker: {_pkg_err}")

        try:
            # Auto-seed all 22 specialist agents if empty
            from backend.models.agent import Agent, AgentType, AgentStatus
            from backend.services.agent_registry import AGENT_REGISTRY
            for agent_type, agent_def in AGENT_REGISTRY.items():
                existing = (await db.execute(select(Agent).where(Agent.type == agent_type))).scalar_one_or_none()
                if not existing:
                    a = Agent(
                        name=agent_def.name,
                        type=agent_type,
                        description=agent_def.description,
                        system_prompt=agent_def.system_prompt,
                        capabilities=json.dumps(agent_def.capabilities),
                        status=AgentStatus.IDLE
                    )
                    db.add(a)
            await db.commit()
        except Exception as _agent_err:
            await db.rollback()
            _startup_logger.info(f"Agents already seeded by peer worker: {_agent_err}")

    # Resume ACTIVE missions and clear dangling locks
    if os.environ.get("KOBITS_NO_RESUME") != "1" and os.environ.get("KOBITS_NO_AUTO_EXECUTE") != "1":
        from backend.models.mission import Mission, MissionStatus
        from backend.services.mission_runtime import MissionRuntime
        from sqlalchemy import update
        
        async with AsyncSessionLocal() as db:
            try:
                from datetime import datetime, timezone
                # Clear any dangling locks that have expired
                stmt = (
                    update(Mission)
                    .where(Mission.execution_lock_id != None)
                    .where(
                        (Mission.execution_lock_expires_at == None) | 
                        (Mission.execution_lock_expires_at < datetime.now(timezone.utc))
                    )
                    .values(execution_lock_id=None, execution_lock_expires_at=None)
                )
                await db.execute(stmt)
                await db.commit()
                
                # Resume active missions that aren't currently locked
                stmt = (
                    select(Mission)
                    .where(Mission.status == MissionStatus.ACTIVE)
                    .where(Mission.execution_lock_id == None)
                )
                res = await db.execute(stmt)
                for m in res.scalars().all():
                    print(f"Resuming active mission {m.id} via durable queue")
                    from backend.services.durable_queue import DurableQueue
                    await DurableQueue.enqueue(
                        task_type="EXECUTE_MISSION",
                        payload={"mission_id": m.id, "org_id": m.organization_id, "user_id": m.created_by},
                        queue_name="missions",
                        priority=20,
                        job_id=f"resume_mission_{m.id}",
                    )
            except Exception as _resume_err:
                await db.rollback()
                _startup_logger.warning(f"Mission recovery warning: {_resume_err}")

    # Load custom tools from database into ToolRegistry
    try:
        from backend.api.v1.tools import load_custom_tools_on_startup
        await load_custom_tools_on_startup()
    except Exception as _tool_err:
        _startup_logger.warning(f"Custom tools load warning: {_tool_err}")

    # Start PostgreSQL Durable Worker Pool
    from backend.services.durable_queue import DurableQueue, get_worker_pool
    worker_pool = None
    try:
        await DurableQueue.init_schema()
        worker_pool = get_worker_pool()
        await worker_pool.start()
        _startup_logger.info("PostgreSQL Durable Worker Pool (FOR UPDATE SKIP LOCKED) successfully started.")
    except Exception as _wq_err:
        _startup_logger.error(f"Failed to start Durable Worker Pool: {_wq_err}")

    yield

    # Cleanly drain and stop worker pool on server shutdown
    if worker_pool:
        try:
            await worker_pool.stop(drain_timeout=15.0)
        except Exception as _stop_err:
            _startup_logger.warning(f"Worker pool shutdown notice: {_stop_err}")

    await engine.dispose()


# ══════════════════════════════════════════
#  RATE LIMITER MIDDLEWARE
# ══════════════════════════════════════════

class RateLimiterMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window in-memory rate limiter middleware.

    Tracks request timestamps per client IP and rejects requests that exceed
    ``max_requests`` within the rolling ``window_seconds`` window.

    Headers returned on every response:
        X-RateLimit-Limit     – configured request ceiling
        X-RateLimit-Remaining – requests remaining in current window
        X-RateLimit-Reset     – UTC epoch second when the oldest slot expires
        Retry-After           – seconds to wait (only on 429 responses)

    Paths in ``exempt_paths`` bypass rate limiting entirely (e.g. health checks).
    """

    def __init__(
        self,
        app: ASGIApp,
        max_requests: int = 100,
        window_seconds: int = 60,
        exempt_paths: Tuple[str, ...] = (
            "/api/health",
            "/api/v1/auth/google/login",
            "/api/v1/auth/google/callback",
            "/api/v1/auth/github/login",
            "/api/v1/auth/github/callback",
            "/api/v1/auth/dev-mode",
        ),
    ) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.exempt_paths = exempt_paths
        # { client_ip: [timestamp, ...] }
        self._store: Dict[str, list] = defaultdict(list)
        self._lock = asyncio.Lock()

    def _get_client_ip(self, request: Request) -> str:
        """Return the real client IP, honouring X-Forwarded-For if present."""
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    async def dispatch(self, request: Request, call_next):
        # Skip exempt paths
        if request.url.path in self.exempt_paths:
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        now = time.time()
        window_start = now - self.window_seconds

        async with self._lock:
            # Evict timestamps outside the current window
            self._store[client_ip] = [
                ts for ts in self._store[client_ip] if ts > window_start
            ]
            request_count = len(self._store[client_ip])

            if request_count >= self.max_requests:
                # Oldest timestamp determines when the client's window resets
                reset_at = int(self._store[client_ip][0] + self.window_seconds)
                retry_after = max(0, reset_at - int(now))
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too Many Requests – rate limit exceeded.",
                        "retry_after_seconds": retry_after,
                    },
                    headers={
                        "X-RateLimit-Limit": str(self.max_requests),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(reset_at),
                        "Retry-After": str(retry_after),
                    },
                )

            # Record this request
            self._store[client_ip].append(now)
            remaining = self.max_requests - (request_count + 1)
            reset_at = int(self._store[client_ip][0] + self.window_seconds)

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response


app = FastAPI(
    title="Kobits API",
    description="Backend foundation of an AI software product engineering platform.",
    version="1.0.0",
    lifespan=lifespan,
)

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
import logging

logger = logging.getLogger(__name__)

@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.error(f"Database Error on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "A secure database constraint or internal database error occurred."}
    )

@app.exception_handler(404)
async def not_found_exception_handler(request: Request, exc):
    import os
    if request.url.path.startswith("/api/"):
        detail = getattr(exc, "detail", "Not found")
        return JSONResponse(status_code=404, content={"detail": detail})
    if os.path.isfile("404.html"):
        return FileResponse("404.html", status_code=404)
    return JSONResponse(status_code=404, content={"detail": "Page not found"})

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled Exception on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred."}
    )

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.FRONTEND_URL,
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "https://kobits.space",
        "http://kobits.space",
        "https://www.kobits.space",
        "http://www.kobits.space",
    ],
    allow_origin_regex=r"https?://(.*\.)?kobits\.space",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiter middleware – registered AFTER CORS so CORS headers are always
# present even on 429 responses.  Defaults: 100 req / 60 s per IP.
# Override via subclassing or replace the middleware instance in tests.
app.add_middleware(
    RateLimiterMiddleware,
    max_requests=100,
    window_seconds=60,
    exempt_paths=("/api/health",),
)

# ══════════════════════════════════════════
#  API ROUTES
# ══════════════════════════════════════════


from fastapi import WebSocket, WebSocketDisconnect
import json

from backend.services.websocket_manager import manager
import uuid
from backend.core.database import AsyncSessionLocal
from backend.models.project import Task, TaskStatus
from backend.models.mission import Mission

async def _handle_terminal_ws(websocket: WebSocket, mission_id: str):
    await manager.connect(websocket, mission_id)
    stream_task = None
    pty_session = None
    try:
        # Resolve active sandbox worktree for interactive PTY shell
        sandbox_dir = os.getcwd()
        try:
            from backend.services.sandbox_manager import SandboxManager
            import backend.core.database as db_core
            async with db_core.AsyncSessionLocal() as db:
                m = await db.get(Mission, mission_id)
                if m and m.active_branch:
                    sess = SandboxManager.get_session_by_branch(m.active_branch)
                    if sess and sess.sandbox_dir and os.path.isdir(sess.sandbox_dir):
                        sandbox_dir = sess.sandbox_dir
        except Exception:
            pass

        try:
            from backend.services.pty_terminal import pty_manager
            pty_session = await pty_manager.get_or_create_session(mission_id, cwd=sandbox_dir)
        except Exception as _pty_err:
            pass

        greeting_payload = {
            "type": "system",
            "mission_id": mission_id,
            "message": f"Connected to Mission {mission_id} live terminal."
        }
        if pty_session:
            hist = pty_session.get_history()
            if hist:
                greeting_payload["history"] = hist
        await websocket.send_json(greeting_payload)

        if pty_session and pty_session.get_history():
            await websocket.send_json({
                "type": "pty_output",
                "mission_id": mission_id,
                "data": pty_session.get_history(),
            })

        if pty_session:
            async def _forward_pty_output():
                try:
                    async for chunk in pty_session.stream_output():
                        await websocket.send_json({
                            "type": "pty_output",
                            "mission_id": mission_id,
                            "data": chunk,
                        })
                except Exception:
                    pass

            stream_task = asyncio.create_task(_forward_pty_output())


        while True:
            # Receive frame: supports both text frames and raw binary bytes safely
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            data = ""
            raw_bytes = None
            if "bytes" in message and message["bytes"] is not None:
                raw_bytes = message["bytes"]
            if "text" in message and message["text"] is not None:
                data = message["text"]

            if raw_bytes is None and not data:
                continue

            # 1. Handle raw binary frames
            if raw_bytes is not None:
                b_clean = raw_bytes.strip().lower()
                if b_clean in (b"ping", b"heartbeat"):
                    await websocket.send_json({"type": "pong", "message": "pong"})
                    continue
                elif b_clean == b"clear":
                    await websocket.send_json({"type": "clear", "message": "Terminal output cleared."})
                    continue
                elif b_clean == b"status":
                    status_str = "ACTIVE"
                    try:
                        import backend.core.database as db_core
                        async with db_core.AsyncSessionLocal() as db:
                            m = await db.get(Mission, mission_id)
                            if m and m.status:
                                status_str = m.status.value if hasattr(m.status, "value") else str(m.status)
                    except Exception:
                        pass
                    await websocket.send_json({
                        "type": "system",
                        "mission_id": mission_id,
                        "message": f"[Status] Mission {mission_id}: {status_str} (live terminal active)."
                    })
                    continue
                else:
                    # Forward raw binary payload directly into interactive shell process
                    if pty_session and pty_session.is_alive:
                        await pty_session.write(raw_bytes)
                    continue

            # 2. Parse text frames
            is_json = False
            raw_payload = None
            try:
                payload = json.loads(data)
                if isinstance(payload, dict):
                    is_json = True
                    raw_payload = payload
            except Exception:
                pass

            if is_json and raw_payload:
                msg_type = str(raw_payload.get("type") or "").lower()
                if msg_type in ("ping", "heartbeat"):
                    await websocket.send_json({"type": "pong", "message": "pong"})
                    continue
                elif msg_type == "clear":
                    await websocket.send_json({"type": "clear", "message": "Terminal output cleared."})
                    continue
                elif msg_type == "status":
                    status_str = "ACTIVE"
                    try:
                        import backend.core.database as db_core
                        async with db_core.AsyncSessionLocal() as db:
                            m = await db.get(Mission, mission_id)
                            if m and m.status:
                                status_str = m.status.value if hasattr(m.status, "value") else str(m.status)
                    except Exception:
                        pass
                    await websocket.send_json({
                        "type": "system",
                        "mission_id": mission_id,
                        "message": f"[Status] Mission {mission_id}: {status_str} (live terminal active)."
                    })
                    continue
                elif msg_type in ("history", "get_history"):
                    hist = pty_session.get_history() if pty_session else ""
                    await websocket.send_json({
                        "type": "pty_output",
                        "mission_id": mission_id,
                        "data": hist,
                    })
                    continue
                elif msg_type == "resize":
                    cols = int(raw_payload.get("cols", 80))
                    rows = int(raw_payload.get("rows", 24))
                    if pty_session:
                        pty_session.resize(cols, rows)
                    await websocket.send_json({"type": "system", "message": f"Terminal resized to {cols}x{rows}."})
                    continue
                elif msg_type in ("stdin", "pty_input", "terminal_input"):
                    stdin_data = raw_payload.get("data")
                    if stdin_data is None:
                        stdin_data = raw_payload.get("content") or raw_payload.get("message") or ""
                    if pty_session and pty_session.is_alive:
                        await pty_session.write(stdin_data)
                    continue
                elif msg_type in ("command", "execute"):
                    cmd_data = raw_payload.get("command") or raw_payload.get("data") or ""
                    if cmd_data:
                        if not cmd_data.endswith("\n") and not cmd_data.endswith("\r"):
                            cmd_data += "\n"
                        if pty_session and pty_session.is_alive:
                            await pty_session.write(cmd_data)
                    continue
                elif raw_payload.get("message") or raw_payload.get("content"):
                    clean_text = str(raw_payload.get("message") or raw_payload.get("content") or "").strip()
                    try:
                        import backend.core.database as db_core
                        async with db_core.AsyncSessionLocal() as db:
                            mission = await db.get(Mission, mission_id)
                            if mission:
                                new_task = Task(
                                    id=str(uuid.uuid4()),
                                    mission_id=mission.id,
                                    project_id=mission.project_id,
                                    created_by=mission.created_by,
                                    title="Process User Feedback",
                                    description=f"The user has provided live input/feedback:\n\n\"{clean_text}\"\n\nPlease analyze this and adjust the mission trajectory accordingly.",
                                    status=TaskStatus.PENDING,
                                    phase=mission.phase,
                                    is_correction=True,
                                    metadata_json=json.dumps({"agent_role": "SOLUTION_ARCHITECT", "source": "terminal"})
                                )
                                db.add(new_task)
                                await db.commit()
                                
                                ph_name = mission.phase.value if hasattr(mission.phase, 'value') else str(mission.phase)
                                await manager.broadcast(mission_id, {
                                    "type": "system", 
                                    "mission_id": mission_id,
                                    "message": f"[User Input Acknowledged] Appended real-time task to {ph_name} queue: \"{clean_text}\""
                                })
                            else:
                                await manager.broadcast(mission_id, {
                                    "type": "system", 
                                    "mission_id": mission_id,
                                    "message": f"[User Input Acknowledged] Live terminal instruction recorded: \"{clean_text}\""
                                })
                    except Exception as db_err:
                        await websocket.send_json({"type": "system", "message": f"[Input Error] {str(db_err)}"})
                    continue

            # 3. Handle raw text strings (e.g. "ping", "clear", "status", keystrokes, shell commands)
            clean_text = data.strip()
            lower_cmd = clean_text.lower()
            if lower_cmd in ("ping", "heartbeat"):
                await websocket.send_json({"type": "pong", "message": "pong"})
                continue
            elif lower_cmd == "clear":
                await websocket.send_json({"type": "clear", "message": "Terminal output cleared."})
                continue
            elif lower_cmd == "status":
                status_str = "ACTIVE"
                try:
                    import backend.core.database as db_core
                    async with db_core.AsyncSessionLocal() as db:
                        m = await db.get(Mission, mission_id)
                        if m and m.status:
                            status_str = m.status.value if hasattr(m.status, "value") else str(m.status)
                except Exception:
                    pass
                await websocket.send_json({
                    "type": "system",
                    "mission_id": mission_id,
                    "message": f"[Status] Mission {mission_id}: {status_str} (live terminal active)."
                })
                continue

            # Forward interactive shell keystrokes or line commands
            if pty_session and pty_session.is_alive:
                if "\n" not in data and "\r" not in data:
                    await pty_session.write(data + "\n")
                else:
                    await pty_session.write(data)
            else:
                if clean_text:
                    await websocket.send_json({
                        "type": "system",
                        "mission_id": mission_id,
                        "message": f"[Terminal Command] {clean_text}"
                    })

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if stream_task and not stream_task.done():
            stream_task.cancel()
        manager.disconnect(websocket, mission_id)


@app.websocket("/ws/terminal/{mission_id}")
async def websocket_terminal(websocket: WebSocket, mission_id: str):
    await _handle_terminal_ws(websocket, mission_id)


@app.websocket("/api/v1/ws/terminal/{mission_id}")
async def websocket_terminal_v1(websocket: WebSocket, mission_id: str):
    await _handle_terminal_ws(websocket, mission_id)

@app.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    await manager.connect(websocket, "dashboard")
    try:
        await websocket.send_json({"type": "system", "message": "Connected to Kobits real-time dashboard stream."})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, "dashboard")

@app.websocket("/ws/missions/{mission_id}")
async def websocket_mission(websocket: WebSocket, mission_id: str):
    await manager.connect(websocket, mission_id)
    try:
        await websocket.send_json({"type": "system", "message": f"Connected to Mission {mission_id} stream."})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, mission_id)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(projects.router, prefix="/api/v1/projects", tags=["Projects & Tasks"])
app.include_router(agents.router, prefix="/api/v1/ai", tags=["AI Agents & Orchestration"])
app.include_router(github.router, prefix="/api/v1/github", tags=["GitHub"])
from backend.api.v1 import webhooks
app.include_router(webhooks.router, prefix="/api/v1/webhooks", tags=["Webhooks"])

from backend.api.v1 import billing
app.include_router(billing.router, prefix="/api/v1/billing", tags=["Billing & Ledger"])

app.include_router(missions.router, prefix="/api/v1/missions", tags=["Missions"])

from backend.api.v1 import memory
app.include_router(memory.router, prefix="/api/v1/memory", tags=["Knowledge & Memory"])
app.include_router(tools.router, prefix="/api/v1/tools", tags=["Tools"])

from backend.api.v1 import builder
app.include_router(builder.router, prefix="/api/v1/builder", tags=["Builder Agent"])

from backend.api.v1 import provider
app.include_router(provider.router, prefix="/api/v1/provider", tags=["LLM Provider"])

from backend.api.v1 import analytics
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["Analytics"])

from backend.api.v1 import org
app.include_router(org.router, prefix="/api/v1/org", tags=["Organisation"])

from backend.api.v1 import profile
app.include_router(profile.router, prefix="/api/v1/profile", tags=["User Profile"])

from backend.api.v1 import training
app.include_router(training.router, prefix="/api/v1/training", tags=["Model Fine-Tuning Flywheel"])

from backend.api.v1 import preview
app.include_router(preview.router, prefix="/api/v1/preview", tags=["Live Ephemeral Preview"])

from backend.api.v1 import telemetry
app.include_router(telemetry.router, prefix="/api/v1/telemetry", tags=["Observability & Telemetry"])

from backend.api.v1 import waitlist
app.include_router(waitlist.router, prefix="/api/v1", tags=["Waitlist"])

from backend.api.v1 import extension
app.include_router(extension.router, prefix="/api/v1/extension", tags=["VS Code & IDE Extension"])



# ══════════════════════════════════════════
#  LEGACY FRONTEND ENDPOINTS
# ══════════════════════════════════════════

class WaitlistRequest(BaseModel):
    email: str
    full_name: Optional[str] = None
    company: Optional[str] = None
    use_case: Optional[str] = None
    github_handle: Optional[str] = None


@app.post("/api/apply")
@app.post("/api/v1/waitlist/apply")
async def apply_waitlist(req: WaitlistRequest):
    from backend.models.waitlist import WaitlistLead
    from backend.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from fastapi import HTTPException
    
    clean_email = (req.email or "").strip().lower()
    if not clean_email or "@" not in clean_email or "." not in clean_email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(WaitlistLead).where(WaitlistLead.email == clean_email))
        existing = res.scalar_one_or_none()
        if existing:
            if req.full_name:
                existing.full_name = req.full_name
            if req.company:
                existing.company = req.company
            if req.use_case:
                existing.use_case = req.use_case
            if req.github_handle:
                existing.github_handle = req.github_handle
            await db.commit()
            return {
                "status": "success",
                "message": "Your application was already received! We have updated your details and will notify you soon."
            }

        lead = WaitlistLead(
            email=clean_email,
            full_name=req.full_name,
            company=req.company,
            use_case=req.use_case,
            github_handle=req.github_handle,
            status="PENDING"
        )
        db.add(lead)
        await db.commit()
        return {
            "status": "success",
            "message": "You're on the early access list! We review applications daily and will send an invitation to your email."
        }


@app.get("/api/v1/waitlist/leads")
async def list_waitlist_leads():
    """Retrieve all waitlist early access submissions."""
    from backend.models.waitlist import WaitlistLead
    from backend.core.database import AsyncSessionLocal
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(WaitlistLead).order_by(WaitlistLead.created_at.desc()))
        leads = res.scalars().all()
        return [
            {
                "id": l.id,
                "email": l.email,
                "full_name": l.full_name,
                "company": l.company,
                "use_case": l.use_case,
                "github_handle": l.github_handle,
                "status": l.status,
                "created_at": l.created_at.isoformat() if l.created_at else None
            }
            for l in leads
        ]


@app.post("/api/v1/waitlist/leads/{lead_id}/approve")
async def approve_waitlist_lead(lead_id: str, request: Request):
    """Approve a waitlist lead, provision their user account & organization with 50 credits, and return their invite URL."""
    from backend.models.waitlist import WaitlistLead
    from backend.api.v1.auth import _find_or_create_user_and_org
    from backend.core.database import AsyncSessionLocal
    from fastapi import HTTPException

    async with AsyncSessionLocal() as db:
        lead = await db.get(WaitlistLead, lead_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Waitlist lead not found")

        lead.status = "APPROVED"

        # Provision user account & default workspace with 50 welcome credits
        try:
            await _find_or_create_user_and_org(
                db=db,
                email=lead.email,
                full_name=lead.full_name
            )
        except Exception:
            pass

        await db.commit()
        await db.refresh(lead)

        # Craft invite URL
        base_url = "https://kobits.space"
        if request:
            host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
            proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
            if host:
                base_url = f"{proto}://{host}".rstrip("/")

        invite_url = f"{base_url}/portal.html?email={lead.email}#register"

        return {
            "status": "success",
            "message": f"Lead {lead.email} approved and workspace provisioned with 50 credits.",
            "lead": {
                "id": lead.id,
                "email": lead.email,
                "full_name": lead.full_name,
                "company": lead.company,
                "status": lead.status,
                "invite_url": invite_url
            }
        }


@app.post("/api/v1/waitlist/leads/{lead_id}/reject")
async def reject_waitlist_lead(lead_id: str):
    """Mark a waitlist lead as rejected."""
    from backend.models.waitlist import WaitlistLead
    from backend.core.database import AsyncSessionLocal
    from fastapi import HTTPException

    async with AsyncSessionLocal() as db:
        lead = await db.get(WaitlistLead, lead_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Waitlist lead not found")
        lead.status = "REJECTED"
        await db.commit()
        await db.refresh(lead)
        return {
            "status": "success",
            "message": f"Lead {lead.email} marked as rejected.",
            "lead": {
                "id": lead.id,
                "email": lead.email,
                "status": lead.status
            }
        }


@app.delete("/api/v1/waitlist/leads/{lead_id}")
async def delete_waitlist_lead(lead_id: str):
    """Delete a waitlist submission."""
    from backend.models.waitlist import WaitlistLead
    from backend.core.database import AsyncSessionLocal
    from fastapi import HTTPException

    async with AsyncSessionLocal() as db:
        lead = await db.get(WaitlistLead, lead_id)
        if not lead:
            raise HTTPException(status_code=404, detail="Waitlist lead not found")
        await db.delete(lead)
        await db.commit()
        return {
            "status": "success",
            "message": "Lead deleted successfully."
        }


@app.get("/api/health")
async def health_check():
    """
    Health-check endpoint for load balancers and monitoring.
    Returns DB connectivity status, app version, and uptime.
    Exempt from rate limiting (see RateLimiterMiddleware.exempt_paths).
    """
    import time as _time
    from sqlalchemy import text as _text
    from backend.core.database import AsyncSessionLocal

    db_status = "ok"
    db_error: str | None = None
    try:
        async with AsyncSessionLocal() as _db:
            await _db.execute(_text("SELECT 1"))
    except Exception as exc:
        db_status = "error"
        db_error = str(exc)

    redis_status = "not_configured"
    if settings.REDIS_URL:
        try:
            import aioredis  # type: ignore
            _redis = await aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            await _redis.ping()
            await _redis.close()
            redis_status = "ok"
        except Exception as exc:
            redis_status = f"error: {exc}"

    overall = "ok" if db_status == "ok" else "degraded"
    return {
        "status": overall,
        "service": "Kobits API",
        "version": "1.0.0",
        "database": db_status,
        "database_error": db_error,
        "redis": redis_status,
        "dev_mode": settings.KOBITS_DEV_MODE,
    }


# ══════════════════════════════════════════
#  STATIC FRONTEND SERVING
# ══════════════════════════════════════════

@app.get("/")


async def serve_index():
    from fastapi.responses import FileResponse
    import os
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    if os.path.isfile("landing.html"):
        return FileResponse("landing.html", headers=headers)
    if os.path.isfile("index.html"):
        return FileResponse("index.html", headers=headers)
    if os.path.isfile("web/index.html"):
        return FileResponse("web/index.html", headers=headers)
    return FileResponse("portal.html", headers=headers)

@app.get("/landing")
async def serve_landing():
    from fastapi.responses import FileResponse
    import os
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    target = "landing.html" if os.path.isfile("landing.html") else "index.html"
    return FileResponse(target, headers=headers)

def _has_valid_session_token(request: Request) -> str | None:
    """Return the validated JWT string if the request carries a valid session cookie, query token, or Bearer header."""
    import jwt as _jwt
    candidates = [
        request.query_params.get("session_token"),
        request.cookies.get(settings.SESSION_COOKIE_NAME),
        request.headers.get("Authorization", "").replace("Bearer ", "").strip(),
    ]
    for tok in candidates:
        if not tok:
            continue
        try:
            payload = _jwt.decode(tok, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            if payload.get("sub"):
                return tok
        except Exception:
            continue
    return None


@app.get("/dashboard")
@app.get("/dashboard/")
async def serve_dashboard(request: Request):
    from fastapi.responses import FileResponse, RedirectResponse
    from backend.api.v1.auth import _set_session_cookie
    import os
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    is_demo = request.query_params.get("demo") == "true" or request.query_params.get("mock") == "true"
    valid_token = _has_valid_session_token(request)
    if not valid_token and not settings.KOBITS_DEV_MODE and not is_demo:
        # Require login for accessing personal workspace
        return RedirectResponse(url="/portal.html#login", status_code=302)

    target = "web/index.html" if os.path.isfile("web/index.html") else "portal.html"
    resp = FileResponse(target, headers=headers)
    if valid_token and request.query_params.get("session_token"):
        _set_session_cookie(resp, valid_token)
    return resp

@app.get("/home")
async def serve_home():
    from fastapi.responses import FileResponse
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return FileResponse("landing.html", headers=headers)

@app.get("/{file_path:path}")
async def serve_static(file_path: str, request: Request):
    from fastapi import HTTPException
    import os
    from fastapi.responses import FileResponse, RedirectResponse
    from backend.api.v1.auth import _set_session_cookie
    
    clean_path = file_path.strip("/").rstrip(".")

    # Do not intercept API routes
    if clean_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found")
        
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    # If landing page is requested
    if clean_path in ("landing.html", "landing", "index.html"):
        if os.path.isfile("landing.html"):
            return FileResponse("landing.html", headers=headers)
        if os.path.isfile("index.html"):
            return FileResponse("index.html", headers=headers)

    # If portal is requested, serve portal.html directly (allows user to sign in or register without loop)
    if clean_path in ("portal.html", "portal"):
        if os.path.isfile("portal.html"):
            return FileResponse("portal.html", headers=headers)

    # Protect personal dashboard workspace routes behind authentication
    if clean_path in ("dashboard", "web/index.html", "web") or clean_path.startswith("web/"):
        ext = os.path.splitext(clean_path)[1].lower()
        if ext in ("", ".html"):
            is_demo = request.query_params.get("demo") == "true" or request.query_params.get("mock") == "true"
            valid_token = _has_valid_session_token(request)
            if not valid_token and not settings.KOBITS_DEV_MODE and not is_demo:
                return RedirectResponse(url="/portal.html#login", status_code=302)
            if clean_path in ("dashboard", "web/index.html", "web") and os.path.isfile("web/index.html"):
                resp = FileResponse("web/index.html", headers=headers)
                if valid_token and request.query_params.get("session_token"):
                    _set_session_cookie(resp, valid_token)
                return resp

    # If dashboard is requested (authenticated)
    if clean_path == "dashboard":
        if os.path.isfile("web/index.html"):
            return FileResponse("web/index.html", headers=headers)
        if os.path.isfile("portal.html"):
            return FileResponse("portal.html", headers=headers)
    
    # Check web/ subdirectory first
    web_file = os.path.join("web", clean_path)
    if os.path.isfile(web_file):
        return FileResponse(web_file, headers=headers)

    # Check root workspace file
    if os.path.isfile(clean_path):
        return FileResponse(clean_path, headers=headers)
        
    # Check if clean URL matches an HTML page (e.g., /pricing -> pricing.html)
    if os.path.isfile(clean_path + ".html"):
        return FileResponse(clean_path + ".html", headers=headers)
    
    # Never return HTML for code/asset file extensions
    ext = os.path.splitext(clean_path)[1].lower()
    if ext in (".js", ".mjs", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2", ".ttf"):
        raise HTTPException(status_code=404, detail=f"Static asset '{clean_path}' not found")
        
    # 404 fallback
    if os.path.isfile("404.html"):
        return FileResponse("404.html", status_code=404)
    raise HTTPException(status_code=404, detail="Page not found")
