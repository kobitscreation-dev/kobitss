# Secure User Profile System — Architecture Design Document

**Status:** Approved  
**Version:** 1.0  
**Author:** Solution Architect Agent (Kobits Autonomous Platform)  
**Based on:** Nexus Agent Implementation Report — 11/11 Tests Passing  

---

## 1. Executive Summary

The Kobits platform implements a **JWT-authenticated, soft-delete–safe, bcrypt-hardened** user profile subsystem. This document defines the complete architectural picture: service decomposition, data flows, API contracts, security layers, scalability patterns, and forward-looking enhancement roadmap derived from the verified Nexus implementation.

---

## 2. System Context Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                                │
│  Browser (HttpOnly Cookie)    │    API Client (Bearer Token)        │
└──────────────┬────────────────┴──────────────┬──────────────────────┘
               │                               │
               ▼                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     TRANSPORT / SECURITY LAYER                       │
│   TLS 1.3  │  CORS Middleware  │  RateLimiterMiddleware (100/60s)   │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       FASTAPI APPLICATION                            │
│                                                                      │
│   ┌─────────────────────┐    ┌────────────────────────────────────┐ │
│   │  /api/v1/auth/*      │    │      /api/v1/profile/*             │ │
│   │  (Registration,      │    │  GET  /                            │ │
│   │   Login, OAuth,      │    │  PUT  /                            │ │
│   │   Password Reset)    │    │  DELETE /                          │ │
│   └──────────┬───────────┘    │  POST /change-password             │ │
│              │                └──────────────┬─────────────────────┘ │
│              │                               │                       │
│              ▼                               ▼                       │
│   ┌────────────────────────────────────────────────────────────────┐ │
│   │              AUTH DEPENDENCY CHAIN                             │ │
│   │  get_current_active_user → JWT decode → DB user lookup         │ │
│   │  → is_active check → User ORM object injected                  │ │
│   └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│   ┌────────────────────────────────────────────────────────────────┐ │
│   │              SECURITY SERVICES                                 │ │
│   │  bcrypt(rounds=12)  │  HS256-JWT  │  Legacy SHA-256 fallback   │ │
│   └────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        DATA LAYER                                    │
│                                                                      │
│  SQLAlchemy AsyncSession (aiosqlite/asyncpg)                        │
│  ┌──────────┐  ┌────────────────┐  ┌────────────────────────────┐  │
│  │  users   │  │ organizations  │  │     audit_logs              │  │
│  │          │  │                │  │  (append-only, immutable)   │  │
│  └──────────┘  └────────────────┘  └────────────────────────────┘  │
│                                                                      │
│  SQLite WAL (dev) ←──────────────────────→ PostgreSQL (prod)        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Inventory

| Component | File | Responsibility |
|---|---|---|
| Profile Router | `backend/api/v1/profile.py` | HTTP route handlers for all profile operations |
| Auth Dependency | `backend/api/deps.py` | JWT resolution, user lookup, is_active guard |
| Security Primitives | `backend/core/security.py` | bcrypt hash/verify, JWT encode/decode |
| Profile Schemas | `backend/schemas/profile.py` | Pydantic v2 request/response validation |
| User Model | `backend/models/organization.py` | SQLAlchemy User ORM (users table) |
| Audit Model | `backend/models/audit_log.py` | Immutable event trail (audit_logs table) |
| Config | `backend/core/config.py` | Pydantic Settings (env-driven, .env file) |
| Database | `backend/core/database.py` | Async engine, session factory, WAL pragmas |
| Rate Limiter | `backend/main.py::RateLimiterMiddleware` | Sliding-window IP-based rate limiting |
| Integration Tests | `tests/test_profile.py` | 11-test HTTPX AsyncClient test suite |

---

## 4. API Contract Specification

### 4.1 Authentication Scheme

All profile endpoints require one of:
- **Cookie:** `kobits_session=<JWT>` (HttpOnly, set by `/api/v1/auth/login`)
- **Header:** `Authorization: Bearer <JWT>`

Token format: HS256 JWT with `{ "sub": "<user_id>", "exp": <unix_ts>, "email": "<email>" }`

### 4.2 Route Definitions

```
GET    /api/v1/profile/
  Auth: Required
  Response 200: ProfileResponse { id, email, full_name, avatar_url, is_active, created_at, updated_at }
  Response 401: { "detail": "Not authenticated" | "Account is inactive." }

PUT    /api/v1/profile/
  Auth: Required
  Body: ProfileUpdateRequest { full_name?, avatar_url?, current_password?, new_password? }
  Constraints:
    - At least one field must be non-null → else 422
    - new_password requires current_password → else 422 (schema-level)
    - current_password must match stored hash → else 400
  Response 200: ProfileResponse (updated)
  Response 400: { "detail": "Current password is incorrect." }
  Response 422: { "detail": "No update fields were provided." }

DELETE /api/v1/profile/
  Auth: Required
  Effect: Sets User.is_active = False (soft delete, record preserved)
  Response 200: { "message": "Account deleted successfully." }
  Side effect: All subsequent JWT requests for this user return 401

POST   /api/v1/profile/change-password
  Auth: Required
  Body: PasswordChangeRequest { current_password, new_password, confirm_new_password }
  Constraints:
    - new_password == confirm_new_password → else 422 (schema-level)
    - current_password must match stored hash → else 400
  Response 200: { "message": "Password updated successfully." }
  Response 400: { "detail": "Current password is incorrect." }
  Response 422: (schema validation failures)
```

### 4.3 Schema Definitions

```python
# ProfileResponse — Read-only projection of User ORM
ProfileResponse:
  id:         str (UUID)
  email:      EmailStr
  full_name:  Optional[str]
  avatar_url: Optional[str]
  is_active:  bool
  created_at: datetime (UTC)
  updated_at: datetime (UTC)

# ProfileUpdateRequest — Partial update payload
ProfileUpdateRequest:
  full_name:        Optional[str]  # 1-120 chars
  avatar_url:       Optional[HttpUrl]
  current_password: Optional[str]  # Required with new_password
  new_password:     Optional[str]  # 8-128 chars
  [model_validator]: new_password → current_password required

# PasswordChangeRequest — Dedicated change-password payload
PasswordChangeRequest:
  current_password:     str
  new_password:         str  # 8-128 chars
  confirm_new_password: str
  [model_validator]: new_password == confirm_new_password
```

---

## 5. Security Architecture

### 5.1 Authentication Flow

```
Client Request
    │
    ▼
get_current_active_user(request, db)
    │
    ├── Extract token from: cookie → Authorization header → DEV_MODE bypass
    │
    ▼
jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    │
    ├── InvalidTokenError → 401
    ├── sub = None → 401
    │
    ▼
SELECT * FROM users WHERE id = sub
    │
    ├── Not found → try email fallback → ephemeral rehydration
    ├── Still None → 401
    │
    ▼
user.is_active check
    │
    ├── is_active = False → 401 "Account is inactive."
    │
    ▼
User object injected into route handler
```

### 5.2 Password Security Layers

| Layer | Mechanism | Details |
|---|---|---|
| Storage | bcrypt (rounds=12) | Adaptive cost, salt auto-generated per hash |
| Verification | bcrypt.checkpw | Constant-time comparison via libsodium |
| Legacy fallback | SHA-256 + salt | Reads `salt$hash` format; triggers re-hash on next login (recommended) |
| Transport | HTTPS + HttpOnly cookie | Prevents JS extraction of session tokens |
| Brute force | Rate Limiter (100 req/60s) | IP-based sliding window; recommended: tighten to 5/15min on change-password |

### 5.3 Token Security

| Property | Value | Risk |
|---|---|---|
| Algorithm | HS256 | Symmetric — SECRET_KEY must be secret |
| Key length | ≥32 bytes (enforced at startup) | Currently 26 bytes in test env (WARNING) |
| Expiry | 7 days (ACCESS_TOKEN_EXPIRE_MINUTES=10080) | Long-lived; mitigate with refresh rotation |
| Cookie flags | HttpOnly=True, SameSite=lax, Secure=True (prod) | Prevents XSS token theft |
| Revocation | None (stateless JWT) | Soft-delete deactivates user, next request 401 |

### 5.4 Audit Trail

The `AuditLog` model (append-only) captures:
- `profile.update` — field changes
- `profile.password_change` — password mutations
- `account.deactivate` — soft-delete events

**Architecture note:** The audit log write is currently not wired into the profile endpoints. See Section 8.1 for the recommended enhancement.

---

## 6. Data Model

### 6.1 Entity Relationship Diagram

```
┌──────────────────────────────────────┐
│              users                   │
│──────────────────────────────────────│
│ id            : VARCHAR (UUID) PK    │
│ email         : VARCHAR UNIQUE INDEX │
│ hashed_password: VARCHAR NULLABLE    │
│ full_name     : VARCHAR NULLABLE     │
│ is_active     : BOOLEAN DEFAULT TRUE │
│ google_id     : VARCHAR UNIQUE NULL  │
│ github_id     : VARCHAR UNIQUE NULL  │
│ avatar_url    : VARCHAR NULLABLE     │
│ created_at    : DATETIME TZ          │
│ updated_at    : DATETIME TZ          │
└──────────────────────────────────────┘
         │1
         │
         │N
┌────────────────────────────────────────┐
│         organization_members           │
│────────────────────────────────────────│
│ id             : VARCHAR (UUID) PK     │
│ user_id        : FK → users.id CASCADE │
│ organization_id: FK → orgs.id CASCADE  │
│ role           : ENUM(OWNER,ADMIN,...) │
└────────────────────────────────────────┘

┌──────────────────────────────────────┐
│           audit_logs                 │
│──────────────────────────────────────│
│ id          : VARCHAR (UUID) PK      │
│ user_id     : VARCHAR NULLABLE INDEX │
│ event       : VARCHAR(128) INDEX     │
│ ip_address  : VARCHAR(64) NULLABLE   │
│ user_agent  : VARCHAR(512) NULLABLE  │
│ detail      : TEXT NULLABLE          │
│ occurred_at : DATETIME TZ NOT NULL   │
│ created_at  : DATETIME TZ            │
│ updated_at  : DATETIME TZ            │
└──────────────────────────────────────┘
```

### 6.2 Soft-Delete Pattern

```
Before DELETE /api/v1/profile/:
  users.is_active = True

After DELETE /api/v1/profile/:
  users.is_active = False
  → All subsequent requests return 401 via get_current_active_user()
  → Record preserved for: audit trail, billing reconciliation, GDPR export window

GDPR Hard-Delete Schedule (PENDING IMPLEMENTATION):
  - Cron job at T+30 days after is_active=False
  - DELETE FROM users WHERE is_active=False AND updated_at < NOW() - INTERVAL '30 days'
  - Log AuditEvent.ACCOUNT_HARD_DELETE before execution
```

---

## 7. Dependency Graph

```
test_profile.py
    └── backend.main (FastAPI app)
            ├── backend.api.v1.profile (router)
            │       ├── backend.api.deps.get_current_active_user
            │       │       ├── backend.core.database.get_db
            │       │       ├── backend.models.organization.User
            │       │       └── backend.core.config.settings
            │       ├── backend.core.security.{verify_password, get_password_hash}
            │       │       └── backend.core.config.settings (SECRET_KEY, ALGORITHM)
            │       └── backend.schemas.profile.{ProfileResponse, ProfileUpdateRequest, PasswordChangeRequest}
            │
            └── backend.core.database.engine (SQLite WAL / PostgreSQL)
                    └── backend.models.base.Base (all table metadata)
```

---

## 8. Recommended Enhancements

### 8.1 Priority 1 — Security Hardening (P0)

#### 8.1.1 Enforce 32-byte SECRET_KEY at Startup

**Current state:** 26-byte key in test env raises `InsecureKeyLengthWarning`.  
**Target:** Startup guard that raises `RuntimeError` if key < 32 bytes in non-SQLite environments.

```python
# In backend/core/config.py — add validator
from pydantic import field_validator

@field_validator("SECRET_KEY")
@classmethod
def validate_secret_key_length(cls, v: str) -> str:
    import os
    db_url = os.environ.get("DATABASE_URL", "")
    if "postgresql" in db_url.lower() and len(v.encode()) < 32:
        raise ValueError(
            f"SECRET_KEY must be ≥32 bytes in production (got {len(v.encode())}). "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return v
```

#### 8.1.2 Legacy Hash Re-upgrade on Login

**Current state:** Legacy SHA-256 hashes are readable but never upgraded.  
**Target:** On successful verify_password via legacy path → silently upgrade to bcrypt.

```python
# Pattern to add to auth_service login path:
def verify_and_upgrade(plain: str, stored: str, user: User, db) -> bool:
    if not stored.startswith("$2"):
        if _legacy_verify(plain, stored):
            user.hashed_password = get_password_hash(plain)  # upgrade
            return True
        return False
    return bcrypt.checkpw(plain.encode(), stored.encode())
```

### 8.2 Priority 2 — Rate Limiting on Change-Password (P1)

**Current state:** `RateLimiterMiddleware` is global (100 req/60s per IP).  
**Target:** Route-specific rate limit of 5 attempts per 15 minutes per **user ID** (not IP, to prevent lockout bypass via IP rotation).

```
Architecture: Add UserRateLimiter service
  - Store: Redis (if REDIS_URL set) or in-memory defaultdict
  - Key: f"rate_limit:change_password:{user.id}"
  - Window: 15 minutes
  - Limit: 5 attempts
  - On exceed: raise HTTP 429 with Retry-After header
```

### 8.3 Priority 3 — Refresh Token Rotation (P1)

**Current state:** 7-day access tokens with no rotation mechanism.  
**Target:** Short-lived access tokens (15 min) + long-lived refresh tokens (30 days) with single-use rotation.

```
New endpoints:
  POST /api/v1/auth/refresh
    - Accepts: refresh_token (HttpOnly cookie)
    - Returns: new access_token + rotated refresh_token
    - Invalidates: old refresh token (stored in password_reset_tokens table or new refresh_tokens table)

New DB table: refresh_tokens
  - id: UUID PK
  - user_id: FK → users.id
  - token_hash: VARCHAR (SHA-256 of the raw token)
  - expires_at: DATETIME
  - used_at: DATETIME NULLABLE (rotation detection)
  - created_at: DATETIME
```

### 8.4 Priority 4 — Avatar Upload Endpoint (P2)

```
POST /api/v1/profile/avatar
  Auth: Required
  Content-Type: multipart/form-data
  Field: file (image/jpeg, image/png, image/webp ≤ 5MB)
  
  Architecture:
    1. Validate MIME type + file size
    2. Generate deterministic key: f"avatars/{user_id}/{uuid4()}.webp"
    3. Convert to WebP (Pillow) for storage efficiency
    4. Upload to: S3 (prod) / local /storage/avatars (dev)
    5. Update users.avatar_url
    6. Return: ProfileResponse with new avatar_url
  
  Storage service interface:
    class AvatarStorageService:
        async def upload(self, user_id: str, file_bytes: bytes) -> str: ...
        async def delete(self, url: str) -> None: ...
```

### 8.5 Priority 5 — Audit Log Integration (P2)

**Current state:** `AuditLog` model and `AuditEvent` constants exist but are not called from profile endpoints.  
**Target:** Wire audit writes into every profile mutation.

```python
# In profile.py — add after each successful mutation:
from backend.models.audit_log import AuditLog, AuditEvent

async def _write_audit(db, user_id, event, request, detail=None):
    entry = AuditLog(
        user_id=user_id,
        event=event,
        ip_address=_get_ip(request),
        user_agent=request.headers.get("user-agent", "")[:512],
        detail=detail,
    )
    db.add(entry)
    # No commit — caller commits the parent transaction

# GET profile → no audit (read-only)
# PUT profile → AuditEvent.PROFILE_UPDATE (with changed fields, excluding passwords)
# PUT profile (password) → AuditEvent.PASSWORD_CHANGE
# POST change-password → AuditEvent.PASSWORD_CHANGE
# DELETE profile → AuditEvent.ACCOUNT_DEACTIVATE
```

### 8.6 Priority 6 — OpenAPI Security Scheme (P2)

**Current state:** Swagger UI has no "Authorize" button for profile routes.  
**Target:** Add OAuth2PasswordBearer scheme to FastAPI app startup.

```python
# In backend/main.py
from fastapi.security import OAuth2PasswordBearer

app = FastAPI(
    ...
    swagger_ui_parameters={"persistAuthorization": True},
)
# The oauth2_scheme in deps.py already declares tokenUrl;
# this just makes it visible in the Swagger UI
```

---

## 9. Scalability Architecture

### 9.1 SQLite → PostgreSQL Migration Path

| Concern | SQLite (Dev) | PostgreSQL (Prod) |
|---|---|---|
| Concurrency | WAL mode, 30s timeout | Connection pool (20+10 overflow) |
| Write throughput | Single-writer WAL | MVCC, parallel writes |
| Session cookie | SameSite=lax, Secure=False | SameSite=lax, Secure=True |
| Test isolation | File-per-run (test_kobits.db) | Recommend `file::memory:?cache=shared` |
| Migrations | alembic (manual) | alembic with auto-revision on deploy |

### 9.2 Horizontal Scaling Considerations

```
Multiple FastAPI Instances (e.g. Cloud Run, ECS):
  - JWT is stateless → scales without sticky sessions
  - RateLimiterMiddleware uses in-memory dict → does NOT scale horizontally
    → Fix: Replace with Redis-backed sliding window (REDIS_URL already in Settings)
  - OAuth state tokens are in _oauth_states dict → same problem
    → Fix: Already partially addressed (JWT-signed stateless state tokens)
  - bcrypt is CPU-bound (rounds=12 ≈ 200ms) → profile change-password will block
    → Fix: run_in_executor(None, bcrypt.hashpw, ...) or reduce rounds to 10 in prod
```

### 9.3 Database Connection Pool Tuning

```
Current (backend/core/database.py):
  pool_size=20, max_overflow=10, pool_pre_ping=True, pool_recycle=3600

Recommended for Profile service (high read, low write):
  pool_size=10  # Profiles are cached at CDN layer in future
  max_overflow=5
  pool_timeout=30
  statement_cache_size=0  # For asyncpg compatibility
```

---

## 10. Test Coverage Map

| Test ID | Route | Scenario | Expected |
|---|---|---|---|
| 1 | GET / | No token | 401 |
| 2 | GET / | Valid JWT | 200 + profile fields |
| 3 | PUT / | full_name update | 200 + updated name |
| 4 | PUT / | Empty payload | 422 |
| 5 | PUT / | Password change (correct current) | 200 + login with new pw |
| 6 | PUT / | Password change (wrong current) | 400 |
| 7 | PUT / | new_password without current | 422 |
| 8 | POST /change-password | Valid change | 200 |
| 9 | POST /change-password | new ≠ confirm | 422 |
| 10 | POST /change-password | Wrong current | 400 |
| 11 | DELETE / | Soft-delete → subsequent GET | 200 → 401 |

**Coverage gaps to add:**
- PATCH /avatar (not yet implemented)
- GET / after token expiry → 401
- Rate limit test (POST /change-password × 6) → 429
- PUT / with avatar_url (currently untested)
- PUT / with both full_name + new_password in same request

---

## 11. CI/CD Integration

```yaml
# Recommended .github/workflows/profile_tests.yml additions:

- name: Clean test database artifacts
  run: |
    rm -f test_kobits.db test_kobits.db-shm test_kobits.db-wal
    rm -f test_analytics.db test_workflow_e2e.db

- name: Run profile integration tests
  run: pytest tests/test_profile.py -v --timeout=60 --tb=short

- name: Enforce SECRET_KEY length in CI
  run: |
    python -c "
    import os, sys
    key = os.environ.get('SECRET_KEY', '')
    if len(key.encode()) < 32:
      print(f'ERROR: SECRET_KEY too short ({len(key.encode())} bytes)')
      sys.exit(1)
    print('SECRET_KEY length OK')
    "
  env:
    SECRET_KEY: ${{ secrets.SECRET_KEY }}
```

---

## 12. Risk Register

| ID | Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | SECRET_KEY < 32 bytes in production | HIGH | LOW (warning exists) | Add startup validator that hard-fails for PostgreSQL env |
| R2 | PII retained after soft-delete (GDPR) | HIGH | MEDIUM | Schedule GDPR hard-delete cron (T+30 days) |
| R3 | Legacy SHA-256 hash never upgraded | MEDIUM | MEDIUM | Upgrade hash on successful legacy login |
| R4 | RateLimiter not IP-per-user for change-password | MEDIUM | HIGH | Implement Redis-backed user-ID rate limit |
| R5 | 7-day JWT without refresh rotation | MEDIUM | HIGH | Implement refresh token table + rotation endpoint |
| R6 | Corrupt WAL test files from production spill | LOW | MEDIUM | CI pre-test cleanup step (Nexus fix applied) |
| R7 | bcrypt blocks event loop (rounds=12 ≈ 200ms) | LOW | LOW | run_in_executor for hash operations |
| R8 | In-memory OAuth state does not scale horizontally | MEDIUM | HIGH | Leverage stateless JWT state tokens already partially implemented |

---

## 13. Architecture Decision Records (ADRs)

### ADR-001: Soft-Delete over Hard-Delete
**Decision:** `DELETE /api/v1/profile/` sets `is_active=False` instead of deleting the row.  
**Rationale:** Preserves audit trail, billing reconciliation data, and foreign key integrity across missions/projects.  
**Consequence:** GDPR hard-delete job required; PII must not remain after retention period.

### ADR-002: JWT Stored in HttpOnly Cookie + Bearer Header Fallback
**Decision:** Token extracted from `kobits_session` cookie first, then `Authorization: Bearer`.  
**Rationale:** Web sessions use HttpOnly cookies (XSS-resistant); API clients use Bearer headers.  
**Consequence:** CSRF protection must be applied for cookie-based sessions (SameSite=lax handles same-site, but cross-site POST requires CSRF token for banking-grade security).

### ADR-003: bcrypt with Rounds=12
**Decision:** New passwords hashed with bcrypt rounds=12 (~200ms on modern hardware).  
**Rationale:** Industry standard for interactive login; rounds=12 yields 2^12 iterations providing strong brute-force resistance.  
**Consequence:** CPU-bound; high-concurrency deployments should offload to thread pool executor.

### ADR-004: Model-Level Pydantic Validators for Password Pair Rules
**Decision:** Password pair invariants enforced at Pydantic `model_validator` level, not in router.  
**Rationale:** Validation failures return 422 from FastAPI before route handler executes; router handles only business logic 400s (wrong current password).  
**Consequence:** Clean separation between schema validation (422) and business logic (400).

### ADR-005: Single Shared `RateLimiterMiddleware` at App Level
**Decision:** Global middleware rate-limits all routes rather than per-route decorators.  
**Rationale:** Simpler to configure; protects entire API surface without per-route boilerplate.  
**Consequence:** Change-password route needs TIGHTER limits (5/15min per user); current 100/60s per IP is insufficient for password brute-force protection.