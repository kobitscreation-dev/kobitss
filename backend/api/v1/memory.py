from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any
from pydantic import BaseModel

from backend.core.database import get_db, AsyncSessionLocal
from backend.api.v1.auth import get_current_active_user
from backend.models.organization import User
from backend.services.indexer import RepositoryIndexer
from backend.services.memory_service import MemoryService

router = APIRouter()

class IndexRequest(BaseModel):
    repo_path: str


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


class MemoryCreate(BaseModel):
    category: str
    content: str


async def run_indexing_job(project_id: str, repo_path: str):
    """Background task to index a repository."""
    async with AsyncSessionLocal() as db:
        try:
            await RepositoryIndexer.index_repository(db, project_id, repo_path)
            from backend.services.graph_indexer import GraphIndexerService
            await GraphIndexerService.index_repository(db, project_id, repo_path)
        except Exception as e:
            print(f"Error indexing repo {repo_path}: {e}")


@router.post("/{project_id}/index")
async def index_project(project_id: str, req: IndexRequest, background_tasks: BackgroundTasks, current_user: User = Depends(get_current_active_user)):
    """Trigger a background indexing job for the project's repository."""
    background_tasks.add_task(run_indexing_job, project_id, req.repo_path)
    return {"message": "Indexing started in the background", "repo_path": req.repo_path}


