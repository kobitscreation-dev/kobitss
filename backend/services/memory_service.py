from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
import json

from backend.models.memory import AgentMemory, MissionMemory
from backend.models.project import ProjectMemory, ProjectMemoryStatus
from backend.models.communication import AgentMessage, AgentArtifact, AgentDecision, AgentLesson, AgentLessonType, MessageType

async def store_agent_memory(
    db: AsyncSession,
    agent_id: str,
    organization_id: str,
    project_id: Optional[str],
    category: str,
    key: str,
    value: str
) -> AgentMemory:
    """Stores private agent memory after basic scrubbing (mocked security check)."""
    # Scrubbing check mocked
    if "api_key" in value.lower() or "password" in value.lower():
        value = "<SCRUBBED_SECRET>"

    mem = AgentMemory(
        agent_id=agent_id,
        organization_id=organization_id,
        project_id=project_id,
        category=category,
        key=key,
        value=value,
        relevance_score=1.0
    )
    db.add(mem)
    await db.commit()
    await db.refresh(mem)
    return mem

async def publish_project_knowledge(
    db: AsyncSession,
    agent_id: str,
    organization_id: str,
    project_id: str,
    category: str,
    key: str,
    value: str,
    confidence: float = 1.0,
    evidence: str = None
) -> ProjectMemory:
    """Publishes knowledge to the shared project bus, handling versioning."""
    if "api_key" in value.lower() or "password" in value.lower():
        value = "<SCRUBBED_SECRET>"

    mem = ProjectMemory(
        project_id=project_id,
        organization_id=organization_id,
        category=category,
        key=key,
        value=value,
        source_agent_id=agent_id,
        confidence=confidence,
        evidence=evidence,
        version=1,
        memory_status=ProjectMemoryStatus.ACTIVE
    )
    db.add(mem)
    await db.commit()
    await db.refresh(mem)
    return mem

async def store_mission_memory(
    db: AsyncSession,
    organization_id: str,
    project_id: str,
    mission_id: str,
    key: str,
    value: str
) -> MissionMemory:
    mem = MissionMemory(
        mission_id=mission_id,
        project_id=project_id,
        organization_id=organization_id,
        key=key,
        value=value
    )
    db.add(mem)
    await db.commit()
    return mem

async def record_decision(
    db: AsyncSession,
    organization_id: str,
    project_id: str,
    mission_id: str,
    agent_id: str,
    decision: str,
    reason: str,
    evidence: Optional[str] = None,
    alternatives: Optional[str] = None
) -> AgentDecision:
    dec = AgentDecision(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        agent_id=agent_id,
        decision=decision,
        reason=reason,
        evidence=evidence,
        alternatives=alternatives
    )
    db.add(dec)
    await db.commit()
    return dec

async def record_lesson(
    db: AsyncSession,
    organization_id: str,
    project_id: str,
    mission_id: str,
    agent_id: str,
    type_enum: AgentLessonType,
    cause: str,
    lesson: str
) -> AgentLesson:
    les = AgentLesson(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        agent_id=agent_id,
        type=type_enum,
        cause=cause,
        lesson=lesson
    )
    db.add(les)
    await db.commit()
    return les

async def send_agent_message(
    db: AsyncSession,
    organization_id: str,
    mission_id: str,
    sender_agent_id: str,
    receiver_agent_id: Optional[str],
    task_id: Optional[str],
    msg_type: MessageType,
    content: str
) -> AgentMessage:
    msg = AgentMessage(
        organization_id=organization_id,
        mission_id=mission_id,
        sender_agent_id=sender_agent_id,
        receiver_agent_id=receiver_agent_id,
        task_id=task_id,
        type=msg_type,
        content=content
    )
    db.add(msg)
    await db.commit()
    return msg

async def publish_artifact(
    db: AsyncSession,
    organization_id: str,
    mission_id: str,
    task_id: Optional[str],
    producer_agent_id: str,
    consumer_agent_ids: List[str],
    artifact_type: str,
    content: str,
    summary: str
) -> AgentArtifact:
    art = AgentArtifact(
        organization_id=organization_id,
        mission_id=mission_id,
        task_id=task_id,
        producer_agent_id=producer_agent_id,
        consumer_agent_ids_json=json.dumps(consumer_agent_ids),
        type=artifact_type,
        content=content,
        summary=summary
    )
    db.add(art)
    await db.commit()
    return art

from sqlalchemy import select
from backend.models.memory import CodeDocument, AgentMemory

