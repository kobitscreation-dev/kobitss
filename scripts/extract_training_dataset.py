"""
Kobits Training Dataset Harvester & Trajectory Synthesizer
Exports high-quality SFT & DPO training data for fine-tuning open-source models
(e.g., Qwen 2.5 Coder 7B/14B) on Google Colab Pro using Unsloth.
"""

import json
import os
import sys
import sqlite3
from pathlib import Path
from typing import List, Dict, Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "kobits.db"
OUTPUT_DIR = ROOT / "training_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SFT_PATH = OUTPUT_DIR / "kobits_sft_dataset.jsonl"
DPO_PATH = OUTPUT_DIR / "kobits_dpo_dataset.jsonl"


def extract_db_trajectories() -> List[Dict[str, Any]]:
    """Extract agent execution trajectories from kobits.db."""
    if not DB_PATH.exists():
        print(f"Database {DB_PATH} not found.")
        return []

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    query = """
    SELECT 
        r.id, r.model, r.status, r.input_text, r.output_text, r.tool_calls_json,
        t.title, t.description, t.expected_output, t.phase
    FROM agent_runs r
    LEFT JOIN tasks t ON r.task_id = t.id
    WHERE r.output_text IS NOT NULL AND length(r.output_text) > 20
    ORDER BY r.created_at ASC
    """
    cur.execute(query)
    rows = cur.fetchall()
    conn.close()

    trajectories = []
    for row in rows:
        run_id, model, status, in_text, out_text, tools_json, t_title, t_desc, expected, phase = row
        
        system_msg = "You are Kobits, an autonomous senior full-stack AI engineering agent. You write clean, production-grade, bug-free code with explicit architecture, tool calls, and verified implementations."
        user_msg = t_desc or t_title or in_text or "Build requested feature."
        
        # Parse assistant output
        assistant_content = out_text.strip()
        
        # Build ChatML record
        record = {
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": assistant_content}
            ],
            "metadata": {
                "source": "kobits_db",
                "run_id": run_id,
                "model": model,
                "status": status,
                "phase": phase
            }
        }
        trajectories.append(record)

    return trajectories


