# Kobits Platform — System Architecture Design
**Document version:** 2.1 | **Authored by:** SOLUTION_ARCHITECT Agent  
**Last updated:** 2026-10-09 | **Status:** APPROVED FOR IMPLEMENTATION

---

## 1. Executive Summary

Kobits is an AI-driven, multi-agent software-engineering platform built on a FastAPI + SQLAlchemy async backend, targeting both SQLite (dev) and PostgreSQL (production) storage backends. The platform orchestrates autonomous agents across missions, projects, GitHub, billing, analytics, and now a hardened user-profile management sub-system delivered by the upstream Nexus agent.

This document captures the complete system architecture, data-flow model, service topology, API surface, security posture, scalability patterns, and the technical roadmap for the next major iteration.

---

## 2. System Context (C4 Level 1)

```
┌──────────────────────────────────────────────────────────┐
│                    EXTERNAL ACTORS                        │
│   Browser/Portal   CLI (kobits_cli)   Webhooks (GitHub)  │
│   OAuth Providers (Google, GitHub)     Stripe Billing     │
└──────────────┬───────────────────────────────────────────┘
               │  HTTPS / WebSocket
┌──────────────▼───────────────────────────────────────────┐
│              KOBITS PLATFORM  (FastAPI ASGI)             │
│   ┌─────────────┐  ┌───────────────┐  ┌──────────────┐  │
│   │  Auth &     │  │  Mission &    │  │  User        │  │
│   │  OAuth      │  │  Agent Orch.  │  │  Profile     │  │
│   └─────────────┘  └───────────────┘  └──────────────┘  │
│   ┌─────────────┐  ┌───────────────┐  ┌──────────────┐  │
│   │  Projects & │  │  GitHub Int.  │  │  Analytics & │  │
│   │  Tasks      │  │  & Webhooks   │  │  Billing     │  │
│   └─────────────┘  └───────────────┘  └──────────────┘  │
│   ┌─────────────┐  ┌───────────────┐  ┌──────────────┐  │
│   │  Memory &   │  │  Tools &      │  │  Builder     │  │
│   │  Knowledge  │  │  Registry     │  │  Agent       │  │
│   └─────────────┘  └───────────────┘  └──────────────┘  │
└──────────────┬───────────────────────────────────────────┘
               │  SQLAlchemy Async / aiosqlite / asyncpg
┌──────────────▼───────────────────────────────────────────┐
│        DATA STORES                                        │
│   SQLite (dev)  PostgreSQL (prod)   Redis (planned)      │
└──────────────────────────────────────────────────────────┘
```

---

## 3. Container Architecture (C4 Level 2)

### 3.1 API Gateway Layer

| Component | File | Responsibility |
|-----------|------|----------------|
| **FastAPI App** | `backend/main.py` | ASGI app, lifespan, middleware, router registration |
| **CORSMiddleware** | `backend/main.py` | Allow-list + regex CORS for kobits.space domains |
| **RateLimiterMiddleware** | `backend/main.py` | In-memory sliding-window 100 req/60s per IP |
| **SQLAlchemy Exception Handler** | `backend/main.py` | Translates DB exceptions → HTTP 500 (no leakage) |
| **Global Exception Handler** | `backend/main.py` | Catch-all HTTP 500 with opaque error messages |

### 3.2 Authentication & Security Layer

| Component | File | Responsibility |
|-----------|------|----------------|
| **JWT Auth** | `backend/core/security.py` | HS256 tokens, bcrypt hashing, legacy SHA-256 compat |
| **OAuth 2.0** | `backend/api/v1/auth.py` | Google & GitHub OAuth2 PKCE flow |
| **get_current_active_user** | `backend/api/deps.py` | Cookie + Bearer token resolution; is_active guard |
| **Session Cookie** | `backend/api/v1/auth.py` | HttpOnly, SameSite=lax, configurable Secure flag |

### 3.3 Domain Services