class MemoryService:
    @staticmethod
    async def search_codebase(db, project_id: str, query: str, limit: int = 10, threshold: float = 0.05):
        import math
        import numpy as np
        from backend.services.embedding import EmbeddingUtils, tokenize_code_identifiers

        stmt = select(CodeDocument).where(CodeDocument.project_id == project_id)
        result = await db.execute(stmt)
        docs = result.scalars().all()

        if not docs or not query or not query.strip():
            return []

        query_vec = np.array(EmbeddingUtils.generate_embedding(query), dtype=np.float64)
        q_tokens = tokenize_code_identifiers(query)
        q_token_set = set(q_tokens)

        # Precompute document token sets & IDF weights for BM25-style lexical scoring
        N = len(docs)
        doc_token_sets = []
        df_counts = {}
        for d in docs:
            meta = d.metadata_json if isinstance(d.metadata_json, dict) else {}
            sym = meta.get("symbol", "")
            combined_text = f"{d.file_path} {sym} {d.content_chunk or ''}"
            d_toks = set(tokenize_code_identifiers(combined_text))
            doc_token_sets.append(d_toks)
            for t in q_token_set:
                if t in d_toks:
                    df_counts[t] = df_counts.get(t, 0) + 1

        idf_weights = {
            t: math.log(1.0 + (N - df_counts.get(t, 0) + 0.5) / (df_counts.get(t, 0) + 0.5))
            for t in q_token_set
        }
        total_q_idf = sum(idf_weights.values()) or 1.0

        scored_docs = []
        q_lower = query.strip().lower()

        for idx, d in enumerate(docs):
            meta = d.metadata_json if isinstance(d.metadata_json, dict) else {}
            sym = (meta.get("symbol") or "").strip()
            sym_lower = sym.lower()
            path_lower = (d.file_path or "").lower()
            d_toks = doc_token_sets[idx]

            # 1. Dense Cosine Similarity
            dense_sim = 0.0
            emb_data = getattr(d, "embedding", None)
            if emb_data:
                try:
                    doc_vec = np.array(emb_data, dtype=np.float64)
                    if doc_vec.shape == query_vec.shape:
                        dense_sim = max(0.0, float(np.dot(query_vec, doc_vec)))
                except Exception:
                    dense_sim = 0.0

            # 2. IDF-Weighted Lexical Identifier Overlap
            matched_idf = sum(idf_weights[t] for t in q_token_set if t in d_toks)
            lexical_sim = matched_idf / total_q_idf if q_token_set else 0.0

            # 3. Structural AST Symbol & File Path Boost
            symbol_boost = 0.0
            if sym_lower:
                if sym_lower == q_lower or sym_lower.endswith("." + q_lower):
                    symbol_boost += 0.35
                elif any(t == sym_lower or sym_lower.endswith("." + t) for t in q_token_set):
                    symbol_boost += 0.25
                elif any(t in sym_lower for t in q_token_set):
                    symbol_boost += 0.15

            if any(t in path_lower for t in q_token_set):
                symbol_boost += 0.10

            # Exact contiguous substring bonus
            if len(q_lower) >= 3 and q_lower in (d.content_chunk or "").lower():
                symbol_boost += 0.20

            hybrid_score = (0.50 * dense_sim) + (0.35 * lexical_sim) + symbol_boost

            if hybrid_score >= threshold:
                scored_docs.append({
                    "file": d.file_path,
                    "start_line": meta.get("start_line", 1),
                    "end_line": meta.get("end_line", 1),
                    "symbol": sym,
                    "kind": meta.get("kind", "block"),
                    "content": d.content_chunk,
                    "similarity": round(min(1.0, hybrid_score), 4),
                    "dense_similarity": round(dense_sim, 4),
                    "lexical_similarity": round(lexical_sim, 4),
                })

        scored_docs.sort(key=lambda x: (x["similarity"], x["lexical_similarity"], x["dense_similarity"]), reverse=True)
        return scored_docs[:limit]

    @staticmethod
    async def get_architecture_context(db, project_id: str):
        stmt = select(AgentMemory).where(
            AgentMemory.project_id == project_id,
            AgentMemory.category == "architecture"
        )
        result = await db.execute(stmt)
        memories = result.scalars().all()
        return {m.key: m.value for m in memories}

    @staticmethod
    async def add_project_memory(db, project_id: str, category: str, content: str):
        import uuid
        memory = AgentMemory(
            id=str(uuid.uuid4()),
            organization_id="default",
            project_id=project_id,
            agent_id="human",
            category=category,
            key=f"manual-{str(uuid.uuid4())[:8]}",
            value=content
        )
        db.add(memory)
        await db.commit()
        return memory

    @staticmethod
    async def get_full_knowledge(db, project_id: str):
        # Fetch ProjectMemory
        res_pm = await db.execute(select(ProjectMemory).where(ProjectMemory.project_id == project_id))
        project_memories = res_pm.scalars().all()
        
        # Fetch AgentMemory
        res_am = await db.execute(select(AgentMemory).where(AgentMemory.project_id == project_id))
        agent_memories = res_am.scalars().all()
        
        # Fetch MissionMemory
        res_mm = await db.execute(select(MissionMemory).where(MissionMemory.project_id == project_id))
        mission_memories = res_mm.scalars().all()
        
        # Fetch AgentDecision
        res_dec = await db.execute(select(AgentDecision).where(AgentDecision.project_id == project_id))
        decisions = res_dec.scalars().all()
        
        # Fetch AgentLesson
        res_les = await db.execute(select(AgentLesson).where(AgentLesson.project_id == project_id))
        lessons = res_les.scalars().all()
        
        knowledge = []
        
        for pm in project_memories:
            knowledge.append({
                "id": pm.id,
                "type": "Project Knowledge",
                "content": f"{pm.key}: {pm.value}",
                "source": pm.source_agent_id or "System",
                "scope": "Project",
                "timestamp": pm.created_at,
                "confidence": pm.confidence,
                "category": pm.category
            })
            
        for am in agent_memories:
            knowledge.append({
                "id": am.id,
                "type": "Agent Memory",
                "content": f"{am.key}: {am.value}",
                "source": am.agent_id or "System",
                "scope": "Agent",
                "timestamp": am.created_at,
                "confidence": am.relevance_score,
                "category": am.category
            })
            
        for mm in mission_memories:
            knowledge.append({
                "id": mm.id,
                "type": "Mission Context",
                "content": f"{mm.key}: {mm.value}",
                "source": "Mission",
                "scope": "Mission",
                "timestamp": mm.created_at,
                "confidence": None,
                "category": "Context"
            })
            
        for d in decisions:
            knowledge.append({
                "id": d.id,
                "type": "Decision",
                "content": f"Decision: {d.decision}\nReason: {d.reason}",
                "source": d.agent_id or "System",
                "scope": "Mission",
                "timestamp": d.created_at,
                "confidence": None,
                "category": "Architecture"
            })
            
        for l in lessons:
            knowledge.append({
                "id": l.id,
                "type": "Lesson",
                "content": f"Cause: {l.cause}\nLesson: {l.lesson}",
                "source": l.agent_id or "System",
                "scope": "Mission",
                "timestamp": l.created_at,
                "confidence": None,
                "category": "Retrospective"
            })
            
        return knowledge

    @staticmethod
    async def search_graph_nodes(db: AsyncSession, project_id: str, query: str, limit: int = 20):
        from backend.models.graph import GraphNode
        from backend.services.embedding import tokenize_code_identifiers

        if not query or not query.strip():
            return []

        stmt = select(GraphNode).where(GraphNode.project_id == project_id)
        result = await db.execute(stmt)
        all_nodes = result.scalars().all()
        if not all_nodes:
            return []

        q_lower = query.strip().lower()
        q_tokens = set(tokenize_code_identifiers(query))

        scored_nodes = []
        for n in all_nodes:
            name_lower = (n.name or "").lower()
            file_lower = (n.file_path or "").lower()
            node_tokens = set(tokenize_code_identifiers(f"{n.name or ''} {n.file_path or ''} {n.content or ''}"))

            score = 0.0
            if q_lower in name_lower:
                score += 2.0 if q_lower == name_lower else 1.2
            if q_tokens:
                overlap = len(q_tokens & node_tokens)
                if overlap > 0:
                    score += overlap / len(q_tokens)
            if q_lower in file_lower:
                score += 0.5

            if score > 0:
                meta = n.metadata_json if isinstance(n.metadata_json, dict) else {}
                scored_nodes.append((
                    score,
                    {
                        "id": n.id,
                        "type": n.node_type,
                        "name": n.name,
                        "file": n.file_path,
                        "line": meta.get("line", 1),
                        "content": n.content,
                        "score": round(score, 4),
                    },
                ))

        scored_nodes.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored_nodes[:limit]]

    @staticmethod
    async def get_graph_node_details(db: AsyncSession, project_id: str, node_id: str):
        from backend.models.graph import GraphNode, GraphEdge
        from sqlalchemy.orm import selectinload
        
        stmt = select(GraphNode).where(
            GraphNode.project_id == project_id,
            GraphNode.id == node_id
        )
        result = await db.execute(stmt)
        node = result.scalars().first()
        if not node:
            return None
            
        # Get edges where this node is source (what it defines/calls)
        stmt_out = select(GraphEdge).options(selectinload(GraphEdge.target)).where(GraphEdge.source_id == node_id)
        result_out = await db.execute(stmt_out)
        edges_out = result_out.scalars().all()
        
        # Get edges where this node is target (where it is defined/called)
        stmt_in = select(GraphEdge).options(selectinload(GraphEdge.source)).where(GraphEdge.target_id == node_id)
        result_in = await db.execute(stmt_in)
        edges_in = result_in.scalars().all()
        
        return {
            "node": {"id": node.id, "type": node.node_type, "name": node.name, "file": node.file_path, "content": node.content},
            "outgoing_edges": [{"edge_type": e.edge_type, "target": {"id": e.target.id, "type": e.target.node_type, "name": e.target.name, "file": e.target.file_path}} for e in edges_out if e.target],
            "incoming_edges": [{"edge_type": e.edge_type, "source": {"id": e.source.id, "type": e.source.node_type, "name": e.source.name, "file": e.source.file_path}} for e in edges_in if e.source]
        }
