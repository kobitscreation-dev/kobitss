"""
Kobits Enterprise Trajectory Harvester (AWS Bedrock Claude Sonnet)
Harvests high-value, production-grade enterprise software engineering trajectories
with strict budget capping ($5–$10), rate-limiting, and instant JSONL streaming.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "training_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TARGET_JSONL = OUTPUT_DIR / "kobits_frontier_v2.jsonl"
STATE_FILE = OUTPUT_DIR / "harvest_state.json"

# Claude Sonnet Pricing on AWS Bedrock (per 1,000,000 tokens)
INPUT_PRICE_PER_M = 3.00
OUTPUT_PRICE_PER_M = 15.00

# BUDGET CAP (Stops automatically when reached)
MAX_BUDGET_USD = 10.00
CONCURRENCY_LIMIT = 2  # Safe for AWS Bedrock quotas without triggering 429 throttling


# =====================================================================
# THE ENTERPRISE SOFTWARE PROMPT MATRIX (KYROS GRADE)
# =====================================================================
ENTERPRISE_PROMPTS = [
    # 1. Stripe & Billing Infrastructure
    {
        "category": "stripe_billing",
        "task": "Design and implement a production Stripe webhook handler in FastAPI that verifies HMAC signatures, handles customer.subscription.updated and customer.subscription.deleted with grace period calculations, and updates organization subscription state in PostgreSQL."
    },
    {
        "category": "stripe_billing",
        "task": "Build a Stripe Checkout and Customer Portal session service in FastAPI with client_reference_id mapping, coupon promotion code support, and metadata tracking for multi-tenant organizations."
    },
    {
        "category": "stripe_billing",
        "task": "Implement an idempotent payment processing service using the transactional outbox pattern to guarantee zero double-charging on network retries."
    },
    {
        "category": "stripe_billing",
        "task": "Build an automated subscription usage metering service that aggregates API request counts and reports usage records to Stripe Metered Billing API."
    },

    # 2. Multi-Tenant RBAC & Security Infrastructure
    {
        "category": "auth_rbac",
        "task": "Implement a Multi-Tenant RBAC authorization engine in FastAPI with organization scoping, role hierarchy (Owner > Admin > Member > Viewer), and JWT claims decoding."
    },
    {
        "category": "auth_rbac",
        "task": "Build an organization member invitation system with cryptographically secure expiring invitation tokens, email invitation dispatching, and role assignment."
    },
    {
        "category": "auth_rbac",
        "task": "Build a JWT authentication service with rotating refresh tokens stored in Redis with revocation blacklisting and device fingerprint tracking."
    },
    {
        "category": "auth_rbac",
        "task": "Implement organization-scoped API key management with bcrypt hashed keys, permission scoping (read/write/admin), and last-used timestamp telemetry."
    },

    # 3. Async Task Workers & Event Queues
    {
        "category": "async_workers",
        "task": "Build an asynchronous task worker in Python using Redis streams with exponential backoff retries (1s, 2s, 4s, 8s) and dead-letter queue (DLQ) routing after 3 failures."
    },
    {
        "category": "async_workers",
        "task": "Implement a bulk CSV data export worker that streams 100,000 database rows in chunks, generates compressed CSV, and uploads to S3 with signed download URLs."
    },
    {
        "category": "async_workers",
        "task": "Build an outgoing webhook delivery dispatcher that signs JSON payloads with HMAC-SHA256, delivers to third-party endpoints with timeout handling, and logs delivery attempts."
    },
    {
        "category": "async_workers",
        "task": "Build an atomic Redis sliding-window rate limiting middleware for FastAPI with IP and API-key tracking, injecting X-RateLimit headers and 429 Retry-After responses."
    },

    # 4. Enterprise Audit Logging & Real-Time Systems
    {
        "category": "audit_telemetry",
        "task": "Implement an enterprise audit logging engine in SQLAlchemy 2.0 with JSON diff tracking (before vs after), actor tracking, IP addresses, and paginated time-series queries."
    },
    {
        "category": "audit_telemetry",
        "task": "Build a real-time WebSocket connection manager in FastAPI with connection pooling, heartbeat ping/pong keep-alives, room broadcasts, and graceful disconnect cleanup."
    },
    {
        "category": "audit_telemetry",
        "task": "Implement a Server-Sent Events (SSE) streaming endpoint in FastAPI for publishing live build and deployment progress logs to frontend dashboards."
    },

    # 5. Async Database Architecture & Migrations
    {
        "category": "database_architecture",
        "task": "Design and implement production SQLAlchemy 2.0 async models for a multi-tenant SaaS platform with Tenant, User, Organization, and APIKey models with foreign keys, composite indexes, and soft-delete mixins."
    },
    {
        "category": "database_architecture",
        "task": "Write an enterprise database connection pool setup using asyncpg and PgBouncer with pool_pre_ping=True, pool recycle timeout, and health check validation."
    },
    {
        "category": "database_architecture",
        "task": "Write a zero-downtime Alembic migration script that adds a new status column with server defaults and creates a concurrent composite index without table locking."
    },

    # 6. Security Hardening & Vulnerability Remediation
    {
        "category": "security_hardening",
        "task": "Audit and fix Insecure Direct Object Reference (IDOR) vulnerabilities across customer invoice and order endpoints by strictly enforcing tenant organization scoping."
    },
    {
        "category": "security_hardening",
        "task": "Implement a production security headers middleware in FastAPI that configures Content Security Policy (CSP), HSTS, X-Frame-Options, and CORS with origin whitelisting."
    },
    {
        "category": "security_hardening",
        "task": "Build an input validation and sanitization pipeline that protects against SQL injection in dynamic query filters and XSS in user profile input."
    },

    # 7. Automated QA Testing & CI/CD
    {
        "category": "qa_testing",
        "task": "Write a comprehensive Pytest test suite with async database fixtures, database transaction rollback after each test, and AsyncClient integration testing."
    },
    {
        "category": "qa_testing",
        "task": "Write an integration test suite for Stripe webhooks using unittest.mock and httpx to verify signature rejection, successful checkout handling, and idempotency."
    },
    {
        "category": "qa_testing",
        "task": "Create a production GitHub Actions CI pipeline (.github/workflows/ci.yml) with Postgres and Redis services, Ruff linting, Mypy type-checking, and Pytest coverage gates."
    }
]


def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"total_spent_usd": 0.0, "total_input_tokens": 0, "total_output_tokens": 0, "completed_prompts": []}


def save_state(state: Dict[str, Any]):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


async def harvest_single_prompt(
    client,
    model: str,
    prompt_item: Dict[str, str],
    semaphore: asyncio.Semaphore,
    state: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    if state["total_spent_usd"] >= MAX_BUDGET_USD:
        return None

    task_desc = prompt_item["task"]
    category = prompt_item["category"]

    sys_prompt = (
        "You are Kobits, an autonomous senior full-stack AI engineering agent. "
        "You design and implement enterprise production-grade software with explicit architectural decomposition, "
        "strict type safety, comprehensive error handling, zero placeholders (no // TODO or pass), "
        "and complete tool executions using `repository_write`."
    )

    user_prompt = (
        f"Task: {task_desc}\n\n"
        "Requirements:\n"
        "1. Start with `### 1. SPECIFICATION & ARCHITECTURAL PLAN` analyzing requirements, edge cases, and security.\n"
        "2. Provide complete multi-file implementations with zero placeholders using `repository_write` tool calls.\n"
        "3. Conclude with `### 2. VERIFICATION & QUALITY AUDIT` summarizing syntax checks and test assertions."
    )

    async with semaphore:
        for attempt in range(4):
            try:
                resp = await client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=sys_prompt,
                    messages=[{"role": "user", "content": user_prompt}]
                )

                content = resp.content[0].text if resp.content else ""
                in_tok = getattr(resp.usage, "input_tokens", 0)
                out_tok = getattr(resp.usage, "output_tokens", 0)

                cost = (in_tok * INPUT_PRICE_PER_M / 1_000_000) + (out_tok * OUTPUT_PRICE_PER_M / 1_000_000)
                state["total_spent_usd"] += cost
                state["total_input_tokens"] += in_tok
                state["total_output_tokens"] += out_tok
                save_state(state)

                if len(content) > 200:
                    record = {
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": task_desc},
                            {"role": "assistant", "content": content}
                        ],
                        "metadata": {
                            "source": "claude_sonnet_enterprise_harvest",
                            "model": model,
                            "category": category,
                            "input_tokens": in_tok,
                            "output_tokens": out_tok,
                            "cost_usd": cost
                        }
                    }
                    return record

            except Exception as e:
                err_str = str(e)
                if "ThrottlingException" in err_str or "rate" in err_str.lower():
                    wait_sec = (2 ** attempt) * 2
                    print(f"    [Rate Limit] Backing off for {wait_sec}s...")
                    await asyncio.sleep(wait_sec)
                else:
                    print(f"    [Error] {category}: {e}")
                    await asyncio.sleep(2)

    return None


async def main():
    print("=================================================================")
    print("🚀 Kobits Enterprise Trajectory Harvester (AWS Bedrock Claude)")
    print(f"   Budget Cap: ${MAX_BUDGET_USD:.2f} USD | Concurrency: {CONCURRENCY_LIMIT}")
    print("=================================================================")

    try:
        from backend.services.llm.bedrock_provider import BedrockProvider
        provider = BedrockProvider()
        client = provider._get_client()
        model = provider._normalize_model_id(provider.default_model)
    except Exception as e:
        print(f"[Fatal] Failed to initialize Bedrock client: {e}")
        return

    state = load_state()
    print(f"[*] Starting state: Spent: ${state['total_spent_usd']:.4f} | Total Tokens: {state['total_output_tokens']:,}")

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    harvested_count = 0

    # Expand the prompt list to iterate through multiple enterprise tasks until budget cap
    prompts_to_run = ENTERPRISE_PROMPTS * 8  # Loop through variations to fill budget up to $10

    for i, item in enumerate(prompts_to_run):
        if state["total_spent_usd"] >= MAX_BUDGET_USD:
            print(f"\n[BUDGET CAP REACHED] Total spent: ${state['total_spent_usd']:.4f}. Stopping harvester.")
            break

        print(f"\n[{i+1}/{len(prompts_to_run)}] Harvesting: {item['category']}...")
        result = await harvest_single_prompt(client, model, item, semaphore, state)

        if result:
            harvested_count += 1
            # Append immediately to training data
            with open(TARGET_JSONL, "a", encoding="utf-8") as f:
                f.write(json.dumps(result) + "\n")

            out_tok = result["metadata"]["output_tokens"]
            cost = result["metadata"]["cost_usd"]
            print(f"  ✓ Harvested {out_tok:,} tokens (${cost:.4f}) | Running Total: ${state['total_spent_usd']:.4f}")

    print("\n=================================================================")
    print(f"🎉 Harvest Batch Complete!")
    print(f"  Trajectories Harvested : {harvested_count}")
    print(f"  Total Spent            : ${state['total_spent_usd']:.4f} USD")
    print(f"  Total Output Tokens    : {state['total_output_tokens']:,} tokens")
    print(f"  Target File            : {TARGET_JSONL}")
    print("=================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
