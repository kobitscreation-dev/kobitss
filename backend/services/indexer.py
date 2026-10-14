import os
import re
import ast
import uuid
import hashlib
from typing import List, Optional, Dict, Any
from collections import defaultdict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete
from backend.models.memory import CodeDocument
from backend.models.project import Project
from backend.services.embedding import EmbeddingUtils

# Multi-Language AST/CST Extraction Patterns
_JS_TS_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_TS_INTERFACE_RE = re.compile(
    r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_TS_TYPE_RE = re.compile(
    r"^\s*(?:export\s+)?type\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_TS_ENUM_RE = re.compile(
    r"^\s*(?:export\s+)?enum\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_TS_FUNC_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*(?:\*\s*)?([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_TS_VAR_FUNC_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>"
)
_JS_TS_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|async|get|set|readonly|override)\s+)*(?:([A-Za-z_$][A-Za-z0-9_$]*)|constructor)\s*\([^)]*\)\s*(?::\s*[^;{]+)?\s*\{?"
)

_GO_METHOD_RE = re.compile(
    r"^\s*func\s*\(\s*(?:[A-Za-z0-9_]+\s+)?\*?([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]+\])?\s*\)\s*([A-Za-z_][A-Za-z0-9_]*)"
)
_GO_FUNC_RE = re.compile(
    r"^\s*func\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_GO_TYPE_RE = re.compile(
    r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface)"
)

_RUST_IMPL_RE = re.compile(
    r"^\s*impl(?:\s*<[^>]+>)?\s+(?:(?:[A-Za-z_][A-Za-z0-9_]*::)*[A-Za-z_][A-Za-z0-9_]*(?:<[^>]+>)?\s+for\s+)?(?:[A-Za-z_][A-Za-z0-9_]*::)*([A-Za-z_][A-Za-z0-9_]*)"
)
_RUST_FN_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:const\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_RUST_TYPE_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)"
)

# Multi-group legacy patterns imported by GraphIndexerService
_JS_TS_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(?:(class|interface|type|enum|function)\s+([A-Za-z_$][A-Za-z0-9_$]*)|"
    r"(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>)"
)
_GO_DECL_RE = re.compile(
    r"^\s*(?:(func)\s+(?:\([^)]+\)\s*)?([A-Za-z_][A-Za-z0-9_]*)|(type)\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:struct|interface))"
)
_RUST_DECL_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(fn|struct|enum|trait|impl)\s+(?:<[^>]+>\s*)?([A-Za-z_][A-Za-z0-9_]*)"
)


