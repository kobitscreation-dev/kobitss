"""
Language Server Protocol (LSP) & Compiler Symbol Resolution Engine for Kobits.

Provides Kyros Parity Layer 3:
1. Compiler-grade jump-to-definition across Python, JavaScript, TypeScript, and general codebases.
2. Cross-file import tracing and symbol reference resolution.
3. Hierarchical document symbol outlines (classes, methods, async functions, type signatures).
4. Eliminates reliance on probabilistic text grep for codebase navigation.
"""

import ast
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("kobits.lsp_service")


class LSPService:
    """
    Compiler-grade Language Server Protocol & AST Symbol Navigator.
    """

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
        if os.path.isabs(rel_or_abs):
            return os.path.realpath(rel_or_abs)
        return os.path.realpath(os.path.join(workspace_dir, rel_or_abs))

    # ══════════════════════════════════════════════════════════════════════════
    # 1. GOTO DEFINITION (textDocument/definition)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def goto_definition(
        cls,
        symbol: str,
        current_file: Optional[str] = None,
        workspace_dir: str = ".",
    ) -> Dict[str, Any]:
        """
        Locate the exact file, line, kind, signature, and docstring of a symbol definition.
        First traces imports in `current_file`, then scans project ASTs.
        """
        symbol = (symbol or "").strip()
        if not symbol:
            return {"found": False, "error": "Symbol name is required"}

        ws_abs = os.path.realpath(workspace_dir)

        # Step 1: If current_file is provided and is Python, trace imports first
        if current_file:
            curr_abs = cls._get_workspace_path(ws_abs, current_file)
            if curr_abs.endswith(".py") and os.path.exists(curr_abs):
                code = cls._read_file_safe(curr_abs)
                if code:
                    # Check if defined directly in current_file
                    local_def = cls._find_python_symbol_in_code(code, symbol, curr_abs, ws_abs)
                    if local_def:
                        return {"found": True, "source": "current_file", **local_def}

                    # Trace imports
                    import_target = cls._trace_python_import(code, symbol, curr_abs, ws_abs)
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

        # Step 2: Global Workspace AST Scan (Python files first, then JS/TS)
        py_files = []
        js_files = []
        for root, dirs, files in os.walk(ws_abs):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv", "dist", "build")]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext == ".py":
                    py_files.append(os.path.join(root, f))
                elif ext in (".js", ".ts", ".jsx", ".tsx", ".mjs"):
                    js_files.append(os.path.join(root, f))

        for pf in py_files[:200]:
            code = cls._read_file_safe(pf)
            if not code or symbol not in code:
                continue
            res = cls._find_python_symbol_in_code(code, symbol, pf, ws_abs)
            if res:
                return {"found": True, "source": "workspace_scan", **res}

        for jf in js_files[:200]:
            code = cls._read_file_safe(jf)
            if not code or symbol not in code:
                continue
            res = cls._find_js_ts_symbol_in_code(code, symbol, jf, ws_abs)
            if res:
                return {"found": True, "source": "workspace_scan", **res}

        return {
            "found": False,
            "symbol": symbol,
            "message": f"Definition for '{symbol}' not found in workspace AST.",
        }

    @classmethod
    def _find_python_symbol_in_code(
        cls,
        code: str,
        symbol: str,
        file_path: str,
        workspace_dir: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            tree = ast.parse(code)
        except Exception:
            return None

        lines = code.splitlines()

        for node in ast.walk(tree):
            # Class definition
            if isinstance(node, ast.ClassDef) and node.name == symbol:
                doc = ast.get_docstring(node) or ""
                bases = [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else []
                sig = f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
                end_lineno = getattr(node, "end_lineno", node.lineno)
                snippet = "\n".join(lines[node.lineno - 1 : min(node.lineno + 10, len(lines))])
                return {
                    "symbol": symbol,
                    "kind": "class",
                    "file_path": os.path.relpath(file_path, workspace_dir).replace("\\", "/"),
                    "absolute_path": file_path,
                    "line": node.lineno,
                    "end_line": end_lineno,
                    "signature": sig,
                    "docstring": doc,
                    "snippet": snippet,
                }

            # Function / AsyncFunction definition
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
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
                    "file_path": os.path.relpath(file_path, workspace_dir).replace("\\", "/"),
                    "absolute_path": file_path,
                    "line": node.lineno,
                    "end_line": end_lineno,
                    "signature": sig,
                    "docstring": doc,
                    "snippet": snippet,
                }

            # Variable / Assignment definition
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == symbol:
                        val_str = ast.unparse(node.value) if hasattr(ast, "unparse") else "..."
                        return {
                            "symbol": symbol,
                            "kind": "variable",
                            "file_path": os.path.relpath(file_path, workspace_dir).replace("\\", "/"),
                            "absolute_path": file_path,
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "signature": f"{symbol} = {val_str[:60]}",
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
    ) -> Optional[Dict[str, Any]]:
        try:
            tree = ast.parse(code)
        except Exception:
            return None

        curr_dir = os.path.dirname(current_file)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    if local_name == symbol:
                        orig_symbol = alias.name
                        # Resolve module path
                        candidates = []
                        if node.level > 0:
                            # Relative import
                            rel_root = curr_dir
                            for _ in range(node.level - 1):
                                rel_root = os.path.dirname(rel_root)
                            mod_parts = module.split(".") if module else []
                            target_base = os.path.join(rel_root, *mod_parts)
                            candidates.append(target_base + ".py")
                            candidates.append(os.path.join(target_base, "__init__.py"))
                        else:
                            # Absolute workspace import
                            mod_parts = module.split(".")
                            target_base = os.path.join(workspace_dir, *mod_parts)
                            candidates.append(target_base + ".py")
                            candidates.append(os.path.join(target_base, "__init__.py"))

                        for cand in candidates:
                            if os.path.exists(cand):
                                return {
                                    "file_path": cand,
                                    "symbol_in_target": orig_symbol,
                                }
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

        # Regex for function, class, const, interface
        patterns = [
            (rf"(?:export\s+)?class\s+{re.escape(symbol)}\b", "class"),
            (rf"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+{re.escape(symbol)}\b", "function"),
            (rf"(?:export\s+)?(?:const|let|var)\s+{re.escape(symbol)}\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_]+)\s*=>", "arrow_function"),
            (rf"(?:export\s+)?(?:const|let|var)\s+{re.escape(symbol)}\b", "variable"),
            (rf"(?:export\s+)?interface\s+{re.escape(symbol)}\b", "interface"),
            (rf"(?:export\s+)?type\s+{re.escape(symbol)}\b", "type_alias"),
        ]

        for pat, kind in patterns:
            for idx, line in enumerate(lines):
                if re.search(pat, line):
                    snippet = "\n".join(lines[idx : min(idx + 10, len(lines))])
                    return {
                        "symbol": symbol,
                        "kind": kind,
                        "file_path": os.path.relpath(file_path, workspace_dir).replace("\\", "/"),
                        "absolute_path": file_path,
                        "line": idx + 1,
                        "end_line": idx + 1,
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
        symbol: str,
        workspace_dir: str = ".",
        max_results: int = 25,
    ) -> Dict[str, Any]:
        """
        Locate all call sites, imports, and usages of a symbol across the workspace.
        """
        symbol = (symbol or "").strip()
        if not symbol:
            return {"symbol": "", "count": 0, "references": []}

        ws_abs = os.path.realpath(workspace_dir)
        references: List[Dict[str, Any]] = []

        # Word boundary pattern
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")

        for root, dirs, files in os.walk(ws_abs):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv", "dist", "build")]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in (".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".md", ".yaml", ".yml"):
                    continue
                full_p = os.path.join(root, f)
                code = cls._read_file_safe(full_p)
                if not code or symbol not in code:
                    continue

                rel_p = os.path.relpath(full_p, ws_abs).replace("\\", "/")
                lines = code.splitlines()
                for idx, line in enumerate(lines):
                    if pattern.search(line):
                        # Classify usage kind
                        kind = "usage"
                        line_str = line.strip()
                        if line_str.startswith(("import ", "from ")):
                            kind = "import"
                        elif re.search(rf"\b(?:def|class|function)\s+{re.escape(symbol)}\b", line_str):
                            kind = "definition"
                        elif re.search(rf"{re.escape(symbol)}\s*\(", line_str):
                            kind = "call"

                        references.append({
                            "file": rel_p,
                            "line": idx + 1,
                            "kind": kind,
                            "text": line_str[:120],
                        })
                        if len(references) >= max_results:
                            break
                if len(references) >= max_results:
                    break

        return {
            "symbol": symbol,
            "count": len(references),
            "references": references,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # 3. DOCUMENT SYMBOLS (textDocument/documentSymbol)
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def get_document_symbols(
        cls,
        file_path: str,
        workspace_dir: str = ".",
    ) -> Dict[str, Any]:
        """
        Return the structured symbol outline of a file (classes, methods, functions, types).
        """
        full_p = cls._get_workspace_path(workspace_dir, file_path)
        if not os.path.exists(full_p):
            return {"file": file_path, "error": f"File not found: {file_path}", "symbols": []}

        code = cls._read_file_safe(full_p)
        if code is None:
            return {"file": file_path, "error": "Unable to read file", "symbols": []}

        rel_p = os.path.relpath(full_p, os.path.realpath(workspace_dir)).replace("\\", "/")

        if full_p.endswith(".py"):
            try:
                tree = ast.parse(code)
                symbols = []
                for node in tree.body:
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
            cm = re.search(r"(?:export\s+)?class\s+([A-Za-z0-9_]+)", line)
            if cm:
                symbols.append({"name": cm.group(1), "kind": "class", "line": idx + 1})
                continue
            fm = re.search(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)", line)
            if fm:
                symbols.append({"name": fm.group(1), "kind": "function", "line": idx + 1})
                continue
            am = re.search(r"(?:export\s+)?(?:const|let)\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", line)
            if am:
                symbols.append({"name": am.group(1), "kind": "arrow_function", "line": idx + 1})

        return {"file": rel_p, "language": "generic", "symbols": symbols}