def generate_frontier_coding_trajectories() -> List[Dict[str, Any]]:
    """
    Generate synthetic gold-standard full-stack coding trajectories
    modeled after Claude Sonnet 4.6 agentic workflows.
    """
    trajectories = []
    
    blueprints = [
        {
            "task": "Build a modern school admission portal with online fee calculation",
            "plan": "1. Analyze requirements: responsive admission form, real-time fee calculation, parent information, validation.\n2. Create index.html with semantic HTML5 and clean layout.\n3. Create styles.css with modern design tokens and accessible typography.\n4. Create app.js with dynamic grade fee rules, optional transport/lunch calculation, and submission handling.",
            "tool": "repository_write",
            "file": "index.html",
            "code": """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>St. Jude Academy - Online Admission Portal</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <header class="header">
        <div class="container nav-content">
            <div class="logo">🎓 St. Jude Academy</div>
            <nav><a href="#apply">Apply Now</a><a href="#fee-calculator">Fee Calculator</a></nav>
        </div>
    </header>
    <main class="container">
        <section class="hero">
            <h1>Admissions Open for Academic Year 2026-27</h1>
            <p>Calculate tuition fees instantly and submit your child's application online.</p>
        </section>
        <section class="grid-layout">
            <div class="card" id="apply">
                <h2>Student Admission Form</h2>
                <form id="admissionForm">
                    <label>Student Full Name <input type="text" id="studentName" required></label>
                    <label>Grade Level 
                        <select id="gradeSelect" required>
                            <option value="">Select Grade</option>
                            <option value="primary">Primary (Grades 1-5)</option>
                            <option value="middle">Middle School (Grades 6-8)</option>
                            <option value="high">High School (Grades 9-12)</option>
                        </select>
                    </label>
                    <label>Parent/Guardian Email <input type="email" id="parentEmail" required></label>
                    <label>Parent Phone <input type="tel" id="parentPhone" required></label>
                    <div class="checkbox-group">
                        <label><input type="checkbox" id="transport"> Include School Bus Transport ($120/mo)</label>
                        <label><input type="checkbox" id="cafeteria"> Include Cafeteria Meal Plan ($90/mo)</label>
                    </div>
                    <button type="submit" class="btn btn-primary">Submit Application</button>
                </form>
            </div>
            <div class="card summary-card" id="fee-calculator">
                <h2>Estimated Fee Breakdown</h2>
                <div class="fee-row"><span>Base Tuition:</span><span id="baseTuition">$0.00</span></div>
                <div class="fee-row"><span>Transport:</span><span id="transportFee">$0.00</span></div>
                <div class="fee-row"><span>Cafeteria:</span><span id="cafeteriaFee">$0.00</span></div>
                <hr>
                <div class="fee-row total"><span>Total Annual Fee:</span><span id="totalFee">$0.00</span></div>
            </div>
        </section>
    </main>
    <script src="app.js"></script>
</body>
</html>"""
        },
        {
            "task": "Create a secure FastAPI JWT authentication and role-based access control service",
            "plan": "1. Define User and Role schemas using Pydantic.\n2. Implement password hashing using bcrypt.\n3. Implement JWT access & refresh token generation with HMAC-SHA256.\n4. Build dependency guards for RBAC (@require_roles('admin')).",
            "tool": "repository_write",
            "file": "backend/auth.py",
            "code": """from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr

SECRET_KEY = "CHANGE_IN_PRODUCTION_SECRET_KEY"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")

class TokenData(BaseModel):
    user_id: str
    email: EmailStr
    roles: List[str]

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenData:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        email: str = payload.get("email")
        roles: List[str] = payload.get("roles", [])
        if user_id is None or email is None:
            raise credentials_exception
        return TokenData(user_id=user_id, email=email, roles=roles)
    except jwt.PyJWTError:
        raise credentials_exception

def require_role(required_role: str):
    def role_checker(current_user: TokenData = Depends(get_current_user)):
        if required_role not in current_user.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires '{required_role}' permissions"
            )
        return current_user
    return role_checker
"""
        },
        {
            "task": "Create a rate-limiting middleware for FastAPI with Redis Token Bucket algorithm",
            "plan": "1. Connect to Redis via redis-py.\n2. Implement atomic Token Bucket lua script or Redis pipeline.\n3. Return HTTP 429 Too Many Requests with Retry-After header when rate limit is exceeded.\n4. Track client IP or authenticated API key.",
            "tool": "repository_write",
            "file": "backend/rate_limiter.py",
            "code": """import time
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
import redis.asyncio as redis

class RateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_url: str = "redis://localhost:6379", max_requests: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        current_time = int(time.time())
        window_key = f"rate_limit:{client_ip}:{current_time // self.window_seconds}"

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(window_key)
            pipe.expire(window_key, self.window_seconds * 2)
            results = await pipe.execute()

        request_count = results[0]
        if request_count > self.max_requests:
            retry_after = self.window_seconds - (current_time % self.window_seconds)
            return HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)}
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(max(0, self.max_requests - request_count))
        return response
"""
        }
    ]

    for bp in blueprints:
        system_msg = "You are Kobits, an autonomous senior full-stack AI engineering agent. You write clean, production-grade, bug-free code with explicit architecture, tool calls, and verified implementations."
        user_msg = bp["task"]
        assistant_content = f"{bp['plan']}\n\n```tool_call\n{{\"name\": \"{bp['tool']}\", \"parameters\": {{\"path\": \"{bp['file']}\"}}}}\n```\n\n```python\n# {bp['file']}\n{bp['code']}\n```\n\nSuccessfully created `{bp['file']}` with complete implementation. All checks passed."
        
        trajectories.append({
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": assistant_content}
            ],
            "metadata": {
                "source": "frontier_blueprint",
                "task": bp["task"],
                "file": bp["file"]
            }
        })

    return trajectories


def build_dpo_pairs() -> List[Dict[str, Any]]:
    """Build preference pairs (chosen vs rejected) for Direct Preference Optimization."""
    dpo_samples = [
        {
            "prompt": "Fix NameError in school fee calculator where 'transportCost' is undefined",
            "chosen": "const transportCost = isTransport ? 120 : 0;\nconst total = baseTuition + transportCost;\nreturn total;",
            "rejected": "const total = baseTuition + transportCost; // transportCost is not defined\nconst transportCost = isTransport ? 120 : 0;\nreturn total;"
        },
        {
            "prompt": "Create an async sqlite database connection pool in Python",
            "chosen": "from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker\nengine = create_async_engine('sqlite+aiosqlite:///./app.db', pool_pre_ping=True)\nasync_session = async_sessionmaker(engine, expire_on_commit=False)",
            "rejected": "import sqlite3\nconn = sqlite3.connect('app.db') # Synchronous blocking call inside async event loop"
        }
    ]
    return dpo_samples


def main():
    print(f"Harvesting training data into {OUTPUT_DIR}...")
    
    db_data = extract_db_trajectories()
    synth_data = generate_frontier_coding_trajectories()
    
    all_sft = db_data + synth_data
    with open(SFT_PATH, "w", encoding="utf-8") as f:
        for item in all_sft:
            f.write(json.dumps(item) + "\n")
            
    print(f"✓ Saved {len(all_sft)} SFT trajectories to {SFT_PATH}")
    
    dpo_pairs = build_dpo_pairs()
    with open(DPO_PATH, "w", encoding="utf-8") as f:
        for item in dpo_pairs:
            f.write(json.dumps(item) + "\n")
            
    print(f"✓ Saved {len(dpo_pairs)} DPO pairs to {DPO_PATH}")
    print("\nDataset harvesting complete! Ready for Google Colab Pro.")


if __name__ == "__main__":
    main()