| Domain | Router Prefix | Service Layer |
|--------|--------------|---------------|
| Auth | `/api/v1/auth` | `auth_service.py` |
| User Profile | `/api/v1/profile` | Inline (profile router) |
| Projects & Tasks | `/api/v1/projects` | — |
| AI Agents | `/api/v1/ai` | `agent_executor.py`, `agent_registry.py` |
| Missions | `/api/v1/missions` | `mission_runtime.py`, `mission_engine.py` |
| GitHub | `/api/v1/github` | `github_service.py` |
| Memory | `/api/v1/memory` | `memory_service.py` |
| Tools | `/api/v1/tools` | `tool_registry.py` |
| Builder | `/api/v1/builder` | `builder_agent.py` |
| Billing | `/api/v1/billing` | `billing_policy.py` |
| Analytics | `/api/v1/analytics` | — |
| Organisation | `/api/v1/org` | — |
| LLM Provider | `/api/v1/provider` | `llm_provider.py` |
| Webhooks | `/api/v1/webhooks` | `merge_guard.py`, `post_merge_sync.py` |

### 3.4 Intelligence Layer

| Module | File | Responsibility |
|--------|------|----------------|
| Adaptive Planner | `intelligence/adaptive_planning.py` | Dynamic mission replanning |
| Consensus Engine | `intelligence/consensus_engine.py` | Multi-agent debate and consensus |
| Learning Loop | `intelligence/learning_loop.py` | Outcome-based memory refinement |
| Policy Engine | `intelligence/policy_engine.py` | Agent behavior constraints |
| Risk Analyzer | `intelligence/risk_analysis.py` | Mission-level risk scoring |
| Task Parser | `intelligence/task_parser.py` | NLP → structured task decomposition |

---

## 4. Data Model (Entity-Relationship Overview)

```
User ──────────────── OrganizationMember ──── Organization
 │  (is_active, email,    (role: OWNER/ADMIN/   (credit_balance,
 │   hashed_pw, avatar,    MEMBER/VIEWER)        stripe_customer_id)
 │   google_id, github_id)
 │
 ├── Project ──── Task ──── Activity
 │       │          └── TaskStatus, ActivityType
 │       ├── ProjectMemory
 │       └── Notification
 │
 ├── Mission ──── MissionPlan ──── Milestone
 │       │         WorkflowPhase     MilestoneStatus
 │       │         ApprovalStatus
 │       │
 │       └── AgentRun ──── Agent ──── ApprovalRequest
 │                          AgentType   ApprovalStatus
 │
 ├── GitHubConnection ── Repository ── Branch ── Snapshot
 │                                └── PullRequest ── Changeset
 │
 ├── Finding, DebateLog
 ├── AgentMessage, AgentArtifact, AgentDecision, AgentLesson
 ├── ProcessMemory, ProcessOutcomeRecord, IntelligenceTrace
 ├── MemoryUsefulness, HumanFeedback, ContextEfficiencyRecord
 ├── TaskContract
 ├── AgentMemory, MissionMemory
 └── CustomTool
```

### Key Model Properties (Base)
All models extend `backend/models/base.py::Base`:
- `id: str` — UUID v4 primary key
- `created_at: datetime` — UTC timezone-aware
- `updated_at: datetime` — UTC, auto-updated via `onupdate`

---

## 5. API Surface — User Profile Sub-System (New)

### 5.1 Endpoints

| Method | Path | Auth | Status Codes | Description |
|--------|------|------|-------------|-------------|
| GET | `/api/v1/profile/` | JWT/Cookie | 200, 401 | Retrieve profile |
| PUT | `/api/v1/profile/` | JWT/Cookie | 200, 400, 401, 422 | Update profile / change password |
| DELETE | `/api/v1/profile/` | JWT/Cookie | 200, 401 | Soft-delete account |
| POST | `/api/v1/profile/change-password` | JWT/Cookie | 200, 400, 401, 422 | Dedicated password change |

### 5.2 Schema Contracts

```
ProfileResponse {
  id: str, email: EmailStr, full_name: str|null,
  avatar_url: str|null, is_active: bool,
  created_at: datetime, updated_at: datetime
}

ProfileUpdateRequest {
  full_name?: str[1-120], avatar_url?: HttpUrl,
  current_password?: str, new_password?: str[8-128]
  RULE: new_password requires current_password
}

PasswordChangeRequest {
  current_password: str, new_password: str[8-128],
  confirm_new_password: str
  RULE: new_password == confirm_new_password
}
```

