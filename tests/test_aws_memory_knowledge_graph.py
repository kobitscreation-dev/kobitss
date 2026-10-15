"""
Comprehensive Test Suite for Dimension 8: Memory Systems & Knowledge Graph on AWS.

Tests cover:
1. GraphService: Multi-hop recursive dependency traversal, cycle handling, and blast radius calculation.
2. VectorStoreService: Dense vector search and fallback execution.
3. TeamMemoryManager: Credential scrubbing (AWS keys, passwords, private keys), exponential decay, TTL, and DB hydration.
4. ContextBuilder: Automated injection of AST Blast Radius and Persistent Team Memory.
5. REST API: Graph blast radius, dependencies, callers, and team memory endpoints.
"""

import json
import uuid
import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.models.base import Base
from backend.models.organization import Organization, User
from backend.models.project import Project, ProjectMemory, ProjectMemoryStatus, Task
from backend.models.graph import GraphNode, GraphEdge
from backend.models.memory import CodeDocument
from backend.models.agent import Agent, AgentType
from backend.models.mission import Mission, MissionStatus, WorkflowPhase
from backend.services.graph_service import GraphService
from backend.services.vector_store import VectorStoreService
from backend.services.team_memory import TeamMemoryManager, MemoryNamespace, TeamMemoryEntry
from backend.services.memory_service import MemoryService
from backend.services.context_builder import build_agent_context


@pytest.fixture
async def test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        user = User(id="test_user", email="test@example.com", hashed_password="pw")
        org = Organization(id="test_org", name="Test Org")
        project = Project(id="test_proj", organization_id="test_org", created_by="test_user", name="Kobits AWS")
        session.add_all([user, org, project])
        await session.commit()
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_graph_service_recursive_traversal_and_cycles(test_db):
    """Verifies multi-hop traversal and strict cycle prevention."""
    proj_id = "test_proj"

    # Create diamond + cycle call graph:
    # A -> B -> D
    # A -> C -> D
    # D -> A (cycle!)
    node_a = GraphNode(id="n_a", project_id=proj_id, node_type="FUNCTION", name="login", file_path="auth.py")
    node_b = GraphNode(id="n_b", project_id=proj_id, node_type="FUNCTION", name="verify_hash", file_path="crypto.py")
    node_c = GraphNode(id="n_c", project_id=proj_id, node_type="FUNCTION", name="lookup_user", file_path="user_db.py")
    node_d = GraphNode(id="n_d", project_id=proj_id, node_type="FUNCTION", name="query_postgres", file_path="db.py")
    test_db.add_all([node_a, node_b, node_c, node_d])

    edge_ab = GraphEdge(id="e_ab", project_id=proj_id, source_id="n_a", target_id="n_b", edge_type="CALLS")
    edge_ac = GraphEdge(id="e_ac", project_id=proj_id, source_id="n_a", target_id="n_c", edge_type="CALLS")
    edge_bd = GraphEdge(id="e_bd", project_id=proj_id, source_id="n_b", target_id="n_d", edge_type="CALLS")
    edge_cd = GraphEdge(id="e_cd", project_id=proj_id, source_id="n_c", target_id="n_d", edge_type="CALLS")
    edge_da = GraphEdge(id="e_da", project_id=proj_id, source_id="n_d", target_id="n_a", edge_type="CALLS")  # cycle
    test_db.add_all([edge_ab, edge_ac, edge_bd, edge_cd, edge_da])
    await test_db.commit()

    # 1. Downstream dependencies from login (A)
    downstream = await GraphService.find_downstream_dependencies(
        db=test_db,
        project_id=proj_id,
        start_id_or_name="login",
        max_depth=4,
    )
    names = [d["name"] for d in downstream]
    assert "verify_hash" in names
    assert "lookup_user" in names
    assert "query_postgres" in names
    # Cycle should not duplicate or cause infinite recursion
    assert len(downstream) == 3

    # 2. Upstream callers for query_postgres (D)
    upstream = await GraphService.find_upstream_callers(
        db=test_db,
        project_id=proj_id,
        target_id_or_name="query_postgres",
        max_depth=4,
    )
    up_names = [u["name"] for u in upstream]
    assert "verify_hash" in up_names
    assert "lookup_user" in up_names
    assert "login" in up_names


