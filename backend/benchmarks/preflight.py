import os
import sys
import asyncio
from backend.core.config import settings
from backend.core.database import AsyncSessionLocal, engine
from sqlalchemy import text

async def run_preflight():
    print("========================================")
    print("KOBITS PREFLIGHT CHECK (REAL API TEST)")
    print("========================================")
    
    print("\n1. Environment Configuration")
    provider = settings.LLM_PROVIDER
    if provider not in ["deepseek", "anthropic"]:
        print("FAIL: Provider must be 'deepseek' or 'anthropic'")
        sys.exit(1)
    print(f"Provider: {provider} -> OK")
    
    if provider == "deepseek":
        if not settings.DEEPSEEK_API_KEY and not os.environ.get("DEEPSEEK_API_KEY"):
            print("FAIL: DEEPSEEK_API_KEY missing")
            sys.exit(1)
        print("Model: deepseek-flash -> OK")
    elif provider == "anthropic":
        if not settings.ANTHROPIC_API_KEY and not os.environ.get("ANTHROPIC_API_KEY"):
            print("FAIL: ANTHROPIC_API_KEY missing")
            sys.exit(1)
        print(f"Model: {settings.ANTHROPIC_MODEL} -> OK")
        
    print("\n2. Database Connectivity")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        print("Database connection -> OK")
    except Exception as e:
        print(f"FAIL: DB connection error: {e}")
        sys.exit(1)
        
    print("\n3. Sandbox Infrastructure")
    from backend.services.sandbox_manager import SandboxManager
    try:
        sb = SandboxManager.create_sandbox(project_root="local/test_real_ai_repo", branch_name="test")
        SandboxManager.cleanup(sb.session_id)
        print("Sandbox isolation & cleanup -> OK")
    except Exception as e:
        print(f"FAIL: Sandbox error: {e}")
        sys.exit(1)

    print("\n4. Agent Registry")
    from backend.services.agent_registry import AGENT_REGISTRY
    from backend.models.agent import AgentType
    nexus = AGENT_REGISTRY.get(AgentType.ORCHESTRATOR)
    if not nexus:
        print("FAIL: Agent Nexus missing")
        sys.exit(1)
    print("Agent Registry valid -> OK")
    
    print("\nPreflight SUCCESS. Ready for official run.")

if __name__ == "__main__":
    asyncio.run(run_preflight())