### 5.3 Auth Flow

```
Request → RateLimiterMiddleware
        → CORSMiddleware
        → Router.endpoint()
        → get_current_active_user dependency
           ├── cookie: kobits_session → JWT decode → User lookup
           ├── Authorization: Bearer <token> → JWT decode → User lookup
           ├── KOBITS_DEV_MODE=true → first User in DB (bypass)
           └── is_active=False → HTTP 401 "Account is inactive."
```

---

## 6. Security Architecture

### 6.1 Current Posture

| Control | Implementation | Status |
|---------|---------------|--------|
| Password hashing | bcrypt (rounds=12) | ✅ Production-grade |
| Legacy hash compat | SHA-256+salt backward compat | ✅ Migration path |
| JWT signing | HS256, `sub` = user_id | ⚠️ Key too short (26 bytes) |
| Session cookie | HttpOnly, SameSite=lax | ✅ |
| HTTPS enforcement | SESSION_COOKIE_SECURE (False by default) | ⚠️ Must be True in prod |
| Rate limiting | 100 req/60s per IP (in-memory) | ⚠️ Not Redis-backed |
| CORS | Allow-list + regex | ✅ |
| Auth bypass | KOBITS_DEV_MODE env flag | ⚠️ Must never reach prod |
| Inactive user guard | is_active check in deps.py | ✅ NEW (Nexus) |
| SQL injection | SQLAlchemy ORM parameterized | ✅ |
| OAuth state | In-memory dict | ⚠️ Not multi-instance safe |

### 6.2 Threat Model

| Threat | Mitigation | Gap |
|--------|-----------|-----|
| Brute-force password | Rate limiter (global, not endpoint-specific) | No per-endpoint throttle on auth routes |
| Session hijacking | HttpOnly cookie, SameSite=lax | No CSRF token for state-mutating endpoints |
| JWT secret exposure | Load from env | Weak default still present |
| Account takeover via password reset | N/A — no reset flow yet | Missing password-reset endpoint |
| GDPR data erasure | Soft-delete only | No hard delete / scheduled purge |
| OAuth state CSRF | In-memory state token | Must move to Redis for multi-pod |

---

## 7. Data Flow Diagrams

### 7.1 Authenticated Request Flow

```
Client
  │
  ├─[GET /api/v1/profile/]──────────────────────────────┐
  │                                                      ▼
  │                                          RateLimiterMiddleware
  │                                                      │
  │                                          CORSMiddleware
  │                                                      │
  │                                          FastAPI Router
  │                                                      │
  │                                     get_current_active_user
  │                                         ├── Extract token
  │                                         │   (cookie → Bearer → None)
  │                                         ├── KOBITS_DEV_MODE check
  │                                         ├── JWT decode (PyJWT)
  │                                         ├── DB lookup (User by id)
  │                                         └── is_active guard
  │                                                      │
  │                                         get_profile handler
  │                                         ProfileResponse.model_validate()
  │                                                      │
  └─[200 + ProfileResponse JSON]◄───────────────────────┘
```

### 7.2 Mission Execution Flow

```
User → POST /api/v1/missions/
          │
          ▼
    MissionRuntime.execute()
          │
          ├── AdaptivePlanner → task decomposition
          ├── RiskAnalyzer → risk scoring
          ├── OrchestratorDecision → agent selection
          │
          ├──[for each task]──────────────────────────────┐
          │                                               ▼
          │                                    AgentExecutor.run()
          │                                         │
          │                                    ToolRegistry.dispatch()
          │                                         │
          │                                    SandboxManager
          │                                    (git worktree isolation)
          │                                         │
          │                                    _verify_sandbox_code()
          │                                    (syntax + import + test gate)
          │                                         │
          │                                    LearningLoop.record()
          │◄──────────────────────────────────────┘
          │
    WebSocket broadcast → /ws/missions/{id}
    Activity log → DB
```

---

## 8. Scalability Architecture

### 8.1 Current (Single-Node SQLite)

