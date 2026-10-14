"""
Phase 1 SOTA Transformation Verification Suite for Kobits.

Thoroughly verifies:
1. SandboxRollbackManager: Git checkpoint creation, atomic hard rollback (`git reset --hard` + `git clean -fdx`), and error paths.
2. Multi-Language AST/CST Indexer: Symbol, method (`ClassName.method`), and 1-indexed line extraction across Python, TS/JS, Go, and Rust, plus directory exclusions.
3. Queue Consolidation: Standardizing on DurableQueue, eliminating duplicate WorkerQueue dispatch, and backward compatibility in WorkerQueue.get_job.
4. Multi-Tenant Webhook Isolation: Elimination of SELECT * FROM projects LIMIT 1 fallbacks and 404 enforcement across GitHub, Jira, Linear, and Slack.
5. Hybrid Code Search with RRF: Reciprocal Rank Fusion combining dense semantic vectors and BM25 lexical token matching.
"""

import os
import shutil
import tempfile
import subprocess
import json
import uuid
import pytest
import numpy as np
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.core.database import AsyncSessionLocal
from backend.models.organization import Organization, User, OrganizationMember
from backend.models.project import Project
from backend.models.github import Repository, RepositoryStatus
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.models.durable_job import DurableJob, JobStatus
from backend.models.memory import CodeDocument
from backend.services.sandbox_rollback import SandboxRollbackManager
from backend.services.indexer import RepositoryIndexer
from backend.services.worker_queue import WorkerQueue
from backend.services.durable_queue import DurableQueue
from backend.services.memory_service import MemoryService
from backend.services.embedding import EmbeddingUtils


# =====================================================================
# 1. SandboxRollbackManager Tests
# =====================================================================