@router.post("/{project_id}/search")
async def search_codebase(project_id: str, req: SearchRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Search the indexed codebase for the given query."""
    results = await MemoryService.search_codebase(db, project_id, req.query, limit=req.limit)
    return {"results": results}


@router.get("/{project_id}/memory")
async def get_project_memory(project_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Get the full knowledge context for a project."""
    context = await MemoryService.get_full_knowledge(db, project_id)
    return {"context": context}


@router.post("/{project_id}/memory")
async def add_project_memory(project_id: str, mem: MemoryCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Add a new architectural decision or convention to the project memory."""
    memory = await MemoryService.add_project_memory(db, project_id, mem.category, mem.content)
    return {"message": "Memory added", "id": "placeholder"}

@router.get("/{project_id}/graph/search")
async def search_graph(project_id: str, query: str, limit: int = 20, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Search for specific classes, functions, or files in the code graph."""
    results = await MemoryService.search_graph_nodes(db, project_id, query, limit)
    return {"nodes": results}

@router.get("/{project_id}/graph/node/{node_id}")
async def get_graph_node(project_id: str, node_id: str, include_blast_radius: bool = False, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Get a specific node and all its incoming/outgoing edges (dependencies) with optional blast radius."""
    node_data = await MemoryService.get_graph_node_details(db, project_id, node_id, include_blast_radius=include_blast_radius)
    if not node_data:
        raise HTTPException(status_code=404, detail="Node not found")
    return node_data


@router.get("/{project_id}/graph/blast-radius")
async def get_blast_radius(project_id: str, symbol: str, depth: int = 4, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Calculate the transitive impact blast radius of changing a symbol or file."""
    from backend.services.graph_service import GraphService
    radius = await GraphService.compute_impact_blast_radius(db, project_id, symbol, max_depth=depth)
    return radius


@router.get("/{project_id}/graph/dependencies")
async def get_graph_dependencies(project_id: str, symbol: str, depth: int = 4, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Find all transitive downstream dependencies for a symbol."""
    from backend.services.graph_service import GraphService
    deps = await GraphService.find_downstream_dependencies(db, project_id, symbol, max_depth=depth)
    return {"symbol": symbol, "dependencies": deps}


@router.get("/{project_id}/graph/callers")
async def get_graph_callers(project_id: str, symbol: str, depth: int = 4, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Find all transitive upstream callers for a symbol."""
    from backend.services.graph_service import GraphService
    callers = await GraphService.find_upstream_callers(db, project_id, symbol, max_depth=depth)
    return {"symbol": symbol, "callers": callers}


@router.get("/{project_id}/graph/subgraph")
async def get_graph_subgraph(project_id: str, root_ids: str, depth: int = 2, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Get connected subgraph for interactive visualization."""
    from backend.services.graph_service import GraphService
    ids = [rid.strip() for rid in root_ids.split(",") if rid.strip()]
    return await GraphService.get_subgraph(db, project_id, ids, max_depth=depth)


@router.get("/{project_id}/team-memory")
async def query_team_memory(project_id: str, query: str = "", namespace: str = None, agent: str = None, limit: int = 10, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Query persistent team memory using hybrid dense + BM25 RRF search."""
    from backend.services.team_memory import TeamMemoryManager
    # Ensure hydrated from DB
    await TeamMemoryManager.load_from_db(db, project_id)
    if query:
        results = TeamMemoryManager.query_team_memory(
            project_id=project_id,
            query=query,
            namespace=namespace,
            agent_name=agent,
            limit=limit,
        )
    else:
        # Return all active unexpired memories
        results = TeamMemoryManager.query_team_memory(
            project_id=project_id,
            query="all",
            namespace=namespace,
            agent_name=agent,
            limit=limit,
            min_confidence=0.0,
        )
    return {"memories": results}


# ══════════════════════════════════════════════════════════════════════════════
#  BIOLOGICALLY INSPIRED MEMORY OS — MULTI-TENANT 3-TIER + LEDGER ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

from typing import Optional, Tuple
from fastapi import Body, Query, Request
from pydantic import Field
from sqlalchemy import and_, select

from backend.core.config import settings
from backend.api.deps import get_current_org_member
from backend.models.project import Project
from backend.models.memory_os import GENESIS_HASH, MemoryLedgerEntry
from backend.services.memory_os_service import MemoryOSService


async def resolve_memory_tenant(
    request: Request,
    db: AsyncSession,
    project_id: Optional[str] = None,
) -> Tuple[User, str, Optional[str]]:
    """
    Resolves authenticated (user, organization_id, project_id) for Memory OS operations.
    Enforces strict cross-tenant isolation when explicit credentials or org headers are provided.
    """
    current_user = await get_current_active_user(request=request, db=db)

    explicit_org = (
        request.headers.get("X-Organization-Id")
        or request.headers.get("x-organization-id")
        or request.headers.get("X-Org-Id")
        or request.headers.get("x-org-id")
    )
    has_explicit_auth = bool(
        request.headers.get("Authorization")
        or request.headers.get("X-API-Key")
        or request.headers.get("x-api-key")
        or request.cookies.get(settings.SESSION_COOKIE_NAME)
        or explicit_org
    )

    if explicit_org and explicit_org.strip():
        from backend.services.auth_service import get_user_org_membership

        membership = await get_user_org_membership(db, current_user.id, explicit_org.strip())
        org_id = membership.organization_id
    else:
        membership = await get_current_org_member(request=request, current_user=current_user, db=db)
        org_id = membership.organization_id

    if project_id:
        proj = await db.get(Project, project_id)
        if proj is not None:
            if has_explicit_auth and proj.organization_id != org_id:
                if explicit_org and explicit_org.strip():
                    raise HTTPException(
                        status_code=403,
                        detail="Cross-tenant project access denied",
                    )
                from backend.services.auth_service import get_user_org_membership

                membership = await get_user_org_membership(
                    db, current_user.id, proj.organization_id
                )
                org_id = membership.organization_id
            elif not has_explicit_auth and settings.KOBITS_DEV_MODE:
                org_id = proj.organization_id

    return current_user, org_id, project_id


class EpisodicCreateRequest(BaseModel):
    content: str = Field(..., min_length=1)
    stability: float = Field(default=86400.0, gt=0.0)
    tags: List[str] = Field(default_factory=list)


class EpisodicRecallRequest(BaseModel):
    alpha: float = Field(default=0.5, gt=0.0, le=10.0)


class EpisodicPruneRequest(BaseModel):
    min_retention: float = Field(default=0.05, ge=0.0, le=1.0)


class SemanticCreateRequest(BaseModel):
    subject: str = Field(..., min_length=1)
    key: str = Field(..., min_length=1)
    statement: str = Field(..., min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    base_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    depends_on_ids: List[str] = Field(default_factory=list)


class SemanticResolveRequest(BaseModel):
    winning_id: str = Field(..., min_length=1)
    superseded_id: str = Field(..., min_length=1)
    reason: str = Field(default="Manual conflict resolution")


class SemanticTargetedResolveRequest(BaseModel):
    superseded_id: str = Field(..., min_length=1)
    reason: str = Field(default="Manual conflict resolution")


class PlaybookCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = ""
    steps: List[Any] = Field(default_factory=list)
    trigger_tags: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    parent_playbook_id: Optional[str] = None


class PlaybookExecuteRequest(BaseModel):
    outcome: str = Field(..., min_length=1)


# ── Tier 1: Episodic Memory (Ebbinghaus Decay & Monotonic Reinforcement) ────

@router.post("/{project_id}/episodic", status_code=201)
async def create_episodic_memory(
    project_id: str,
    req: EpisodicCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    try:
        rec = await MemoryOSService.store_episodic(
            db=db,
            org_id=org_id,
            project_id=project_id,
            content=req.content,
            stability=req.stability,
            tags=req.tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return rec.to_dict()


@router.get("/{project_id}/episodic")
async def list_episodic_memories(
    project_id: str,
    request: Request,
    query: Optional[str] = None,
    min_retention: float = Query(default=0.10, ge=0.0, le=1.0),
    limit: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    pairs = await MemoryOSService.query_episodic(
        db=db,
        org_id=org_id,
        project_id=project_id,
        query_text=query,
        min_retention=min_retention,
        limit=limit,
    )
    items = []
    for rec, live_r in pairs:
        d = rec.to_dict()
        d["retention"] = round(float(live_r), 6)
        items.append(d)
    return {"results": items, "count": len(items), "project_id": project_id}


@router.post("/{project_id}/episodic/prune")
async def prune_episodic_memories(
    project_id: str,
    request: Request,
    req: Optional[EpisodicPruneRequest] = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    threshold = req.min_retention if req is not None else 0.05
    pruned = await MemoryOSService.prune_decayed_episodic(
        db=db,
        org_id=org_id,
        project_id=project_id,
        min_retention=threshold,
    )
    return {"pruned_count": pruned, "min_retention": threshold, "project_id": project_id}


@router.post("/{project_id}/episodic/{memory_id}/recall")
@router.post("/{project_id}/episodic/{memory_id}/reinforce")
async def recall_episodic_memory_endpoint(
    project_id: str,
    memory_id: str,
    request: Request,
    req: Optional[EpisodicRecallRequest] = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    alpha = req.alpha if req is not None else 0.5
    try:
        rec = await MemoryOSService.recall_episodic(
            db=db,
            org_id=org_id,
            project_id=project_id,
            memory_id=memory_id,
            alpha=alpha,
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return rec.to_dict()


# ── Tier 2: Semantic Memory (Contradiction Engine & Belief Propagation) ─────

@router.post("/{project_id}/semantic", status_code=201)
async def create_semantic_memory(
    project_id: str,
    req: SemanticCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    try:
        rec, conflict = await MemoryOSService.store_semantic(
            db=db,
            org_id=org_id,
            project_id=project_id,
            subject=req.subject,
            key=req.key,
            statement=req.statement,
            confidence=req.confidence,
            base_confidence=req.base_confidence,
            depends_on_ids=req.depends_on_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rec_dict = rec.to_dict()
    return {
        **rec_dict,
        "record": rec_dict,
        "conflict": conflict.to_dict() if conflict is not None else None,
    }


@router.get("/{project_id}/semantic")
async def list_semantic_memories(
    project_id: str,
    request: Request,
    status: Optional[str] = Query(default="ACTIVE"),
    subject: Optional[str] = None,
    key: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    effective_status: Optional[str] = status
    if status is not None and status.strip().upper() in ("ALL", "*", ""):
        effective_status = None
    elif status is not None:
        effective_status = status.strip().upper()

    records = await MemoryOSService.query_semantic(
        db=db,
        org_id=org_id,
        project_id=project_id,
        status=effective_status,
        subject=subject,
        key=key,
        limit=limit,
    )
    items = [r.to_dict() for r in records]
    return {"results": items, "count": len(items), "project_id": project_id}


@router.post("/{project_id}/semantic/resolve")
async def resolve_semantic_conflict_endpoint(
    project_id: str,
    req: SemanticResolveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    try:
        return await MemoryOSService.resolve_conflict(
            db=db,
            org_id=org_id,
            project_id=project_id,
            winning_id=req.winning_id,
            superseded_id=req.superseded_id,
            reason=req.reason,
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.post("/{project_id}/semantic/{winning_id}/resolve")
async def resolve_semantic_conflict_targeted_endpoint(
    project_id: str,
    winning_id: str,
    req: SemanticTargetedResolveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    try:
        return await MemoryOSService.resolve_conflict(
            db=db,
            org_id=org_id,
            project_id=project_id,
            winning_id=winning_id,
            superseded_id=req.superseded_id,
            reason=req.reason,
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.post("/{project_id}/semantic/{root_id}/propagate")
async def propagate_semantic_beliefs_endpoint(
    project_id: str,
    root_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    report = await MemoryOSService.propagate_beliefs(
        db=db,
        org_id=org_id,
        project_id=project_id,
        root_id=root_id,
    )
    await db.commit()
    return report.to_dict()


# ── Tier 3: Procedural Memory (Playbooks & Laplace Reliability) ─────────────

@router.post("/{project_id}/playbooks", status_code=201)
@router.post("/{project_id}/procedural", status_code=201)
async def create_procedural_playbook_endpoint(
    project_id: str,
    req: PlaybookCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    try:
        pb = await MemoryOSService.store_playbook(
            db=db,
            org_id=org_id,
            project_id=project_id,
            name=req.name,
            description=req.description,
            steps=req.steps,
            trigger_tags=req.trigger_tags,
            preconditions=req.preconditions,
            parent_playbook_id=req.parent_playbook_id,
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return pb.to_dict()


@router.get("/{project_id}/playbooks")
@router.get("/{project_id}/procedural")
async def list_procedural_playbooks_endpoint(
    project_id: str,
    request: Request,
    query: Optional[str] = None,
    context_query: Optional[str] = None,
    tags: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    effective_query = query if query is not None else context_query
    pbs = await MemoryOSService.query_playbooks(
        db=db,
        org_id=org_id,
        project_id=project_id,
        context_query=effective_query,
        tags=tag_list,
        limit=limit,
    )
    items = [p.to_dict() for p in pbs]
    return {"results": items, "count": len(items), "project_id": project_id}


@router.post("/{project_id}/playbooks/{playbook_id}/execute")
@router.post("/{project_id}/procedural/{playbook_id}/record-execution")
async def record_playbook_execution_endpoint(
    project_id: str,
    playbook_id: str,
    req: PlaybookExecuteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    try:
        pb = await MemoryOSService.record_playbook_execution(
            db=db,
            org_id=org_id,
            project_id=project_id,
            playbook_id=playbook_id,
            outcome=req.outcome.strip().upper(),  # type: ignore[arg-type]
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return pb.to_dict()


# ── Tier 4: Cryptographic SHA-256 Mutation Ledger & Tamper Detection ────────

@router.get("/{project_id}/ledger/verify")
@router.post("/{project_id}/ledger/verify")
@router.get("/{project_id}/integrity/verify")
async def verify_project_ledger_endpoint(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    is_valid, corrupted_id, error_msg = await MemoryOSService.verify_integrity(
        db=db,
        org_id=org_id,
        project_id=project_id,
    )

    stmt = (
        select(MemoryLedgerEntry)
        .where(
            and_(
                MemoryLedgerEntry.organization_id == org_id,
                MemoryLedgerEntry.project_id == project_id,
            )
        )
        .order_by(MemoryLedgerEntry.sequence_number.desc())
    )
    res = await db.execute(stmt)
    entries = list(res.scalars().all())
    head_entry = entries[0] if entries else None

    return {
        "is_valid": is_valid,
        "valid": is_valid,
        "corrupted_entry_id": corrupted_id,
        "error_message": error_msg,
        "entry_count": len(entries),
        "latest_sequence": int(head_entry.sequence_number) if head_entry else 0,
        "head_hash": str(head_entry.entry_hash) if head_entry else GENESIS_HASH,
        "project_id": project_id,
    }


@router.get("/{project_id}/ledger")
@router.get("/{project_id}/integrity/audit-trail")
async def list_project_ledger_entries_endpoint(
    project_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)
    stmt = (
        select(MemoryLedgerEntry)
        .where(
            and_(
                MemoryLedgerEntry.organization_id == org_id,
                MemoryLedgerEntry.project_id == project_id,
            )
        )
        .order_by(MemoryLedgerEntry.sequence_number.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    entries = [e.to_dict() for e in res.scalars().all()]
    return {"entries": entries, "count": len(entries), "project_id": project_id}