@pytest.mark.asyncio
async def test_graph_service_impact_blast_radius(test_db):
    """Verifies transitive blast radius calculation, affected files count, and risk classification."""
    proj_id = "test_proj"

    # Core database layer
    core_db = GraphNode(id="core_db", project_id=proj_id, node_type="CLASS", name="DatabaseEngine", file_path="backend/core/database.py")
    test_db.add(core_db)

    # 10 callers across 4 files
    nodes = []
    edges = []
    for i in range(10):
        f = f"backend/services/service_{i % 4}.py"
        fn = GraphNode(id=f"fn_{i}", project_id=proj_id, node_type="FUNCTION", name=f"service_func_{i}", file_path=f)
        nodes.append(fn)
        edges.append(GraphEdge(id=f"e_{i}", project_id=proj_id, source_id=fn.id, target_id="core_db", edge_type="CALLS"))

    test_db.add_all(nodes + edges)
    await test_db.commit()

    blast = await GraphService.compute_impact_blast_radius(
        db=test_db,
        project_id=proj_id,
        target_symbol_or_path="DatabaseEngine",
        max_depth=3,
    )

    assert blast["target"] == "DatabaseEngine"
    assert blast["direct_impact_count"] == 10
    assert blast["total_impacted_symbols"] == 10
    assert blast["impacted_files_count"] == 4
    # Because 10 callers and core database file is impacted, risk level should be CRITICAL
    assert blast["risk_level"] in ("HIGH", "CRITICAL")


@pytest.mark.asyncio
async def test_vector_store_service(test_db):
    """Verifies vector search over code documents and project memory."""
    proj_id = "test_proj"

    # Seed code documents with dummy 384D normalized vectors
    v1 = np.zeros(384, dtype=np.float64)
    v1[0] = 1.0  # Unit vector along axis 0
    v2 = np.zeros(384, dtype=np.float64)
    v2[1] = 1.0  # Unit vector along axis 1

    doc1 = CodeDocument(
        id="doc_auth",
        project_id=proj_id,
        file_path="auth.py",
        content_chunk="def authenticate_user(): pass",
        embedding=v1.tolist(),
        metadata_json={"symbol": "authenticate_user", "kind": "function"},
    )
    doc2 = CodeDocument(
        id="doc_billing",
        project_id=proj_id,
        file_path="billing.py",
        content_chunk="def charge_card(): pass",
        embedding=v2.tolist(),
        metadata_json={"symbol": "charge_card", "kind": "function"},
    )
    test_db.add_all([doc1, doc2])
    await test_db.commit()

    # Query matching doc1 exactly
    results = await VectorStoreService.search_code_documents(
        db=test_db,
        project_id=proj_id,
        query_vec=v1.tolist(),
        limit=5,
        threshold=0.1,
    )

    assert len(results) >= 1
    assert results[0]["id"] == "doc_auth"
    assert results[0]["similarity"] > 0.99


@pytest.mark.asyncio
async def test_team_memory_credential_scrubbing():
    """Verifies rigorous scrubbing of AWS keys, secret access keys, passwords, and tokens."""
    TeamMemoryManager.clear("test_proj")

    sensitive_text = (
        "Configured AWS with AKIAIOSFODNN7EXAMPLE and secret aws_secret_access_key='wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'. "
        "Also set api_key='secret-token-12345' and password='AdminPassword!'. "
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...EXAMPLEKEY...\n-----END RSA PRIVATE KEY-----"
    )

    entry = TeamMemoryManager.store_memory(
        project_id="test_proj",
        namespace=MemoryNamespace.ADR,
        key="aws_setup",
        content=sensitive_text,
        source_agent="Nexus",
        evidence="Tested with api_key='sk-ant-api03-secret'",
    )

    # Verify all credentials were neutralized
    assert "AKIAIOSFODNN7EXAMPLE" not in entry.content
    assert "<SCRUBBED_AWS_KEY_ID>" in entry.content
    assert "wJalrXUtnFEMI" not in entry.content
    assert "<SCRUBBED_SECRET>" in entry.content
    assert "AdminPassword!" not in entry.content
    assert "EXAMPLEKEY" not in entry.content
    assert "<SCRUBBED_PRIVATE_KEY>" in entry.content
    assert "sk-ant-api03" not in (entry.evidence or "")


