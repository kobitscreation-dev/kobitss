import os

# Force hermetic test suite environment before any backend modules import config
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_kobits.db"
os.environ["KOBITS_DEV_MODE"] = "true"
os.environ["REAL_AI_TEST"] = "0"
os.environ["LLM_PROVIDER"] = "mock"
os.environ.pop("USE_ONLY_CLAUDE", None)
os.environ.pop("AWS_BEDROCK_API_KEY", None)
os.environ.pop("E2B_API_KEY", None)

try:
    from backend.core.config import settings
    settings.LLM_PROVIDER = "mock"
    settings.AWS_BEDROCK_API_KEY = None
except Exception:
    pass