def test_sandbox_rollback_manager_lifecycle():
    """Verify git checkpoint creation, atomic reset, and untracked file cleanup."""
    temp_dir = tempfile.mkdtemp(prefix="kobits_test_rollback_")
    try:
        # Initialize bare test git repo
        subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "config", "user.name", "Tester"],
            cwd=temp_dir, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "config", "user.email", "test@kobits.ai"],
            cwd=temp_dir, check=True, capture_output=True
        )

        test_file = os.path.join(temp_dir, "service.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("def calculate_total(items):\n    return sum(items)\n")

        subprocess.run(["git", "add", "service.py"], cwd=temp_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Tester", "-c", "user.email=test@kobits.ai", "commit", "-m", "initial commit"],
            cwd=temp_dir, check=True, capture_output=True
        )

        # 1. Create Checkpoint
        checkpoint_sha = SandboxRollbackManager.create_checkpoint(temp_dir, "pre_task_test")
        assert checkpoint_sha is not None
        assert len(checkpoint_sha) >= 7

        current_sha = SandboxRollbackManager.get_current_sha(temp_dir)
        assert current_sha == checkpoint_sha

        # 2. Introduce destructive changes & untracked files
        with open(test_file, "a", encoding="utf-8") as f:
            f.write("\n# Corrupted code\ndef syntax_error():\n    return {\n")

        garbage_file = os.path.join(temp_dir, "corrupt_data.tmp")
        with open(garbage_file, "w", encoding="utf-8") as f:
            f.write("junk data that should be cleaned")

        garbage_dir = os.path.join(temp_dir, "bad_build_dir")
        os.makedirs(garbage_dir, exist_ok=True)
        with open(os.path.join(garbage_dir, "nested.bin"), "w", encoding="utf-8") as f:
            f.write("nested garbage")

        assert os.path.exists(garbage_file)
        assert os.path.exists(garbage_dir)

        # 3. Perform atomic hard rollback
        success = SandboxRollbackManager.rollback(temp_dir, checkpoint_sha)
        assert success is True

        # 4. Verify original state restored and garbage deleted
        with open(test_file, "r", encoding="utf-8") as f:
            restored_content = f.read()
        assert "syntax_error" not in restored_content
        assert "def calculate_total(items):" in restored_content
        assert not os.path.exists(garbage_file)
        assert not os.path.exists(garbage_dir)
        assert SandboxRollbackManager.get_current_sha(temp_dir) == checkpoint_sha

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_sandbox_rollback_manager_error_handling():
    """Verify graceful handling for non-git directories and invalid SHAs."""
    temp_dir = tempfile.mkdtemp(prefix="kobits_non_git_")
    try:
        # Non-git directory
        assert SandboxRollbackManager.create_checkpoint(temp_dir, "test") is None
        assert SandboxRollbackManager.get_current_sha(temp_dir) is None
        assert SandboxRollbackManager.rollback(temp_dir, "abc1234") is False

        # Non-existent directory
        assert SandboxRollbackManager.create_checkpoint("/non/existent/path", "test") is None
        assert SandboxRollbackManager.rollback("/non/existent/path", "abc1234") is False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# =====================================================================
# 2. Multi-Language AST / CST Indexer Tests
# =====================================================================

def test_indexer_multi_language_parsing():
    """Verify surgical symbol extraction and ClassName.method across languages."""
    # 1. Directory ignore filters
    for expected_dir in ("repos", "training_data", "benchmark_fixture_repo"):
        assert expected_dir in RepositoryIndexer.IGNORE_DIRS

    # 2. Python (.py) AST
    py_code = (
        "import os\n"
        "import sys\n\n"
        "class AuthService:\n"
        "    def __init__(self, key):\n"
        "        self.key = key\n\n"
        "    async def verify_token(self, token: str) -> bool:\n"
        "        return token == self.key\n\n"
        "def hash_password(pwd: str) -> str:\n"
        "    return 'hashed_' + pwd\n"
    )
    py_chunks = RepositoryIndexer._chunk_file("auth.py", py_code)
    py_symbols = {c.get("symbol") for c in py_chunks if c.get("symbol")}
    assert "AuthService.verify_token" in py_symbols
    assert "AuthService.__init__" in py_symbols
    assert "hash_password" in py_symbols
    assert "module_imports" in py_symbols

    # 3. TypeScript (.ts) CST
    ts_code = (
        "import { Database } from './db';\n\n"
        "export class PaymentProcessor {\n"
        "    constructor(private db: Database) {}\n\n"
        "    async chargeCard(amount: number): Promise<boolean> {\n"
        "        return true;\n"
        "    }\n"
        "}\n\n"
        "export interface ChargeResult {\n"
        "    id: string;\n"
        "    success: boolean;\n"
        "}\n\n"
        "export const validateAmount = (amt: number) => {\n"
        "    return amt > 0;\n"
        "};\n"
    )
    ts_chunks = RepositoryIndexer._chunk_file("payment.ts", ts_code)
    ts_symbols = {c.get("symbol") for c in ts_chunks if c.get("symbol")}
    assert "PaymentProcessor.chargeCard" in ts_symbols
    assert "PaymentProcessor.constructor" in ts_symbols
    assert "ChargeResult" in ts_symbols
    assert "validateAmount" in ts_symbols

    # 4. Go (.go) CST
    go_code = (
        "package billing\n\n"
        "import \"context\"\n\n"
        "type InvoiceService struct {\n"
        "    apiToken string\n"
        "}\n\n"
        "func (s *InvoiceService) CreateInvoice(ctx context.Context, id string) error {\n"
        "    return nil\n"
        "}\n\n"
        "func FormatCurrency(amount int64) string {\n"
        "    return \"$100\"\n"
        "}\n"
    )
    go_chunks = RepositoryIndexer._chunk_file("billing.go", go_code)
    go_symbols = {c.get("symbol") for c in go_chunks if c.get("symbol")}
    assert "InvoiceService.CreateInvoice" in go_symbols
    assert "InvoiceService" in go_symbols
    assert "FormatCurrency" in go_symbols

    # 5. Rust (.rs) CST
    rs_code = (
        "use std::sync::Arc;\n\n"
        "pub struct OrderRepository {\n"
        "    conn: Arc<String>,\n"
        "}\n\n"
        "impl OrderRepository {\n"
        "    pub async fn find_order(&self, id: &str) -> Option<String> {\n"
        "        Some(id.to_string())\n"
        "    }\n"
        "}\n\n"
        "pub fn default_timeout() -> u64 {\n"
        "    30\n"
        "}\n"
    )
    rs_chunks = RepositoryIndexer._chunk_file("order.rs", rs_code)
    rs_symbols = {c.get("symbol") for c in rs_chunks if c.get("symbol")}
    assert "OrderRepository.find_order" in rs_symbols
    assert "OrderRepository" in rs_symbols
    assert "default_timeout" in rs_symbols


# =====================================================================
# 3. Queue Consolidation Tests
# =====================================================================

@pytest.mark.asyncio
async def test_queue_consolidation_and_backward_compatibility():
    """Verify exclusive DurableQueue standardization and WorkerQueue.get_job fallback lookup."""
    mission_id = f"test_m_{uuid.uuid4().hex[:8]}"
    job_id = f"job_mission_{mission_id}"

    # 1. Enqueue strictly to DurableQueue
    await DurableQueue.init_schema()
    await DurableQueue.enqueue(
        task_type="EXECUTE_MISSION",
        payload={"mission_id": mission_id, "org_id": "test_org", "user_id": "test_user"},
        queue_name="missions",
        priority=10,
        job_id=job_id,
    )

    # 2. WorkerQueue.get_job should resolve the DurableJob seamlessly via fallback
    job_record = await WorkerQueue.get_job(job_id)
    assert job_record is not None
    assert job_record.id == job_id
    assert job_record.task_type == "mission.execute"
    assert job_record.payload["mission_id"] == mission_id
    assert job_record.queue == "missions"

    # Also resolve without job_ prefix
    alt_record = await WorkerQueue.get_job(job_id.removeprefix("job_"))
    assert alt_record is not None
    assert alt_record.payload["mission_id"] == mission_id


# =====================================================================
# 4. Multi-Tenant Webhook Isolation Tests
# =====================================================================

@pytest.mark.asyncio
async def test_webhook_multi_tenant_isolation_404():
    """Verify that unmapped webhooks raise 404 rather than defaulting to arbitrary projects."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GitHub webhook with unmapped repo
        gh_payload = {
            "action": "opened",
            "issue": {
                "title": "Bug in billing",
                "body": "@kobits please investigate the billing failure",
                "assignees": [{"login": "kobits"}]
            },
            "repository": {
                "html_url": "https://github.com/unmapped-org/unmapped-repo",
                "full_name": "unmapped-org/unmapped-repo"
            }
        }
        res_gh = await client.post(
            "/api/v1/webhooks/github",
            json=gh_payload,
            headers={"x-github-event": "issues"}
        )
        assert res_gh.status_code == 404
        assert "Project not found for repository" in res_gh.json()["detail"]

        # 2. Jira webhook with unmapped project
        jira_payload = {
            "issue": {
                "fields": {
                    "summary": "Fix payment gateway timeout",
                    "description": "Payment timeouts on card charge",
                    "project": {"key": "NON_EXISTENT_PROJ_999"}
                }
            }
        }
        res_jira = await client.post("/api/v1/webhooks/jira", json=jira_payload)
        assert res_jira.status_code == 404
        assert "Project not found for Jira issue" in res_jira.json()["detail"]

        # 3. Linear webhook with unmapped team/project
        linear_payload = {
            "action": "create",
            "type": "Issue",
            "data": {
                "title": "App crashes on launch",
                "description": "Immediate SIGSEGV on start",
                "teamId": "unmapped-team-xyz-888"
            }
        }
        res_linear = await client.post("/api/v1/webhooks/linear", json=linear_payload)
        assert res_linear.status_code == 404
        assert "Project not found for Linear issue" in res_linear.json()["detail"]

        # 4. Slack webhook with unmapped channel
        slack_payload = {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C_UNMAPPED_CHANNEL_777",
                "text": "<@U12345> deploy latest release"
            }
        }
        res_slack = await client.post("/api/v1/webhooks/slack", json=slack_payload)
        assert res_slack.status_code == 404
        assert "Project not mapped for Slack channel" in res_slack.json()["detail"]


# =====================================================================
# 5. Hybrid Code Search with RRF Tests
# =====================================================================

@pytest.mark.asyncio
async def test_memory_service_rrf_hybrid_ranking():
    """Verify Reciprocal Rank Fusion combines dense semantics and BM25 lexical token matches."""
    query = "authenticate user token"
    q_vec = EmbeddingUtils.generate_embedding(query)

    # Document 1: Strong dense semantics + strong lexical match
    doc1_content = "def authenticate_user(token: str) -> bool:\n    return verify_token(token)"
    doc1 = CodeDocument(
        file_path="auth_controller.py",
        content_chunk=doc1_content,
        embedding=EmbeddingUtils.generate_embedding(doc1_content),
        project_id="test_proj_rrf",
        metadata_json={"symbol": "authenticate_user", "kind": "function", "start_line": 1, "end_line": 2}
    )

    # Document 2: Purely lexical match without semantic vector embedding
    doc2_content = "def token_generator():\n    return generate_random_token()"
    doc2 = CodeDocument(
        file_path="token_util.py",
        content_chunk=doc2_content,
        embedding=[],
        project_id="test_proj_rrf",
        metadata_json={"symbol": "token_generator", "kind": "function", "start_line": 1, "end_line": 2}
    )

    # Document 3: Completely unrelated
    doc3_content = "class DatabaseConnectionPool:\n    def connect(self): pass"
    doc3 = CodeDocument(
        file_path="database.py",
        content_chunk=doc3_content,
        embedding=EmbeddingUtils.generate_embedding(doc3_content),
        project_id="test_proj_rrf",
        metadata_json={"symbol": "DatabaseConnectionPool", "kind": "class", "start_line": 1, "end_line": 2}
    )

    db_mock = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars().all.return_value = [doc1, doc2, doc3]
    db_mock.execute.return_value = mock_res

    results = await MemoryService.search_codebase(db_mock, "test_proj_rrf", query, limit=5, threshold=0.01)

    assert len(results) >= 2
    # doc1 should rank first due to joint high dense + lexical + symbol RRF
    top_hit = results[0]
    assert top_hit["file"] == "auth_controller.py"
    assert "rrf_score" in top_hit
    assert top_hit["dense_rank"] is not None
    assert top_hit["lexical_rank"] is not None
    assert top_hit["rrf_score"] > 0

    # Unrelated document should either be absent or scored at bottom
    files_ranked = [r["file"] for r in results]
    assert files_ranked[0] == "auth_controller.py"
    if "database.py" in files_ranked:
        assert files_ranked.index("database.py") > files_ranked.index("auth_controller.py")