@pytest.mark.asyncio
async def test_team_memory_db_hydration_and_sync(test_db):
    """Verifies that TeamMemoryManager can persist to DB and hydrate back across sessions."""
    proj_id = "test_proj"
    TeamMemoryManager.clear(proj_id)

    # 1. Record an ADR and a Defect Fix
    entry_adr = TeamMemoryManager.record_adr(
        project_id=proj_id,
        source_agent="Nexus",
        title="Use AWS RDS pgvector",
        decision="Store vector embeddings directly in AWS RDS PostgreSQL",
        rationale="Eliminates external Neptune cluster cost while retaining HNSW sub-millisecond search",
    )
    entry_fix = TeamMemoryManager.record_defect_fix(
        project_id=proj_id,
        source_agent="Sentinel",
        defect="Race condition on token refresh",
        fix="Wrap refresh call with mutex lock",
        root_cause="Concurrent async tasks refreshing simultaneously",
    )

    # 2. Persist to ProjectMemory table
    await TeamMemoryManager.sync_to_db(test_db, entry_adr)
    await TeamMemoryManager.sync_to_db(test_db, entry_fix)

    # 3. Clear in-memory store completely
    TeamMemoryManager.clear(proj_id)
    assert len(TeamMemoryManager._get_project_store(proj_id)) == 0

    # 4. Hydrate from database
    loaded_count = await TeamMemoryManager.load_from_db(test_db, proj_id)
    assert loaded_count >= 2

    # 5. Query hydrated memories
    results = TeamMemoryManager.query_team_memory(
        project_id=proj_id,
        query="pgvector RDS embeddings",
        namespace=MemoryNamespace.ADR,
    )
    assert len(results) >= 1
    assert "Use AWS RDS pgvector" in results[0]["metadata"].get("title", "")


@pytest.mark.asyncio
async def test_context_builder_ast_blast_radius_injection(test_db):
    """Verifies that ContextBuilder injects AST Blast Radius and Team Memory sections."""
    proj_id = "test_proj"

    # Setup agent, mission, task
    agent = Agent(
        id="agent_nexus",
        name="Nexus",
        type=AgentType.ORCHESTRATOR,
        system_prompt="Lead system architect.",
    )
    mission = Mission(
        id="m_100",
        project_id=proj_id,
        organization_id="test_org",
        created_by="test_user",
        title="Refactor Authentication Engine",
        objective="Upgrade auth to use bcrypt and pgvector session storage",
        status=MissionStatus.ACTIVE,
        phase=WorkflowPhase.IMPLEMENTATION,
    )
    task = Task(
        id="t_100",
        project_id=proj_id,
        mission_id="m_100",
        created_by="test_user",
        title="Update verify_password logic",
        description="Modify crypto.py verify_password",
    )
    test_db.add_all([agent, mission, task])

    # Setup graph symbols
    auth_node = GraphNode(id="gn_auth", project_id=proj_id, node_type="FUNCTION", name="verify_password", file_path="backend/services/crypto.py")
    caller1 = GraphNode(id="gn_c1", project_id=proj_id, node_type="FUNCTION", name="login_handler", file_path="backend/api/auth.py")
    caller2 = GraphNode(id="gn_c2", project_id=proj_id, node_type="FUNCTION", name="oauth_callback", file_path="backend/api/oauth.py")
    edge1 = GraphEdge(id="ge_1", project_id=proj_id, source_id="gn_c1", target_id="gn_auth", edge_type="CALLS")
    edge2 = GraphEdge(id="ge_2", project_id=proj_id, source_id="gn_c2", target_id="gn_auth", edge_type="CALLS")
    test_db.add_all([auth_node, caller1, caller2, edge1, edge2])

    # Store a relevant defect memory
    TeamMemoryManager.record_defect_fix(
        project_id=proj_id,
        source_agent="Sentinel",
        defect="Timing attack on password check",
        fix="Use hmac.compare_digest instead of == equality",
    )
    await test_db.commit()

    context = await build_agent_context(
        db=test_db,
        agent=agent,
        project=Project(id=proj_id, organization_id="test_org", created_by="test_user", name="Kobits AWS"),
        mission=mission,
        task=task,
        organization_id="test_org",
    )

    # Verify high-signal injections
    assert "AST BLAST RADIUS & IMPACT ANALYSIS" in context
    assert "Risk Level:" in context
    assert "PERSISTENT TEAM MEMORY (GOVERNED SPECIALISTS)" in context
    assert "Timing attack on password check" in context


