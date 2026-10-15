"""
Vector Store & pgvector Integration Service for Multi-Agent Systems.

Production dual-mode vector similarity search:
1. AWS RDS PostgreSQL with pgvector:
   Executes native SQL cosine distance (`<=>`) queries with index acceleration.
2. SQLite / Local In-Memory Fallback:
   Loads candidate JSON embeddings and computes vectorized NumPy dot products
   for zero-cloud local testing and CI/CD pipelines.
"""

import json
from typing import Optional, Dict, Any, List, Union
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from backend.models.memory import CodeDocument
from backend.models.project import ProjectMemory, ProjectMemoryStatus


class VectorStoreService:
    """
    Enterprise Vector Store Service.
    Seamlessly utilizes AWS RDS PostgreSQL native `pgvector` extension when available,
    and falls back to high-performance NumPy vectorized search in SQLite.
    """

    _has_pgvector: Optional[bool] = None

    @classmethod
    async def is_pgvector_active(cls, db: AsyncSession) -> bool:
        """Checks if connected to PostgreSQL and if the `vector` extension is active."""
        if cls._has_pgvector is not None:
            return cls._has_pgvector

        bind = db.bind
        dialect_name = bind.dialect.name if bind else "sqlite"
        if dialect_name != "postgresql":
            cls._has_pgvector = False
            return False

        try:
            res = await db.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector' LIMIT 1;"))
            row = res.scalar()
            cls._has_pgvector = bool(row)
        except Exception:
            cls._has_pgvector = False

        return cls._has_pgvector

    @classmethod
    async def search_code_documents(
        cls,
        db: AsyncSession,
        project_id: str,
        query_vec: Union[List[float], np.ndarray],
        limit: int = 15,
        threshold: float = 0.05,
    ) -> List[Dict[str, Any]]:
        """
        Executes semantic vector search over code documents for a given project.
        Uses native pgvector if on PostgreSQL, or NumPy cosine distance if on SQLite.
        """
        if isinstance(query_vec, list):
            q_arr = np.array(query_vec, dtype=np.float64)
        else:
            q_arr = query_vec.astype(np.float64)

        q_norm = np.linalg.norm(q_arr)
        if q_norm > 0:
            q_arr = q_arr / q_norm

        is_pgv = await cls.is_pgvector_active(db)

        # 1. Native PostgreSQL pgvector path (if vector column exists and active)
        if is_pgv:
            try:
                # Format vector literal e.g. '[0.1,0.2,...]'
                vec_str = "[" + ",".join(f"{x:.6f}" for x in q_arr) + "]"
                raw_sql = text("""
                    SELECT id, file_path, content_chunk, metadata_json,
                           1 - (embedding <=> :vec_param::vector) AS similarity
                    FROM code_documents
                    WHERE project_id = :project_id
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> :vec_param::vector ASC
                    LIMIT :limit;
                """)
                res = await db.execute(raw_sql, {
                    "project_id": project_id,
                    "vec_param": vec_str,
                    "limit": limit,
                })
                rows = res.fetchall()
                if rows:
                    hits = []
                    for r in rows:
                        sim = float(r[4]) if r[4] is not None else 0.0
                        if sim >= threshold:
                            meta = r[3] if isinstance(r[3], dict) else {}
                            hits.append({
                                "id": r[0],
                                "file": r[1],
                                "content": r[2],
                                "start_line": meta.get("start_line", 1),
                                "end_line": meta.get("end_line", 1),
                                "symbol": meta.get("symbol", ""),
                                "kind": meta.get("kind", "block"),
                                "similarity": round(max(0.0, min(1.0, sim)), 4),
                            })
                    return hits
            except Exception:
                # If column is stored as JSON instead of vector type, fallback gracefully
                pass

        # 2. Vectorized NumPy fallback (works across SQLite and PostgreSQL JSON columns)
        stmt = select(CodeDocument).where(CodeDocument.project_id == project_id)
        res = await db.execute(stmt)
        docs = res.scalars().all()
        if not docs:
            return []

        scored = []
        for d in docs:
            raw_emb = d.embedding
            if not raw_emb:
                continue
            if isinstance(raw_emb, str):
                try:
                    raw_emb = json.loads(raw_emb)
                except Exception:
                    continue

            d_arr = np.array(raw_emb, dtype=np.float64)
            d_norm = np.linalg.norm(d_arr)
            if d_norm > 0:
                d_arr = d_arr / d_norm

            sim = 0.0
            if d_arr.shape == q_arr.shape:
                sim = float(np.dot(q_arr, d_arr))
                sim = max(0.0, sim)

            if sim >= threshold:
                meta = d.metadata_json if isinstance(d.metadata_json, dict) else {}
                scored.append({
                    "id": d.id,
                    "file": d.file_path,
                    "content": d.content_chunk,
                    "start_line": meta.get("start_line", 1),
                    "end_line": meta.get("end_line", 1),
                    "symbol": meta.get("symbol", ""),
                    "kind": meta.get("kind", "block"),
                    "similarity": round(min(1.0, sim), 4),
                })

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:limit]

    @classmethod
    async def search_project_memory(
        cls,
        db: AsyncSession,
        project_id: str,
        query_vec: Union[List[float], np.ndarray],
        category: Optional[str] = None,
        limit: int = 10,
        threshold: float = 0.05,
    ) -> List[Dict[str, Any]]:
        """
        Executes semantic search over cross-session project memories (ADRs, defect fixes, etc.).
        """
        if isinstance(query_vec, list):
            q_arr = np.array(query_vec, dtype=np.float64)
        else:
            q_arr = query_vec.astype(np.float64)

        q_norm = np.linalg.norm(q_arr)
        if q_norm > 0:
            q_arr = q_arr / q_norm

        stmt = select(ProjectMemory).where(
            ProjectMemory.project_id == project_id,
            ProjectMemory.memory_status == ProjectMemoryStatus.ACTIVE,
        )
        if category:
            stmt = stmt.where(ProjectMemory.category == category)

        res = await db.execute(stmt)
        memories = res.scalars().all()
        if not memories:
            return []

        scored = []
        for m in memories:
            raw_emb = m.embedding_json
            if not raw_emb:
                # Generate from content on the fly if missing
                from backend.services.embedding import EmbeddingUtils
                full_text = f"{m.key} {m.value}"
                raw_emb = EmbeddingUtils.generate_embedding(full_text)
                m.embedding_json = json.dumps(raw_emb)

            if isinstance(raw_emb, str):
                try:
                    raw_emb = json.loads(raw_emb)
                except Exception:
                    continue

            m_arr = np.array(raw_emb, dtype=np.float64)
            m_norm = np.linalg.norm(m_arr)
            if m_norm > 0:
                m_arr = m_arr / m_norm

            sim = 0.0
            if m_arr.shape == q_arr.shape:
                sim = float(np.dot(q_arr, m_arr))
                sim = max(0.0, sim)

            if sim >= threshold:
                scored.append({
                    "id": m.id,
                    "category": m.category,
                    "key": m.key,
                    "value": m.value,
                    "confidence": m.confidence,
                    "evidence": m.evidence,
                    "similarity": round(min(1.0, sim), 4),
                    "created_at": m.created_at.isoformat() if hasattr(m.created_at, "isoformat") else str(m.created_at),
                })

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:limit]
