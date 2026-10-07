import os
import re
import ast
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete
from backend.models.graph import GraphNode, GraphEdge
from backend.services.indexer import _JS_TS_DECL_RE, _GO_DECL_RE, _RUST_DECL_RE

_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv",
    "dist", "build", ".kobits_sandboxes", "_worktrees", ".pytest_cache",
    ".mypy_cache", ".next", ".nuxt", "coverage", "target", "vendor",
    "bower_components", "bundle", ".tox", ".serverless", "bin", "obj",
    ".idea", ".vscode", "tmp", "temp", "pods", "Pods", ".cache",
    ".turbo", ".gradle", ".cargo", "site-packages",
}
_SUPPORTED_GRAPH_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs"}
_SKIP_GRAPH_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".bundle.css")
_GENERIC_BUILTINS = {
    "get", "set", "print", "len", "str", "int", "dict", "list", "type",
    "isinstance", "append", "extend", "pop", "push", "map", "filter",
    "forEach", "includes", "log", "error", "warn", "info", "debug",
    "format", "join", "split", "replace", "strip", "lower", "upper",
    "startswith", "endswith", "keys", "values", "items", "find", "count",
}


class GraphIndexerService:
    @classmethod
    async def index_repository(cls, db: AsyncSession, project_id: str, repo_path: str):
        if not repo_path or not os.path.isdir(repo_path):
            return {"nodes": 0, "edges": 0}

        # Clear existing graph for project
        await db.execute(delete(GraphEdge).where(GraphEdge.project_id == project_id))
        await db.execute(delete(GraphNode).where(GraphNode.project_id == project_id))

        nodes_created = 0
        edges_created = 0

        file_nodes = {}      # rel_path -> node_id
        symbol_nodes = {}    # short symbol name -> list of node_ids
        pending_calls = []   # (source_node_id, called_symbol_name)
        pending_imports = [] # (source_file_node_id, imported_module_or_symbol)

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _IGNORE_DIRS]

            for file in files:
                if file.startswith("."):
                    continue
                lower_f = file.lower()
                if any(lower_f.endswith(s) for s in _SKIP_GRAPH_SUFFIXES):
                    continue
                ext = os.path.splitext(file)[1].lower()
                if ext not in _SUPPORTED_GRAPH_EXTS:
                    continue

                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, repo_path).replace("\\", "/")

                try:
                    if os.path.getsize(file_path) > 512_000:
                        continue
                    with open(file_path, "r", encoding="utf-8") as f:
                        source = f.read()
                except Exception:
                    continue

                # Create FILE node
                file_node = GraphNode(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    node_type="FILE",
                    name=rel_path,
                    file_path=rel_path,
                    metadata_json={"size": len(source)},
                )
                db.add(file_node)
                file_nodes[rel_path] = file_node.id
                nodes_created += 1

                if ext == ".py":
                    try:
                        tree = ast.parse(source)
                    except Exception:
                        continue

                    for node in ast.iter_child_nodes(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                pending_imports.append((file_node.id, alias.name))
                        elif isinstance(node, ast.ImportFrom) and node.module:
                            pending_imports.append((file_node.id, node.module))

                        elif isinstance(node, ast.ClassDef):
                            class_node = GraphNode(
                                id=str(uuid.uuid4()),
                                project_id=project_id,
                                node_type="CLASS",
                                name=node.name,
                                file_path=rel_path,
                                content=ast.get_docstring(node) or f"class {node.name} (line {node.lineno})",
                                metadata_json={"line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)},
                            )
                            db.add(class_node)
                            symbol_nodes.setdefault(node.name, []).append(class_node.id)
                            nodes_created += 1

                            db.add(GraphEdge(
                                id=str(uuid.uuid4()),
                                project_id=project_id,
                                source_id=file_node.id,
                                target_id=class_node.id,
                                edge_type="DEFINES",
                            ))
                            edges_created += 1

                            for child in node.body:
                                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                    full_name = f"{node.name}.{child.name}"
                                    func_node = GraphNode(
                                        id=str(uuid.uuid4()),
                                        project_id=project_id,
                                        node_type="FUNCTION",
                                        name=full_name,
                                        file_path=rel_path,
                                        content=ast.get_docstring(child) or f"def {full_name} (line {child.lineno})",
                                        metadata_json={"line": child.lineno, "end_line": getattr(child, "end_lineno", child.lineno)},
                                    )
                                    db.add(func_node)
                                    symbol_nodes.setdefault(child.name, []).append(func_node.id)
                                    symbol_nodes.setdefault(full_name, []).append(func_node.id)
                                    nodes_created += 1

                                    db.add(GraphEdge(
                                        id=str(uuid.uuid4()),
                                        project_id=project_id,
                                        source_id=class_node.id,
                                        target_id=func_node.id,
                                        edge_type="DEFINES",
                                    ))
                                    edges_created += 1

                                    for sub in ast.walk(child):
                                        if isinstance(sub, ast.Call):
                                            callee = cls._extract_callee_name(sub.func)
                                            if callee and callee not in _GENERIC_BUILTINS and len(pending_calls) < 20_000:
                                                pending_calls.append((func_node.id, callee))

                        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            func_node = GraphNode(
                                id=str(uuid.uuid4()),
                                project_id=project_id,
                                node_type="FUNCTION",
                                name=node.name,
                                file_path=rel_path,
                                content=ast.get_docstring(node) or f"def {node.name} (line {node.lineno})",
                                metadata_json={"line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)},
                            )
                            db.add(func_node)
                            symbol_nodes.setdefault(node.name, []).append(func_node.id)
                            nodes_created += 1

                            db.add(GraphEdge(
                                id=str(uuid.uuid4()),
                                project_id=project_id,
                                source_id=file_node.id,
                                target_id=func_node.id,
                                edge_type="DEFINES",
                            ))
                            edges_created += 1

                            for sub in ast.walk(node):
                                if isinstance(sub, ast.Call):
                                    callee = cls._extract_callee_name(sub.func)
                                    if callee and callee not in _GENERIC_BUILTINS and len(pending_calls) < 20_000:
                                        pending_calls.append((func_node.id, callee))
                else:
                    # Multi-language declaration, method, import, and call extraction (TS/JS/Go/Rust)
                    pattern = _GO_DECL_RE if ext == ".go" else (_RUST_DECL_RE if ext == ".rs" else _JS_TS_DECL_RE)
                    js_import_re = re.compile(r"""(?:import\s+.*?\s+from\s+|require\s*\(\s*)['"]([^'"]+)['"]""")
                    method_re = re.compile(
                        r"^\s{2,8}(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*"
                        r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^;]*\)\s*(?::\s*[^{]+)?\{"
                    )
                    call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
                    js_keywords = {
                        "if", "for", "while", "switch", "catch", "function", "return",
                        "typeof", "instanceof", "require", "import", "export", "super", "constructor",
                    }

                    current_class_name = None
                    current_class_node_id = None
                    current_func_node_id = None

                    for idx, line in enumerate(source.splitlines(), start=1):
                        for imp_m in js_import_re.finditer(line):
                            imp_target = imp_m.group(1)
                            if imp_target.startswith("."):
                                pending_imports.append((file_node.id, imp_target))

                        m = pattern.match(line)
                        if m:
                            groups = [g for g in m.groups() if g]
                            if len(groups) >= 2:
                                raw_kind, sym = groups[0].upper(), groups[1]
                            elif len(groups) == 1:
                                raw_kind, sym = "FUNCTION", groups[0]
                            else:
                                continue
                            node_type = "CLASS" if raw_kind in ("CLASS", "STRUCT", "INTERFACE", "TRAIT", "TYPE", "IMPL") else "FUNCTION"
                            sym_node = GraphNode(
                                id=str(uuid.uuid4()),
                                project_id=project_id,
                                node_type=node_type,
                                name=sym,
                                file_path=rel_path,
                                content=line.strip()[:200],
                                metadata_json={"line": idx},
                            )
                            db.add(sym_node)
                            symbol_nodes.setdefault(sym, []).append(sym_node.id)
                            nodes_created += 1

                            db.add(GraphEdge(
                                id=str(uuid.uuid4()),
                                project_id=project_id,
                                source_id=file_node.id,
                                target_id=sym_node.id,
                                edge_type="DEFINES",
                            ))
                            edges_created += 1

                            if node_type == "CLASS":
                                current_class_name = sym
                                current_class_node_id = sym_node.id
                                current_func_node_id = sym_node.id
                            else:
                                current_func_node_id = sym_node.id
                            continue

                        # Check class method inside TS/JS class
                        if current_class_name and current_class_node_id and ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                            mm = method_re.match(line)
                            if mm and mm.group(1) not in js_keywords:
                                m_name = mm.group(1)
                                full_m_name = f"{current_class_name}.{m_name}"
                                m_node = GraphNode(
                                    id=str(uuid.uuid4()),
                                    project_id=project_id,
                                    node_type="FUNCTION",
                                    name=full_m_name,
                                    file_path=rel_path,
                                    content=line.strip()[:200],
                                    metadata_json={"line": idx},
                                )
                                db.add(m_node)
                                symbol_nodes.setdefault(m_name, []).append(m_node.id)
                                symbol_nodes.setdefault(full_m_name, []).append(m_node.id)
                                nodes_created += 1

                                db.add(GraphEdge(
                                    id=str(uuid.uuid4()),
                                    project_id=project_id,
                                    source_id=current_class_node_id,
                                    target_id=m_node.id,
                                    edge_type="DEFINES",
                                ))
                                edges_created += 1
                                current_func_node_id = m_node.id
                                continue

                        # Extract function call references inside active declaration
                        if current_func_node_id:
                            for cm in call_re.finditer(line):
                                callee = cm.group(1)
                                if callee not in js_keywords and callee not in _GENERIC_BUILTINS and len(pending_calls) < 20_000:
                                    pending_calls.append((current_func_node_id, callee))

        # Pass 2: Resolve IMPORTS edges between files (Python dotted modules + JS/TS relative imports)
        seen_edges = set()
        for src_file_id, mod_name in pending_imports:
            clean_mod = mod_name.lstrip("./").replace(".", "/")
            for candidate_rel, target_file_id in file_nodes.items():
                if src_file_id == target_file_id:
                    continue
                cand_no_ext = os.path.splitext(candidate_rel)[0]
                if (
                    candidate_rel == mod_name.lstrip("./")
                    or cand_no_ext == clean_mod
                    or cand_no_ext.endswith("/" + clean_mod)
                ):
                    edge_key = (src_file_id, target_file_id, "IMPORTS")
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        db.add(GraphEdge(
                            id=str(uuid.uuid4()),
                            project_id=project_id,
                            source_id=src_file_id,
                            target_id=target_file_id,
                            edge_type="IMPORTS",
                        ))
                        edges_created += 1
                        if edges_created % 250 == 0:
                            await db.flush()

        # Pass 3: Resolve CALLS edges between project functions/classes
        for src_node_id, callee_name in pending_calls:
            target_ids = symbol_nodes.get(callee_name, [])
            for target_id in target_ids[:2]:
                if src_node_id != target_id:
                    edge_key = (src_node_id, target_id, "CALLS")
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        db.add(GraphEdge(
                            id=str(uuid.uuid4()),
                            project_id=project_id,
                            source_id=src_node_id,
                            target_id=target_id,
                            edge_type="CALLS",
                        ))
                        edges_created += 1
                        if edges_created % 250 == 0:
                            await db.flush()

        await db.commit()
        return {"nodes": nodes_created, "edges": edges_created}

    @staticmethod
    def _extract_callee_name(func_expr: ast.AST) -> str:
        if isinstance(func_expr, ast.Name):
            return func_expr.id
        if isinstance(func_expr, ast.Attribute):
            if isinstance(func_expr.value, ast.Name) and func_expr.value.id not in ("self", "cls"):
                return f"{func_expr.value.id}.{func_expr.attr}"
            return func_expr.attr
        return ""


