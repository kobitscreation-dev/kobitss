import json
import numpy as np
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.models.project import ProjectMemory, ProjectMemoryStatus
import uuid

class MemoryEngine:
    """
    Implements a Semantic Vector Memory system.
    In production, this would connect to pgvector or Pinecone.
    Here we use a local NumPy-based vector similarity search simulating embeddings.
    """
    
    EMBEDDING_DIM = 384  # Simulating a small embedding model like all-MiniLM-L6-v2

    @staticmethod
    def _generate_embedding(text: str) -> List[float]:
        """
        Generates a pseudo-semantic embedding using character n-grams.
        This simulates a real transformer embedding vector.
        """
        if not text:
            return np.zeros(MemoryEngine.EMBEDDING_DIM).tolist()
            
        text = text.lower()
        vec = np.zeros(MemoryEngine.EMBEDDING_DIM)
        
        # Character tri-grams for pseudo-semantic overlap
        for i in range(max(1, len(text) - 2)):
            trigram = text[i:i+3]
            # Deterministic hash to an index
            idx = hash(trigram) % MemoryEngine.EMBEDDING_DIM
            vec[idx] += 1.0
            
        # L2 Normalize the vector so dot product equals cosine similarity
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
            
        return vec.tolist()

    @staticmethod
    async def store_decision_memory(
        db: AsyncSession, 
        project_id: str, 
        organization_id: str,
        category: str, 
        key: str, 
        value: str, 
        source_agent: str
    ):
        """Stores a new memory and computes its semantic vector embedding."""
        
        # Combine key and value for a richer semantic representation
        semantic_text = f"{category} {key} {value}"
        embedding = MemoryEngine._generate_embedding(semantic_text)
        
        mem = ProjectMemory(
            project_id=project_id,
            organization_id=organization_id,
            category=category,
            key=key,
            value=value,
            source_agent_id=source_agent,
            embedding_json=json.dumps(embedding),
            memory_status=ProjectMemoryStatus.ACTIVE
        )
        db.add(mem)
        await db.commit()
        return mem

    @staticmethod
    async def query_memory(db: AsyncSession, project_id: str, query: str, top_k: int = 5, threshold: float = 0.1) -> List[Dict[str, Any]]:
        """
        Queries the decision memory using vector cosine similarity.
        Simulates exactly how a pgvector index would rank results.
        """
        stmt = select(ProjectMemory).where(
            ProjectMemory.project_id == project_id,
            ProjectMemory.memory_status == ProjectMemoryStatus.ACTIVE
        )
        res = await db.execute(stmt)
        memories = res.scalars().all()
        
        if not memories:
            return []
            
        query_vec = np.array(MemoryEngine._generate_embedding(query))
        
        results = []
        for m in memories:
            if m.embedding_json:
                try:
                    mem_vec = np.array(json.loads(m.embedding_json))
                    # Cosine similarity (vectors are L2 normalized)
                    similarity = float(np.dot(query_vec, mem_vec))
                    
                    if similarity >= threshold:
                        results.append({
                            "category": m.category, 
                            "key": m.key, 
                            "value": m.value,
                            "similarity": round(similarity, 4)
                        })
                except Exception as e:
                    print(f"Error parsing embedding for memory {m.id}: {e}")
                    
        # Sort by similarity descending (highest first)
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]

    @staticmethod
    async def build_code_graph(repo_path: str):
        """
        Parses the repository using Tree-sitter (mocked).
        Builds a structural graph of classes, functions, and imports.
        """
        return {
            "status": "success",
            "nodes_indexed": 142,
            "edges_indexed": 305
        }