class RepositoryIndexer:
    """
    Scans a repository and chunks its codebase using AST (Python) and
    declaration-boundary (TS/JS/Go/Rust) analysis for semantic & hybrid RAG.
    Performs fast SHA-256 delta indexing so unchanged files are skipped.
    """

    IGNORE_DIRS = {
        ".git", "__pycache__", "node_modules", "venv", ".venv",
        "dist", "build", ".kobits_sandboxes", "sandboxes", "_worktrees", ".pytest_cache",
        ".mypy_cache", ".next", ".nuxt", "coverage", "target", "vendor",
        "bower_components", "bundle", ".tox", ".serverless", "bin", "obj",
        ".idea", ".vscode", "tmp", "temp", "pods", "Pods", ".cache",
        ".turbo", ".gradle", ".cargo", "site-packages",
        "repos", "training_data", "benchmark_fixture_repo",
    }
    IGNORE_EXTS = {
        ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
        ".pdf", ".zip", ".tar", ".gz", ".sqlite3", ".db", ".db-journal",
        ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".exe", ".dll", ".so", ".dylib",
        ".lock", ".map", ".wasm", ".parquet", ".arrow", ".avro", ".bin", ".dat",
    }
    IGNORE_FILENAMES = {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock",
        "poetry.lock", "composer.lock", "gemfile.lock", "go.sum",
    }

    @classmethod
    async def index_repository(
        cls,
        db: AsyncSession,
        project_id: str,
        repo_path: str,
        only_files: Optional[List[str]] = None,
    ) -> int:
        """
        Walks the repository path (or a specific list of modified relative file paths),
        AST-chunks files, and saves them as CodeDocuments using SHA-256 delta indexing.
        """
        if not repo_path or not os.path.isdir(repo_path):
            return 0

        provider_version = EmbeddingUtils.get_version()
        chunker_version = "ast_v2"

        stmt = select(CodeDocument).where(CodeDocument.project_id == project_id)
        res = await db.execute(stmt)
        existing_docs = res.scalars().all()

        existing_by_file: Dict[str, List[CodeDocument]] = defaultdict(list)
        for d in existing_docs:
            existing_by_file[d.file_path].append(d)

        seen_files = set()
        docs_created = 0

        # Normalize optional filter set for incremental post-task indexing
        only_set = None
        if only_files is not None:
            only_set = {
                f.replace("[DELETED] ", "").strip().replace("\\", "/")
                for f in only_files
                if f and not f.startswith("[DELETED] ")
            }
            deleted_set = {
                f.replace("[DELETED] ", "").strip().replace("\\", "/")
                for f in only_files
                if f and f.startswith("[DELETED] ")
            }
            for del_rel in deleted_set:
                if del_rel in existing_by_file:
                    for d in existing_by_file[del_rel]:
                        await db.delete(d)

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in cls.IGNORE_DIRS
            ]

            for file in files:
                if file.startswith("."):
                    continue
                lower_file = file.lower()
                if lower_file in cls.IGNORE_FILENAMES:
                    continue
                if any(lower_file.endswith(s) for s in (".min.js", ".min.css", ".bundle.js", ".bundle.css")):
                    continue
                ext = os.path.splitext(file)[1].lower()
                if ext in cls.IGNORE_EXTS:
                    continue

                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, repo_path).replace("\\", "/")

                if only_set is not None and rel_path not in only_set:
                    continue

                seen_files.add(rel_path)

                try:
                    if os.path.getsize(file_path) > 512_000:
                        continue  # Skip huge generated/minified bundles > 512KB
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    continue

                if not content.strip():
                    continue

                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

                needs_reindex = True
                if rel_path in existing_by_file:
                    file_docs = existing_by_file[rel_path]
                    if file_docs and all(
                        isinstance(d.metadata_json, dict)
                        and d.metadata_json.get("content_hash") == content_hash
                        and d.metadata_json.get("embedding_version") == provider_version
                        and d.metadata_json.get("chunker_version") == chunker_version
                        for d in file_docs
                    ):
                        needs_reindex = False

                if needs_reindex:
                    if rel_path in existing_by_file:
                        for d in existing_by_file[rel_path]:
                            await db.delete(d)

                    structured_chunks = cls._chunk_file(rel_path, content)
                    for ch in structured_chunks:
                        chunk_text = ch["content"]
                        emb = EmbeddingUtils.generate_embedding(chunk_text)
                        doc = CodeDocument(
                            id=str(uuid.uuid4()),
                            project_id=project_id,
                            file_path=rel_path,
                            content_chunk=chunk_text,
                            metadata_json={
                                "size": len(chunk_text),
                                "content_hash": content_hash,
                                "embedding_version": provider_version,
                                "chunker_version": chunker_version,
                                "start_line": ch.get("start_line", 1),
                                "end_line": ch.get("end_line", 1),
                                "symbol": ch.get("symbol") or "",
                                "kind": ch.get("kind") or "block",
                            },
                            embedding=emb,
                        )
                        db.add(doc)
                        docs_created += 1
                        if docs_created % 200 == 0:
                            await db.flush()

        # Delete stale files only during a full repository walk
        if only_set is None:
            stale_files = set(existing_by_file.keys()) - seen_files
            for stale_file in stale_files:
                for d in existing_by_file[stale_file]:
                    await db.delete(d)

        await db.commit()
        return docs_created

    @classmethod
    def _format_chunk_header(
        cls,
        rel_path: str,
        raw_text: str,
        start_line: int,
        end_line: int,
        symbol: Optional[str] = None,
        kind: str = "block",
    ) -> Dict[str, Any]:
        sym_label = f" | Symbol: {symbol} ({kind})" if symbol else ""
        header = f"# File: {rel_path}{sym_label} | Lines: {start_line}-{end_line}\n"
        return {
            "content": header + raw_text,
            "raw_content": raw_text,
            "start_line": start_line,
            "end_line": end_line,
            "symbol": symbol or "",
            "kind": kind,
        }

    @classmethod
    def _chunk_file(cls, rel_path: str, content: str, max_chunk_chars: int = 1800) -> List[Dict[str, Any]]:
        """
        AST & declaration-boundary code chunker.
        - Python (.py): Uses `ast.parse` to extract classes, methods, functions, and module headers
          with exact 1-indexed [start_line, end_line] and symbol names.
        - TS/JS/Go/Rust (.ts, .tsx, .js, .jsx, .go, .rs): Splits on top-level declarations.
        - Fallback: Splits strictly on line boundaries (never mid-line).
        """
        ext = os.path.splitext(rel_path)[1].lower()
        lines = content.splitlines()
        if not lines:
            return []

        if ext == ".py":
            py_chunks = cls._chunk_python_ast(rel_path, content, lines, max_chunk_chars)
            if py_chunks:
                return py_chunks

        if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            ts_chunks = cls._chunk_ts_js(rel_path, lines, max_chunk_chars)
            if ts_chunks:
                return ts_chunks

        if ext == ".go":
            go_chunks = cls._chunk_go(rel_path, lines, max_chunk_chars)
            if go_chunks:
                return go_chunks

        if ext == ".rs":
            rs_chunks = cls._chunk_rust(rel_path, lines, max_chunk_chars)
            if rs_chunks:
                return rs_chunks

        return cls._chunk_lines_fallback(rel_path, lines, max_chunk_chars=max_chunk_chars)

    @classmethod
    def _node_start_line(cls, node: ast.AST) -> int:
        decorators = getattr(node, "decorator_list", None)
        if decorators:
            return min([ getattr(node, "lineno", 1) ] + [getattr(d, "lineno", 1) for d in decorators])
        return getattr(node, "lineno", 1)

    @classmethod
    def _chunk_python_ast(
        cls, rel_path: str, content: str, lines: List[str], max_chunk_chars: int = 1800
    ) -> List[Dict[str, Any]]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return cls._chunk_lines_fallback(rel_path, lines, max_chunk_chars=max_chunk_chars)

        top_nodes = [
            n for n in tree.body
            if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not top_nodes:
            return cls._chunk_lines_fallback(rel_path, lines, max_chunk_chars=max_chunk_chars)

        chunks: List[Dict[str, Any]] = []

        # 1. Module header / imports before the first class or function
        first_line = cls._node_start_line(top_nodes[0])
        if first_line > 1:
            header_lines = lines[: first_line - 1]
            header_text = "\n".join(header_lines).strip()
            if header_text:
                chunks.extend(
                    cls._split_oversized_block(
                        rel_path, header_lines, 1, first_line - 1,
                        symbol="module_imports", kind="module_header",
                        max_chunk_chars=max_chunk_chars,
                    )
                )

        # 2. Walk top-level classes and functions
        for node in top_nodes:
            s_line = cls._node_start_line(node)
            e_line = getattr(node, "end_lineno", s_line) or s_line
            node_lines = lines[s_line - 1 : e_line]
            node_text = "\n".join(node_lines)

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                chunks.extend(
                    cls._split_oversized_block(
                        rel_path, node_lines, s_line, e_line,
                        symbol=node.name, kind=kind,
                        max_chunk_chars=max_chunk_chars,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                methods = [
                    ch for ch in node.body
                    if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                if not methods:
                    chunks.extend(
                        cls._split_oversized_block(
                            rel_path, node_lines, s_line, e_line,
                            symbol=node.name, kind="class",
                            max_chunk_chars=max_chunk_chars,
                        )
                    )
                else:
                    # Emit class header (signature + docstring + attributes before first method)
                    first_method_line = cls._node_start_line(methods[0])
                    if first_method_line > s_line:
                        cls_hdr_lines = lines[s_line - 1 : first_method_line - 1]
                        if "\n".join(cls_hdr_lines).strip():
                            chunks.append(
                                cls._format_chunk_header(
                                    rel_path,
                                    "\n".join(cls_hdr_lines),
                                    s_line,
                                    first_method_line - 1,
                                    symbol=node.name,
                                    kind="class_header",
                                )
                            )
                    # Emit each method as its own surgical AST chunk with ClassName.method_name
                    for m_node in methods:
                        m_start = cls._node_start_line(m_node)
                        m_end = getattr(m_node, "end_lineno", m_start) or m_start
                        m_lines = lines[m_start - 1 : m_end]
                        chunks.extend(
                            cls._split_oversized_block(
                                rel_path, m_lines, m_start, m_end,
                                symbol=f"{node.name}.{m_node.name}",
                                kind="method",
                                max_chunk_chars=max_chunk_chars,
                            )
                        )

        return chunks

    @classmethod
    def _find_matching_brace(cls, lines: List[str], start_idx: int) -> int:
        """
        Finds the 0-indexed line where the brace that opens at or after start_idx closes.
        Handles quotes, escapes, line comments, block comments, and multiline template strings.
        """
        depth = 0
        opened = False
        in_block_comment = False
        in_string = None

        for idx in range(start_idx, len(lines)):
            line = lines[idx]
            i = 0
            n = len(line)

            while i < n:
                if in_block_comment:
                    if line[i : i + 2] == "*/":
                        in_block_comment = False
                        i += 2
                        continue
                    i += 1
                    continue

                if in_string:
                    if line[i] == "\\":
                        i += 2
                        continue
                    if line[i] == in_string:
                        in_string = None
                    i += 1
                    continue

                # Comments
                if line[i : i + 2] == "//":
                    break
                if line[i : i + 2] == "/*":
                    in_block_comment = True
                    i += 2
                    continue

                # Strings
                if line[i] in ('"', "'", "`"):
                    in_string = line[i]
                    i += 1
                    continue

                # Braces
                if line[i] == "{":
                    depth += 1
                    opened = True
                elif line[i] == "}":
                    depth -= 1
                    if opened and depth <= 0:
                        return idx

                i += 1

            # End of line string state handling:
            # Single/double quotes in JS/TS/Go/Rust do not span multiple lines unless escaped/raw
            if in_string in ('"', "'"):
                in_string = None

            # If not yet opened: check if it's a signature terminating with ; (e.g. abstract/interface method)
            if not opened:
                trimmed = line.strip()
                if trimmed.endswith(";") and not in_block_comment and not in_string:
                    return idx

        return len(lines) - 1

    @classmethod
    def _chunk_ts_js(
        cls, rel_path: str, lines: List[str], max_chunk_chars: int = 1800
    ) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        n = len(lines)
        idx = 0
        first_decl_seen = False
        reserved_words = {"if", "for", "while", "switch", "catch", "with"}

        while idx < n:
            line = lines[idx]
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                idx += 1
                continue

            # 1. Class definition
            m_class = _JS_TS_CLASS_RE.match(line)
            if m_class:
                if not first_decl_seen:
                    first_decl_seen = True
                    if idx > 0:
                        hdr_lines = lines[:idx]
                        if "\n".join(hdr_lines).strip():
                            chunks.extend(
                                cls._split_oversized_block(
                                    rel_path, hdr_lines, 1, idx,
                                    symbol="module_imports", kind="module_header",
                                    max_chunk_chars=max_chunk_chars,
                                )
                            )
                cls_name = m_class.group(1)
                end_idx = cls._find_matching_brace(lines, idx)
                cls_s_line = idx + 1
                cls_e_line = end_idx + 1

                # Parse methods inside the class
                method_chunks = []
                inner_idx = idx + 1
                while inner_idx < end_idx:
                    m_line = lines[inner_idx]
                    m_match = _JS_TS_METHOD_RE.match(m_line)
                    if m_match:
                        m_sym = m_match.group(1) or "constructor"
                        if m_sym not in reserved_words:
                            m_end_idx = cls._find_matching_brace(lines, inner_idx)
                            if m_end_idx <= end_idx:
                                m_s = inner_idx + 1
                                m_e = m_end_idx + 1
                                m_lines = lines[inner_idx : m_end_idx + 1]
                                method_chunks.extend(
                                    cls._split_oversized_block(
                                        rel_path, m_lines, m_s, m_e,
                                        symbol=f"{cls_name}.{m_sym}",
                                        kind="method",
                                        max_chunk_chars=max_chunk_chars,
                                    )
                                )
                                inner_idx = m_end_idx + 1
                                continue
                    inner_idx += 1

                cls_lines = lines[idx : end_idx + 1]
                cls_text = "\n".join(cls_lines)
                if not method_chunks:
                    chunks.extend(
                        cls._split_oversized_block(
                            rel_path, cls_lines, cls_s_line, cls_e_line,
                            symbol=cls_name, kind="class",
                            max_chunk_chars=max_chunk_chars,
                        )
                    )
                else:
                    first_m_line = method_chunks[0]["start_line"]
                    if first_m_line > cls_s_line:
                        hdr_lines = lines[idx : first_m_line - 1]
                        if "\n".join(hdr_lines).strip():
                            chunks.append(
                                cls._format_chunk_header(
                                    rel_path, "\n".join(hdr_lines), cls_s_line, first_m_line - 1,
                                    symbol=cls_name, kind="class_header"
                                )
                            )
                    chunks.extend(method_chunks)

                idx = end_idx + 1
                continue

            # 2. Function / Arrow Function / Interface / Type / Enum
            m_func = _JS_TS_FUNC_RE.match(line)
            m_arrow = _JS_TS_VAR_FUNC_RE.match(line)
            m_iface = _JS_TS_INTERFACE_RE.match(line)
            m_type = _JS_TS_TYPE_RE.match(line)
            m_enum = _JS_TS_ENUM_RE.match(line)

            match_res = None
            if m_func:
                match_res = (m_func.group(1), "function")
            elif m_arrow:
                match_res = (m_arrow.group(1), "function")
            elif m_iface:
                match_res = (m_iface.group(1), "interface")
            elif m_type:
                match_res = (m_type.group(1), "type")
            elif m_enum:
                match_res = (m_enum.group(1), "enum")

            if match_res:
                sym_name, kind_name = match_res
                if not first_decl_seen:
                    first_decl_seen = True
                    if idx > 0:
                        hdr_lines = lines[:idx]
                        if "\n".join(hdr_lines).strip():
                            chunks.extend(
                                cls._split_oversized_block(
                                    rel_path, hdr_lines, 1, idx,
                                    symbol="module_imports", kind="module_header",
                                    max_chunk_chars=max_chunk_chars,
                                )
                            )

                end_idx = cls._find_matching_brace(lines, idx)
                s_line = idx + 1
                e_line = end_idx + 1
                decl_lines = lines[idx : end_idx + 1]
                chunks.extend(
                    cls._split_oversized_block(
                        rel_path, decl_lines, s_line, e_line,
                        symbol=sym_name, kind=kind_name,
                        max_chunk_chars=max_chunk_chars,
                    )
                )
                idx = end_idx + 1
                continue

            idx += 1

        return chunks

    @classmethod
    def _chunk_go(
        cls, rel_path: str, lines: List[str], max_chunk_chars: int = 1800
    ) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        n = len(lines)
        idx = 0
        first_decl_seen = False

        while idx < n:
            line = lines[idx]
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                idx += 1
                continue

            m_method = _GO_METHOD_RE.match(line)
            m_func = _GO_FUNC_RE.match(line)
            m_type = _GO_TYPE_RE.match(line)

            match_res = None
            if m_method:
                receiver = m_method.group(1)
                m_name = m_method.group(2)
                match_res = (f"{receiver}.{m_name}", "method")
            elif m_func:
                fn_name = m_func.group(1)
                match_res = (fn_name, "function")
            elif m_type:
                t_name = m_type.group(1)
                t_kind = m_type.group(2)
                match_res = (t_name, t_kind)

            if match_res:
                sym_name, kind_name = match_res
                if not first_decl_seen:
                    first_decl_seen = True
                    if idx > 0:
                        hdr_lines = lines[:idx]
                        if "\n".join(hdr_lines).strip():
                            chunks.extend(
                                cls._split_oversized_block(
                                    rel_path, hdr_lines, 1, idx,
                                    symbol="module_imports", kind="module_header",
                                    max_chunk_chars=max_chunk_chars,
                                )
                            )

                end_idx = cls._find_matching_brace(lines, idx)
                s_line = idx + 1
                e_line = end_idx + 1
                decl_lines = lines[idx : end_idx + 1]
                chunks.extend(
                    cls._split_oversized_block(
                        rel_path, decl_lines, s_line, e_line,
                        symbol=sym_name, kind=kind_name,
                        max_chunk_chars=max_chunk_chars,
                    )
                )
                idx = end_idx + 1
                continue

            idx += 1

        return chunks

    @classmethod
    def _chunk_rust(
        cls, rel_path: str, lines: List[str], max_chunk_chars: int = 1800
    ) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        n = len(lines)
        idx = 0
        first_decl_seen = False

        while idx < n:
            line = lines[idx]
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                idx += 1
                continue

            # 1. Impl block
            m_impl = _RUST_IMPL_RE.match(line)
            if m_impl:
                if not first_decl_seen:
                    first_decl_seen = True
                    if idx > 0:
                        hdr_lines = lines[:idx]
                        if "\n".join(hdr_lines).strip():
                            chunks.extend(
                                cls._split_oversized_block(
                                    rel_path, hdr_lines, 1, idx,
                                    symbol="module_imports", kind="module_header",
                                    max_chunk_chars=max_chunk_chars,
                                )
                            )

                target_name = m_impl.group(1)
                end_idx = cls._find_matching_brace(lines, idx)

                # Scan inside impl block for methods
                method_chunks = []
                inner_idx = idx + 1
                while inner_idx < end_idx:
                    m_line = lines[inner_idx]
                    m_fn = _RUST_FN_RE.match(m_line)
                    if m_fn:
                        fn_name = m_fn.group(1)
                        m_end_idx = cls._find_matching_brace(lines, inner_idx)
                        if m_end_idx <= end_idx:
                            m_s = inner_idx + 1
                            m_e = m_end_idx + 1
                            m_lines = lines[inner_idx : m_end_idx + 1]
                            method_chunks.extend(
                                cls._split_oversized_block(
                                    rel_path, m_lines, m_s, m_e,
                                    symbol=f"{target_name}.{fn_name}",
                                    kind="method",
                                    max_chunk_chars=max_chunk_chars,
                                )
                            )
                            inner_idx = m_end_idx + 1
                            continue
                    inner_idx += 1

                impl_lines = lines[idx : end_idx + 1]
                impl_text = "\n".join(impl_lines)
                if not method_chunks:
                    chunks.extend(
                        cls._split_oversized_block(
                            rel_path, impl_lines, idx + 1, end_idx + 1,
                            symbol=target_name, kind="impl",
                            max_chunk_chars=max_chunk_chars,
                        )
                    )
                else:
                    first_m_line = method_chunks[0]["start_line"]
                    if first_m_line > idx + 1:
                        hdr_lines = lines[idx : first_m_line - 1]
                        if "\n".join(hdr_lines).strip():
                            chunks.append(
                                cls._format_chunk_header(
                                    rel_path, "\n".join(hdr_lines), idx + 1, first_m_line - 1,
                                    symbol=target_name, kind="impl_header"
                                )
                            )
                    chunks.extend(method_chunks)

                idx = end_idx + 1
                continue

            # 2. Standalone functions, structs, enums, traits
            m_fn = _RUST_FN_RE.match(line)
            m_type = _RUST_TYPE_RE.match(line)

            match_res = None
            if m_fn:
                match_res = (m_fn.group(1), "function")
            elif m_type:
                t_kind = m_type.group(1)
                t_name = m_type.group(2)
                match_res = (t_name, t_kind)

            if match_res:
                sym_name, kind_name = match_res
                if not first_decl_seen:
                    first_decl_seen = True
                    if idx > 0:
                        hdr_lines = lines[:idx]
                        if "\n".join(hdr_lines).strip():
                            chunks.extend(
                                cls._split_oversized_block(
                                    rel_path, hdr_lines, 1, idx,
                                    symbol="module_imports", kind="module_header",
                                    max_chunk_chars=max_chunk_chars,
                                )
                            )

                end_idx = cls._find_matching_brace(lines, idx)
                s_line = idx + 1
                e_line = end_idx + 1
                decl_lines = lines[idx : end_idx + 1]
                chunks.extend(
                    cls._split_oversized_block(
                        rel_path, decl_lines, s_line, e_line,
                        symbol=sym_name, kind=kind_name,
                        max_chunk_chars=max_chunk_chars,
                    )
                )
                idx = end_idx + 1
                continue

            idx += 1

        return chunks

    @classmethod
    def _chunk_declarations(
        cls, rel_path: str, lines: List[str], ext: str, max_chunk_chars: int = 1800
    ) -> List[Dict[str, Any]]:
        """Legacy dispatcher preserved for backward compatibility."""
        if ext == ".go":
            return cls._chunk_go(rel_path, lines, max_chunk_chars)
        elif ext == ".rs":
            return cls._chunk_rust(rel_path, lines, max_chunk_chars)
        elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            return cls._chunk_ts_js(rel_path, lines, max_chunk_chars)
        return cls._chunk_lines_fallback(rel_path, lines, max_chunk_chars=max_chunk_chars)

    @classmethod
    def _split_oversized_block(
        cls,
        rel_path: str,
        block_lines: List[str],
        start_line: int,
        end_line: int,
        symbol: Optional[str],
        kind: str,
        max_chunk_chars: int = 1800,
        overlap_lines: int = 5,
    ) -> List[Dict[str, Any]]:
        raw = "\n".join(block_lines)
        if len(raw) <= max_chunk_chars or len(block_lines) <= 15:
            return [cls._format_chunk_header(rel_path, raw, start_line, end_line, symbol=symbol, kind=kind)]

        sub_chunks: List[Dict[str, Any]] = []
        idx = 0
        n = len(block_lines)
        while idx < n:
            cur_lines = []
            cur_chars = 0
            cur_end = idx
            while cur_end < n and (cur_chars + len(block_lines[cur_end]) + 1 <= max_chunk_chars or not cur_lines):
                cur_lines.append(block_lines[cur_end])
                cur_chars += len(block_lines[cur_end]) + 1
                cur_end += 1

            c_start = start_line + idx
            c_end = start_line + cur_end - 1
            sub_chunks.append(
                cls._format_chunk_header(
                    rel_path, "\n".join(cur_lines), c_start, c_end, symbol=symbol, kind=kind
                )
            )
            if cur_end >= n:
                break
            idx = max(idx + 1, cur_end - overlap_lines)

        return sub_chunks

    @classmethod
    def _chunk_lines_fallback(
        cls, rel_path: str, lines: List[str], max_chunk_chars: int = 1800
    ) -> List[Dict[str, Any]]:
        return cls._split_oversized_block(
            rel_path, lines, 1, len(lines), symbol=None, kind="block", max_chunk_chars=max_chunk_chars
        )

    @classmethod
    def _chunk_content(cls, content: str, chunk_size: int = 1500, overlap: int = 200) -> List[str]:
        """Legacy helper preserved for backward compatibility with unit tests."""
        if len(content) <= chunk_size:
            return [content]

        chunks = []
        start = 0
        while start < len(content):
            end = min(start + chunk_size, len(content))
            chunks.append(content[start:end])
            if end == len(content):
                break
            start += chunk_size - overlap
        return chunks