```
[FastAPI Process]
       │
[SQLite WAL mode]    ← aiosqlite + WAL + busy_timeout=30s
       │              + 64MB page cache + NORMAL sync
[File System]
```

**Suitable for:** <1,000 concurrent users, single mission at a time.

### 8.2 Target (Multi-Node PostgreSQL + Redis)

```
[Load Balancer]
       │
  ┌────┴────┐
[API Pod 1] [API Pod 2]      ← K8s Deployment, N replicas
       │
[PostgreSQL (RDS/Cloud SQL)]  ← asyncpg, pool_size=20, max_overflow=10
       │
[Redis Cluster]               ← OAuth state, rate-limit windows, worker queues
       │
[Worker Pods]                 ← Mission execution isolation
```

### 8.3 Migration Path

| Phase | Action | Owner |
|-------|--------|-------|
| 1 | Replace `DATABASE_URL` → `postgresql+asyncpg://...` | DevOps |
| 2 | Run Alembic migrations against PostgreSQL | Backend |
| 3 | Replace `_oauth_states` dict → Redis Hash with TTL | Backend |
| 4 | Move rate-limiter to Redis INCR+EXPIRE | Backend |
| 5 | Deploy `worker_queue.py` as dedicated pod | DevOps |
| 6 | Add horizontal scaling of API pods | DevOps |

---

## 9. Test Architecture

### 9.1 Test Isolation Model

```
tests/conftest.py:
  setup_db (autouse, function scope):
    ├── Base.metadata.create_all   [before test]
    └── Base.metadata.drop_all     [after test]

  isolate_test_state (autouse):
    ├── Save env vars + settings
    ├── Clear SandboxManager._sessions
    └── Restore on teardown
```

**Known Issue (Pre-existing):** When `test_profile.py` and `test_analytics.py` run in the same pytest session, the `registered_user` fixture state leaks across tests because the DB is shared within the same process (SQLite file-based). The `test_analytics.py` fixture uses hardcoded IDs (`demo_org`) that cause `UNIQUE constraint` errors. This is NOT caused by the Nexus profile changes — it predates them.

**Resolution Pattern:** Each test module that seeds its own fixtures should use `uuid4()` for IDs, not hardcoded strings.

### 9.2 Profile Test Coverage (11/11 passing in isolation)

| Test | Coverage |
|------|---------|
| test_get_profile_unauthenticated | 401 guard |
| test_get_profile_authenticated | Happy path GET |
| test_update_profile_full_name | PUT display name |
| test_update_profile_no_fields | 422 validation |
| test_update_profile_password_change_success | Password change + re-login |
| test_update_profile_password_wrong_current | 400 bad credentials |
| test_update_profile_new_password_without_current | 422 schema validation |
| test_change_password_success | Dedicated endpoint |
| test_change_password_mismatch | 422 confirm mismatch |
| test_change_password_wrong_current | 400 bad credentials |
| test_delete_account | Soft-delete + 401 on subsequent request |

---

## 10. Configuration & Environment

### 10.1 Settings Reference (`backend/core/config.py`)

| Setting | Default | Production Requirement |
|---------|---------|----------------------|
| `SECRET_KEY` | `super-secret-key-change-me` | **MUST** be 32+ random bytes |
| `DATABASE_URL` | `sqlite+aiosqlite:///./kobits.db` | PostgreSQL URL |
| `KOBITS_DEV_MODE` | `False` | **MUST** be `False` |
| `SESSION_COOKIE_SECURE` | `False` | **MUST** be `True` |
| `REDIS_URL` | `None` | Required for multi-node |
| `WORKER_CONCURRENCY` | `2` | Scale per CPU |
| `ALGORITHM` | `HS256` | Consider RS256 for multi-service |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `10080` (1 week) | Consider shorter for high-security |

### 10.2 Required Environment Variables (Production)

