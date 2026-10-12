import ast
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("kobits.context_compactor")


@dataclass
class CompactionReport:
    """Telemetry report produced when compaction runs."""
    original_tokens: int = 0
    compacted_tokens: int = 0
    tokens_saved: int = 0
    compression_ratio: float = 0.0
    observations_pruned: int = 0
    checkpoints_created: int = 0
    turns_before: int = 0
    turns_after: int = 0
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_tokens": self.original_tokens,
            "compacted_tokens": self.compacted_tokens,
            "tokens_saved": self.tokens_saved,
            "compression_ratio": round(self.compression_ratio, 4),
            "observations_pruned": self.observations_pruned,
            "checkpoints_created": self.checkpoints_created,
            "turns_before": self.turns_before,
            "turns_after": self.turns_after,
            "duration_ms": round(self.duration_ms, 2),
        }


class ContextCompactor:
    """
    Kyros-Grade Dynamic Context Window Compactor & Memory Pruning Engine.

    Designed for long-horizon autonomous ReAct loops (Turns 15-30+) where context
    tokens balloon to 60k-120k+, causing model degradation, cache thrashing, and high latency.

    Core capabilities:
    1. Hierarchical Observation Pruning:
       - File reads (`repository_read`, `read_file`): Compacted to AST/Symbol outlines
         preserving path, line count, classes, functions, and imports while pruning body.
       - Test & terminal logs (`terminal_execute`, `run_command`):
         * Succeeded: Collapsed to exit code + high-level summary.
         * Failed: Surgically isolates failing assertion, traceback, line numbers; drops noise.
       - Code searches: Collapsed to matching file paths and line numbers; drops raw dumps.
       - File writes: Collapsed to path and lines written status.
    2. Golden Recent Window:
       - Keeps the last N turns (default 3) in 100% full raw fidelity so immediate actions
         operate on exact feedback.
    3. Structural Rolling Episodic Trajectory Checkpoints:
       - Compresses older historical turns into a high-density structured markdown checkpoint
         retaining mission invariants, files modified, hypotheses tested, and active errors.
    4. Cache Prefix Invariance:
       - Turn 0 (System prompt & initial user spec) is NEVER modified, preserving Anthropic
         and DeepSeek prompt cache hits.
    5. Schema & ID Pairing Integrity:
       - Preserves tool call IDs (`tool_call_id`, `tool_use_id`) to ensure zero 400 Bad Requests
         across OpenAI, Anthropic, Bedrock, and Gemini schemas.
    """

    # Calibrated character-to-token heuristic ratios
    CHARS_PER_TOKEN_CODE_JSON = 3.6
    CHARS_PER_TOKEN_PROSE = 4.0
    MESSAGE_ENVELOPE_OVERHEAD_TOKENS = 4

    def __init__(
        self,
        max_token_budget: int = 32000,
        recent_turns: int = 3,
        observation_token_threshold: int = 300,
        enable_checkpoints: bool = True,
    ):
        self.max_token_budget = max_token_budget
        self.recent_turns = recent_turns
        self.observation_token_threshold = observation_token_threshold
        self.enable_checkpoints = enable_checkpoints

    # ─────────────────────────────────────────────────────────────────────────
    # 1. TOKEN ESTIMATION
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def estimate_tokens(cls, content: Any) -> int:
        """
        Fast, accurate token estimation heuristic calibrated for code, JSON, and prose.
        Handles str, dict, list, and primitives.
        """
        if content is None:
            return 0

        if isinstance(content, str):
            if not content:
                return 0
            # Detect whether content is predominantly code/JSON
            is_code_or_json = (
                "{" in content or "}" in content or
                "def " in content or "class " in content or
                "import " in content or "function " in content or
                "\n    " in content or "\t" in content or
                content.startswith("[") or content.startswith("{")
            )
            ratio = cls.CHARS_PER_TOKEN_CODE_JSON if is_code_or_json else cls.CHARS_PER_TOKEN_PROSE
            return max(1, math.ceil(len(content) / ratio))

        if isinstance(content, dict):
            total = cls.MESSAGE_ENVELOPE_OVERHEAD_TOKENS
            for k, v in content.items():
                total += cls.estimate_tokens(k) + cls.estimate_tokens(v)
            return total

        if isinstance(content, (list, tuple)):
            total = 2
            for item in content:
                total += cls.estimate_tokens(item)
            return total

        # Fallback for int, float, bool
        return cls.estimate_tokens(str(content))

    @classmethod
    def estimate_messages_tokens(cls, messages: List[Dict[str, Any]]) -> int:
        """Estimate cumulative tokens for a sequence of conversation messages."""
        if not messages:
            return 0
        total = 0
        for msg in messages:
            total += cls.estimate_tokens(msg)
        return total

    # ─────────────────────────────────────────────────────────────────────────
    # 2. SYMBOL & AST EXTRACTION (Python, JS/TS, Config)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def extract_python_symbols(code_str: str) -> Dict[str, Any]:
        """
        Extract classes, methods, functions, and imports from Python code using ast.
        Falls back to regex if code is a partial slice or contains syntax errors.
        """
        classes = []
        functions = []
        imports = []

        try:
            tree = ast.parse(code_str)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    methods = [
                        m.name for m in node.body
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ]
                    classes.append({"name": node.name, "methods": methods[:8]})
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [a.arg for a in node.args.args]
                    functions.append(f"{node.name}({', '.join(args[:4])})")
                elif isinstance(node, ast.Import):
                    for n in node.names:
                        imports.append(n.name)
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    names = [n.name for n in node.names[:4]]
                    imports.append(f"{mod}: {', '.join(names)}")
        except Exception:
            # Regex fallback for incomplete slices or syntax variants
            for m in re.finditer(r"class\s+([A-Za-z0-9_]+)", code_str):
                classes.append({"name": m.group(1), "methods": []})
            for m in re.finditer(r"(?:def|async\s+def)\s+([A-Za-z0-9_]+)\s*\((.*?)\)", code_str):
                functions.append(f"{m.group(1)}({m.group(2)[:30]})")
            for m in re.finditer(r"(?:from\s+([A-Za-z0-9_.]+)\s+import\s+([A-Za-z0-9_, ]+)|import\s+([A-Za-z0-9_.]+))", code_str):
                if m.group(1):
                    imports.append(f"{m.group(1)}: {m.group(2)}")
                elif m.group(3):
                    imports.append(m.group(3))

        return {
            "classes": classes,
            "functions": functions[:15],
            "imports": list(dict.fromkeys(imports))[:10]
        }

    @staticmethod
    def extract_generic_symbols(code_str: str, file_path: str = "") -> Dict[str, Any]:
        """Extract symbol outline for JS/TS, Go, Rust, JSON, or generic files."""
        lower_path = file_path.lower()
        if lower_path.endswith(".json"):
            try:
                data = json.loads(code_str)
                if isinstance(data, dict):
                    keys_info = [f"{k} ({type(v).__name__})" for k, v in list(data.items())[:12]]
                    return {"json_keys": keys_info, "item_count": len(data)}
                elif isinstance(data, list):
                    return {"json_array_length": len(data), "sample_item": str(data[0])[:80] if data else ""}
            except Exception:
                pass

        # JS / TS regex extraction
        classes = re.findall(r"(?:export\s+)?class\s+([A-Za-z0-9_]+)", code_str)
        fn_matches = re.finditer(
            r"(?:(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)|"
            r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_]+)\s*=>)",
            code_str
        )
        fn_names = []
        for m in fn_matches:
            name = m.group(1) or m.group(2)
            if name:
                fn_names.append(name)

        exports = re.findall(r"export\s+(?:default\s+)?(?:const|function|class|type|interface)\s+([A-Za-z0-9_]+)", code_str)

        return {
            "classes": [{"name": c, "methods": []} for c in classes[:8]],
            "functions": fn_names[:12],
            "exports": exports[:10]
        }

    @classmethod
    def format_symbol_outline(cls, file_path: str, code_str: str, total_lines: Optional[int] = None) -> str:
        """Constructs a concise markdown symbol outline replacing verbose file content."""
        lines = code_str.splitlines() if code_str else []
        line_count = total_lines or len(lines)
        char_count = len(code_str)
        lower_path = (file_path or "").lower()
        is_js_ts = lower_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"))
        is_json = lower_path.endswith(".json")
        is_python = lower_path.endswith(".py") or ("def " in code_str and not is_js_ts and not is_json)

        if is_python:
            syms = cls.extract_python_symbols(code_str)
        else:
            syms = cls.extract_generic_symbols(code_str, file_path)

        parts = [
            f"[Observation Pruned: '{file_path or 'file'}' ({line_count} lines, ~{char_count} chars). AST Outline:]"
        ]

        if syms.get("classes"):
            class_descs = []
            for c in syms["classes"]:
                m_str = f"({', '.join(c['methods'][:5])})" if c.get("methods") else ""
                class_descs.append(f"class {c['name']}{m_str}")
            parts.append(f"- Classes: {', '.join(class_descs)}")

        if syms.get("functions"):
            parts.append(f"- Functions: {', '.join(syms['functions'][:8])}")

        if syms.get("exports"):
            parts.append(f"- Exports: {', '.join(syms['exports'])}")

        if syms.get("json_keys"):
            parts.append(f"- JSON Keys: {', '.join(syms['json_keys'])}")

        if syms.get("imports"):
            parts.append(f"- Imports: {', '.join(syms['imports'][:6])}")

        return "\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # 3. TERMINAL & TEST LOG PRUNING
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def prune_terminal_output(cls, output: str, command: str = "") -> str:
        """
        Isolates actionable diagnostic signals from terminal/test output:
        - For passing runs: Collapses to exit code and test counts.
        - For failing runs: Surgically extracts failing assertions, test names, and tracebacks,
          while shedding environment setup, compilation headers, and warnings.
        """
        if not output:
            return output

        total_lines = output.splitlines()
        if len(total_lines) <= 5 and len(output) < 300:
            return output

        # Check for success vs failure indicators
        has_failure = any(term in output for term in [
            "FAILED ", "AssertionError", "Traceback (most recent call last):",
            "Error:", "Exception:", "FAIL:", "exit code 1", "exit code 2",
            "ERR!", "panic:"
        ])

        if not has_failure:
            first_lines = "\n".join(total_lines[:2]).strip()
            last_lines = "\n".join(total_lines[-2:]).strip()
            return (
                f"[Terminal Output Pruned: Command '{command}' succeeded (exit code 0).\n"
                f"Summary: {first_lines} ... {last_lines} ({len(total_lines)} lines collapsed)]"
            )

        # Failure extraction: surgically pull failing tests and tracebacks
        extracted_diagnostics = []

        # 1. Capture lines with FAILED or FAIL
        for line in total_lines:
            if re.search(r"(?:FAILED|FAIL:)\s+.*", line):
                extracted_diagnostics.append(line.strip())

        # 2. Capture traceback sections
        in_tb = False
        tb_lines = []
        for line in total_lines:
            if "Traceback (most recent call last):" in line:
                in_tb = True
                tb_lines.append(line)
                continue
            if in_tb:
                tb_lines.append(line)
                if line and not line.startswith(" ") and not line.startswith("\t"):
                    if len(tb_lines) > 2:
                        in_tb = False
                if len(tb_lines) > 30:
                    in_tb = False

        if tb_lines:
            extracted_diagnostics.append("\n--- Root Cause Traceback ---\n" + "\n".join(tb_lines[-25:]))

        # 3. Capture assertion failures
        assertion_lines = [l.strip() for l in total_lines if "AssertionError" in l or "Error:" in l][:5]
        if assertion_lines and not tb_lines:
            extracted_diagnostics.extend(assertion_lines)

        # 4. Include the final summary line
        summary_line = total_lines[-1] if total_lines else ""
        if summary_line:
            extracted_diagnostics.append(f"Test Run Summary: {summary_line.strip()}")

        diag_content = "\n".join(extracted_diagnostics) if extracted_diagnostics else "\n".join(total_lines[-15:])
        return (
            f"[Terminal Output Diagnostic Focus: Command '{command}' failed.\n"
            f"Actionable Diagnostics Extracted:\n{diag_content}\n"
            f"({len(total_lines) - len(diag_content.splitlines())} lines of non-diagnostic logs pruned)]"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 4. SEARCH & GREP PRUNING
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def prune_search_output(cls, output: str, query: str = "") -> str:
        """Condenses search results into unique matching files and line references."""
        if not output or len(output) < 400:
            return output

        lines = output.splitlines()
        file_matches: Dict[str, List[str]] = {}

        for line in lines:
            m = re.match(r"^([^:\s]+):(\d+):", line)
            if m:
                path, lineno = m.group(1), m.group(2)
                file_matches.setdefault(path, []).append(lineno)
            else:
                m2 = re.match(r"^([^\s]+\.(?:py|js|ts|json|md|html|css))\b", line)
                if m2:
                    file_matches.setdefault(m2.group(1), []).append("match")

        if not file_matches:
            return f"[Search results for '{query}' ({len(lines)} lines): {lines[0] if lines else ''} ... ({len(lines)-2} lines pruned)]"

        summary_parts = [
            f"[Search Pruned: Query '{query}' matched {len(file_matches)} files ({len(lines)} total hits):"
        ]
        for fpath, lnums in list(file_matches.items())[:12]:
            lines_str = f"lines {', '.join(lnums[:5])}" if lnums and lnums[0] != "match" else f"{len(lnums)} hits"
            summary_parts.append(f"- {fpath} ({lines_str})")

        if len(file_matches) > 12:
            summary_parts.append(f"... and {len(file_matches) - 12} additional files.")
        summary_parts.append("Verbose match snippets pruned. Use repository_read on target files.]")

        return "\n".join(summary_parts)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. DISPATCHER: COMPACT SINGLE TOOL OUTPUT
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def compact_tool_output(cls, tool_name: str, tool_args: Dict[str, Any], raw_output: Any) -> Any:
        """
        Compacts an observation based on tool semantics:
        - File reads -> AST symbol outlines
        - Terminal executions -> diagnostic traceback slices
        - Searches -> matched file manifests
        - File writes -> success confirmations
        """
        # Idempotency: avoid double-compacting already pruned outputs
        if isinstance(raw_output, dict) and raw_output.get("_pruned") is True:
            return raw_output
        if isinstance(raw_output, str) and (
            raw_output.startswith("[Observation Pruned:")
            or raw_output.startswith("[Terminal Output")
            or raw_output.startswith("[Search Pruned:")
        ):
            return raw_output

        norm_name = (tool_name or "").replace(".", "_").lower()

        # Case A: Dictionary outputs
        if isinstance(raw_output, dict):
            # 1. Repository read
            if any(k in norm_name for k in ["repository_read", "read_file", "view_file"]):
                path = raw_output.get("path") or tool_args.get("path") or tool_args.get("file") or ""
                content = raw_output.get("content") or raw_output.get("raw") or ""
                total_lines = raw_output.get("total_lines")
                if content and len(str(content)) > 250:
                    outline = cls.format_symbol_outline(path, str(content), total_lines)
                    pruned_dict = dict(raw_output)
                    pruned_dict["content"] = outline
                    if "raw" in pruned_dict:
                        pruned_dict["raw"] = "[Pruned]"
                    pruned_dict["_pruned"] = True
                    return pruned_dict
                return raw_output

            # 2. Terminal execution
            if any(k in norm_name for k in ["run_command", "terminal_execute", "test_runner", "pytest"]):
                cmd = tool_args.get("command") or tool_args.get("cmd") or ""
                stdout = raw_output.get("stdout", "")
                stderr = raw_output.get("stderr", "")
                combined = f"{stdout}\n{stderr}".strip() if (stdout or stderr) else raw_output.get("output", "")
                if combined and len(str(combined)) > 300:
                    pruned_combined = cls.prune_terminal_output(str(combined), cmd)
                    pruned_dict = dict(raw_output)
                    if "stdout" in pruned_dict:
                        pruned_dict["stdout"] = pruned_combined
                        pruned_dict["stderr"] = "[Extracted into stdout diagnostics]"
                    else:
                        pruned_dict["output"] = pruned_combined
                    pruned_dict["_pruned"] = True
                    return pruned_dict
                return raw_output

            # 3. Search
            if any(k in norm_name for k in ["repository_search", "search_code", "find_in_files"]):
                query = tool_args.get("query") or tool_args.get("pattern") or ""
                res_content = raw_output.get("results") or raw_output.get("content") or str(raw_output)
                if len(str(res_content)) > 350:
                    pruned_dict = dict(raw_output)
                    pruned_dict["results"] = cls.prune_search_output(str(res_content), query)
                    pruned_dict["_pruned"] = True
                    return pruned_dict
                return raw_output

            # 4. File write / edit
            if any(k in norm_name for k in ["repository_write", "write_file", "repository_edit", "edit_file_block"]):
                path = raw_output.get("path") or tool_args.get("path") or ""
                status = raw_output.get("status", "SUCCESS")
                return {
                    "status": status,
                    "path": path,
                    "summary": f"File '{path}' written/edited successfully. Content payload omitted.",
                    "_pruned": True
                }

            out_str = json.dumps(raw_output)
            if len(out_str) > 1500:
                return {
                    "_pruned": True,
                    "summary": f"Large tool result ({len(out_str)} chars) pruned.",
                    "keys": list(raw_output.keys())[:15]
                }
            return raw_output

        # Case B: String outputs
        if isinstance(raw_output, str):
            if any(k in norm_name for k in ["repository_read", "read_file", "view_file"]):
                path = tool_args.get("path") or tool_args.get("file") or ""
                if len(raw_output) > 250:
                    return cls.format_symbol_outline(path, raw_output)
            elif any(k in norm_name for k in ["run_command", "terminal_execute", "test_runner", "pytest"]):
                cmd = tool_args.get("command") or tool_args.get("cmd") or ""
                if len(raw_output) > 300:
                    return cls.prune_terminal_output(raw_output, cmd)
            elif any(k in norm_name for k in ["repository_search", "search_code", "find_in_files"]):
                query = tool_args.get("query") or tool_args.get("pattern") or ""
                if len(raw_output) > 350:
                    return cls.prune_search_output(raw_output, query)
            elif len(raw_output) > 1500:
                return f"[Output ({len(raw_output)} chars) truncated: {raw_output[:400]}\n...\n{raw_output[-300:]}]"

        return raw_output

    # ─────────────────────────────────────────────────────────────────────────
    # 6. ROLLING EPISODIC TRAJECTORY CHECKPOINT SYNTHESIS
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def synthesize_trajectory_checkpoint(
        cls,
        turns_summary_data: List[Dict[str, Any]],
        mission_objective: str = "",
        start_turn: int = 1,
        end_turn: int = 10,
    ) -> str:
        """
        Synthesizes historical turns into a high-density, structured markdown checkpoint.
        Preserves causal continuity, files touched, hypotheses tested, and active issues.
        """
        files_inspected = set()
        files_modified = set()
        actions_taken = []
        failing_signals = []

        for item in turns_summary_data:
            tool = item.get("tool", "")
            args = item.get("args", {})
            status = item.get("status", "")

            # Track files inspected
            if any(k in tool for k in ["read", "search", "list"]):
                p = args.get("path") or args.get("file")
                if p:
                    files_inspected.add(p)

            # Track files modified
            if any(k in tool for k in ["write", "edit"]):
                p = args.get("path") or args.get("file")
                if p:
                    files_modified.add(p)

            # Track action summary
            if tool:
                actions_taken.append(f"- {tool}({json.dumps(args)[:60]}) -> {status or 'DONE'}")

            # Track failing signals
            if item.get("error"):
                failing_signals.append(f"Turn error: {item['error']}")
            elif "fail" in str(item.get("result", "")).lower() or "assertionerror" in str(item.get("result", "")).lower():
                failing_signals.append(f"Observation failure in {tool}: {str(item.get('result', ''))[:120]}")

        lines = [
            f"## [MISSION TRAJECTORY CHECKPOINT: Turns {start_turn} to {end_turn}]",
            "### 1. Invariant Objective & Scope",
            f"{mission_objective or 'Autonomous engineering objective in progress.'}",
            "",
            "### 2. Workspace State & Touched Files",
            f"- Inspected Files ({len(files_inspected)}): {', '.join(sorted(files_inspected)) if files_inspected else 'None'}",
            f"- Modified Files ({len(files_modified)}): {', '.join(sorted(files_modified)) if files_modified else 'None'}",
            "",
            "### 3. Key Hypotheses Tested & Progression",
        ]

        if actions_taken:
            lines.extend(actions_taken[:12])
            if len(actions_taken) > 12:
                lines.append(f"- ... and {len(actions_taken) - 12} earlier intermediate operations completed.")
        else:
            lines.append("- Intermediate tool operations executed.")

        lines.extend([
            "",
            "### 4. Active Blockers & Diagnostics",
            "\n".join(failing_signals[-3:]) if failing_signals else "- No active blocking exceptions. Implementation proceeding.",
            "",
            "### 5. Execution Continuity",
            "Older history compressed into this checkpoint. Recent verbatim turns follow below."
        ])

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. MULTI-FORMAT HISTORY COMPACTION (OpenAI, Anthropic, Bedrock, Gemini)
    # ─────────────────────────────────────────────────────────────────────────

    def compact_openai_messages(
        self,
        messages: List[Dict[str, Any]],
        max_token_budget: Optional[int] = None,
        recent_turns: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], CompactionReport]:
        """
        Compacts OpenAI / DeepSeek format messages:
        `[{"role": "system"/"user"/"assistant"/"tool", "content": ..., "tool_calls": ..., "tool_call_id": ...}]`

        Maintains strict tool_call_id matching to prevent 400 Bad Request.
        Turn 0 (System & initial user prompt) is never modified for prompt caching.
        """
        start_t = time.time()
        budget = max_token_budget or self.max_token_budget
        keep_recent = self.recent_turns if recent_turns is None else recent_turns

        report = CompactionReport(
            original_tokens=self.estimate_messages_tokens(messages),
            turns_before=len(messages),
        )

        # Fast path: already within budget
        if report.original_tokens <= budget:
            report.compacted_tokens = report.original_tokens
            report.turns_after = len(messages)
            report.duration_ms = (time.time() - start_t) * 1000
            return messages, report

        # Step 1: Identify Tool Calls in History
        prefix_count = 0
        while prefix_count < len(messages) and messages[prefix_count].get("role") in ("system", "user") and prefix_count < 2:
            prefix_count += 1

        prefix_messages = messages[:prefix_count]
        active_history = messages[prefix_count:]

        # Identify recent boundary: count assistant messages from the end
        assistant_indices = [i for i, m in enumerate(active_history) if m.get("role") == "assistant"]
        if keep_recent == 0:
            cutoff_index = len(active_history)
        elif len(assistant_indices) <= keep_recent:
            cutoff_index = 0
        else:
            cutoff_index = assistant_indices[-keep_recent]

        older_part = active_history[:cutoff_index]
        recent_part = active_history[cutoff_index:]

        # Map tool_call_id to tool metadata for pruning
        tool_call_meta: Dict[str, Dict[str, Any]] = {}
        for m in older_part:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    tc_id = tc.get("id")
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")
                    try:
                        args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        args_dict = {}
                    if tc_id:
                        tool_call_meta[tc_id] = {"name": fn_name, "args": args_dict}

        # Step 2: Level 1 Compaction - Prune observation content in older_part
        compacted_older = []
        pruned_count = 0
        turns_summary = []

        for m in older_part:
            if m.get("role") == "tool":
                tc_id = m.get("tool_call_id")
                meta = tool_call_meta.get(tc_id, {})
                t_name = meta.get("name") or m.get("name") or ""
                t_args = meta.get("args") or {}

                raw_content = m.get("content", "")
                try:
                    parsed_content = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
                except Exception:
                    parsed_content = raw_content

                compacted_res = self.compact_tool_output(t_name, t_args, parsed_content)
                new_content = json.dumps(compacted_res) if not isinstance(compacted_res, str) else compacted_res

                if new_content != raw_content:
                    pruned_count += 1

                new_msg = dict(m)
                new_msg["content"] = new_content
                compacted_older.append(new_msg)

                turns_summary.append({
                    "tool": t_name,
                    "args": t_args,
                    "result": str(compacted_res)[:150]
                })
            else:
                compacted_older.append(m)

        tentative_messages = prefix_messages + compacted_older + recent_part
        est_tokens = self.estimate_messages_tokens(tentative_messages)

        # Step 3: Level 2 Compaction - Trajectory Checkpoint if still over budget
        if est_tokens > budget and self.enable_checkpoints and len(compacted_older) > 4:
            mission_spec = prefix_messages[-1].get("content", "") if prefix_messages else ""
            checkpoint_text = self.synthesize_trajectory_checkpoint(
                turns_summary,
                mission_objective=mission_spec[:600],
                start_turn=1,
                end_turn=len(assistant_indices) - keep_recent
            )

            checkpoint_messages = [
                {
                    "role": "user",
                    "content": f"[Trajectory Checkpoint Request]: Please summarize current engineering progress."
                },
                {
                    "role": "assistant",
                    "content": checkpoint_text
                }
            ]

            final_messages = prefix_messages + checkpoint_messages + recent_part
            report.checkpoints_created += 1
        else:
            final_messages = tentative_messages

        report.observations_pruned = pruned_count
        report.compacted_tokens = self.estimate_messages_tokens(final_messages)
        report.tokens_saved = max(0, report.original_tokens - report.compacted_tokens)
        report.compression_ratio = report.tokens_saved / report.original_tokens if report.original_tokens > 0 else 0.0
        report.turns_after = len(final_messages)
        report.duration_ms = (time.time() - start_t) * 1000

        return final_messages, report

    def compact_anthropic_messages(
        self,
        messages: List[Dict[str, Any]],
        max_token_budget: Optional[int] = None,
        recent_turns: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], CompactionReport]:
        """
        Compacts Anthropic / Bedrock Messages API format messages:
        `[{"role": "user"/"assistant", "content": [{"type": "text"|"tool_use"|"tool_result", ...}]}]`

        Maintains tool_use_id matching in tool_result blocks.
        Turn 0 (User specification) is never modified for prompt caching.
        """
        start_t = time.time()
        budget = max_token_budget or self.max_token_budget
        keep_recent = self.recent_turns if recent_turns is None else recent_turns

        report = CompactionReport(
            original_tokens=self.estimate_messages_tokens(messages),
            turns_before=len(messages),
        )

        if report.original_tokens <= budget:
            report.compacted_tokens = report.original_tokens
            report.turns_after = len(messages)
            report.duration_ms = (time.time() - start_t) * 1000
            return messages, report

        # Preserve Turn 0
        prefix_messages = [messages[0]] if messages else []
        active_history = messages[1:] if len(messages) > 1 else []

        # Find assistant turns
        assistant_indices = [i for i, m in enumerate(active_history) if m.get("role") == "assistant"]
        if keep_recent == 0:
            cutoff = len(active_history)
        elif len(assistant_indices) <= keep_recent:
            cutoff = 0
        else:
            cutoff = assistant_indices[-keep_recent]

        older_part = active_history[:cutoff]
        recent_part = active_history[cutoff:]

        # Map tool_use ids to their name and input
        tool_use_meta: Dict[str, Dict[str, Any]] = {}
        for m in older_part:
            if m.get("role") == "assistant":
                content = m.get("content", [])
                if isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "tool_use":
                            tu_id = blk.get("id")
                            if tu_id:
                                tool_use_meta[tu_id] = {
                                    "name": blk.get("name", ""),
                                    "input": blk.get("input", {})
                                }

        # Level 1 Compaction: Prune tool_result blocks in older_part
        compacted_older = []
        pruned_count = 0
        turns_summary = []

        for m in older_part:
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, list):
                    new_content = []
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "tool_result":
                            tu_id = blk.get("tool_use_id")
                            meta = tool_use_meta.get(tu_id, {})
                            t_name = meta.get("name", "")
                            t_args = meta.get("input", {})

                            raw_str = blk.get("content", "")
                            try:
                                parsed = json.loads(raw_str) if isinstance(raw_str, str) else raw_str
                            except Exception:
                                parsed = raw_str

                            compacted_res = self.compact_tool_output(t_name, t_args, parsed)
                            res_str = json.dumps(compacted_res) if not isinstance(compacted_res, str) else compacted_res

                            if res_str != raw_str:
                                pruned_count += 1

                            new_blk = dict(blk)
                            new_blk["content"] = res_str
                            new_content.append(new_blk)

                            turns_summary.append({
                                "tool": t_name,
                                "args": t_args,
                                "result": str(compacted_res)[:150]
                            })
                        else:
                            new_content.append(blk)
                    new_m = dict(m)
                    new_m["content"] = new_content
                    compacted_older.append(new_m)
                else:
                    compacted_older.append(m)
            else:
                compacted_older.append(m)

        tentative_messages = prefix_messages + compacted_older + recent_part
        est_tokens = self.estimate_messages_tokens(tentative_messages)

        # Level 2 Compaction: Trajectory Checkpoint if still over budget
        if est_tokens > budget and self.enable_checkpoints and len(compacted_older) > 2:
            mission_spec = str(prefix_messages[0].get("content", "")) if prefix_messages else ""
            checkpoint_text = self.synthesize_trajectory_checkpoint(
                turns_summary,
                mission_objective=mission_spec[:600],
                start_turn=1,
                end_turn=len(assistant_indices) - keep_recent
            )

            checkpoint_messages = [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Resume engineering execution with current status checkpoint."}]
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": checkpoint_text}]
                }
            ]

            final_messages = prefix_messages + checkpoint_messages + recent_part
            report.checkpoints_created += 1
        else:
            final_messages = tentative_messages

        report.observations_pruned = pruned_count
        report.compacted_tokens = self.estimate_messages_tokens(final_messages)
        report.tokens_saved = max(0, report.original_tokens - report.compacted_tokens)
        report.compression_ratio = report.tokens_saved / report.original_tokens if report.original_tokens > 0 else 0.0
        report.turns_after = len(final_messages)
        report.duration_ms = (time.time() - start_t) * 1000

        return final_messages, report

    def compact_gemini_contents(
        self,
        contents: List[Dict[str, Any]],
        max_token_budget: Optional[int] = None,
        recent_turns: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], CompactionReport]:
        """
        Compacts Google Gemini format contents:
        `[{"role": "user"/"model", "parts": [{"text": ...} | {"functionCall": ...} | {"functionResponse": ...}]}]`
        """
        start_t = time.time()
        budget = max_token_budget or self.max_token_budget
        keep_recent = self.recent_turns if recent_turns is None else recent_turns

        report = CompactionReport(
            original_tokens=self.estimate_messages_tokens(contents),
            turns_before=len(contents),
        )

        if report.original_tokens <= budget:
            report.compacted_tokens = report.original_tokens
            report.turns_after = len(contents)
            report.duration_ms = (time.time() - start_t) * 1000
            return contents, report

        prefix_contents = [contents[0]] if contents else []
        active_history = contents[1:] if len(contents) > 1 else []

        model_indices = [i for i, m in enumerate(active_history) if m.get("role") == "model"]
        if keep_recent == 0:
            cutoff = len(active_history)
        elif len(model_indices) <= keep_recent:
            cutoff = 0
        else:
            cutoff = model_indices[-keep_recent]

        older_part = active_history[:cutoff]
        recent_part = active_history[cutoff:]

        # Collect function call metadata
        fn_meta = {}
        for m in older_part:
            if m.get("role") == "model":
                for p in m.get("parts", []):
                    if "functionCall" in p:
                        fc = p["functionCall"]
                        fn_name = fc.get("name", "")
                        fn_meta[fn_name] = fc.get("args", {})

        compacted_older = []
        pruned_count = 0

        for m in older_part:
            if m.get("role") == "user":
                new_parts = []
                for p in m.get("parts", []):
                    if "functionResponse" in p:
                        fr = p["functionResponse"]
                        fn_name = fr.get("name", "")
                        fn_args = fn_meta.get(fn_name, {})
                        raw_resp = fr.get("response", {})
                        compacted_resp = self.compact_tool_output(fn_name, fn_args, raw_resp)
                        if compacted_resp != raw_resp:
                            pruned_count += 1
                        new_parts.append({
                            "functionResponse": {
                                "name": fn_name,
                                "response": compacted_resp if isinstance(compacted_resp, dict) else {"result": str(compacted_resp)}
                            }
                        })
                    else:
                        new_parts.append(p)
                compacted_older.append({"role": "user", "parts": new_parts})
            else:
                compacted_older.append(m)

        final_contents = prefix_contents + compacted_older + recent_part
        report.observations_pruned = pruned_count
        report.compacted_tokens = self.estimate_messages_tokens(final_contents)
        report.tokens_saved = max(0, report.original_tokens - report.compacted_tokens)
        report.compression_ratio = report.tokens_saved / report.original_tokens if report.original_tokens > 0 else 0.0
        report.turns_after = len(final_contents)
        report.duration_ms = (time.time() - start_t) * 1000

        return final_contents, report
