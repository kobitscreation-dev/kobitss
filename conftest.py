import os

# Force the test suite to use a local test database before any backend modules import config
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_kobits.db"
os.environ["KOBITS_DEV_MODE"] = "true"
os.environ.pop("E2B_API_KEY", None)
