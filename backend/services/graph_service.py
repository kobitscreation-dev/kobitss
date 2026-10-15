"""
Graph Traversal & Impact Blast Radius Service for Multi-Agent Systems.

Provides high-performance dependency graph traversal and blast radius analysis
over code symbols (FILE, CLASS, FUNCTION) and relations (CALLS, IMPORTS, DEFINES, INHERITS).

Dual-Engine Architecture:
1. PostgreSQL Native: Executes SQL `WITH RECURSIVE` Common Table Expressions (CTEs)
   with cycle detection (visited path tracking) directly inside PostgreSQL.
2. In-Memory / SQLite Engine: Executes depth-bounded BFS/DFS graph traversals with
   cycle prevention for local development and test suites.
"""

import os
from typing import Optional, Dict, Any, List, Set, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from backend.models.graph import GraphNode, GraphEdge


class GraphService:
    """
    Enterprise-grade Graph Traversal & Blast Radius Service.
    Enables autonomous agents (Nexus, Sentinel, Forge, Aegis) to analyze
    code dependencies, call hierarchies, and blast radius before changing code.
    """

    @classmethod
    async def find_downstream_dependencies(
        cls,
        db: AsyncSession,
        project_id: str,
        start_id_or_name: str,
        max_depth: int = 4,
        edge_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Traverses outgoing edges from a starting node to find all downstream
        dependencies up to `max_depth` hops.
        E.g. A calls B, B calls C -> [B (depth 1), C (depth 2)].
        """
        return await cls._traverse(
            db=db,
            project_id=project_id,
            start_id_or_name=start_id_or_name,
            direction="downstream",
            max_depth=max_depth,
            edge_types=edge_types,
        )

    @classmethod
    async def find_upstream_callers(
        cls,
        db: AsyncSession,
        project_id: str,
        target_id_or_name: str,
        max_depth: int = 4,
        edge_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Traverses incoming edges to find what depends on or calls the target node
        up to `max_depth` hops.
        E.g. X calls A -> [X (depth 1)].
        """
        return await cls._traverse(
            db=db,
            project_id=project_id,
            start_id_or_name=target_id_or_name,
            direction="upstream",
            max_depth=max_depth,
            edge_types=edge_types,
        )

    @classmethod
    async def compute_impact_blast_radius(
        cls,
        db: AsyncSession,
        project_id: str,
        target_symbol_or_path: str,
        max_depth: int = 4,
    ) -> Dict[str, Any]:
        """
        Computes the complete impact blast radius if a given symbol or file is modified.
        Traverses upstream callers and dependers, identifying:
        - Direct impact count (depth 1)
        - Transitive impact count (depth 2..max_depth)
        - List of distinct impacted files
        - Categorized risk level (LOW, MEDIUM, HIGH, CRITICAL)
        """
        if not target_symbol_or_path or not target_symbol_or_path.strip():
            return {
                "target": target_symbol_or_path,
                "risk_level": "LOW",
                "direct_impact_count": 0,
                "transitive_impact_count": 0,
                "total_impacted_symbols": 0,
                "impacted_files": [],
                "impacted_symbols": [],
            }

        # Find all nodes matching the target (could be exact ID, exact name, or file path)
        query_str = target_symbol_or_path.strip()
        stmt = select(GraphNode).where(
            GraphNode.project_id == project_id,
            (GraphNode.id == query_str) | (GraphNode.name == query_str) | (GraphNode.file_path == query_str)
        )
        res = await db.execute(stmt)
        root_nodes = res.scalars().all()

        if not root_nodes:
            # Try matching by filename basename or symbol suffix
            alt_stmt = select(GraphNode).where(
                GraphNode.project_id == project_id,
                (GraphNode.name.ilike(f"%{query_str}%")) | (GraphNode.file_path.ilike(f"%{query_str}%"))
            ).limit(10)
            res_alt = await db.execute(alt_stmt)
            root_nodes = res_alt.scalars().all()

        if not root_nodes:
            return {
                "target": target_symbol_or_path,
                "risk_level": "LOW",
                "direct_impact_count": 0,
                "transitive_impact_count": 0,
                "total_impacted_symbols": 0,
                "impacted_files": [],
                "impacted_symbols": [],
            }

        all_impacted: Dict[str, Dict[str, Any]] = {}
        for root in root_nodes:
            upstream = await cls.find_upstream_callers(
                db=db,
                project_id=project_id,
                target_id_or_name=root.id,
                max_depth=max_depth,
            )
            for item in upstream:
                node_id = item["id"]
                if node_id == root.id:
                    continue
                if node_id not in all_impacted or item["depth"] < all_impacted[node_id]["depth"]:
                    all_impacted[node_id] = item

        direct_count = sum(1 for it in all_impacted.values() if it["depth"] == 1)
        transitive_count = sum(1 for it in all_impacted.values() if it["depth"] > 1)
        impacted_files = sorted(list({it["file_path"] for it in all_impacted.values() if it.get("file_path")}))

        # Evaluate risk level based on blast radius size and core dependencies
        total = direct_count + transitive_count
        num_files = len(impacted_files)

        is_critical_file = any(
            any(frag in f.lower() for frag in ("config", "auth", "security", "database", "models", "main.py"))
            for f in impacted_files
        )

        if total >= 15 or num_files >= 6 or (total >= 8 and is_critical_file):
            risk_level = "CRITICAL"
        elif total >= 8 or num_files >= 3:
            risk_level = "HIGH"
        elif total >= 3 or num_files >= 2:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        sorted_symbols = sorted(list(all_impacted.values()), key=lambda x: (x["depth"], x["name"]))

        return {
            "target": target_symbol_or_path,
            "root_symbols": [{"id": r.id, "name": r.name, "type": r.node_type, "file": r.file_path} for r in root_nodes],
            "risk_level": risk_level,
            "direct_impact_count": direct_count,
            "transitive_impact_count": transitive_count,
            "total_impacted_symbols": total,
            "impacted_files_count": num_files,
            "impacted_files": impacted_files,
            "impacted_symbols": sorted_symbols[:50],  # Bound to top 50
        }

    @classmethod
    async def get_subgraph(
        cls,
        db: AsyncSession,
        project_id: str,
        root_ids: List[str],
        max_depth: int = 2,
    ) -> Dict[str, Any]:
        """
        Retrieves a connected subgraph centered around `root_ids` up to `max_depth`.
        Returns structured nodes and edges for UI visualization.
        """
        if not root_ids:
            return {"nodes": [], "edges": []}

        visited_node_ids: Set[str] = set()
        frontier: Set[str] = set(root_ids)
        edges_collector: Dict[str, Dict[str, Any]] = {}

        for depth in range(max_depth + 1):
            if not frontier:
                break
            visited_node_ids.update(frontier)
            if depth == max_depth:
                break

            # Fetch outgoing and incoming edges for frontier
            e_stmt = select(GraphEdge).where(
                GraphEdge.project_id == project_id,
                (GraphEdge.source_id.in_(frontier)) | (GraphEdge.target_id.in_(frontier))
            )
            e_res = await db.execute(e_stmt)
            next_frontier = set()
            for edge in e_res.scalars().all():
                edges_collector[edge.id] = {
                    "id": edge.id,
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "edge_type": edge.edge_type,
                }
                if edge.source_id not in visited_node_ids:
                    next_frontier.add(edge.source_id)
                if edge.target_id not in visited_node_ids:
                    next_frontier.add(edge.target_id)
            frontier = next_frontier

        # Fetch node details
        if not visited_node_ids:
            return {"nodes": [], "edges": []}

        n_stmt = select(GraphNode).where(
            GraphNode.project_id == project_id,
            GraphNode.id.in_(visited_node_ids)
        )
        n_res = await db.execute(n_stmt)
        nodes_list = [
            {
                "id": n.id,
                "name": n.name,
                "type": n.node_type,
                "file_path": n.file_path,
                "metadata": n.metadata_json or {},
            }
            for n in n_res.scalars().all()
        ]

        return {
            "nodes": nodes_list,
            "edges": list(edges_collector.values()),
        }

    # =========================================================================
    # Internal Traversal Dispatcher (PostgreSQL Recursive CTE vs In-Memory BFS)
    # =========================================================================

    @classmethod
    async def _traverse(
        cls,
        db: AsyncSession,
        project_id: str,
        start_id_or_name: str,
        direction: str = "downstream",
        max_depth: int = 4,
        edge_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        bind = db.bind
        dialect_name = bind.dialect.name if bind else "sqlite"

        # Check if connected to PostgreSQL
        if dialect_name == "postgresql":
            try:
                return await cls._traverse_postgresql_cte(
                    db=db,
                    project_id=project_id,
                    start_id_or_name=start_id_or_name,
                    direction=direction,
                    max_depth=max_depth,
                    edge_types=edge_types,
                )
            except Exception:
                # Fallback to python traversal if any CTE error occurs
                pass

        return await cls._traverse_in_memory_bfs(
            db=db,
            project_id=project_id,
            start_id_or_name=start_id_or_name,
            direction=direction,
            max_depth=max_depth,
            edge_types=edge_types,
        )

    @classmethod
    async def _traverse_postgresql_cte(
        cls,
        db: AsyncSession,
        project_id: str,
        start_id_or_name: str,
        direction: str,
        max_depth: int,
        edge_types: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        """
        Executes high-performance SQL WITH RECURSIVE query in PostgreSQL with cycle detection.
        """
        # Downstream: g.id = e.source_id -> next node is e.target_id
        # Upstream:   g.id = e.target_id -> next node is e.source_id
        if direction == "downstream":
            edge_match = "e.source_id = g.id"
            next_node_col = "e.target_id"
        else:
            edge_match = "e.target_id = g.id"
            next_node_col = "e.source_id"

        type_filter = ""
        if edge_types:
            quoted_types = ", ".join(f"'{t}'" for t in edge_types)
            type_filter = f"AND e.edge_type IN ({quoted_types})"

        cte_query = text(f"""
        WITH RECURSIVE graph_cte AS (
            -- Base case: Starting node(s)
            SELECT 
                n.id,
                n.node_type,
                n.name,
                n.file_path,
                n.content,
                0 AS depth,
                e.edge_type AS via_edge_type,
                ARRAY[n.id]::text[] AS path
            FROM graph_nodes n
            LEFT JOIN graph_edges e ON 1=0
            WHERE n.project_id = :project_id
              AND (n.id = :start_id OR n.name = :start_id)

            UNION ALL

            -- Recursive case
            SELECT 
                nxt.id,
                nxt.node_type,
                nxt.name,
                nxt.file_path,
                nxt.content,
                g.depth + 1,
                e.edge_type AS via_edge_type,
                g.path || nxt.id
            FROM graph_cte g
            JOIN graph_edges e ON {edge_match} AND e.project_id = :project_id {type_filter}
            JOIN graph_nodes nxt ON nxt.id = {next_node_col} AND nxt.project_id = :project_id
            WHERE g.depth < :max_depth
              AND NOT (nxt.id = ANY(g.path)) -- Cycle prevention
        )
        SELECT DISTINCT ON (id)
            id, node_type, name, file_path, content, depth, via_edge_type
        FROM graph_cte
        WHERE depth > 0
        ORDER BY id, depth ASC;
        """)

        res = await db.execute(cte_query, {
            "project_id": project_id,
            "start_id": start_id_or_name,
            "max_depth": max_depth,
        })
        rows = res.fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r[0],
                "type": r[1],
                "name": r[2],
                "file_path": r[3],
                "content": r[4],
                "depth": r[5],
                "via_edge": r[6],
            })
        results.sort(key=lambda x: (x["depth"], x["name"]))
        return results

    @classmethod
    async def _traverse_in_memory_bfs(
        cls,
        db: AsyncSession,
        project_id: str,
        start_id_or_name: str,
        direction: str,
        max_depth: int,
        edge_types: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        """
        Breadth-First Search traversal with cycle prevention for SQLite and unit test mocks.
        """
        # Find start node(s)
        n_stmt = select(GraphNode).where(
            GraphNode.project_id == project_id,
            (GraphNode.id == start_id_or_name) | (GraphNode.name == start_id_or_name)
        )
        n_res = await db.execute(n_stmt)
        start_nodes = n_res.scalars().all()
        if not start_nodes:
            return []

        # Load all edges for this project to do fast in-memory graph walk
        e_stmt = select(GraphEdge).where(GraphEdge.project_id == project_id)
        if edge_types:
            e_stmt = e_stmt.where(GraphEdge.edge_type.in_(edge_types))
        e_res = await db.execute(e_stmt)
        edges = e_res.scalars().all()

        # Build adjacency map
        # Downstream: source -> [(target, edge_type)]
        # Upstream: target -> [(source, edge_type)]
        adj: Dict[str, List[Tuple[str, str]]] = {}
        for e in edges:
            if direction == "downstream":
                adj.setdefault(e.source_id, []).append((e.target_id, e.edge_type))
            else:
                adj.setdefault(e.target_id, []).append((e.source_id, e.edge_type))

        visited: Dict[str, Tuple[int, Optional[str]]] = {}  # node_id -> (min_depth, via_edge)
        queue: List[Tuple[str, int, Optional[str]]] = []

        for s in start_nodes:
            visited[s.id] = (0, None)
            queue.append((s.id, 0, None))

        while queue:
            curr_id, curr_depth, _ = queue.pop(0)
            if curr_depth >= max_depth:
                continue

            for neighbor_id, e_type in adj.get(curr_id, []):
                next_depth = curr_depth + 1
                if neighbor_id not in visited or next_depth < visited[neighbor_id][0]:
                    visited[neighbor_id] = (next_depth, e_type)
                    queue.append((neighbor_id, next_depth, e_type))

        # Remove starting nodes from output
        start_ids = {s.id for s in start_nodes}
        target_ids = [nid for nid in visited if nid not in start_ids]
        if not target_ids:
            return []

        # Fetch details for traversed nodes
        detail_stmt = select(GraphNode).where(
            GraphNode.project_id == project_id,
            GraphNode.id.in_(target_ids)
        )
        detail_res = await db.execute(detail_stmt)
        node_map = {n.id: n for n in detail_res.scalars().all()}

        results = []
        for nid in target_ids:
            node = node_map.get(nid)
            if not node:
                continue
            depth, via_edge = visited[nid]
            results.append({
                "id": node.id,
                "type": node.node_type,
                "name": node.name,
                "file_path": node.file_path,
                "content": node.content,
                "depth": depth,
                "via_edge": via_edge,
            })

        results.sort(key=lambda x: (x["depth"], x["name"]))
        return results