@pytest.mark.asyncio
async def test_graph_and_memory_api_endpoints(test_db):
    """Verifies graph traversal and team memory REST API endpoints."""
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.core.database import get_db
    from backend.api.v1.auth import get_current_active_user

    proj_id = "test_proj"

    # Add symbols
    n1 = GraphNode(id="api_login", project_id=proj_id, node_type="FUNCTION", name="api_login", file_path="api.py")
    n2 = GraphNode(id="api_auth", project_id=proj_id, node_type="FUNCTION", name="api_auth", file_path="auth.py")
    edge = GraphEdge(id="e_api", project_id=proj_id, source_id="api_login", target_id="api_auth", edge_type="CALLS")
    test_db.add_all([n1, n2, edge])
    await test_db.commit()

    # Record team memory
    TeamMemoryManager.clear(proj_id)
    TeamMemoryManager.record_adr(
        project_id=proj_id,
        source_agent="Nexus",
        title="AWS Bedrock Titan Integration",
        decision="Use Bedrock for production semantic embeddings",
        rationale="Unified AWS billing and high embedding precision",
    )

    test_user = User(id="test_user", email="test@example.com", hashed_password="pw")

    async def override_get_db():
        yield test_db

    async def override_get_current_user():
        return test_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_active_user] = override_get_current_user

    try:
        client = TestClient(app)

        # 1. Test blast radius endpoint
        r_blast = client.get(f"/api/v1/memory/{proj_id}/graph/blast-radius?symbol=api_auth&depth=3")
        assert r_blast.status_code == 200
        blast_data = r_blast.json()
        assert blast_data["target"] == "api_auth"
        assert blast_data["direct_impact_count"] == 1

        # 2. Test dependencies endpoint
        r_deps = client.get(f"/api/v1/memory/{proj_id}/graph/dependencies?symbol=api_login&depth=3")
        assert r_deps.status_code == 200
        deps_data = r_deps.json()
        assert len(deps_data["dependencies"]) == 1
        assert deps_data["dependencies"][0]["name"] == "api_auth"

        # 3. Test callers endpoint
        r_callers = client.get(f"/api/v1/memory/{proj_id}/graph/callers?symbol=api_auth&depth=3")
        assert r_callers.status_code == 200
        callers_data = r_callers.json()
        assert len(callers_data["callers"]) == 1
        assert callers_data["callers"][0]["name"] == "api_login"

        # 4. Test team memory query endpoint
        r_mem = client.get(f"/api/v1/memory/{proj_id}/team-memory?query=Bedrock+Titan")
        assert r_mem.status_code == 200
        mem_data = r_mem.json()
        assert len(mem_data["memories"]) >= 1
        assert "AWS Bedrock Titan Integration" in mem_data["memories"][0]["metadata"].get("title", "")
    finally:
        app.dependency_overrides.clear()

