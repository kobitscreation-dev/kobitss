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

from backend.api.v1 import auth, projects, agents, github, missions, memory, tools


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate provider configuration on startup
    from backend.core.config import settings
    from backend.services.llm import get_llm_provider
    try:
        get_llm_provider()
    except Exception as e:
        if settings.REAL_AI_TEST:
            # Hard crash if REAL_AI_TEST is enabled and provider is misconfigured
            raise e
        print(f"Warning: Provider misconfigured: {e}")

    # Ensure database tables exist (idempotent, safe for SQLite on cloud containers)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Auto-seed default user if database is fresh
    from backend.core.database import AsyncSessionLocal
    from backend.models.organization import User, Organization, OrganizationMember, OrgRole
    from backend.core.security import get_password_hash
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        admin_user = (await db.execute(select(User).where(User.email == "realuser@kobits.space"))).scalar_one_or_none()
        if not admin_user:
            new_u = User(
                email="realuser@kobits.space",
                hashed_password=get_password_hash("MyPass12345!"),
                full_name="Kobits Founder",
                is_active=True
            )
            db.add(new_u)
            new_org = Organization(name="Kobits Core Workspace")
            db.add(new_org)
            await db.flush()
            db.add(OrganizationMember(user_id=new_u.id, organization_id=new_org.id, role=OrgRole.OWNER))
            await db.commit()
            print("Auto-seeded default user realuser@kobits.space")

    # Resume ACTIVE missions and clear dangling locks
    from backend.core.database import AsyncSessionLocal
    from backend.models.mission import Mission, MissionStatus
    from backend.services.mission_runtime import MissionRuntime
    import asyncio
    from sqlalchemy import select, update
    
    async with AsyncSessionLocal() as db:
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
            print(f"Resuming active mission {m.id}")
            asyncio.create_task(MissionRuntime(m.id, m.organization_id, m.created_by).execute())

    # Load custom tools from database into ToolRegistry
    from backend.api.v1.tools import load_custom_tools_on_startup
    await load_custom_tools_on_startup()

    yield
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
        exempt_paths: Tuple[str, ...] = ("/api/health",),
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

@app.websocket("/ws/terminal/{mission_id}")
async def websocket_endpoint(websocket: WebSocket, mission_id: str):
    await manager.connect(websocket, mission_id)
    try:
        # Acknowledge connection
        await websocket.send_json({"type": "system", "message": f"Connected to Mission {mission_id} live terminal."})
        while True:
            data = await websocket.receive_text()
            
            # Parse user input
            try:
                payload = json.loads(data)
                text = payload.get("message", payload.get("content", data))
            except Exception:
                text = data
                
            if not text or not str(text).strip():
                continue
                
            # Connect the terminal input to the active API/LLM pipeline
            async with AsyncSessionLocal() as db:
                mission = await db.get(Mission, mission_id)
                if mission:
                    new_task = Task(
                        id=str(uuid.uuid4()),
                        mission_id=mission.id,
                        project_id=mission.project_id,
                        created_by=mission.created_by,
                        title="Process User Feedback",
                        description=f"The user has provided live input/feedback:\n\n\"{text}\"\n\nPlease analyze this and adjust the mission trajectory accordingly.",
                        status=TaskStatus.PENDING,
                        phase=mission.phase,
                        is_correction=True,
                        metadata_json=json.dumps({"agent_role": "SOLUTION_ARCHITECT"})
                    )
                    db.add(new_task)
                    await db.commit()
                    
                    # Echo back to terminal
                    await manager.broadcast(mission_id, {
                        "type": "system", 
                        "message": f"[User Input Acknowledged] Appended real-time task to the {mission.phase.value if hasattr(mission.phase, 'value') else mission.phase} phase queue."
                    })

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


# ══════════════════════════════════════════
#  LEGACY FRONTEND ENDPOINTS
# ══════════════════════════════════════════

class WaitlistRequest(BaseModel):
    email: str


@app.post("/api/apply")
async def apply_waitlist(req: WaitlistRequest):
    return {"message": "Success"}


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "Kobits API", "version": "1.0.0"}


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

@app.get("/dashboard")
@app.get("/dashboard/")
async def serve_dashboard(request: Request):
    from fastapi.responses import FileResponse, RedirectResponse
    import os
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    # Check if user has active session cookie or Authorization header
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    auth_header = request.headers.get("Authorization", "")
    if not token and not auth_header:
        # Require login for accessing personal workspace
        return RedirectResponse(url="/portal.html#login", status_code=302)

    if os.path.isfile("web/index.html"):
        return FileResponse("web/index.html", headers=headers)
    return FileResponse("portal.html", headers=headers)

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

    # Protect personal dashboard workspace routes behind authentication
    if clean_path in ("dashboard", "portal", "web/index.html", "web") or clean_path.startswith("web/"):
        ext = os.path.splitext(clean_path)[1].lower()
        if ext in ("", ".html"):
            token = request.cookies.get(settings.SESSION_COOKIE_NAME)
            auth_header = request.headers.get("Authorization", "")
            if not token and not auth_header:
                return RedirectResponse(url="/portal.html#login", status_code=302)

    # If portal is requested
    if clean_path in ("portal.html", "portal"):
        if os.path.isfile("portal.html"):
            return FileResponse("portal.html", headers=headers)

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
    
    # Never return HTML for code/asset file extensions
    ext = os.path.splitext(clean_path)[1].lower()
    if ext in (".js", ".mjs", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2", ".ttf"):
        raise HTTPException(status_code=404, detail=f"Static asset '{clean_path}' not found")
        
    # SPA fallback for page navigation
    if os.path.isfile("web/index.html"):
        return FileResponse("web/index.html", headers=headers)
    if os.path.isfile("portal.html"):
        return FileResponse("portal.html", headers=headers)
    raise HTTPException(status_code=404, detail="Page not found")
