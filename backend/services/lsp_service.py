"""
Language Server Protocol (LSP) & Compiler Symbol Resolution Engine for Kobits.

Provides Kyros Parity Layer 3:
1. Compiler-grade jump-to-definition across Python, JavaScript, TypeScript, and general codebases.
2. Cross-file import tracing and symbol reference resolution.
3. Real-time compiler diagnostics (syntax, broken imports, missing symbols, signature mismatches, ripple diagnostics).
4. In-memory Symbol Reference Graph (Callers, Callees, Classes, Methods, Exports, Imports).
5. Eliminates reliance on probabilistic text grep for codebase navigation.
"""

import ast
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    import networkx as nx
except ImportError:
    nx = None

try:
    import esprima
except ImportError:
    esprima = None

try:
    import jedi
except ImportError:
    jedi = None

logger = logging.getLogger("kobits.lsp_service")


class HeadlessLSPService:
    """
    Enterprise-grade Headless Language Server Protocol & Compiler Symbol Graph Engine.
    Provides sub-15ms deterministic symbol resolution, call-graph generation, and compiler diagnostics.
    """

    IGNORE_DIRS = {
        ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
        ".kobits_sandboxes", "sandboxes", "_worktrees", ".pytest_cache", ".mypy_cache",
        ".next", ".nuxt", "coverage", "target", "vendor", "tmp", "temp", ".idea", ".vscode",
        ".cache", ".turbo", ".gradle", ".cargo", "site-packages",
    }

    IGNORE_EXTS = {
        ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
        ".pdf", ".zip", ".tar", ".gz", ".sqlite3", ".db", ".woff", ".woff2",
        ".ttf", ".eot", ".mp4", ".mp3", ".exe", ".dll", ".so", ".dylib",
        ".lock", ".map", ".wasm",
    }

    # In-memory AST cache: full_path -> (mtime, code_hash, ast_tree)
    _ast_cache: Dict[str, Tuple[float, int, Any]] = {}

    # In-memory Symbol Graph cache: repo_path -> (timestamp, mtimes_map, graph_data)
    _graph_cache: Dict[str, Tuple[float, Dict[str, float], Dict[str, Any]]] = {}

    @classmethod
    def clear_cache(cls):
        """Clear all in-memory AST and Symbol Graph caches."""
        cls._ast_cache.clear()
        cls._graph_cache.clear()

    @classmethod
    def invalidate_cache(cls, file_or_repo_path: str):
        """Invalidate caches for a specific file or workspace."""
        if not file_or_repo_path:
            return
        real_p = os.path.realpath(file_or_repo_path)
        cls._ast_cache.pop(real_p, None)
        cls._graph_cache.pop(real_p, None)

    @classmethod
    def _read_file_safe(cls, full_path: str) -> Optional[str]:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            logger.debug(f"Failed to read file {full_path}: {e}")
            return None

    @classmethod
    def _get_workspace_path(cls, workspace_dir: str, rel_or_abs: str) -> str:
        if not rel_or_abs:
            return os.path.realpath(workspace_dir)
        if os.path.isabs(rel_or_abs):
            return os.path.realpath(rel_or_abs)
        return os.path.realpath(os.path.join(workspace_dir, rel_or_abs))

    @classmethod
    def _to_rel_path(cls, full_path: str, workspace_dir: str) -> str:
        try:
            return os.path.relpath(full_path, workspace_dir).replace("\\", "/")
        except ValueError:
            return full_path.replace("\\", "/")

    @classmethod
    def _get_cached_ast(cls, full_path: str, code: str) -> Optional[ast.AST]:
        try:
            norm_path = os.path.realpath(full_path)
            mtime = os.path.getmtime(norm_path) if os.path.exists(norm_path) else 0.0
            code_hash = hash(code)
            cached = cls._ast_cache.get(norm_path)
            if cached and cached[0] == mtime and cached[1] == code_hash:
                return cached[2]
            tree = ast.parse(code, filename=norm_path)
            cls._ast_cache[norm_path] = (mtime, code_hash, tree)
            return tree
        except Exception:
            return None

    # ══════════════════════════════════════════════════════════════════════════
    # 1. FIND DEFINITION (textDocument/definition)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def find_definition(
        cls,
        symbol: str = "",
        file_path: Optional[str] = None,
        line: Optional[int] = None,
        col: Optional[int] = None,
        repo_path: str = ".",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Compiler-grade jump-to-definition.
        Resolves symbol to exact target file path, line, column, signature, docstring, and kind.
        Accepts current_file and workspace_dir aliases.
        """
        # Aliases
        if not file_path and "current_file" in kwargs:
            file_path = kwargs["current_file"]
        if repo_path == "." and "workspace_dir" in kwargs:
            repo_path = kwargs["workspace_dir"]

        ws_abs = os.path.realpath(repo_path)
        symbol = (symbol or "").strip()

        # If symbol is empty but line/col in file_path are given, extract symbol under cursor
        curr_abs = cls._get_workspace_path(ws_abs, file_path) if file_path else None
        curr_code = cls._read_file_safe(curr_abs) if curr_abs and os.path.isfile(curr_abs) else None

        if not symbol and curr_code and line:
            extracted = cls._extract_symbol_at_position(curr_code, line, col or 0)
            if extracted:
                symbol = extracted

        if not symbol:
            return {"found": False, "error": "Symbol name or valid (file_path, line) position is required"}

        # Attempt 1: If jedi is available and this is Python, try jedi
        if jedi and curr_abs and curr_abs.endswith(".py") and curr_code and line:
            try:
                script = jedi.Script(code=curr_code, path=curr_abs)
                c_pos = col if col is not None else 0
                defs = script.goto(line=line, column=c_pos) or script.infer(line=line, column=c_pos)
                if defs:
                    d = defs[0]
                    target_file = d.module_path or curr_abs
                    t_rel = cls._to_rel_path(str(target_file), ws_abs)
                    t_line = d.line or 1
                    t_col = d.column or 0
                    t_code = cls._read_file_safe(str(target_file)) or curr_code
                    t_lines = t_code.splitlines()
                    snip = "\n".join(t_lines[max(0, t_line - 1): min(len(t_lines), t_line + 10)])
                    return {
                        "found": True,
                        "source": "jedi",
                        "symbol": d.name or symbol,
                        "file_path": t_rel,
                        "absolute_path": str(target_file),
                        "line": t_line,
                        "column": t_col,
                        "kind": d.type or "symbol",
                        "signature": d.description or f"{d.type} {d.name}",
                        "docstring": d.docstring() or "",
                        "snippet": snip,
                    }
            except Exception as e:
                logger.debug(f"Jedi lookup failed: {e}")

        # Attempt 2: If current_file is provided and is Python, inspect local AST and trace imports
        if curr_abs and curr_abs.endswith((".py", ".pyw")) and curr_code:
            # Check if defined directly in current_file
            local_def = cls._find_python_symbol_in_code(curr_code, symbol, curr_abs, ws_abs)
            if local_def:
                return {"found": True, "source": "current_file", **local_def}

            # Trace imports with circular import protection
            import_target = cls._trace_python_import(curr_code, symbol, curr_abs, ws_abs, visited=set())
            if import_target:
                target_code = cls._read_file_safe(import_target["file_path"])
                if target_code:
                    target_def = cls._find_python_symbol_in_code(
                        target_code,
                        import_target["symbol_in_target"],
                        import_target["file_path"],
                        ws_abs,
                    )
                    if target_def:
                        return {"found": True, "source": "imported_module", **target_def}

                    # Module definition jump: if symbol is a module or submodule file
                    target_stem = os.path.splitext(os.path.basename(import_target["file_path"]))[0]
                    target_rel = cls._to_rel_path(import_target["file_path"], ws_abs)
                    if import_target.get("is_module") or target_stem == import_target["symbol_in_target"] or target_stem == "__init__":
                        t_lines = target_code.splitlines()
                        snip = "\n".join(t_lines[:min(10, len(t_lines))])
                        mod_doc = ""
                        try:
                            t_tree = cls._get_cached_ast(import_target["file_path"], target_code)
                            if t_tree:
                                mod_doc = ast.get_docstring(t_tree) or ""
                        except Exception:
                            pass
                        return {
                            "found": True,
                            "source": "imported_module",
                            "symbol": symbol,
                            "kind": "module",
                            "file_path": target_rel,
                            "absolute_path": import_target["file_path"],
                            "line": 1,
                            "column": 0,
                            "signature": f"module {import_target['symbol_in_target']}",
                            "docstring": mod_doc,
                            "snippet": snip,
                        }

        # Attempt 3: If current_file is JS/TS, check local definitions and relative imports
        if curr_abs and curr_code and any(curr_abs.endswith(ext) for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
            local_js_def = cls._find_js_ts_symbol_in_code(curr_code, symbol, curr_abs, ws_abs)
            if local_js_def:
                return {"found": True, "source": "current_file", **local_js_def}

            # Trace JS/TS imports
            js_target = cls._trace_js_ts_import(curr_code, symbol, curr_abs, ws_abs)
            if js_target:
                t_code = cls._read_file_safe(js_target["file_path"])
                if t_code:
                    t_def = cls._find_js_ts_symbol_in_code(t_code, js_target["symbol_in_target"], js_target["file_path"], ws_abs)
                    if t_def:
                        return {"found": True, "source": "imported_module", **t_def}
                    if js_target.get("is_default"):
                        t_default_def = cls._find_js_ts_default_export(t_code, js_target["file_path"], ws_abs)
                        if t_default_def:
                            return {"found": True, "source": "imported_module", **t_default_def}

        # Attempt 4: Global Workspace AST Scan (Python first, then JS/TS, then Go/Rust)
        py_files: List[str] = []
        js_files: List[str] = []
        other_files: List[str] = []

        for root, dirs, files in os.walk(ws_abs):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in cls.IGNORE_DIRS]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in cls.IGNORE_EXTS:
                    continue
                full_p = os.path.join(root, f)
                if ext in (".py", ".pyw"):
                    py_files.append(full_p)
                elif ext in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"):
                    js_files.append(full_p)
                elif ext in (".go", ".rs"):
                    other_files.append(full_p)

        for pf in py_files:
            code = cls._read_file_safe(pf)
            if not code or symbol not in code:
                continue
            res = cls._find_python_symbol_in_code(code, symbol, pf, ws_abs)
            if res:
                return {"found": True, "source": "workspace_scan", **res}

        for jf in js_files:
            code = cls._read_file_safe(jf)
            if not code or symbol not in code:
                continue
            res = cls._find_js_ts_symbol_in_code(code, symbol, jf, ws_abs)
            if res:
                return {"found": True, "source": "workspace_scan", **res}

        for of in other_files:
            code = cls._read_file_safe(of)
            if not code or symbol not in code:
                continue
            res = cls._find_multi_lang_symbol(code, symbol, of, ws_abs)
            if res:
                return {"found": True, "source": "workspace_scan", **res}

        return {
            "found": False,
            "symbol": symbol,
            "message": f"Definition for '{symbol}' not found in codebase.",
        }

    @classmethod
    def goto_definition(
        cls,
        symbol: str,
        current_file: Optional[str] = None,
        workspace_dir: str = ".",
        **kwargs,
    ) -> Dict[str, Any]:
        """Backwards-compatible wrapper around find_definition."""
        return cls.find_definition(
            symbol=symbol,
            file_path=current_file,
            repo_path=workspace_dir,
            **kwargs,
        )

    @classmethod
    def _extract_symbol_at_position(cls, code: str, line: int, col: int) -> Optional[str]:
        lines = code.splitlines()
        if line < 1 or line > len(lines):
            return None
        target_line = lines[line - 1]
        if not target_line:
            return None
        c = max(0, min(col, len(target_line) - 1))
        # Find identifier boundary around c
        ident_pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
        for m in ident_pattern.finditer(target_line):
            if m.start() <= c <= m.end():
                return m.group(0)
        return None

    @classmethod
    def _find_python_symbol_in_code(
        cls,
        code: str,
        symbol: str,
        file_path: str,
        workspace_dir: str,
    ) -> Optional[Dict[str, Any]]:
        tree = cls._get_cached_ast(file_path, code)
        if not tree:
            try:
                tree = ast.parse(code, filename=file_path)
            except Exception:
                return None

        lines = code.splitlines()
        rel_path = cls._to_rel_path(file_path, workspace_dir)

        # Check for dotted symbol: Class.method or module.Class.method
        class_filter = None
        method_target = symbol
        if "." in symbol:
            parts = symbol.split(".")
            receiver = parts[-2]
            if receiver.lower() not in ("self", "cls", "this"):
                class_filter = receiver
            method_target = parts[-1]

        for node in ast.walk(tree):
            # Class definition
            if isinstance(node, ast.ClassDef):
                if not class_filter and node.name == symbol:
                    doc = ast.get_docstring(node) or ""
                    bases = [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else []
                    sig = f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
                    end_lineno = getattr(node, "end_lineno", node.lineno)
                    snippet = "\n".join(lines[node.lineno - 1 : min(node.lineno + 12, len(lines))])
                    return {
                        "symbol": symbol,
                        "kind": "class",
                        "file_path": rel_path,
                        "absolute_path": file_path,
                        "line": node.lineno,
                        "column": getattr(node, "col_offset", 0),
                        "end_line": end_lineno,
                        "signature": sig,
                        "docstring": doc,
                        "snippet": snippet,
                    }

                # Inspect methods inside this class
                if class_filter is None or node.name == class_filter:
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_target:
                            doc = ast.get_docstring(child) or ""
                            prefix = "async def " if isinstance(child, ast.AsyncFunctionDef) else "def "
                            args_list = [a.arg for a in child.args.args]
                            sig = f"{prefix}{node.name}.{child.name}({', '.join(args_list)})"
                            if child.returns and hasattr(ast, "unparse"):
                                sig += f" -> {ast.unparse(child.returns)}"
                            end_lineno = getattr(child, "end_lineno", child.lineno)
                            snippet = "\n".join(lines[child.lineno - 1 : min(child.lineno + 12, len(lines))])
                            return {
                                "symbol": symbol,
                                "kind": "method",
                                "class_name": node.name,
                                "file_path": rel_path,
                                "absolute_path": file_path,
                                "line": child.lineno,
                                "column": getattr(child, "col_offset", 0),
                                "end_line": end_lineno,
                                "signature": sig,
                                "docstring": doc,
                                "snippet": snippet,
                            }

            # Top-level Function / AsyncFunction definition
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if (not class_filter and (node.name == symbol or node.name == method_target)) or node.name == symbol:
                    doc = ast.get_docstring(node) or ""
                    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
                    args_list = [a.arg for a in node.args.args]
                    sig = f"{prefix}{node.name}({', '.join(args_list)})"
                    if node.returns and hasattr(ast, "unparse"):
                        sig += f" -> {ast.unparse(node.returns)}"
                    end_lineno = getattr(node, "end_lineno", node.lineno)
                    snippet = "\n".join(lines[node.lineno - 1 : min(node.lineno + 12, len(lines))])
                    return {
                        "symbol": symbol,
                        "kind": "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                        "file_path": rel_path,
                        "absolute_path": file_path,
                        "line": node.lineno,
                        "column": getattr(node, "col_offset", 0),
                        "end_line": end_lineno,
                        "signature": sig,
                        "docstring": doc,
                        "snippet": snippet,
                    }

            # Variable / Assignment definition
            if not class_filter and isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and (target.id == symbol or target.id == method_target):
                        val_str = ast.unparse(node.value) if hasattr(ast, "unparse") else "..."
                        return {
                            "symbol": symbol,
                            "kind": "variable",
                            "file_path": rel_path,
                            "absolute_path": file_path,
                            "line": node.lineno,
                            "column": getattr(node, "col_offset", 0),
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "signature": f"{target.id} = {val_str[:60]}",
                            "docstring": "",
                            "snippet": lines[node.lineno - 1] if node.lineno <= len(lines) else "",
                        }

            if not class_filter and isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and (node.target.id == symbol or node.target.id == method_target):
                    ann_str = ast.unparse(node.annotation) if hasattr(ast, "unparse") else ""
                    return {
                        "symbol": symbol,
                        "kind": "variable",
                        "file_path": rel_path,
                        "absolute_path": file_path,
                        "line": node.lineno,
                        "column": getattr(node, "col_offset", 0),
                        "end_line": getattr(node, "end_lineno", node.lineno),
                        "signature": f"{node.target.id}: {ann_str}",
                        "docstring": "",
                        "snippet": lines[node.lineno - 1] if node.lineno <= len(lines) else "",
                    }

        return None

    @classmethod
    def _trace_python_import(
        cls,
        code: str,
        symbol: str,
        current_file: str,
        workspace_dir: str,
        visited: Optional[Set[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        if visited is None:
            visited = set()
        real_curr = os.path.realpath(current_file)
        if real_curr in visited:
            return None
        visited.add(real_curr)

        tree = cls._get_cached_ast(current_file, code)
        if not tree:
            try:
                tree = ast.parse(code, filename=current_file)
            except Exception:
                return None

        curr_dir = os.path.dirname(current_file)
        sym_head = symbol.split(".")[0] if "." in symbol else symbol
        sym_tail = symbol[len(sym_head) + 1:] if "." in symbol else ""

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                rel_root = curr_dir
                if node.level > 1:
                    for _ in range(node.level - 1):
                        rel_root = os.path.dirname(rel_root)

                for alias in node.names:
                    # 1. Star import: from .module import * or from module import *
                    if alias.name == "*":
                        star_mod_p = cls._resolve_module_to_path(module, node.level, curr_dir, workspace_dir)
                        if star_mod_p and os.path.exists(star_mod_p) and os.path.realpath(star_mod_p) not in visited:
                            star_code = cls._read_file_safe(star_mod_p)
                            if star_code:
                                star_tree = cls._get_cached_ast(star_mod_p, star_code)
                                if star_tree:
                                    exported = cls._extract_exported_symbols(star_tree, star_mod_p, workspace_dir)
                                    if sym_head in exported:
                                        deep = cls._trace_python_import(star_code, symbol, star_mod_p, workspace_dir, visited)
                                        if deep:
                                            return deep
                                        return {
                                            "file_path": star_mod_p,
                                            "symbol_in_target": symbol,
                                        }

                    local_name = alias.asname or alias.name
                    is_match = (local_name == symbol) or (local_name == sym_head)
                    if is_match:
                        orig_symbol = alias.name
                        target_sym_in_mod = f"{orig_symbol}.{sym_tail}" if sym_tail else orig_symbol

                        # A. Relative import without module: from . import models
                        if node.level > 0 and not module:
                            sub_mod_cand = os.path.join(rel_root, orig_symbol + ".py")
                            sub_pkg_cand = os.path.join(rel_root, orig_symbol, "__init__.py")
                            if os.path.isfile(sub_mod_cand):
                                return {
                                    "file_path": os.path.realpath(sub_mod_cand),
                                    "symbol_in_target": sym_tail or orig_symbol,
                                    "is_module": not bool(sym_tail),
                                }
                            if os.path.isfile(sub_pkg_cand):
                                return {
                                    "file_path": os.path.realpath(sub_pkg_cand),
                                    "symbol_in_target": sym_tail or orig_symbol,
                                    "is_module": not bool(sym_tail),
                                }

                        # B. Normal module resolution
                        resolved_path = cls._resolve_module_to_path(module, node.level, curr_dir, workspace_dir)
                        if resolved_path and os.path.exists(resolved_path):
                            # Check if alias.name is a submodule in resolved_path's package directory
                            res_dir = os.path.dirname(resolved_path) if resolved_path.endswith("__init__.py") else os.path.splitext(resolved_path)[0]
                            sub_mod_cand = os.path.join(res_dir, orig_symbol + ".py")
                            sub_pkg_cand = os.path.join(res_dir, orig_symbol, "__init__.py")
                            if os.path.isfile(sub_mod_cand):
                                return {
                                    "file_path": os.path.realpath(sub_mod_cand),
                                    "symbol_in_target": sym_tail or orig_symbol,
                                    "is_module": not bool(sym_tail),
                                }
                            if os.path.isfile(sub_pkg_cand):
                                return {
                                    "file_path": os.path.realpath(sub_pkg_cand),
                                    "symbol_in_target": sym_tail or orig_symbol,
                                    "is_module": not bool(sym_tail),
                                }

                            # Check if the resolved file re-exports it
                            sub_code = cls._read_file_safe(resolved_path)
                            if sub_code and os.path.realpath(resolved_path) not in visited:
                                deep_target = cls._trace_python_import(sub_code, target_sym_in_mod, resolved_path, workspace_dir, visited)
                                if deep_target:
                                    return deep_target
                            return {
                                "file_path": resolved_path,
                                "symbol_in_target": target_sym_in_mod,
                            }

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    if local_name == symbol or symbol.startswith(local_name + "."):
                        resolved_path = cls._resolve_module_to_path(alias.name, 0, curr_dir, workspace_dir)
                        if resolved_path and os.path.exists(resolved_path):
                            sub_symbol = symbol[len(local_name) + 1:] if symbol.startswith(local_name + ".") else ""
                            return {
                                "file_path": resolved_path,
                                "symbol_in_target": sub_symbol or alias.name.split(".")[-1],
                                "is_module": not bool(sub_symbol),
                            }
        return None

    @classmethod
    def _resolve_module_to_path(
        cls,
        module: str,
        level: int,
        curr_dir: str,
        workspace_dir: str,
    ) -> Optional[str]:
        candidates: List[str] = []
        if level > 0:
            rel_root = curr_dir
            for _ in range(level - 1):
                rel_root = os.path.dirname(rel_root)
            mod_parts = module.split(".") if module else []
            target_base = os.path.join(rel_root, *mod_parts)
            candidates.append(target_base + ".py")
            candidates.append(os.path.join(target_base, "__init__.py"))
            candidates.append(target_base + ".pyw")
        else:
            mod_parts = module.split(".")
            target_base = os.path.join(workspace_dir, *mod_parts)
            candidates.append(target_base + ".py")
            candidates.append(os.path.join(target_base, "__init__.py"))
            candidates.append(target_base + ".pyw")

            # Check if subfolders in workspace contain the package
            for sub_name in ("src", "backend", "lib", "app"):
                sub_dir = os.path.join(workspace_dir, sub_name)
                if os.path.isdir(sub_dir):
                    sub_cand = os.path.join(sub_dir, *mod_parts)
                    candidates.append(sub_cand + ".py")
                    candidates.append(os.path.join(sub_cand, "__init__.py"))

        for cand in candidates:
            if os.path.isfile(cand):
                return os.path.realpath(cand)
        return None

    @classmethod
    def _find_js_ts_symbol_in_code(
        cls,
        code: str,
        symbol: str,
        file_path: str,
        workspace_dir: str,
    ) -> Optional[Dict[str, Any]]:
        lines = code.splitlines()
        rel_path = cls._to_rel_path(file_path, workspace_dir)

        # Check if symbol is dotted: e.g. ApiClient.getOrders or this.getOrders
        target_name = symbol
        if "." in symbol:
            target_name = symbol.split(".")[-1]

        # 1. Try esprima AST if JS
        if esprima and file_path.endswith((".js", ".mjs", ".cjs")):
            try:
                tree = esprima.parseModule(code, {"loc": True, "range": True, "tolerant": True})
                for item in getattr(tree, "body", []):
                    decl = getattr(item, "declaration", item)
                    # FunctionDeclaration
                    if getattr(decl, "type", None) == "FunctionDeclaration" and getattr(decl.id, "name", None) in (symbol, target_name):
                        loc = decl.loc.start
                        return {
                            "symbol": symbol,
                            "kind": "function",
                            "file_path": rel_path,
                            "absolute_path": file_path,
                            "line": loc.line,
                            "column": loc.column,
                            "signature": lines[loc.line - 1].strip()[:100],
                            "docstring": "",
                            "snippet": "\n".join(lines[loc.line - 1 : min(loc.line + 10, len(lines))]),
                        }
                    # ClassDeclaration
                    if getattr(decl, "type", None) == "ClassDeclaration" and getattr(decl.id, "name", None) in (symbol, target_name):
                        loc = decl.loc.start
                        return {
                            "symbol": symbol,
                            "kind": "class",
                            "file_path": rel_path,
                            "absolute_path": file_path,
                            "line": loc.line,
                            "column": loc.column,
                            "signature": lines[loc.line - 1].strip()[:100],
                            "docstring": "",
                            "snippet": "\n".join(lines[loc.line - 1 : min(loc.line + 10, len(lines))]),
                        }
            except Exception:
                pass

        # 2. Robust Regex AST Symbol Patterns
        patterns = [
            (rf"^\s*(?:export\s+)?(?:default\s+)?class\s+{re.escape(target_name)}\b", "class"),
            (rf"^\s*(?:export\s+)?interface\s+{re.escape(target_name)}\b", "interface"),
            (rf"^\s*(?:export\s+)?type\s+{re.escape(target_name)}\b", "type"),
            (rf"^\s*(?:export\s+)?enum\s+{re.escape(target_name)}\b", "enum"),
            (rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*(?:\*\s*)?{re.escape(target_name)}\b", "function"),
            (rf"^\s*(?:export\s+)?(?:const|let|var)\s+{re.escape(target_name)}\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>", "arrow_function"),
            (rf"^\s*(?:export\s+)?(?:const|let|var)\s+{re.escape(target_name)}\b", "variable"),
            (rf"^\s*(?:public|private|protected|static|async|override)*\s*{re.escape(target_name)}\s*\([^)]*\)", "method"),
        ]

        for pat, kind in patterns:
            rx = re.compile(pat)
            for idx, line in enumerate(lines):
                m = rx.search(line)
                if m:
                    snippet = "\n".join(lines[idx : min(idx + 10, len(lines))])
                    return {
                        "symbol": symbol,
                        "kind": kind,
                        "file_path": rel_path,
                        "absolute_path": file_path,
                        "line": idx + 1,
                        "column": m.start(),
                        "signature": line.strip()[:100],
                        "docstring": "",
                        "snippet": snippet,
                    }
        return None

    @classmethod
    def _find_js_ts_default_export(cls, code: str, file_path: str, workspace_dir: str) -> Optional[Dict[str, Any]]:
        lines = code.splitlines()
        rel_path = cls._to_rel_path(file_path, workspace_dir)
        for idx, line in enumerate(lines):
            m = re.search(r"^\s*export\s+default\s+(?:class|function|interface)?\s*([A-Za-z0-9_$]+)?", line)
            if m:
                name = m.group(1) or "default"
                snippet = "\n".join(lines[idx : min(idx + 10, len(lines))])
                return {
                    "symbol": name,
                    "kind": "default_export",
                    "file_path": rel_path,
                    "absolute_path": file_path,
                    "line": idx + 1,
                    "column": m.start(),
                    "signature": line.strip()[:100],
                    "docstring": "",
                    "snippet": snippet,
                }
        return None

    @classmethod
    def _resolve_js_ts_specifier(cls, specifier: str, curr_dir: str) -> Optional[str]:
        if not specifier or not specifier.startswith("."):
            return None
        base_target = os.path.normpath(os.path.join(curr_dir, specifier))
        extensions = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs")
        candidates = [base_target] + [base_target + ext for ext in extensions]
        for ext in extensions:
            candidates.append(os.path.join(base_target, "index" + ext))
        for cand in candidates:
            if os.path.isfile(cand):
                return os.path.realpath(cand)
        return None

    @classmethod
    def _trace_js_ts_import(
        cls,
        code: str,
        symbol: str,
        current_file: str,
        workspace_dir: str,
    ) -> Optional[Dict[str, Any]]:
        curr_dir = os.path.dirname(current_file)
        lines = code.splitlines()
        sym_head = symbol.split(".")[0] if "." in symbol else symbol
        sym_tail = symbol[len(sym_head) + 1:] if "." in symbol else ""

        for line in lines:
            # 1. Aliased import: import { Foo as Bar } from './api'
            alias_m = re.search(rf"""import\s+\{{[^}}]*\b([A-Za-z0-9_$]+)\s+as\s+{re.escape(sym_head)}\b[^}}]*\}}\s+from\s+['"]([^'"]+)['"]""", line)
            if alias_m:
                orig_name = alias_m.group(1)
                specifier = alias_m.group(2)
                resolved = cls._resolve_js_ts_specifier(specifier, curr_dir)
                if resolved:
                    return {
                        "file_path": resolved,
                        "symbol_in_target": f"{orig_name}.{sym_tail}" if sym_tail else orig_name,
                        "is_default": False,
                    }

            # 2. Default import: import Client from './api'
            default_m = re.search(rf"""import\s+{re.escape(sym_head)}\s+from\s+['"]([^'"]+)['"]""", line)
            if default_m:
                specifier = default_m.group(1)
                resolved = cls._resolve_js_ts_specifier(specifier, curr_dir)
                if resolved:
                    return {
                        "file_path": resolved,
                        "symbol_in_target": sym_tail or sym_head,
                        "is_default": not bool(sym_tail),
                    }

            # 3. Standard named import: import { Client } from './api'
            named_m = re.search(rf"""import\s+\{{[^}}]*\b{re.escape(sym_head)}\b[^}}]*\}}\s+from\s+['"]([^'"]+)['"]""", line)
            if named_m:
                specifier = named_m.group(1)
                resolved = cls._resolve_js_ts_specifier(specifier, curr_dir)
                if resolved:
                    return {
                        "file_path": resolved,
                        "symbol_in_target": symbol,
                        "is_default": False,
                    }

            # 4. CommonJS: const { Client } = require('./api') or const Client = require('./api')
            req_m = re.search(rf"""(?:const|let|var)\s+(?:\{{[^}}]*\b{re.escape(sym_head)}\b[^}}]*\}}|{re.escape(sym_head)})\s*=\s*require\(['"]([^'"]+)['"]\)""", line)
            if req_m:
                specifier = req_m.group(1)
                resolved = cls._resolve_js_ts_specifier(specifier, curr_dir)
                if resolved:
                    return {
                        "file_path": resolved,
                        "symbol_in_target": symbol,
                        "is_default": False,
                    }

        return None

    @classmethod
    def _find_multi_lang_symbol(
        cls,
        code: str,
        symbol: str,
        file_path: str,
        workspace_dir: str,
    ) -> Optional[Dict[str, Any]]:
        lines = code.splitlines()
        rel_path = cls._to_rel_path(file_path, workspace_dir)

        # Go patterns
        go_patterns = [
            (rf"^\s*func\s+(?:\([^)]+\)\s*)?{re.escape(symbol)}\b", "function"),
            (rf"^\s*type\s+{re.escape(symbol)}\s+(?:struct|interface)", "type"),
        ]
        # Rust patterns
        rust_patterns = [
            (rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+{re.escape(symbol)}\b", "function"),
            (rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+{re.escape(symbol)}\b", "type"),
        ]

        patterns = go_patterns if file_path.endswith(".go") else rust_patterns
        for pat, kind in patterns:
            rx = re.compile(pat)
            for idx, line in enumerate(lines):
                m = rx.search(line)
                if m:
                    snippet = "\n".join(lines[idx : min(idx + 10, len(lines))])
                    return {
                        "symbol": symbol,
                        "kind": kind,
                        "file_path": rel_path,
                        "absolute_path": file_path,
                        "line": idx + 1,
                        "column": m.start(),
                        "signature": line.strip()[:100],
                        "docstring": "",
                        "snippet": snippet,
                    }
        return None

    # ══════════════════════════════════════════════════════════════════════════
    # 2. FIND REFERENCES (textDocument/references)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def find_references(
        cls,
        symbol: str = "",
        file_path: Optional[str] = None,
        line: Optional[int] = None,
        col: Optional[int] = None,
        repo_path: str = ".",
        max_results: int = 50,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Locates all call sites, imports, declarations, assignments, and usages of a symbol across the project.
        Determines the enclosing caller function/method for every reference.
        Prioritizes the file under cursor when file_path is given, then searches the entire workspace.
        """
        if repo_path == "." and "workspace_dir" in kwargs:
            repo_path = kwargs["workspace_dir"]

        ws_abs = os.path.realpath(repo_path)
        symbol = (symbol or "").strip()

        # If symbol is empty but line/col are provided, extract symbol under cursor
        curr_abs = cls._get_workspace_path(ws_abs, file_path) if file_path else None
        curr_code = cls._read_file_safe(curr_abs) if curr_abs and os.path.isfile(curr_abs) else None
        if not symbol and curr_code and line:
            extracted = cls._extract_symbol_at_position(curr_code, line, col or 0)
            if extracted:
                symbol = extracted

        if not symbol:
            return {"symbol": "", "count": 0, "references": []}

        references: List[Dict[str, Any]] = []
        sym_tail = symbol.split(".")[-1]
        pattern = re.compile(rf"\b{re.escape(sym_tail)}\b")

        # Collect files to scan: if file_path is given, scan it FIRST, then all other files in workspace
        target_file_first = None
        if curr_abs and os.path.isfile(curr_abs):
            target_file_first = curr_abs

        single_file_only = kwargs.get("single_file_only", False) or kwargs.get("current_file_only", False)
        files_to_scan: List[str] = []
        if target_file_first:
            files_to_scan.append(target_file_first)

        if not single_file_only:
            for root, dirs, files in os.walk(ws_abs):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in cls.IGNORE_DIRS]
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in cls.IGNORE_EXTS and ext in (
                        ".py", ".pyw", ".js", ".ts", ".jsx", ".tsx", ".json", ".md", ".yaml", ".yml", ".go", ".rs"
                    ):
                        fp = os.path.realpath(os.path.join(root, f))
                        if fp != target_file_first:
                            files_to_scan.append(fp)

        for full_p in files_to_scan:
            code = cls._read_file_safe(full_p)
            if not code or (sym_tail not in code and symbol not in code):
                continue

            rel_p = cls._to_rel_path(full_p, ws_abs)
            lines = code.splitlines()

            # For Python, perform AST-grounded reference and caller extraction
            if full_p.endswith((".py", ".pyw")):
                tree = cls._get_cached_ast(full_p, code)
                if tree:
                    py_refs = cls._find_python_ast_references(tree, symbol, rel_p, lines)
                    for r in py_refs:
                        references.append(r)
                        if len(references) >= max_results:
                            break
                    if len(references) >= max_results:
                        break
                    continue

            # Fallback syntactic reference extraction with caller heuristics (JS, TS, Go, Rust)
            current_enclosing = None
            for idx, line in enumerate(lines):
                line_str = line.strip()
                # Check for function or class boundary
                fn_m = re.match(r"^\s*(?:async\s+)?(?:def|function)\s+([A-Za-z0-9_$]+)", line)
                if fn_m:
                    current_enclosing = fn_m.group(1)
                cls_m = re.match(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_$]+)", line)
                if cls_m:
                    current_enclosing = cls_m.group(1)
                method_m = re.match(r"^\s*(?:public|private|protected|static|async|override)*\s*([A-Za-z0-9_$]+)\s*\([^)]*\)", line)
                if method_m and not fn_m and not cls_m:
                    current_enclosing = method_m.group(1)
                arrow_m = re.match(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>", line)
                if arrow_m:
                    current_enclosing = arrow_m.group(1)

                if pattern.search(line):
                    kind = "usage"
                    if line_str.startswith(("import ", "from ", "require(")):
                        kind = "import"
                    elif re.search(rf"\b(?:def|class|function|interface|type)\s+{re.escape(sym_tail)}\b", line_str):
                        kind = "definition"
                    elif re.search(rf"{re.escape(sym_tail)}\s*\(", line_str):
                        kind = "call"

                    references.append({
                        "file": rel_p,
                        "line": idx + 1,
                        "column": line.find(sym_tail) + 1 if sym_tail in line else 1,
                        "kind": kind,
                        "caller": current_enclosing or "<module>",
                        "text": line_str[:120],
                    })
                    if len(references) >= max_results:
                        break
            if len(references) >= max_results:
                break

        return {
            "symbol": symbol,
            "count": len(references),
            "references": references[:max_results],
        }

    @classmethod
    def _find_python_ast_references(
        cls,
        tree: ast.AST,
        symbol: str,
        rel_p: str,
        lines: List[str],
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        sym_tail = symbol.split(".")[-1]
        sym_head = symbol.split(".")[0]

        class ScopeVisitor(ast.NodeVisitor):
            def __init__(self):
                self.scope_stack: List[str] = []

            def visit_ClassDef(self, node):
                self.scope_stack.append(node.name)
                if node.name in (symbol, sym_tail):
                    results.append({
                        "file": rel_p,
                        "line": node.lineno,
                        "column": getattr(node, "col_offset", 0) + 1,
                        "kind": "definition",
                        "caller": ".".join(self.scope_stack[:-1]) or "<module>",
                        "text": lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                    })
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_FunctionDef(self, node):
                self.scope_stack.append(node.name)
                if node.name in (symbol, sym_tail):
                    results.append({
                        "file": rel_p,
                        "line": node.lineno,
                        "column": getattr(node, "col_offset", 0) + 1,
                        "kind": "definition",
                        "caller": ".".join(self.scope_stack[:-1]) or "<module>",
                        "text": lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                    })
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_AsyncFunctionDef(self, node):
                self.visit_FunctionDef(node)

            def visit_Call(self, node):
                callee_name = None
                if isinstance(node.func, ast.Name):
                    callee_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    callee_name = node.func.attr

                if callee_name in (symbol, sym_tail):
                    results.append({
                        "file": rel_p,
                        "line": node.lineno,
                        "column": getattr(node, "col_offset", 0) + 1,
                        "kind": "call",
                        "caller": ".".join(self.scope_stack) or "<module>",
                        "text": lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                    })
                self.generic_visit(node)

            def visit_Attribute(self, node):
                if node.attr in (symbol, sym_tail):
                    # Check if already recorded on this line as call
                    already = any(r["line"] == node.lineno and r["kind"] == "call" for r in results)
                    if not already:
                        kind = "assignment" if isinstance(getattr(node, "ctx", None), ast.Store) else "usage"
                        results.append({
                            "file": rel_p,
                            "line": node.lineno,
                            "column": getattr(node, "col_offset", 0) + 1,
                            "kind": kind,
                            "caller": ".".join(self.scope_stack) or "<module>",
                            "text": lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                        })
                self.generic_visit(node)

            def visit_ImportFrom(self, node):
                for alias in node.names:
                    if alias.name in (symbol, sym_tail) or alias.asname in (symbol, sym_tail):
                        results.append({
                            "file": rel_p,
                            "line": node.lineno,
                            "column": getattr(node, "col_offset", 0) + 1,
                            "kind": "import",
                            "caller": "<module>",
                            "text": lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                        })
                self.generic_visit(node)

            def visit_Import(self, node):
                for alias in node.names:
                    if alias.name in (symbol, sym_tail) or alias.asname in (symbol, sym_tail):
                        results.append({
                            "file": rel_p,
                            "line": node.lineno,
                            "column": getattr(node, "col_offset", 0) + 1,
                            "kind": "import",
                            "caller": "<module>",
                            "text": lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                        })
                self.generic_visit(node)

            def visit_Name(self, node):
                if node.id in (symbol, sym_tail, sym_head):
                    is_store = isinstance(getattr(node, "ctx", None), ast.Store)
                    kind = "assignment" if is_store else "usage"
                    already = any(r["line"] == node.lineno and r["kind"] in ("call", "definition", "import", "assignment") for r in results)
                    if not already:
                        results.append({
                            "file": rel_p,
                            "line": node.lineno,
                            "column": getattr(node, "col_offset", 0) + 1,
                            "kind": kind,
                            "caller": ".".join(self.scope_stack) or "<module>",
                            "text": lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "",
                        })
                self.generic_visit(node)

        visitor = ScopeVisitor()
        try:
            visitor.visit(tree)
        except Exception:
            pass
        return results

    # ══════════════════════════════════════════════════════════════════════════
    # 3. COMPILER DIAGNOSTICS (textDocument/publishDiagnostics)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def get_diagnostics(
        cls,
        file_path: Optional[str] = None,
        repo_path: str = ".",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Compiler diagnostics:
        Returns real-time syntax, import resolution, missing symbols, and broken references
        across the target file and any dependent files that import from it.
        """
        if repo_path == "." and "workspace_dir" in kwargs:
            repo_path = kwargs["workspace_dir"]

        ws_abs = os.path.realpath(repo_path)
        diagnostics: List[Dict[str, Any]] = []

        if file_path:
            full_p = cls._get_workspace_path(ws_abs, file_path)
            if not os.path.exists(full_p):
                return {
                    "status": "errors_found",
                    "file_path": file_path,
                    "error_count": 1,
                    "warning_count": 0,
                    "diagnostics": [{
                        "file": file_path,
                        "line": 1,
                        "column": 1,
                        "severity": "error",
                        "source": "filesystem",
                        "message": f"File not found: {file_path}",
                        "code": "FILE_NOT_FOUND",
                    }],
                }

            # 1. Run diagnostics on the file itself
            cls._check_single_file_diagnostics(full_p, ws_abs, diagnostics)

            # 2. Ripple Diagnostics: check if modifying this file broke importers in the codebase
            cls._check_ripple_diagnostics(full_p, ws_abs, diagnostics)

        else:
            # Workspace-wide diagnostic scan
            for root, dirs, files in os.walk(ws_abs):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in cls.IGNORE_DIRS]
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in (".py", ".js", ".ts", ".json"):
                        cls._check_single_file_diagnostics(os.path.join(root, f), ws_abs, diagnostics)

        err_cnt = len([d for d in diagnostics if d.get("severity") == "error"])
        warn_cnt = len([d for d in diagnostics if d.get("severity") == "warning"])

        return {
            "status": "clean" if err_cnt == 0 else "errors_found",
            "file_path": file_path,
            "error_count": err_cnt,
            "warning_count": warn_cnt,
            "diagnostics": diagnostics,
        }

    @classmethod
    def _check_single_file_diagnostics(
        cls,
        full_path: str,
        ws_abs: str,
        diagnostics: List[Dict[str, Any]],
    ):
        rel_path = cls._to_rel_path(full_path, ws_abs)
        code = cls._read_file_safe(full_path)
        if code is None:
            diagnostics.append({
                "file": rel_path,
                "line": 1,
                "column": 1,
                "severity": "error",
                "source": "filesystem",
                "message": "Unable to read file content",
                "code": "READ_ERROR",
            })
            return

        # Python Diagnostics
        if full_path.endswith((".py", ".pyw")):
            try:
                compile(code, full_path, "exec")
            except SyntaxError as e:
                diagnostics.append({
                    "file": rel_path,
                    "line": e.lineno or 1,
                    "column": e.offset or 1,
                    "severity": "error",
                    "source": "syntax",
                    "message": f"SyntaxError: {e.msg}",
                    "code": "SYNTAX_ERROR",
                })
                return
            except Exception as e:
                diagnostics.append({
                    "file": rel_path,
                    "line": 1,
                    "column": 1,
                    "severity": "error",
                    "source": "syntax",
                    "message": f"{type(e).__name__}: {str(e)}",
                    "code": "SYNTAX_ERROR",
                })
                return

            # AST & Semantic Checks
            tree = cls._get_cached_ast(full_path, code)
            if tree:
                cls._check_python_imports_and_calls(tree, full_path, ws_abs, code, diagnostics)

        # JavaScript Diagnostics (esprima)
        elif full_path.endswith((".js", ".mjs", ".cjs")) and esprima:
            try:
                esprima.parseModule(code, {"loc": True, "tolerant": False})
            except Exception as e:
                line_no = getattr(e, "lineNumber", 1)
                col_no = getattr(e, "column", 1)
                msg = getattr(e, "description", str(e))
                diagnostics.append({
                    "file": rel_path,
                    "line": line_no,
                    "column": col_no,
                    "severity": "error",
                    "source": "compiler",
                    "message": f"JS Compiler Error: {msg}",
                    "code": "JS_SYNTAX_ERROR",
                })

        # JSON Diagnostics
        elif full_path.endswith(".json"):
            try:
                json.loads(code)
            except json.JSONDecodeError as e:
                diagnostics.append({
                    "file": rel_path,
                    "line": e.lineno,
                    "column": e.colno,
                    "severity": "error",
                    "source": "compiler",
                    "message": f"JSON Syntax Error: {e.msg}",
                    "code": "JSON_ERROR",
                })

    @classmethod
    def _check_python_imports_and_calls(
        cls,
        tree: ast.AST,
        current_file: str,
        ws_abs: str,
        code: str,
        diagnostics: List[Dict[str, Any]],
    ):
        rel_path = cls._to_rel_path(current_file, ws_abs)
        curr_dir = os.path.dirname(current_file)

        # Map of local function definitions -> (min_args, max_args, vararg)
        local_functions: Dict[str, Tuple[int, Optional[int], bool]] = {}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                min_a, max_a, has_vararg = cls._get_function_param_counts(node)
                local_functions[node.name] = (min_a, max_a, has_vararg)

            # Check imports
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # Only check project-internal modules
                is_internal = node.level > 0 or cls._is_internal_module(module, ws_abs)
                if is_internal:
                    resolved_p = cls._resolve_module_to_path(module, node.level, curr_dir, ws_abs)
                    if not resolved_p or not os.path.isfile(resolved_p):
                        # Before raising E0401, check if relative import without module points to submodule files
                        rel_root = curr_dir
                        if node.level > 1:
                            for _ in range(node.level - 1):
                                rel_root = os.path.dirname(rel_root)
                        all_submods_exist = bool(node.names) and all(
                            os.path.isfile(os.path.join(rel_root, f"{a.name}.py")) or
                            os.path.isfile(os.path.join(rel_root, a.name, "__init__.py"))
                            for a in node.names if a.name != "*"
                        )
                        if not all_submods_exist:
                            diagnostics.append({
                                "file": rel_path,
                                "line": node.lineno,
                                "column": getattr(node, "col_offset", 0) + 1,
                                "severity": "error",
                                "source": "compiler",
                                "message": f"Cannot resolve import '{module}': module file not found in repository.",
                                "code": "E0401",
                            })
                    else:
                        # Module exists; verify each imported symbol exists in the target module or is a submodule
                        mod_code = cls._read_file_safe(resolved_p)
                        if mod_code:
                            mod_tree = cls._get_cached_ast(resolved_p, mod_code)
                            if mod_tree:
                                target_symbols = cls._extract_exported_symbols(mod_tree, resolved_p, ws_abs)
                                target_dir = os.path.dirname(resolved_p) if resolved_p.endswith("__init__.py") else os.path.splitext(resolved_p)[0]
                                for alias in node.names:
                                    if alias.name != "*" and alias.name not in target_symbols:
                                        # Check if alias.name is a submodule file or subpackage in target_dir
                                        sub_f = os.path.join(target_dir, f"{alias.name}.py")
                                        sub_d = os.path.join(target_dir, alias.name, "__init__.py")
                                        if os.path.isfile(sub_f) or os.path.isfile(sub_d):
                                            continue  # Valid submodule import!
                                        target_rel = cls._to_rel_path(resolved_p, ws_abs)
                                        diagnostics.append({
                                            "file": rel_path,
                                            "line": node.lineno,
                                            "column": getattr(node, "col_offset", 0) + 1,
                                            "severity": "error",
                                            "source": "compiler",
                                            "message": (
                                                f"Cannot import name '{alias.name}' from '{module}' "
                                                f"(symbol not defined in {target_rel})."
                                            ),
                                            "code": "E0611",
                                        })

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if cls._is_internal_module(alias.name, ws_abs):
                        resolved_p = cls._resolve_module_to_path(alias.name, 0, curr_dir, ws_abs)
                        if not resolved_p or not os.path.isfile(resolved_p):
                            diagnostics.append({
                                "file": rel_path,
                                "line": node.lineno,
                                "column": getattr(node, "col_offset", 0) + 1,
                                "severity": "error",
                                "source": "compiler",
                                "message": f"Cannot resolve import '{alias.name}': module file not found in repository.",
                                "code": "E0401",
                            })

            # Check calls for missing required arguments
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                fname = node.func.id
                if fname in local_functions:
                    min_a, max_a, has_vararg = local_functions[fname]
                    # Count provided positional args + kwargs
                    provided_args = len(node.args) + len([k for k in node.keywords if k.arg is not None])
                    if provided_args < min_a and not has_vararg:
                        diagnostics.append({
                            "file": rel_path,
                            "line": node.lineno,
                            "column": getattr(node, "col_offset", 0) + 1,
                            "severity": "error",
                            "source": "type_checker",
                            "message": f"Call to '{fname}()' missing required arguments (expected at least {min_a}, got {provided_args}).",
                            "code": "SIGNATURE_MISMATCH",
                        })

    @classmethod
    def _get_function_param_counts(cls, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> Tuple[int, Optional[int], bool]:
        args = node.args
        total_pos = len(args.posonlyargs) + len(args.args)
        num_defaults = len(args.defaults)
        min_args = total_pos - num_defaults
        has_vararg = args.vararg is not None
        max_args = None if has_vararg else (total_pos + len(args.kwonlyargs))
        return min_args, max_args, has_vararg

    @classmethod
    def _extract_exported_symbols(
        cls,
        tree: ast.AST,
        current_file: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        depth: int = 0,
    ) -> Set[str]:
        symbols: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        symbols.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                symbols.add(node.target.id)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        symbols.add(alias.asname or alias.name)
                    elif current_file and workspace_dir and depth < 3:
                        # Follow star imports to discover re-exported symbols
                        curr_dir = os.path.dirname(current_file)
                        star_p = cls._resolve_module_to_path(node.module or "", node.level, curr_dir, workspace_dir)
                        if star_p and os.path.isfile(star_p):
                            star_c = cls._read_file_safe(star_p)
                            if star_c:
                                star_t = cls._get_cached_ast(star_p, star_c)
                                if star_t:
                                    symbols.update(cls._extract_exported_symbols(star_t, star_p, workspace_dir, depth + 1))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    symbols.add(alias.asname or alias.name)
        return symbols

    @classmethod
    def _extract_js_ts_exports(cls, code: str) -> Set[str]:
        exports: Set[str] = set()
        for line in code.splitlines():
            m = re.search(r"^\s*export\s+(?:default\s+)?(?:class|function|interface|type|const|let|var)\s+([A-Za-z0-9_$]+)", line)
            if m:
                exports.add(m.group(1))
            m_br = re.search(r"^\s*export\s+\{([^}]+)\}", line)
            if m_br:
                for item in m_br.group(1).split(","):
                    p = item.strip().split()
                    if p:
                        exports.add(p[-1])
        return exports

    @classmethod
    def _is_internal_module(cls, module_name: str, ws_abs: str) -> bool:
        if not module_name:
            return False
        first_pkg = module_name.split(".")[0]
        # If top package matches a directory or python file in workspace, it's internal
        if os.path.isdir(os.path.join(ws_abs, first_pkg)) or os.path.isfile(os.path.join(ws_abs, f"{first_pkg}.py")):
            return True
        for sub in ("src", "backend", "lib", "app"):
            sub_p = os.path.join(ws_abs, sub)
            if os.path.isdir(sub_p):
                if os.path.isdir(os.path.join(sub_p, first_pkg)) or os.path.isfile(os.path.join(sub_p, f"{first_pkg}.py")):
                    return True
        return False

    @classmethod
    def _check_ripple_diagnostics(
        cls,
        changed_file: str,
        ws_abs: str,
        diagnostics: List[Dict[str, Any]],
    ):
        """
        Devin/Kyros Ripple Diagnostic Check:
        If changed_file was modified, immediately find all other files that import it
        and report broken imports or calls before tests run!
        """
        changed_rel = cls._to_rel_path(changed_file, ws_abs)
        changed_stem = os.path.splitext(os.path.basename(changed_file))[0]

        # 1. Python Ripple Diagnostics
        if changed_file.endswith((".py", ".pyw")):
            code = cls._read_file_safe(changed_file)
            if not code:
                return

            tree = cls._get_cached_ast(changed_file, code)
            if not tree:
                return

            current_symbols = cls._extract_exported_symbols(tree, changed_file, ws_abs)
            is_init = (changed_stem == "__init__")
            pkg_name = os.path.basename(os.path.dirname(changed_file)) if is_init else ""

            # Scan all other python files in the workspace
            for root, dirs, files in os.walk(ws_abs):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in cls.IGNORE_DIRS]
                for f in files:
                    if not f.endswith((".py", ".pyw")):
                        continue
                    other_p = os.path.join(root, f)
                    if os.path.realpath(other_p) == os.path.realpath(changed_file):
                        continue

                    other_code = cls._read_file_safe(other_p)
                    if not other_code:
                        continue

                    # Match filter: file must reference changed_stem or parent package name
                    filter_tokens = [changed_stem]
                    if is_init and pkg_name:
                        filter_tokens.append(pkg_name)
                    if not any(token in other_code for token in filter_tokens):
                        continue

                    other_tree = cls._get_cached_ast(other_p, other_code)
                    if not other_tree:
                        continue

                    other_dir = os.path.dirname(other_p)
                    other_rel = cls._to_rel_path(other_p, ws_abs)

                    for node in ast.walk(other_tree):
                        if isinstance(node, ast.ImportFrom):
                            res_p = cls._resolve_module_to_path(node.module or "", node.level, other_dir, ws_abs)
                            if res_p and os.path.realpath(res_p) == os.path.realpath(changed_file):
                                target_dir = os.path.dirname(changed_file) if changed_file.endswith("__init__.py") else os.path.splitext(changed_file)[0]
                                for alias in node.names:
                                    if alias.name != "*" and alias.name not in current_symbols:
                                        # Also check if alias is a submodule in the package directory
                                        sub_f = os.path.join(target_dir, f"{alias.name}.py")
                                        sub_d = os.path.join(target_dir, alias.name, "__init__.py")
                                        if os.path.isfile(sub_f) or os.path.isfile(sub_d):
                                            continue
                                        diagnostics.append({
                                            "file": other_rel,
                                            "line": node.lineno,
                                            "column": getattr(node, "col_offset", 0) + 1,
                                            "severity": "error",
                                            "source": "compiler_ripple",
                                            "message": (
                                                f"Broken import in {other_rel}: symbol '{alias.name}' "
                                                f"is no longer defined in {changed_rel}."
                                            ),
                                            "code": "E0611",
                                        })

        # 2. JavaScript / TypeScript Ripple Diagnostics
        elif any(changed_file.endswith(ext) for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
            code = cls._read_file_safe(changed_file)
            if not code:
                return
            js_exports = cls._extract_js_ts_exports(code)

            for root, dirs, files in os.walk(ws_abs):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in cls.IGNORE_DIRS]
                for f in files:
                    if not any(f.endswith(ext) for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
                        continue
                    other_p = os.path.join(root, f)
                    if os.path.realpath(other_p) == os.path.realpath(changed_file):
                        continue
                    other_code = cls._read_file_safe(other_p)
                    if not other_code or changed_stem not in other_code:
                        continue

                    other_dir = os.path.dirname(other_p)
                    other_rel = cls._to_rel_path(other_p, ws_abs)
                    for line_idx, line in enumerate(other_code.splitlines(), 1):
                        m_imp = re.search(r"""import\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]""", line)
                        if m_imp:
                            spec = m_imp.group(2)
                            base_cand = os.path.normpath(os.path.join(other_dir, spec))
                            changed_no_ext = os.path.splitext(changed_file)[0]
                            cand_matches = (
                                base_cand == changed_no_ext or
                                base_cand == changed_file or
                                base_cand.replace("\\", "/") == changed_no_ext.replace("\\", "/")
                            )
                            if cand_matches:
                                syms = [s.strip().split()[0] for s in m_imp.group(1).split(",") if s.strip()]
                                for sym in syms:
                                    if sym and sym not in js_exports:
                                        diagnostics.append({
                                            "file": other_rel,
                                            "line": line_idx,
                                            "column": 1,
                                            "severity": "error",
                                            "source": "compiler_ripple",
                                            "message": (
                                                f"Broken JS/TS import in {other_rel}: symbol '{sym}' "
                                                f"is no longer exported by {changed_rel}."
                                            ),
                                            "code": "TS_IMPORT_ERROR",
                                        })

    # ══════════════════════════════════════════════════════════════════════════
    # 4. IN-MEMORY SYMBOL REFERENCE GRAPH (textDocument/documentSymbol + Graph)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def get_symbol_graph(
        cls,
        repo_path: str = ".",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Generates an in-memory symbol reference graph:
        Classes, Methods, Functions, Exports, Imports, Callers, Callees, and Inheritance.
        Caches results with incremental file-mtime invalidation for sub-15ms queries.
        """
        if repo_path == "." and "workspace_dir" in kwargs:
            repo_path = kwargs["workspace_dir"]

        ws_abs = os.path.realpath(repo_path)
        current_mtimes: Dict[str, float] = {}

        # Scan files and build current mtimes map
        target_files: List[str] = []
        for root, dirs, files in os.walk(ws_abs):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in cls.IGNORE_DIRS]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in cls.IGNORE_EXTS:
                    continue
                if ext in (".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs"):
                    full_p = os.path.join(root, f)
                    try:
                        current_mtimes[full_p] = os.path.getmtime(full_p)
                        target_files.append(full_p)
                    except OSError:
                        pass

        # Check graph cache
        cached = cls._graph_cache.get(ws_abs)
        if cached:
            _, cached_mtimes, cached_data = cached
            if cached_mtimes == current_mtimes:
                return cached_data

        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        callers_map: Dict[str, List[str]] = {}
        callees_map: Dict[str, List[str]] = {}

        classes_count = 0
        funcs_count = 0
        methods_count = 0
        imports_count = 0

        for full_p in target_files:
            rel_p = cls._to_rel_path(full_p, ws_abs)
            code = cls._read_file_safe(full_p)
            if not code:
                continue

            file_node_id = f"file::{rel_p}"
            nodes.append({
                "id": file_node_id,
                "name": os.path.basename(full_p),
                "kind": "file",
                "file": rel_p,
                "line": 1,
            })

            if full_p.endswith((".py", ".pyw")):
                tree = cls._get_cached_ast(full_p, code)
                if not tree:
                    continue
                lines = code.splitlines()

                # Pre-scan classes to record all methods so they are never duplicated as top-level functions
                method_node_ids: Set[int] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        for child in node.body:
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                method_node_ids.add(id(child))
                                child._is_method = True

                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        classes_count += 1
                        class_node_id = f"{rel_p}::{node.name}"
                        nodes.append({
                            "id": class_node_id,
                            "name": node.name,
                            "kind": "class",
                            "file": rel_p,
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "signature": f"class {node.name}",
                            "docstring": ast.get_docstring(node) or "",
                        })
                        edges.append({
                            "source": file_node_id,
                            "target": class_node_id,
                            "type": "contains",
                            "line": node.lineno,
                        })

                        # Inheritance edges
                        for b in node.bases:
                            b_name = ast.unparse(b) if hasattr(ast, "unparse") else getattr(b, "id", "base")
                            edges.append({
                                "source": class_node_id,
                                "target": b_name,
                                "type": "inherits",
                                "line": node.lineno,
                            })

                        # Methods inside class
                        for child in node.body:
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                methods_count += 1
                                method_node_id = f"{rel_p}::{node.name}.{child.name}"
                                nodes.append({
                                    "id": method_node_id,
                                    "name": f"{node.name}.{child.name}",
                                    "kind": "method",
                                    "file": rel_p,
                                    "line": child.lineno,
                                    "signature": f"def {child.name}()",
                                })
                                edges.append({
                                    "source": class_node_id,
                                    "target": method_node_id,
                                    "type": "contains",
                                    "line": child.lineno,
                                })

                                # Method call sites
                                for sub in ast.walk(child):
                                    if isinstance(sub, ast.Call):
                                        cname = None
                                        if isinstance(sub.func, ast.Name):
                                            cname = sub.func.id
                                        elif isinstance(sub.func, ast.Attribute):
                                            cname = sub.func.attr
                                        if cname:
                                            edges.append({
                                                "source": method_node_id,
                                                "target": cname,
                                                "type": "calls",
                                                "line": sub.lineno,
                                            })
                                            callers_map.setdefault(cname, []).append(f"{node.name}.{child.name} ({rel_p}:{child.lineno})")
                                            callees_map.setdefault(f"{node.name}.{child.name}", []).append(cname)

                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Only top-level functions (methods strictly skipped)
                        if id(node) in method_node_ids or getattr(node, "_is_method", False):
                            continue
                        funcs_count += 1
                        func_node_id = f"{rel_p}::{node.name}"
                        nodes.append({
                            "id": func_node_id,
                            "name": node.name,
                            "kind": "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                            "file": rel_p,
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "signature": f"def {node.name}()",
                            "docstring": ast.get_docstring(node) or "",
                        })
                        edges.append({
                            "source": file_node_id,
                            "target": func_node_id,
                            "type": "contains",
                            "line": node.lineno,
                        })

                        # Function call sites
                        for sub in ast.walk(node):
                            if isinstance(sub, ast.Call):
                                cname = None
                                if isinstance(sub.func, ast.Name):
                                    cname = sub.func.id
                                elif isinstance(sub.func, ast.Attribute):
                                    cname = sub.func.attr
                                if cname:
                                    edges.append({
                                        "source": func_node_id,
                                        "target": cname,
                                        "type": "calls",
                                        "line": sub.lineno,
                                    })
                                    callers_map.setdefault(cname, []).append(f"{node.name} ({rel_p}:{node.lineno})")
                                    callees_map.setdefault(node.name, []).append(cname)

                    elif isinstance(node, ast.ImportFrom):
                        imports_count += len(node.names)
                        for alias in node.names:
                            edges.append({
                                "source": file_node_id,
                                "target": f"{node.module or ''}.{alias.name}",
                                "type": "imports",
                                "line": node.lineno,
                            })

            elif full_p.endswith((".js", ".ts", ".jsx", ".tsx")):
                # JS/TS symbol outline into graph
                lines = code.splitlines()
                for idx, line in enumerate(lines):
                    cm = re.search(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_$]+)", line)
                    if cm:
                        classes_count += 1
                        c_id = f"{rel_p}::{cm.group(1)}"
                        nodes.append({"id": c_id, "name": cm.group(1), "kind": "class", "file": rel_p, "line": idx + 1})
                        edges.append({"source": file_node_id, "target": c_id, "type": "contains", "line": idx + 1})
                        continue
                    fm = re.search(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)", line)
                    if fm:
                        funcs_count += 1
                        f_id = f"{rel_p}::{fm.group(1)}"
                        nodes.append({"id": f_id, "name": fm.group(1), "kind": "function", "file": rel_p, "line": idx + 1})
                        edges.append({"source": file_node_id, "target": f_id, "type": "contains", "line": idx + 1})
                        continue
                    af = re.search(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>", line)
                    if af:
                        funcs_count += 1
                        a_id = f"{rel_p}::{af.group(1)}"
                        nodes.append({"id": a_id, "name": af.group(1), "kind": "arrow_function", "file": rel_p, "line": idx + 1})
                        edges.append({"source": file_node_id, "target": a_id, "type": "contains", "line": idx + 1})
                        continue
                    it = re.search(r"^\s*(?:export\s+)?(?:interface|type)\s+([A-Za-z0-9_$]+)\b", line)
                    if it:
                        classes_count += 1
                        it_id = f"{rel_p}::{it.group(1)}"
                        nodes.append({"id": it_id, "name": it.group(1), "kind": "interface", "file": rel_p, "line": idx + 1})
                        edges.append({"source": file_node_id, "target": it_id, "type": "contains", "line": idx + 1})
                        continue
                    im = re.search(r"""(?:import\s+(?:\{[^}]+\}|[A-Za-z0-9_$]+)\s+from\s+['"]([^'"]+)['"]|require\(['"]([^'"]+)['"]\))""", line)
                    if im:
                        imports_count += 1
                        edges.append({
                            "source": file_node_id,
                            "target": im.group(1) or im.group(2),
                            "type": "imports",
                            "line": idx + 1,
                        })

            elif full_p.endswith((".go", ".rs")):
                lines = code.splitlines()
                is_go = full_p.endswith(".go")
                for idx, line in enumerate(lines):
                    if is_go:
                        gm = re.search(r"^\s*func\s+(?:\([^)]+\)\s*)?([A-Za-z0-9_]+)\b", line)
                        if gm:
                            funcs_count += 1
                            g_id = f"{rel_p}::{gm.group(1)}"
                            nodes.append({"id": g_id, "name": gm.group(1), "kind": "function", "file": rel_p, "line": idx + 1})
                            edges.append({"source": file_node_id, "target": g_id, "type": "contains", "line": idx + 1})
                        tm = re.search(r"^\s*type\s+([A-Za-z0-9_]+)\s+(?:struct|interface)", line)
                        if tm:
                            classes_count += 1
                            t_id = f"{rel_p}::{tm.group(1)}"
                            nodes.append({"id": t_id, "name": tm.group(1), "kind": "type", "file": rel_p, "line": idx + 1})
                            edges.append({"source": file_node_id, "target": t_id, "type": "contains", "line": idx + 1})
                    else:
                        rm = re.search(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)\b", line)
                        if rm:
                            funcs_count += 1
                            r_id = f"{rel_p}::{rm.group(1)}"
                            nodes.append({"id": r_id, "name": rm.group(1), "kind": "function", "file": rel_p, "line": idx + 1})
                            edges.append({"source": file_node_id, "target": r_id, "type": "contains", "line": idx + 1})
                        sm = re.search(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+([A-Za-z0-9_]+)\b", line)
                        if sm:
                            classes_count += 1
                            s_id = f"{rel_p}::{sm.group(1)}"
                            nodes.append({"id": s_id, "name": sm.group(1), "kind": "type", "file": rel_p, "line": idx + 1})
                            edges.append({"source": file_node_id, "target": s_id, "type": "contains", "line": idx + 1})

        graph_result = {
            "nodes": nodes,
            "edges": edges,
            "callers": callers_map,
            "callees": callees_map,
            "summary": {
                "total_files": len(target_files),
                "total_symbols": len(nodes),
                "total_edges": len(edges),
                "classes": classes_count,
                "functions": funcs_count,
                "methods": methods_count,
                "imports": imports_count,
            },
        }

        cls._graph_cache[ws_abs] = (time.time(), current_mtimes, graph_result)
        return graph_result

    # ══════════════════════════════════════════════════════════════════════════
    # 5. DOCUMENT SYMBOLS (textDocument/documentSymbol)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def get_document_symbols(
        cls,
        file_path: str,
        workspace_dir: str = ".",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Return the structured hierarchical symbol outline of a specific file.
        """
        full_p = cls._get_workspace_path(workspace_dir, file_path)
        if not os.path.exists(full_p):
            return {"file": file_path, "error": f"File not found: {file_path}", "symbols": []}

        code = cls._read_file_safe(full_p)
        if code is None:
            return {"file": file_path, "error": "Unable to read file", "symbols": []}

        rel_p = cls._to_rel_path(full_p, os.path.realpath(workspace_dir))

        if full_p.endswith((".py", ".pyw")):
            try:
                tree = cls._get_cached_ast(full_p, code) or ast.parse(code, filename=full_p)
                symbols = []
                for node in getattr(tree, "body", []):
                    if isinstance(node, ast.ClassDef):
                        methods = [
                            {"name": m.name, "line": m.lineno, "kind": "method"}
                            for m in node.body
                            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        ]
                        symbols.append({
                            "name": node.name,
                            "kind": "class",
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "methods": methods,
                        })
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append({
                            "name": node.name,
                            "kind": "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                        })
                return {"file": rel_p, "language": "python", "symbols": symbols}
            except Exception as e:
                return {"file": rel_p, "language": "python", "syntax_error": str(e), "symbols": []}

        # For other languages (JS/TS), use regex symbol detection
        lines = code.splitlines()
        symbols = []
        for idx, line in enumerate(lines):
            cm = re.search(r"^\s*(?:export\s+)?class\s+([A-Za-z0-9_$]+)", line)
            if cm:
                symbols.append({"name": cm.group(1), "kind": "class", "line": idx + 1})
                continue
            fm = re.search(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)", line)
            if fm:
                symbols.append({"name": fm.group(1), "kind": "function", "line": idx + 1})
                continue
            am = re.search(r"^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", line)
            if am:
                symbols.append({"name": am.group(1), "kind": "arrow_function", "line": idx + 1})

        return {"file": rel_p, "language": "generic", "symbols": symbols}


# Backwards compatibility alias: LSPService is HeadlessLSPService
LSPService = HeadlessLSPService
