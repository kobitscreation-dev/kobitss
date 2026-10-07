import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import or_, and_, desc

from backend.models.memory import AgentMemory, MissionMemory
from backend.models.project import ProjectMemory, ProjectMemoryStatus
from backend.models.communication import AgentMessage, AgentArtifact, AgentDecision, AgentLesson
from backend.models.agent import Agent
from backend.models.project import Project, Task
from backend.models.mission import Mission

# Quality-First: Cache identical static memory retrievals per project to avoid duplicate reads
_MEMORY_CACHE = {}

def _get_cache_key(prefix: str, org_id: str, proj_id: str, agent_id: str = "") -> str:
    return f"{prefix}_{org_id}_{proj_id}_{agent_id}"

async def build_agent_context(
    db: AsyncSession, 
    agent: Agent, 
    project: Project, 
    mission: Mission, 
    task: Task, 
    organization_id: str
) -> str:
    """
    Assembles the bounded intelligence context for an agent execution.
    Retrieves and ranks Agent Memory, Project Memory, Mission Memory, 
    Communication Messages, Artifacts, Decisions, and Lessons.
    """
    context_parts = []
    
    context_parts.append("==================================================")
    context_parts.append(f"AGENT IDENTITY & ROLE")
    context_parts.append("==================================================")
    context_parts.append(f"Name: {agent.name}")
    context_parts.append(f"Role: {agent.type.value}")
    if agent.system_prompt:
        context_parts.append(f"System Prompt: {agent.system_prompt}")

    # 1. Project Memory (Shared Knowledge)
    pm_cache_key = _get_cache_key("PM", organization_id, project.id)
    if pm_cache_key not in _MEMORY_CACHE:
        project_memories = await db.execute(
            select(ProjectMemory)
            .where(
                ProjectMemory.organization_id == organization_id,
                ProjectMemory.project_id == project.id,
                ProjectMemory.memory_status == ProjectMemoryStatus.ACTIVE
            )
            .order_by(desc(ProjectMemory.confidence))
            .limit(20)
        )
        _MEMORY_CACHE[pm_cache_key] = project_memories.scalars().all()
    
    pm_list = _MEMORY_CACHE[pm_cache_key]
    if pm_list:
        context_parts.append("\n==================================================")
        context_parts.append("PROJECT KNOWLEDGE (SHARED)")
        context_parts.append("==================================================")
        for pm in pm_list:
            source = f" (Source: {pm.source_agent_id})" if pm.source_agent_id else ""
            context_parts.append(f"[{pm.category.upper()}] {pm.key}{source}: {pm.value}")

    # 2. Agent Memory (Private)
    agent_memories = await db.execute(
        select(AgentMemory)
        .where(
            AgentMemory.organization_id == organization_id,
            AgentMemory.agent_id == agent.name,  # using name as ID conceptually, or agent.id
            or_(AgentMemory.project_id == project.id, AgentMemory.project_id == None)
        )
        .order_by(desc(AgentMemory.relevance_score))
        .limit(10)
    )
    am_list = agent_memories.scalars().all()
    if am_list:
        context_parts.append("\n==================================================")
        context_parts.append("YOUR PRIVATE AGENT MEMORY")
        context_parts.append("==================================================")
        for am in am_list:
            context_parts.append(f"[{am.category.upper()}] {am.key}: {am.value}")

    # 3. Mission Memory
    mission_memories = await db.execute(
        select(MissionMemory)
        .where(
            MissionMemory.organization_id == organization_id,
            MissionMemory.mission_id == mission.id
        )
        .limit(10)
    )
    mm_list = mission_memories.scalars().all()
    if mm_list:
        context_parts.append("\n==================================================")
        context_parts.append("MISSION CONTEXT & EVENTS")
        context_parts.append("==================================================")
        for mm in mm_list:
            context_parts.append(f"- {mm.key}: {mm.value}")

    # 4. Agent Decisions (Project level)
    decisions = await db.execute(
        select(AgentDecision)
        .where(
            AgentDecision.organization_id == organization_id,
            AgentDecision.project_id == project.id
        )
        .limit(5) # limit for context bounding
    )
    dec_list = decisions.scalars().all()
    if dec_list:
        context_parts.append("\n==================================================")
        context_parts.append("RECENT PROJECT DECISIONS")
        context_parts.append("==================================================")
        for dec in dec_list:
            context_parts.append(f"Decision by {dec.agent_id}: {dec.decision}")
            context_parts.append(f"Reason: {dec.reason}")

    # 5. Agent Lessons (Failures/Reviews)
    lessons = await db.execute(
        select(AgentLesson)
        .where(
            AgentLesson.organization_id == organization_id,
            AgentLesson.agent_id == agent.name,
            AgentLesson.project_id == project.id
        )
        .limit(5)
    )
    lesson_list = lessons.scalars().all()
    if lesson_list:
        context_parts.append("\n==================================================")
        context_parts.append("LESSONS LEARNED (FAILURES & REVIEWS)")
        context_parts.append("==================================================")
        for lesson in lesson_list:
            context_parts.append(f"Type: {lesson.type.value}")
            context_parts.append(f"Cause: {lesson.cause}")
            context_parts.append(f"Lesson: {lesson.lesson}")

    # 6. Communication (Messages for this agent in this mission)
    messages = await db.execute(
        select(AgentMessage)
        .where(
            AgentMessage.organization_id == organization_id,
            AgentMessage.mission_id == mission.id,
            or_(
                AgentMessage.receiver_agent_id == agent.name,
                AgentMessage.receiver_agent_id == None
            )
        )
        .limit(10)
    )
    msg_list = messages.scalars().all()
    if msg_list:
        context_parts.append("\n==================================================")
        context_parts.append("MESSAGES & COMMUNICATION")
        context_parts.append("==================================================")
        for msg in msg_list:
            target = f" -> {msg.receiver_agent_id}" if msg.receiver_agent_id else " (Broadcast)"
            context_parts.append(f"[{msg.type.value}] From {msg.sender_agent_id}{target}:\n{msg.content}")

    # 7. Artifacts (Produced for this agent)
    # SQLite json extraction is complex, so we'll fetch all mission artifacts and filter in python
    artifacts_res = await db.execute(
        select(AgentArtifact)
        .where(
            AgentArtifact.organization_id == organization_id,
            AgentArtifact.mission_id == mission.id
        )
    )
    artifacts = artifacts_res.scalars().all()
    relevant_artifacts = []
    for art in artifacts:
        try:
            consumers = json.loads(art.consumer_agent_ids_json or "[]")
            if not consumers or agent.name in consumers:
                relevant_artifacts.append(art)
        except:
            pass
    
    if relevant_artifacts:
        context_parts.append("\n==================================================")
        context_parts.append("UPSTREAM ARTIFACTS")
        context_parts.append("==================================================")
        for art in relevant_artifacts:
            context_parts.append(f"Artifact Type: {art.type}")
            context_parts.append(f"Produced by: {art.producer_agent_id}")
            context_parts.append(f"Summary: {art.summary}")
            context_parts.append(f"Content/Location:\n{art.content}")

    # 8. Semantic Codebase RAG & AST Symbol Graph Context
    search_query = f"{task.title or ''} {task.description or ''} {getattr(mission, 'objective', '') or ''}".strip()
    if search_query and project and getattr(project, "id", None):
        try:
            from backend.services.memory_service import MemoryService
            code_hits = await MemoryService.search_codebase(db, project.id, search_query, limit=5, threshold=0.05)
            graph_hits = await MemoryService.search_graph_nodes(db, project.id, search_query, limit=8)

            if code_hits or graph_hits:
                context_parts.append("\n==================================================")
                context_parts.append("RELEVANT CODEBASE CONTEXT (AST & SEMANTIC RAG)")
                context_parts.append("==================================================")
                if graph_hits:
                    context_parts.append("Matched Code Graph Symbols:")
                    for idx_gh, gh in enumerate(graph_hits):
                        context_parts.append(
                            f"  - [{gh.get('type', 'SYMBOL')}] {gh.get('name')} in {gh.get('file')}:{gh.get('line', 1)}"
                        )
                        if idx_gh < 3 and gh.get("id"):
                            details = await MemoryService.get_graph_node_details(db, project.id, gh["id"])
                            if details:
                                for out_e in (details.get("outgoing_edges") or [])[:4]:
                                    tgt = out_e.get("target") or {}
                                    context_parts.append(
                                        f"      -> {out_e.get('edge_type')} [{tgt.get('type')}] {tgt.get('name')} ({tgt.get('file')})"
                                    )
                                for in_e in (details.get("incoming_edges") or [])[:4]:
                                    src = in_e.get("source") or {}
                                    context_parts.append(
                                        f"      <- {in_e.get('edge_type')} from [{src.get('type')}] {src.get('name')} ({src.get('file')})"
                                    )
                if code_hits:
                    context_parts.append("\nTop AST Code Chunks:")
                    for ch in code_hits:
                        sym_info = f" ({ch.get('symbol')})" if ch.get("symbol") else ""
                        context_parts.append(
                            f"\n--- {ch.get('file')}:{ch.get('start_line', 1)}-{ch.get('end_line', 1)}{sym_info} "
                            f"[score={ch.get('similarity')}] ---\n{ch.get('content', '')[:1200]}"
                        )
        except Exception:
            pass

    # 9. Task Context
    context_parts.append("\n==================================================")
    context_parts.append("CURRENT TASK OBJECTIVE")
    context_parts.append("==================================================")
    context_parts.append(f"Task Title: {task.title}")
    context_parts.append(f"Description: {task.description or ''}")
    if task.input_context_json:
        context_parts.append(f"Inputs: {task.input_context_json}")

    return "\n".join(context_parts)

