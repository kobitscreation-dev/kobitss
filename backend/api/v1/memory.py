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
async def get_graph_node(project_id: str, node_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Get a specific node and all its incoming/outgoing edges (dependencies)."""
    node_data = await MemoryService.get_graph_node_details(db, project_id, node_id)
    if not node_data:
        raise HTTPException(status_code=404, detail="Node not found")
    return node_data