```bash
SECRET_KEY=<base64-encoded-32+-random-bytes>
DATABASE_URL=postgresql+asyncpg://user:pass@host/kobits
REDIS_URL=redis://redis-host:6379/0
SESSION_COOKIE_SECURE=true
KOBITS_DEV_MODE=false
STRIPE_SECRET_KEY=sk_live_...
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

---

## 11. Recommended Architecture Improvements (Priority-Ordered)

### P0 — Security Hardening (Pre-Production Blockers)

1. **Rotate SECRET_KEY** — Generate with `python -c "import secrets; print(secrets.token_hex(32))"` and load from env only. Remove hardcoded default.
2. **SESSION_COOKIE_SECURE=True** — Enforce HTTPS-only cookie in all non-local deployments.
3. **Endpoint-specific rate limiting** — Add per-IP throttle on `/api/v1/auth/login`, `/api/v1/auth/register`, and `/api/v1/profile/change-password` (max 5 attempts / 15 min).
4. **KOBITS_DEV_MODE guard** — Add a startup assertion that fails if `KOBITS_DEV_MODE=true` AND `DATABASE_URL` points to production.

### P1 — Feature Completeness

5. **Password Reset Flow** — `POST /api/v1/auth/forgot-password` → email token → `POST /api/v1/auth/reset-password`.
6. **Email Verification** — Require email confirmation before allowing sensitive operations (password change, account deletion).
7. **GDPR Hard-Delete** — `DELETE /api/v1/profile/?confirm=PERMANENTLY_DELETE` that wipes PII columns and sets `email = deleted_{uuid}@kobits.void`.
8. **Password Strength Validation** — Integrate `zxcvbn` in `PasswordChangeRequest` and `ProfileUpdateRequest` schemas; reject passwords scoring < 3.

### P2 — Scalability & Resilience

9. **Redis OAuth State Store** — Replace `_oauth_states` dict in `auth.py` with Redis HSET/TTL for multi-pod safety.
10. **Redis Rate Limiter** — Replace `RateLimiterMiddleware` in-memory dict with Redis INCR/EXPIRE for accurate multi-instance limiting.
11. **Alembic Migrations** — Generate and commit migration scripts for all current models (alembic/ directory exists but no revision files found).
12. **Health Check Endpoint** — Add `GET /api/health` that returns DB connection status, Redis ping, and version info.

### P3 — Observability & Compliance

13. **Audit Log** — Create `AuditLog` model and write entries for: login, password change, account deletion, OAuth link/unlink.
14. **OpenAPI Enrichment** — Add `openapi_extra` with request/response examples on all profile endpoints.
15. **Frontend Integration** — Wire profile GET/PUT into `portal.html` dashboard settings panel.
16. **Token Refresh** — Implement `POST /api/v1/auth/refresh` sliding-window token renewal to reduce 1-week token lifetime risk.

---

## 12. Dependency Graph

```
backend/main.py
  └─ backend/core/config.py          (Settings, pydantic-settings)
  └─ backend/core/database.py        (SQLAlchemy async engine)
  └─ backend/core/security.py        (bcrypt, PyJWT)
  └─ backend/models/base.py
  └─ backend/models/organization.py  (User, Organization, OrganizationMember)
  └─ backend/models/project.py
  └─ backend/models/mission.py
  └─ ... (all other models)
  └─ backend/api/deps.py             (get_current_active_user — cross-cutting)
  └─ backend/api/v1/auth.py
  └─ backend/api/v1/profile.py       ← NEW (Nexus)
  └─ backend/schemas/profile.py      ← NEW (Nexus)
  └─ ... (all other routers)
```

---

## 13. Frontend API Integration Points

| UI Page | Endpoint | Action |
|---------|---------|--------|
| `portal.html` — Settings | `GET /api/v1/profile/` | Load current user info |
| `portal.html` — Settings | `PUT /api/v1/profile/` | Update name / avatar |
| `portal.html` — Security | `POST /api/v1/profile/change-password` | Change password |
| `portal.html` — Danger Zone | `DELETE /api/v1/profile/` | Deactivate account |
| `portal.html` — Login | `POST /api/v1/auth/login` | Credential login |
| `portal.html` — OAuth | `GET /api/v1/auth/google` | Google SSO |
| `portal.html` — OAuth | `GET /api/v1/auth/github` | GitHub SSO |

---

*Architecture document auto-generated by SOLUTION_ARCHITECT Agent based on full codebase inspection and upstream Nexus agent artifacts.*