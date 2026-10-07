"""
Comprehensive Migration Safety & Verification Test Suite.

Tests:
1. Fresh DB -> alembic upgrade head -> PASS
2. Existing DB with current tables & NO Alembic version -> alembic upgrade head -> PASS
3. Existing DB with sample data (Org, User, Project, Mission, Task, Memory) -> migrate -> ALL data preserved
4. Existing DB missing required new columns -> migration adds columns -> existing rows preserved
5. Existing DB with incompatible column type -> fails cleanly with RuntimeError
6. Idempotency -> running upgrade twice is safe
"""

import os
import sqlite3
import pytest
from sqlalchemy import create_engine, inspect
from alembic.config import Config
from alembic import command
from backend.models.base import Base
import backend.models  # ensure all models registered


def run_alembic_upgrade(db_path: str):
    alembic_cfg = Config("backend/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    # Also inject into the section so get_section sees it
    alembic_cfg.attributes['configure_logger'] = False
    section = alembic_cfg.get_section(alembic_cfg.config_ini_section)
    if section:
        section['sqlalchemy.url'] = f"sqlite:///{db_path}"
    command.upgrade(alembic_cfg, "head")


def test_migration_1_fresh_database(tmp_path):
    db_file = str(tmp_path / "fresh.db")
    run_alembic_upgrade(db_file)
    
    engine = create_engine(f"sqlite:///{db_file}")
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    assert "alembic_version" in tables
    assert "users" in tables
    assert "projects" in tables
    assert "missions" in tables
    assert "tasks" in tables
    assert "project_memory" in tables
    assert "agents" in tables


def test_migration_2_existing_pre_alembic_database(tmp_path):
    db_file = str(tmp_path / "existing_pre_alembic.db")
    engine = create_engine(f"sqlite:///{db_file}")
    
    # Create all tables without alembic versioning (simulating pre-alembic create_all)
    Base.metadata.create_all(engine)
    
    inspector = inspect(engine)
    assert "alembic_version" not in inspector.get_table_names()
    assert "users" in inspector.get_table_names()
    
    # Run alembic upgrade head on existing schema
    run_alembic_upgrade(db_file)
    
    inspector = inspect(engine)
    assert "alembic_version" in inspector.get_table_names()


def test_migration_3_existing_database_with_sample_data_preservation(tmp_path):
    db_file = str(tmp_path / "existing_data.db")
    conn = sqlite3.connect(db_file)
    
    # Create tables manually simulating legacy DB
    conn.execute("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            full_name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            google_id TEXT,
            github_id TEXT,
            avatar_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE missions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            project_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE project_memory (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            version INTEGER NOT NULL DEFAULT 1,
            memory_status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    
    # Seed data
    conn.execute("INSERT INTO users VALUES ('u1', 'user@test.com', 'hash', 'Test User', 1, NULL, NULL, NULL, '2026-01-01', '2026-01-01')")
    conn.execute("INSERT INTO organizations VALUES ('o1', 'Test Org', '2026-01-01', '2026-01-01')")
    conn.execute("INSERT INTO projects VALUES ('p1', 'Test Project', 'o1', 'u1', '2026-01-01', '2026-01-01')")
    conn.execute("INSERT INTO missions VALUES ('m1', 'Test Mission', 'p1', 'o1', 'u1', '2026-01-01', '2026-01-01')")
    conn.execute("INSERT INTO tasks VALUES ('t1', 'Test Task', 'm1', 'p1', 'o1', 'u1', '2026-01-01', '2026-01-01')")
    conn.execute("INSERT INTO project_memory VALUES ('pm1', 'p1', 'o1', 'arch_key', 'arch_val', 1.0, 1, 'ACTIVE', '2026-01-01', '2026-01-01')")
    conn.commit()
    conn.close()
    
    # Verify counts before migration
    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM organizations").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM missions").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM project_memory").fetchone()[0] == 1
    conn.close()

    # Run alembic upgrade head
    run_alembic_upgrade(db_file)
    
    # Verify counts and exact values after migration
    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    assert conn.execute("SELECT email FROM users WHERE id='u1'").fetchone()[0] == "user@test.com"
    
    assert conn.execute("SELECT count(*) FROM organizations").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM organizations WHERE id='o1'").fetchone()[0] == "Test Org"
    
    assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM projects WHERE id='p1'").fetchone()[0] == "Test Project"
    
    assert conn.execute("SELECT count(*) FROM missions").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM missions WHERE id='m1'").fetchone()[0] == "Test Mission"
    
    assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert conn.execute("SELECT title FROM tasks WHERE id='t1'").fetchone()[0] == "Test Task"
    
    assert conn.execute("SELECT count(*) FROM project_memory").fetchone()[0] == 1
    assert conn.execute("SELECT key, value FROM project_memory WHERE id='pm1'").fetchone() == ("arch_key", "arch_val")
    conn.close()


def test_migration_4_missing_column_added_safely(tmp_path):
    db_file = str(tmp_path / "legacy_missing_col.db")
    conn = sqlite3.connect(db_file)
    
    # Create project_memory table WITHOUT evidence or source_agent_id
    conn.execute("""
        CREATE TABLE project_memory (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            version INTEGER NOT NULL DEFAULT 1,
            memory_status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("INSERT INTO project_memory VALUES ('pm_old', 'p1', 'o1', 'legacy_k', 'legacy_v', 1.0, 1, 'ACTIVE', '2026-01-01', '2026-01-01')")
    conn.commit()
    conn.close()
    
    # Run migration
    run_alembic_upgrade(db_file)
    
    # Verify missing columns evidence and source_agent_id were added and legacy data preserved
    conn = sqlite3.connect(db_file)
    row = conn.execute("SELECT key, value, evidence FROM project_memory WHERE id='pm_old'").fetchone()
    assert row[0] == "legacy_k"
    assert row[1] == "legacy_v"
    conn.close()


def test_migration_5_incompatible_schema_fails_clearly(tmp_path):
    db_file = str(tmp_path / "incompatible.db")
    conn = sqlite3.connect(db_file)
    
    # Create users table with incompatible column type (e.g. email is BLOB)
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email BLOB NOT NULL)")
    conn.commit()
    conn.close()
    
    with pytest.raises(RuntimeError) as exc_info:
        run_alembic_upgrade(db_file)
        
    assert "Incompatible database schema detected" in str(exc_info.value)


def test_migration_6_idempotency_double_upgrade(tmp_path):
    db_file = str(tmp_path / "idempotent.db")
    run_alembic_upgrade(db_file)
    # Upgrade second time
    run_alembic_upgrade(db_file)
    
    engine = create_engine(f"sqlite:///{db_file}")
    inspector = inspect(engine)
    assert "alembic_version" in inspector.get_table_names()
