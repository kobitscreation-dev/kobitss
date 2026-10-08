import json
import asyncio
import uuid
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import AsyncSessionLocal
from backend.models.mission import Mission, MissionStatus, WorkflowPhase, WorkflowCheckpoint, Milestone, MilestoneStatus
from backend.models.project import Task, TaskStatus, Activity, ActivityType
from backend.models.github import Repository, GitHubConnection, PullRequest, PullRequestStatus
from backend.models.agent import AgentRun, AgentRunStatus, AgentType, Agent, ApprovalRequest, ApprovalStatus
from backend.services.agent_executor import execute_agent_run
from backend.services.agent_registry import AGENT_REGISTRY
from backend.services.orchestrator_decision import OrchestratorDecision
from backend.services.github_service import GitHubService
from backend.services.context_builder import build_agent_context
from backend.models.project import Project

from backend.services.intelligence.adaptive_planning import AdaptivePlanner
from backend.services.intelligence.risk_analysis import RiskAnalyzer
from backend.services.intelligence.policy_engine import PolicyEngine
from backend.services.intelligence.learning_loop import LearningLoop
from backend.models.intelligence import TaskContract


class MissionRuntime:
    def __init__(self, mission_id: str, organization_id: str, user_id: str):
        self.mission_id = mission_id
        self.organization_id = organization_id
        self.user_id = user_id
        self._db_lock = asyncio.Lock()

    @staticmethod
    def _verify_sandbox_code(sandbox_dir: str, target_files: list = None) -> dict:
        """
        Multi-Language Automated Pre-Review Verification Gate (Kyros Parity):
        1. Syntax & Compiler AST Gate across Python (.py), TypeScript (.ts/.tsx),
           JavaScript (.js/.mjs/.cjs/.jsx), Go (.go), Rust (.rs), JSON (.json), TOML (.toml), SQL (.sql).
        2. Local Module & Relative Import Resolution Gate across Python (`from pkg import ...`),
           JS/TS (`import ... from './...'` / `require('./...')`), Go (`go.mod` internal packages),
           and Rust (`mod <name>;` file modules).
        3. Automated Smoke Test Gate: executes changed Python tests via `pytest` and changed
           JS/TS tests (`*.test.js`, `*.spec.js`, `*.test.ts`, `*.spec.ts`) via `node --test`.
        """
        import ast
        import re
        import shutil
        import subprocess
        import sys
        from backend.services.sandbox_manager import SandboxManager

        host_root = os.getcwd()
        changed_files: list[str] = []
        verified_py_files: list[str] = []
        verified_files: list[str] = []
        verified_by_language: dict[str, list[str]] = {}
        verified_imports: list[str] = []
        syntax_errors: list[str] = []
        pytest_summary = None
        node_test_summary = None

        if not sandbox_dir or not os.path.isdir(sandbox_dir):
            return {
                "status": "SKIPPED",
                "changed_files": [],
                "verified_py_files": [],
                "verified_files": [],
                "verified_by_language": {},
                "verified_imports": [],
                "pytest_summary": None,
                "node_test_summary": None,
                "errors": [],
            }

        supported_exts = (
            ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts",
            ".jsx", ".tsx", ".go", ".rs", ".json", ".toml", ".sql", ".html", ".css",
        )

        if os.path.exists(os.path.join(sandbox_dir, ".git")):
            try:
                root_sha = None
                meta_file = os.path.join(sandbox_dir, ".kobits_sandbox.json")
                if os.path.isfile(meta_file):
                    try:
                        with open(meta_file, "r", encoding="utf-8") as mf:
                            root_sha = (json.load(mf).get("base_commit_sha") or "").strip() or None
                    except Exception:
                        root_sha = None
                if not root_sha:
                    for ref in ("main", "master", "origin/main", "origin/master"):
                        mb = subprocess.run(
                            ["git", "merge-base", "HEAD", ref],
                            cwd=sandbox_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
                        ).stdout.strip()
                        if mb:
                            root_sha = mb
                            break
                if not root_sha:
                    h_prev = subprocess.run(
                        ["git", "rev-parse", "HEAD~1"],
                        cwd=sandbox_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
                    ).stdout.strip()
                    root_sha = h_prev or subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=sandbox_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
                    ).stdout.strip() or None
                if root_sha:
                    diff_names = subprocess.run(
                        ["git", "diff", root_sha, "--name-only"],
                        cwd=sandbox_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
                    ).stdout.splitlines()
                    status_lines = subprocess.run(
                        ["git", "status", "--porcelain"],
                        cwd=sandbox_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
                    ).stdout.splitlines()
                    for p in diff_names + [ln[3:].strip() for ln in status_lines if len(ln) > 3]:
                        clean_p = p.strip().replace("\\", "/")
                        if (
                            clean_p
                            and "__pycache__" not in clean_p
                            and not clean_p.endswith(".pyc")
                            and not os.path.basename(clean_p).startswith(".kobits_")
                            and not clean_p.startswith(".kobits_rootfs/")
                            and clean_p != ".kobits_rootfs"
                            and clean_p not in changed_files
                        ):
                            changed_files.append(clean_p)
            except Exception:
                pass

        if target_files:
            for tf in target_files:
                clean_tf = str(tf).strip().replace("\\", "/")
                if clean_tf and os.path.isfile(os.path.join(sandbox_dir, clean_tf)) and clean_tf not in changed_files:
                    changed_files.append(clean_tf)

        if not changed_files and not os.path.exists(os.path.join(sandbox_dir, ".git")):
            skip_dirs = {
                ".git", "__pycache__", "venv", ".venv", "sandboxes",
                "node_modules", ".pytest_cache", "target", "dist", "build", ".kobits_rootfs",
            }
            for root, dirs, files in os.walk(sandbox_dir):
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                for fname in files:
                    if not fname.lower().endswith(supported_exts):
                        continue
                    sb_file = os.path.join(root, fname)
                    rel_path = os.path.relpath(sb_file, sandbox_dir).replace("\\", "/")
                    host_file = os.path.join(host_root, rel_path)
                    try:
                        sb_text = open(sb_file, "r", encoding="utf-8", errors="ignore").read()
                        host_text = (
                            open(host_file, "r", encoding="utf-8", errors="ignore").read()
                            if os.path.exists(host_file)
                            else None
                        )
                        if host_text is None or sb_text != host_text:
                            changed_files.append(rel_path)
                    except Exception:
                        pass

        candidates = list(dict.fromkeys(
            changed_files + [str(f).strip().replace("\\", "/") for f in (target_files or []) if f]
        ))

        # Detect Go module path if go.mod exists in sandbox_dir
        go_module_name = None
        go_mod_file = os.path.join(sandbox_dir, "go.mod")
        if os.path.isfile(go_mod_file):
            try:
                go_mod_src = open(go_mod_file, "r", encoding="utf-8", errors="ignore").read()
                m_mod = re.search(r"^\s*module\s+(\S+)", go_mod_src, flags=re.MULTILINE)
                if m_mod:
                    go_module_name = m_mod.group(1).strip()
            except Exception:
                pass

        js_ts_exts = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx")
        resolve_try_exts = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".json", ".css")

        def _resolve_relative_js_ts_import(importing_rel: str, specifier: str) -> bool:
            base_dir = os.path.dirname(os.path.join(sandbox_dir, importing_rel))
            raw_target = os.path.normpath(os.path.join(base_dir, specifier))
            if os.path.isfile(raw_target):
                return True
            for ext in resolve_try_exts:
                if os.path.isfile(raw_target + ext):
                    return True
            # TypeScript ESM convention: `import './foo.js'` resolves to `./foo.ts` or `./foo.tsx`
            for js_ext, ts_alts in ((".js", (".ts", ".tsx", ".d.ts")), (".mjs", (".mts", ".ts")), (".cjs", (".cts", ".ts")), (".jsx", (".tsx", ".ts"))):
                if raw_target.endswith(js_ext):
                    stem = raw_target[: -len(js_ext)]
                    for alt in ts_alts:
                        if os.path.isfile(stem + alt):
                            return True
            # Directory index resolution
            if os.path.isdir(raw_target):
                for idx_ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                    if os.path.isfile(os.path.join(raw_target, "index" + idx_ext)):
                        return True
            return False

        py_candidates: list[str] = []
        js_ts_candidates: list[str] = []

        for rel_file in candidates:
            sb_path = os.path.join(sandbox_dir, rel_file)
            if not os.path.isfile(sb_path):
                continue
            lower_f = rel_file.lower()
            if not lower_f.endswith(supported_exts):
                continue

            try:
                src = open(sb_path, "r", encoding="utf-8").read()
            except Exception as e:
                syntax_errors.append(f"{rel_file}: ReadError: {e}")
                continue

            # Stage 1: Single-file multi-language AST / compiler syntax check
            val = SandboxManager._validate_file_syntax(rel_file, src)
            lang = val.get("language", "other")
            if not val.get("syntax_valid", True):
                syntax_errors.append(val.get("syntax_error") or f"{rel_file}: Syntax error")
                continue

            verified_files.append(rel_file)
            verified_by_language.setdefault(lang, []).append(rel_file)

            # Stage 2A: Python AST Import Resolution
            if lower_f.endswith((".py", ".pyw")):
                py_candidates.append(rel_file)
                verified_py_files.append(rel_file)
                try:
                    tree = ast.parse(src, filename=rel_file)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                            mod_parts = node.module.split(".")
                            top_pkg = mod_parts[0]
                            if os.path.isdir(os.path.join(sandbox_dir, top_pkg)) or os.path.isfile(os.path.join(sandbox_dir, f"{top_pkg}.py")):
                                mod_file = os.path.join(sandbox_dir, *mod_parts) + ".py"
                                pkg_init = os.path.join(sandbox_dir, *mod_parts, "__init__.py")
                                resolved_target = mod_file if os.path.isfile(mod_file) else (pkg_init if os.path.isfile(pkg_init) else None)
                                if not resolved_target:
                                    all_submods = True
                                    for alias in node.names:
                                        sub_file = os.path.join(sandbox_dir, *mod_parts, f"{alias.name}.py")
                                        if not os.path.isfile(sub_file):
                                            all_submods = False
                                            break
                                    if not all_submods:
                                        syntax_errors.append(
                                            f"{rel_file}:{getattr(node, 'lineno', 1)}: Unresolved local module '{node.module}'"
                                        )
                                    else:
                                        verified_imports.append(node.module)
                                else:
                                    verified_imports.append(node.module)
                except Exception as err:
                    syntax_errors.append(f"{rel_file}: {str(err)}")

            # Stage 2B: JS / TS / JSX / TSX Relative Import Resolution
            elif lower_f.endswith(js_ts_exts):
                js_ts_candidates.append(rel_file)
                no_block = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.DOTALL)
                import_patterns = [
                    re.compile(r"""(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"](\.\.?/[^'"]+)['"]"""),
                    re.compile(r"""(?:require|import)\s*\(\s*['"](\.\.?/[^'"]+)['"]\s*\)"""),
                ]
                for ln_no, raw_ln in enumerate(no_block.splitlines(), 1):
                    code_ln = raw_ln.split("//", 1)[0]
                    for pat in import_patterns:
                        for m_imp in pat.finditer(code_ln):
                            spec = m_imp.group(1)
                            if _resolve_relative_js_ts_import(rel_file, spec):
                                verified_imports.append(spec)
                            else:
                                syntax_errors.append(
                                    f"{rel_file}:{ln_no}: Unresolved local relative import '{spec}'"
                                )

            # Stage 2C: Go Local Module Import Resolution (when go.mod is present)
            elif lower_f.endswith(".go") and go_module_name:
                no_block = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.DOTALL)
                for ln_no, raw_ln in enumerate(no_block.splitlines(), 1):
                    code_ln = raw_ln.split("//", 1)[0]
                    for m_go_imp in re.finditer(r'"([^"]+)"', code_ln):
                        imp_path = m_go_imp.group(1)
                        if imp_path.startswith(go_module_name + "/"):
                            sub_rel = imp_path[len(go_module_name) + 1 :]
                            target_pkg_dir = os.path.join(sandbox_dir, sub_rel)
                            if os.path.isdir(target_pkg_dir):
                                verified_imports.append(imp_path)
                            else:
                                syntax_errors.append(
                                    f"{rel_file}:{ln_no}: Unresolved local Go package '{imp_path}'"
                                )

            # Stage 2D: Rust External Module (`mod foo;`) Resolution
            elif lower_f.endswith(".rs"):
                no_block = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.DOTALL)
                cur_dir = os.path.dirname(sb_path)
                for ln_no, raw_ln in enumerate(no_block.splitlines(), 1):
                    code_ln = raw_ln.split("//", 1)[0].strip()
                    m_rs_mod = re.match(r"^(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;$", code_ln)
                    if m_rs_mod:
                        mod_name = m_rs_mod.group(1)
                        cand_a = os.path.join(cur_dir, f"{mod_name}.rs")
                        cand_b = os.path.join(cur_dir, mod_name, "mod.rs")
                        if os.path.isfile(cand_a) or os.path.isfile(cand_b):
                            verified_imports.append(f"mod::{mod_name}")
                        else:
                            syntax_errors.append(
                                f"{rel_file}:{ln_no}: Unresolved Rust module 'mod {mod_name};' (expected {mod_name}.rs or {mod_name}/mod.rs)"
                            )

        # Stage 2E: Project-Level TypeCheckers (tsc --noEmit / go vet / cargo check) when present
        if not syntax_errors:
            if any(f.lower().endswith((".ts", ".tsx")) for f in js_ts_candidates) and os.path.isfile(os.path.join(sandbox_dir, "tsconfig.json")):
                local_tsc = os.path.join(sandbox_dir, "node_modules", ".bin", "tsc.cmd" if os.name == "nt" else "tsc")
                tsc_bin = local_tsc if os.path.isfile(local_tsc) else shutil.which("tsc")
                if tsc_bin:
                    try:
                        tsc_res = subprocess.run(
                            [tsc_bin, "--noEmit", "--pretty", "false"],
                            cwd=sandbox_dir, capture_output=True, text=True, timeout=15
                        )
                        if tsc_res.returncode != 0:
                            tsc_out = (tsc_res.stdout or tsc_res.stderr or "").strip().splitlines()
                            if tsc_out:
                                syntax_errors.append(f"tsc --noEmit: {tsc_out[0][:200]}")
                    except Exception:
                        pass

            if verified_by_language.get("go") and os.path.isfile(go_mod_file) and shutil.which("go"):
                try:
                    govet_res = subprocess.run(
                        [shutil.which("go"), "vet", "./..."],
                        cwd=sandbox_dir, capture_output=True, text=True, timeout=15
                    )
                    if govet_res.returncode != 0:
                        govet_out = (govet_res.stderr or govet_res.stdout or "").strip().splitlines()
                        if govet_out:
                            syntax_errors.append(f"go vet: {govet_out[0][:200]}")
                except Exception:
                    pass

            if verified_by_language.get("rust") and os.path.isfile(os.path.join(sandbox_dir, "Cargo.toml")) and shutil.which("cargo"):
                try:
                    cargo_res = subprocess.run(
                        [shutil.which("cargo"), "check", "--quiet"],
                        cwd=sandbox_dir, capture_output=True, text=True, timeout=20
                    )
                    if cargo_res.returncode != 0:
                        cargo_out = (cargo_res.stderr or cargo_res.stdout or "").strip().splitlines()
                        if cargo_out:
                            syntax_errors.append(f"cargo check: {cargo_out[0][:200]}")
                except Exception:
                    pass

        # Stage 3A: Automated Pre-Review Python Pytest Gate on changed Python test files
        py_test_files = [
            f for f in py_candidates
            if os.path.basename(f).startswith("test_") or os.path.basename(f).endswith("_test.py")
        ]
        if py_test_files and not syntax_errors:
            try:
                from backend.services.environment_bootstrapper import EnvironmentBootstrapper
                env = EnvironmentBootstrapper.build_isolated_env(sandbox_dir)
                pt_res = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", *py_test_files],
                    cwd=sandbox_dir, capture_output=True, text=True, timeout=12, env=env
                )
                pytest_summary = {
                    "ran": len(py_test_files),
                    "passed": pt_res.returncode == 0,
                    "output": (pt_res.stdout or pt_res.stderr or "").strip()[-400:],
                }
                if pt_res.returncode != 0:
                    syntax_errors.append(f"pytest failed on {', '.join(py_test_files)}: {pytest_summary['output'][:160]}")
            except Exception:
                pass

        # Stage 3B: Automated Pre-Review JS/TS Test Gate on changed *.test.{js,ts} / *.spec.{js,ts} files
        js_ts_test_files = [
            f for f in js_ts_candidates
            if any(
                os.path.basename(f).lower().endswith(suffix)
                for suffix in (".test.js", ".spec.js", "_test.js", ".test.mjs", ".test.ts", ".spec.ts", "_test.ts")
            )
        ]
        node_bin = shutil.which("node")
        if js_ts_test_files and node_bin and not syntax_errors:
            try:
                # Inject a lightweight Jest/Vitest compatibility shim via Data URL `--import` so both
                # standard `node:test` and `describe/it/expect` unit tests execute natively in Node!
                shim_js = (
                    "import * as nt from 'node:test';"
                    "import assert from 'node:assert';"
                    "globalThis.describe ??= nt.describe;"
                    "globalThis.it ??= nt.it;"
                    "globalThis.test ??= nt.test;"
                    "globalThis.beforeEach ??= nt.beforeEach;"
                    "globalThis.afterEach ??= nt.afterEach;"
                    "globalThis.expect ??= (actual) => ({"
                    "  toBe: (exp) => assert.strictEqual(actual, exp),"
                    "  toEqual: (exp) => assert.deepStrictEqual(actual, exp),"
                    "  toBeTruthy: () => assert.ok(actual),"
                    "  toBeFalsy: () => assert.ok(!actual),"
                    "  toContain: (item) => assert.ok(actual && actual.includes(item)),"
                    "  toThrow: () => assert.throws(actual),"
                    "});"
                )
                import urllib.parse
                shim_data_url = "data:text/javascript," + urllib.parse.quote(shim_js)
                nt_res = subprocess.run(
                    [node_bin, "--no-warnings", f"--import={shim_data_url}", "--test", *js_ts_test_files],
                    cwd=sandbox_dir,
                    capture_output=True,
                    text=True,
                    timeout=12,
                )
                node_test_summary = {
                    "ran": len(js_ts_test_files),
                    "passed": nt_res.returncode == 0,
                    "output": (nt_res.stdout or nt_res.stderr or "").strip()[-400:],
                }
                if nt_res.returncode != 0:
                    syntax_errors.append(
                        f"node --test failed on {', '.join(js_ts_test_files)}: {node_test_summary['output'][:160]}"
                    )
            except Exception:
                pass

        return {
            "status": "PASSED" if not syntax_errors else "FAILED",
            "changed_files": changed_files,
            "verified_py_files": verified_py_files,
            "verified_files": verified_files,
            "verified_by_language": verified_by_language,
            "verified_imports": list(dict.fromkeys(verified_imports)),
            "pytest_summary": pytest_summary,
            "node_test_summary": node_test_summary,
            "errors": syntax_errors,
        }

    @staticmethod
    def _build_bounded_upstream_context(dep_runs: list, max_total_chars: int = 8000) -> dict:
        """
        Build a Kyros/Claude-Code style Bounded Context dictionary from completed upstream runs:
        1. Pinned Tier: Always preserves foundational artifacts (`Repository Understanding`,
           `Planning & Architecture`, `Database Architecture`, `Task Decomposition`) so late-phase
           agents (`Code Review`, `Post-Mortem & Memory Sync`) never lose architectural or repo context.
        2. Sliding Tier: Includes the most recent implementation/verification runs within `max_total_chars`.
        """
        pinned_titles = {
            "repository understanding",
            "planning & architecture",
            "database architecture",
            "task decomposition",
        }
        pinned_pairs = []
        recent_pairs = []
        for dr, dt in dep_runs:
            t_lower = (getattr(dt, "title", "") or "").strip().lower()
            if t_lower in pinned_titles:
                pinned_pairs.append((dr, dt))
            else:
                recent_pairs.append((dr, dt))

        ordered_pairs = pinned_pairs + recent_pairs[-5:]
        upstream_artifacts = {}
        used_chars = 0

        for dr, dt in ordered_pairs:
            if not getattr(dr, "output_text", None):
                continue
            try:
                out_obj = json.loads(dr.output_text)
                if not isinstance(out_obj, dict):
                    continue
                prefix = (getattr(dt, "title", "task") or "task").lower().replace(" ", "_")
                arts = out_obj.get("artifacts")
                if isinstance(arts, dict) and arts:
                    for k, v in arts.items():
                        key_name = f"{prefix}_{k}"
                        if k in ("task_decomposition", "tasks"):
                            upstream_artifacts[key_name] = v
                            continue
                        serialized = v if isinstance(v, str) else json.dumps(v)
                        trimmed = serialized[:1200]
                        if used_chars + len(trimmed) <= max_total_chars or key_name not in upstream_artifacts:
                            upstream_artifacts[key_name] = trimmed
                            used_chars += len(trimmed)
                elif out_obj.get("summary"):
                    key_name = f"{prefix}_summary"
                    trimmed = str(out_obj["summary"])[:800]
                    if used_chars + len(trimmed) <= max_total_chars:
                        upstream_artifacts[key_name] = trimmed
                        used_chars += len(trimmed)
            except Exception:
                pass

        return upstream_artifacts

    async def _broadcast(self, event_type: str, data: dict):
        """Broadcast a live event to all WebSocket clients watching this mission."""
        try:
            from backend.services.websocket_manager import manager
            import time
            message = {
                "type": event_type,
                "mission_id": self.mission_id,
                "timestamp": time.time(),
                **data
            }
            await manager.broadcast(self.mission_id, message)
        except Exception:
            pass  # WebSocket failures should never crash the runtime

    async def execute(self):
        print(f"Executing mission {self.mission_id}")
        lock_id = str(uuid.uuid4())
        
        async with AsyncSessionLocal() as db:
            # Atomic lock acquisition
            stmt = (
                update(Mission)
                .where(Mission.id == self.mission_id)
                .where(
                    (Mission.execution_lock_id == None) | 
                    (Mission.execution_lock_expires_at < datetime.now(timezone.utc))
                )
                .where(Mission.status == MissionStatus.ACTIVE)
                .values(
                    execution_lock_id=lock_id, 
                    execution_lock_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)
                )
            )
            result = await db.execute(stmt)
            await db.commit()
            
            if result.rowcount == 0:
                m = await db.get(Mission, self.mission_id)
                print(f"DEBUG: lock acquisition failed. Rowcount={result.rowcount}. DB Status={m.status if m else None}, DB lock_id={m.execution_lock_id if m else None}, DB lock_expires={m.execution_lock_expires_at if m else None}")
                return
                return

        async def heartbeat_loop(l_id):
            try:
                while True:
                    await asyncio.sleep(60)
                    async with AsyncSessionLocal() as hdb:
                        await hdb.execute(
                            update(Mission)
                            .where(Mission.id == self.mission_id)
                            .where(Mission.execution_lock_id == l_id)
                            .values(execution_lock_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
                        )
                        await hdb.commit()
            except asyncio.CancelledError:
                pass
                
        heartbeat_task = asyncio.create_task(heartbeat_loop(lock_id))
                
        try:
            running = True
            while running:
                async with AsyncSessionLocal() as db:
                    mission = await db.get(Mission, self.mission_id)
                    if not mission or mission.status != MissionStatus.ACTIVE or mission.execution_lock_id != lock_id:
                        running = False
                        break

                    phase_handlers = {
                        WorkflowPhase.INTAKE: self._handle_intake,
                        WorkflowPhase.ANALYSIS: self._handle_analysis,
                        WorkflowPhase.PLANNING: self._handle_planning,
                        WorkflowPhase.ARCHITECTURE_REVIEW: self._handle_human_gate,
                        WorkflowPhase.IMPLEMENTATION: self._handle_implementation,
                        WorkflowPhase.VALIDATION: self._handle_validation,
                        WorkflowPhase.SECURITY: self._handle_security,
                        WorkflowPhase.CODE_REVIEW: self._handle_code_review,
                        WorkflowPhase.DELIVERY_REVIEW: self._handle_human_gate,
                        WorkflowPhase.DEPLOYMENT: self._handle_deployment,
                        WorkflowPhase.LEARNING: self._handle_learning
                    }

                    handler = phase_handlers.get(mission.phase)
                    if not handler:
                        print(f"Unknown phase {mission.phase}")
                        break

                    if os.environ.get("KOBITS_VERBOSE") == "1":
                        print(f"Running phase: {mission.phase}")
                    await self._broadcast("phase_start", {
                        "phase": mission.phase.value if hasattr(mission.phase, 'value') else str(mission.phase),
                        "stage": mission.current_stage or "Processing..."
                    })
                    transition_action = await handler(db, mission)
                    
                    # Arbitration Protocol Check
                    if hasattr(mission, 'metadata_json') and mission.metadata_json:
                        try:
                            meta = json.loads(mission.metadata_json)
                            if meta.get("debate_rounds", 0) >= 2:
                                print(f"DEADLOCK DETECTED! Invoking Arbitration Protocol for Mission {mission.id}")
                                # Orchestrator resolves deadlock: Security > Architecture > Speed
                                meta["arbitration_result"] = "Orchestrator Verdict: Priority given to Security."
                                mission.metadata_json = json.dumps(meta)
                                await db.commit()
                        except:
                            pass
                    
                    if transition_action == "HALT":
                        running = False
                        break
                    elif transition_action == "ERROR":
                        mission.status = MissionStatus.FAILED
                        try:
                            from backend.services.billing_policy import CreditPolicy
                            await CreditPolicy.release_reservation(db, mission.organization_id, mission.id)
                        except Exception as e:
                            print(f"Failed to release reservation: {e}")
                            await db.rollback()
                        await db.commit()
                        try:
                            from backend.api.v1.missions import dispatch_outbound_webhook_notification
                            await dispatch_outbound_webhook_notification(db, mission, "MISSION_FAILED")
                        except Exception:
                            pass
                        await self._broadcast("mission_state", {"status": "FAILED", "message": "Mission failed during phase execution."})
                        running = False
                        break
        except Exception as e:
            import traceback
            traceback.print_exc()
            async with AsyncSessionLocal() as db:
                mission = await db.get(Mission, self.mission_id)
                if mission and mission.execution_lock_id == lock_id:
                    mission.status = MissionStatus.FAILED
                    mission.current_stage = f"Fatal Error: {str(e)}"
                    try:
                        from backend.services.billing_policy import CreditPolicy
                        await CreditPolicy.release_reservation(db, mission.organization_id, mission.id)
                    except Exception as e:
                        print(f"Failed to release reservation: {e}")
                        await db.rollback()
                    await db.commit()
                    try:
                        from backend.api.v1.missions import dispatch_outbound_webhook_notification
                        await dispatch_outbound_webhook_notification(db, mission, "MISSION_FAILED", extra={"error": str(e)})
                    except Exception:
                        pass
        finally:
            if 'heartbeat_task' in locals():
                heartbeat_task.cancel()
            # Release lock
            async with AsyncSessionLocal() as db:
                stmt = (
                    update(Mission)
                    .where(Mission.id == self.mission_id)
                    .where(Mission.execution_lock_id == lock_id)
                    .values(execution_lock_id=None, execution_lock_expires_at=None)
                )
                await db.execute(stmt)
                await db.commit()
                if os.environ.get("KOBITS_VERBOSE") == "1":
                    print(f"Released lock for mission {self.mission_id}")

    # =========================================================================
    # PHASE HANDLERS
    # =========================================================================
    
    MAX_FLUID_ITERATIONS = 4

    @staticmethod
    def _load_mission_meta(mission: Mission) -> dict:
        meta = {}
        if getattr(mission, "risk_profile_json", None):
            try:
                rp = json.loads(mission.risk_profile_json)
                if isinstance(rp, dict):
                    meta.update(rp)
            except Exception:
                pass
        if hasattr(mission, "metadata_json") and mission.metadata_json:
            try:
                mj = json.loads(mission.metadata_json)
                if isinstance(mj, dict):
                    meta.update(mj)
            except Exception:
                pass
        return meta

    @staticmethod
    def _save_mission_meta(mission: Mission, meta: dict) -> None:
        raw = json.dumps(meta)
        mission.risk_profile_json = raw
        if hasattr(mission, "metadata_json"):
            mission.metadata_json = raw

    def _is_fluid_mode(self, mission: Mission) -> bool:
        """
        Kyros Fluid Single-Loop Autonomous Engine is the default execution mode.
        Only falls back to legacy 11-phase waterfall if KOBITS_PIPELINE_MODE=waterfall
        or mission metadata explicitly sets {"pipeline_mode": "waterfall"}.
        """
        env_mode = (os.environ.get("KOBITS_PIPELINE_MODE") or "fluid").strip().lower()
        if env_mode == "waterfall":
            return False
        meta = self._load_mission_meta(mission)
        if str(meta.get("pipeline_mode", "")).lower() == "waterfall":
            return False
        return True

    @staticmethod
    def _select_primary_coding_role(scope_triage: dict) -> str:
        domains = scope_triage.get("domains") or ["backend"]
        if domains == ["frontend"]:
            return "FRONTEND_ENGINEER"
        if domains == ["database"]:
            return "DATABASE_ENGINEER"
        if domains == ["ai_ml"]:
            return "AI_ML_ENGINEER"
        if domains == ["devops"]:
            return "DEVOPS_ENGINEER"
        return "BACKEND_ENGINEER"

    @staticmethod
    def _discover_and_run_sandbox_tests(
        sandbox_dir: str,
        candidate_files: list = None,
        custom_test_cmd: str = None,
    ) -> dict:
        """
        Discover and execute unit tests in the sandbox matching touched files or custom_test_cmd.
        """
        import shlex
        import subprocess
        import sys

        if not sandbox_dir or not os.path.isdir(sandbox_dir):
            return {"ran": 0, "passed": True, "output": "", "errors": []}

        errors: list[str] = []
        outputs: list[str] = []
        ran_count = 0

        from backend.services.environment_bootstrapper import EnvironmentBootstrapper
        env = EnvironmentBootstrapper.build_isolated_env(sandbox_dir)

        if custom_test_cmd:
            try:
                res = EnvironmentBootstrapper.execute_isolated(
                    sandbox_dir=sandbox_dir,
                    command=custom_test_cmd,
                    timeout=20,
                )
                ran_count += 1
                out_str = ((res.get("stdout") or "") + "\n" + (res.get("stderr") or "")).strip()
                outputs.append(out_str[-500:])
                if res.get("exit_code", 0) != 0:
                    errors.append(f"Test command '{custom_test_cmd}' failed (exit {res.get('exit_code')}): {out_str[-240:]}")
            except Exception as exc:
                errors.append(f"Test command '{custom_test_cmd}' error: {exc}")

        discovered_py_tests: list[str] = []
        for rel_f in (candidate_files or []):
            clean_f = str(rel_f).strip().replace("\\", "/")
            if not clean_f:
                continue
            base_name = os.path.basename(clean_f)
            stem, ext = os.path.splitext(base_name)
            if ext.lower() not in (".py", ".pyw"):
                continue
            if base_name.startswith("test_") or base_name.endswith("_test.py"):
                if os.path.isfile(os.path.join(sandbox_dir, clean_f)) and clean_f not in discovered_py_tests:
                    discovered_py_tests.append(clean_f)
                continue
            for cand_test in (
                f"test_{stem}.py",
                f"{stem}_test.py",
                f"tests/test_{stem}.py",
                f"tests/{stem}_test.py",
            ):
                if os.path.isfile(os.path.join(sandbox_dir, cand_test)) and cand_test not in discovered_py_tests:
                    discovered_py_tests.append(cand_test)

        if discovered_py_tests:
            try:
                pt_res = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", *discovered_py_tests],
                    cwd=sandbox_dir,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    env=env,
                )
                ran_count += len(discovered_py_tests)
                pt_out = (pt_res.stdout or pt_res.stderr or "").strip()
                outputs.append(pt_out[-500:])
                if pt_res.returncode != 0:
                    errors.append(
                        f"pytest failed on {', '.join(discovered_py_tests)}: {pt_out[-240:]}"
                    )
            except Exception as exc:
                errors.append(f"pytest execution error: {exc}")

        return {
            "ran": ran_count,
            "passed": len(errors) == 0,
            "output": "\n".join(o for o in outputs if o).strip()[-600:],
            "errors": errors,
        }

    async def _run_fluid_verification_gate(self, db: AsyncSession, mission: Mission) -> dict:
        """
        Execute Kyros's deterministic Verification + Test-Runner Gate on the mission sandbox.
        """
        from backend.services.sandbox_manager import SandboxManager

        scope_triage = self._get_scope_triage(db, mission)
        target_files = scope_triage.get("target_files", []) if isinstance(scope_triage, dict) else []

        sb_dir = None
        if mission.active_branch:
            sb_session = SandboxManager.get_session_by_branch(mission.active_branch)
            if sb_session and os.path.isdir(sb_session.sandbox_dir):
                sb_dir = sb_session.sandbox_dir

        if not sb_dir:
            root_sandboxes = os.path.join(os.getcwd(), "sandboxes")
            short_id = mission.id[:8]
            for cand_name in (
                f"mission_{short_id}",
                f"mission_{mission.id}",
                f"kobits_mission_{short_id}",
                f"kobits_mission_{mission.id}",
            ):
                cand_path = os.path.join(root_sandboxes, cand_name)
                if os.path.isdir(cand_path):
                    sb_dir = cand_path
                    break

        if not sb_dir:
            try:
                import kobits_cli
                inspected = kobits_cli._inspect_mission_sandbox(mission.id, mission.active_branch, target_files)
                if inspected.get("abs_sandbox_dir") and os.path.isdir(inspected["abs_sandbox_dir"]):
                    sb_dir = inspected["abs_sandbox_dir"]
            except Exception:
                sb_dir = None

        m_meta = self._load_mission_meta(mission)
        custom_test_cmd = m_meta.get("test_command")

        report = await asyncio.to_thread(self._verify_sandbox_code, sb_dir, target_files)
        candidates = list(dict.fromkeys((report.get("changed_files") or []) + (target_files or [])))
        test_res = await asyncio.to_thread(
            self._discover_and_run_sandbox_tests,
            sb_dir,
            candidates,
            custom_test_cmd,
        )
        merged_errors = list(dict.fromkeys((report.get("errors") or []) + (test_res.get("errors") or [])))
        report["errors"] = merged_errors
        report["test_runner"] = test_res
        if sb_dir and os.path.isdir(sb_dir):
            report["status"] = "PASSED" if not merged_errors else "FAILED"

        await self._broadcast("sandbox_verification", {
            "mission_id": mission.id,
            "status": report.get("status", "SKIPPED"),
            "changed_files": report.get("changed_files", []),
            "verified_files": report.get("verified_files", []),
            "test_runner": test_res,
            "errors": merged_errors,
            "message": (
                f"✓ Fluid Verification & Test Gate PASSED ({len(report.get('verified_files', []))} files verified, {test_res.get('ran', 0)} test suites run)"
                if report.get("status") != "FAILED"
                else f"✗ Fluid Verification & Test Gate FAILED: {'; '.join(merged_errors)}"
            ),
        })
        return report

    def _get_scope_triage(self, db: AsyncSession, mission: Mission) -> dict:
        meta = self._load_mission_meta(mission)
        scope_triage = meta.get("scope_triage")
        if isinstance(scope_triage, dict) and "domains" in scope_triage:
            return scope_triage

        planner = AdaptivePlanner(db)
        raw_triage = planner.classify_scope(mission.objective or "", mission.title or "")
        if not isinstance(raw_triage, dict):
            return {
                "domains": ["backend", "frontend", "database", "ai_ml"],
                "complexity": "standard",
                "task_category": "general_engineering",
                "capabilities": ["architecture", "system design"],
                "target_files": [],
                "fast_track": False,
                "is_generic": True,
                "requires_security_audit": True,
                "requires_devops": True,
                "requires_release_manager": True,
                "requires_docs": True,
                "skipped_planners": [],
                "skipped_specialists": [],
            }
        return raw_triage

    async def _handle_intake(self, db: AsyncSession, mission: Mission) -> str:
        # INTELLIGENCE LAYER: Scope Triage, Risk Analysis & Adaptive Planning
        planner = AdaptivePlanner(db)
        risk_analyzer = RiskAnalyzer(db)

        prev_meta = self._load_mission_meta(mission)
        scope_triage = self._get_scope_triage(db, mission)
        target_files = scope_triage.get("target_files", [])
        new_rp = risk_analyzer.analyze_change_impact(target_files)
        if not isinstance(new_rp, dict):
            new_rp = {}
        for k, v in prev_meta.items():
            if k not in new_rp:
                new_rp[k] = v
        new_rp["scope_triage"] = scope_triage
        if "fluid_loop" not in new_rp:
            new_rp["fluid_loop"] = {
                "enabled": self._is_fluid_mode(mission),
                "iteration": 1,
                "max_iterations": self.MAX_FLUID_ITERATIONS,
                "history": [],
            }
        self._save_mission_meta(mission, new_rp)

        strategy = await planner.select_strategy(scope_triage["task_category"], "LOW")
        team = await planner.select_team(scope_triage["task_category"], scope_triage["capabilities"], strategy)

        mission.workflow_strategy = strategy.value
        mission.team_composition_json = json.dumps(team)

        mission.phase = WorkflowPhase.ANALYSIS
        mission.current_stage = "Fluid Loop: Grounding & Planning" if self._is_fluid_mode(mission) else "Repository Understanding"
        mission.execution_started_at = datetime.now(timezone.utc)
        await db.commit()
        await self._broadcast("scope_triage", scope_triage)
        return "CONTINUE"

    async def _handle_analysis(self, db: AsyncSession, mission: Mission) -> str:
        if self._is_fluid_mode(mission):
            existing_analysis = (
                await db.execute(select(Task).where(Task.mission_id == mission.id, Task.phase == WorkflowPhase.ANALYSIS))
            ).scalars().all()
            if not existing_analysis:
                mission.phase = WorkflowPhase.PLANNING
                mission.current_stage = "Fluid Loop: Planning"
                mission.progress = 20
                await db.commit()
                return "CONTINUE"
            return await self._execute_phase_tasks(
                db, mission, WorkflowPhase.ANALYSIS,
                next_phase=WorkflowPhase.PLANNING,
                stage_name="Fluid Loop: Planning",
                tasks_to_spawn=[],
            )

        scope_triage = self._get_scope_triage(db, mission)
        target_files = scope_triage.get("target_files", [])
        target_hint = f" Grounded candidate files: {', '.join(target_files)}." if target_files else ""

        analysis_tasks = [
            {
                "title": "Repository Understanding",
                "agent": "SOLUTION_ARCHITECT",
                "description": (
                    "Examine the repository structure, conventions, and capabilities to establish a baseline "
                    f"understanding of the project.{target_hint}"
                ),
            }
        ]
        if scope_triage.get("requires_pm", not scope_triage.get("fast_track", False)):
            analysis_tasks.append({
                "title": "Requirement Clarification",
                "agent": "PRODUCT_MANAGER",
                "description": (
                    "Analyze the mission objective against the repository baseline to clarify requirements "
                    "and define concrete acceptance criteria."
                ),
            })

        return await self._execute_phase_tasks(
            db, mission, WorkflowPhase.ANALYSIS,
            next_phase=WorkflowPhase.PLANNING,
            stage_name="Planning & Architecture",
            tasks_to_spawn=analysis_tasks
        )

    async def _handle_planning(self, db: AsyncSession, mission: Mission) -> str:
        scope_triage = self._get_scope_triage(db, mission)
        meta = self._load_mission_meta(mission)
        meta["scope_triage"] = scope_triage

        # Pre-fetch decision memory
        try:
            from backend.services.memory import MemoryEngine
            past_decisions = await MemoryEngine.query_memory(db, mission.project_id, mission.objective)
            if past_decisions:
                meta["past_decisions"] = past_decisions
            self._save_mission_meta(mission, meta)
            await db.commit()
        except Exception:
            pass

        domains = scope_triage.get("domains", ["backend", "database", "ai_ml"])
        target_files = scope_triage.get("target_files", [])
        target_hint = f" Target files identified in repository: {', '.join(target_files)}." if target_files else ""

        if self._is_fluid_mode(mission):
            existing_impl = (
                await db.execute(select(Task).where(Task.mission_id == mission.id, Task.phase == WorkflowPhase.IMPLEMENTATION))
            ).scalars().all()
            existing_plan = (
                await db.execute(select(Task).where(Task.mission_id == mission.id, Task.phase == WorkflowPhase.PLANNING))
            ).scalars().all()

            # For focused / fast-track sprints, skip ceremonial planner round-trips and seed the direct coding task!
            if not existing_impl and not existing_plan and (scope_triage.get("fast_track") or len(domains) <= 1):
                primary_role = self._select_primary_coding_role(scope_triage)
                direct_task = Task(
                    id=str(uuid.uuid4()),
                    mission_id=mission.id,
                    project_id=mission.project_id,
                    created_by=mission.created_by,
                    title=f"Implement: {(mission.title or mission.objective or 'Sprint Objective')[:64]}",
                    description=(
                        f"{mission.objective or mission.title}.{target_hint} "
                        "Use repository.read, repository.edit, or repository.write to implement the changes in the sandbox."
                    ),
                    status=TaskStatus.PENDING,
                    phase=WorkflowPhase.IMPLEMENTATION,
                    metadata_json=json.dumps({"agent_role": primary_role, "fluid_direct": True}),
                    dependencies_json="[]",
                )
                db.add(direct_task)
                mission.phase = WorkflowPhase.ARCHITECTURE_REVIEW
                mission.current_stage = "Fluid Loop: Ready for Execution"
                mission.progress = 30
                await db.commit()
                return "CONTINUE"

            # For multi-domain sprints, run a single concise Planner task decomposition pass
            fluid_planning_tasks = [
                {
                    "title": "Task Decomposition",
                    "agent": "TECHNICAL_LEAD",
                    "description": (
                        f"Inspect the repository and decompose the implementation for domains {domains} "
                        f"(complexity: {scope_triage.get('complexity', 'standard')}) into concrete coding tasks.{target_hint}"
                    ),
                }
            ]
            return await self._execute_phase_tasks(
                db, mission, WorkflowPhase.PLANNING,
                next_phase=WorkflowPhase.ARCHITECTURE_REVIEW,
                stage_name="Fluid Loop: Ready for Execution",
                tasks_to_spawn=fluid_planning_tasks,
            )

        planning_tasks = [
            {
                "title": "Planning & Architecture",
                "agent": "SOLUTION_ARCHITECT",
                "description": (
                    f"Analyze the requirements and existing repository for touched domains {domains} "
                    f"(complexity: {scope_triage.get('complexity', 'standard')}) to design a technical architecture, "
                    f"identify affected files, and define necessary API contracts.{target_hint}"
                ),
            }
        ]
        if "database" in domains:
            planning_tasks.append({
                "title": "Database Architecture",
                "agent": "DATABASE_ENGINEER",
                "description": "Review the architecture plan for data integrity, design schema migrations, and indexing strategies.",
            })
        if "ai_ml" in domains:
            planning_tasks.append({
                "title": "AI/ML Integration Planning",
                "agent": "AI_ML_ENGINEER",
                "description": "Review the architecture plan to determine integration points for model providers, embeddings, and vector stores.",
            })
        if "frontend" in domains and "database" not in domains and "ai_ml" not in domains:
            planning_tasks.append({
                "title": "UX & Interface Architecture",
                "agent": "UX_DESIGNER",
                "description": "Define component hierarchy, layout states, and user interaction flows matching the existing Kobits design system.",
            })
        planning_tasks.append({
            "title": "Task Decomposition",
            "agent": "TECHNICAL_LEAD",
            "description": (
                f"Read the upstream architecture and decompose the implementation for domains {domains} "
                f"(complexity: {scope_triage.get('complexity', 'standard')}) into specific, actionable engineering tasks "
                f"assigned only to the relevant specialist agents.{target_hint}"
            ),
        })

        return await self._execute_phase_tasks(
            db, mission, WorkflowPhase.PLANNING,
            next_phase=WorkflowPhase.ARCHITECTURE_REVIEW,
            stage_name="Awaiting Architecture Approval",
            tasks_to_spawn=planning_tasks
        )
        
    async def _handle_human_gate(self, db: AsyncSession, mission: Mission) -> str:
        from backend.models.mission import ApprovalStatus
        
        # If approval is required and hasn't been granted yet, we stop.
        if mission.requires_approval and mission.approval_status == ApprovalStatus.PENDING:
            mission.status = MissionStatus.AWAITING_APPROVAL
            await db.commit()
            try:
                from backend.api.v1.missions import dispatch_outbound_webhook_notification
                await dispatch_outbound_webhook_notification(db, mission, "AWAITING_APPROVAL")
            except Exception:
                pass
            await self._broadcast("mission_state", {"status": "AWAITING_APPROVAL", "message": "Mission is waiting for your approval to proceed."})
            return "HALT"
            
        mission.status = MissionStatus.ACTIVE
        
        # Advance to the next appropriate phase based on current phase
        if mission.phase == WorkflowPhase.ARCHITECTURE_REVIEW or getattr(mission.phase, "value", mission.phase) == "ARCHITECTURE_REVIEW":
            mission.phase = WorkflowPhase.IMPLEMENTATION
            mission.current_stage = "Fluid Loop: Coding & Tool Execution" if self._is_fluid_mode(mission) else "Implementation"
        elif mission.phase == WorkflowPhase.DELIVERY_REVIEW or getattr(mission.phase, "value", mission.phase) == "DELIVERY_REVIEW":
            mission.phase = WorkflowPhase.DEPLOYMENT
            mission.current_stage = "Deployment"
        else:
            mission.phase = WorkflowPhase.IMPLEMENTATION
            mission.current_stage = "Fluid Loop: Coding & Tool Execution" if self._is_fluid_mode(mission) else "Implementation"
        
        await db.commit()
        return "CONTINUE"

    async def _handle_implementation(self, db: AsyncSession, mission: Mission) -> str:
        # Credit Reservation Check before billable execution
        import os
        try:
            from backend.services.billing_policy import CreditPolicy
            await CreditPolicy.reserve_credits(db, mission.organization_id, mission.id)
        except Exception as e:
            if os.environ.get("KOBITS_VERBOSE") == "1":
                print(f"Credit Reservation Failed for Mission {mission.id}: {e}")
            await db.rollback()
            if os.getenv("KOBITS_DEV_MODE", "true").lower() in ("true", "1", "yes"):
                if os.environ.get("KOBITS_VERBOSE") == "1":
                    print("Bypassing credit reservation failure in DEV MODE.")
                # Refresh mission because rollback expired it
                mission = await db.get(Mission, self.mission_id)
            else:
                mission.status = MissionStatus.FAILED
                await db.commit()
                return "HALT"

        # Pull any pending live steering override tasks from other phases into IMPLEMENTATION
        steer_tasks = (
            await db.execute(
                select(Task).where(
                    Task.mission_id == mission.id,
                    Task.is_correction == True,
                    Task.status == TaskStatus.PENDING,
                    Task.phase != WorkflowPhase.IMPLEMENTATION,
                )
            )
        ).scalars().all()
        if steer_tasks:
            for st in steer_tasks:
                st.phase = WorkflowPhase.IMPLEMENTATION
            await db.commit()

        scope_triage = self._get_scope_triage(db, mission)
        primary_role = self._select_primary_coding_role(scope_triage)
        fallback_spawn = [
            {
                "title": f"Implement: {(mission.title or 'Sprint Objective')[:64]}",
                "agent": primary_role,
                "description": (
                    f"{mission.objective or mission.title}. "
                    "Use repository.read, repository.edit, or repository.write to modify the repository in the sandbox."
                ),
            }
        ]
        return await self._execute_phase_tasks(
            db, mission, WorkflowPhase.IMPLEMENTATION,
            next_phase=WorkflowPhase.VALIDATION,
            stage_name="Fluid Loop: Verification & Test Runner" if self._is_fluid_mode(mission) else "Validation & Testing",
            tasks_to_spawn=fallback_spawn
        )

    async def _handle_validation(self, db: AsyncSession, mission: Mission) -> str:
        if self._is_fluid_mode(mission):
            # 1. If any explicit VALIDATION tasks were pre-created, run them without spawning ceremonial waterfall tasks
            existing_val_tasks = (
                await db.execute(select(Task).where(Task.mission_id == mission.id, Task.phase == WorkflowPhase.VALIDATION))
            ).scalars().all()
            pending_val = [t for t in existing_val_tasks if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)]
            if pending_val:
                for t in pending_val:
                    t.status = TaskStatus.IN_PROGRESS
                await db.commit()
                for t in pending_val:
                    await self.execute_task(t.id)
                await db.rollback()
                mission = await db.get(Mission, self.mission_id)
                existing_val_tasks = (
                    await db.execute(select(Task).where(Task.mission_id == mission.id, Task.phase == WorkflowPhase.VALIDATION))
                ).scalars().all()

            if any(t.status == TaskStatus.BLOCKED for t in existing_val_tasks):
                return "HALT"

            # 2. Run the Kyros Fluid Verification & Test-Runner Gate
            ver_report = await self._run_fluid_verification_gate(db, mission)
            val_failed_tasks = [t for t in existing_val_tasks if t.status == TaskStatus.FAILED]
            has_gate_failure = (ver_report.get("status") == "FAILED") or bool(val_failed_tasks)

            m_meta = self._load_mission_meta(mission)
            fluid_loop = m_meta.get("fluid_loop")
            if not isinstance(fluid_loop, dict):
                fluid_loop = {
                    "enabled": True,
                    "iteration": 1,
                    "max_iterations": self.MAX_FLUID_ITERATIONS,
                    "history": [],
                }
            iteration = int(fluid_loop.get("iteration", 1))
            max_iter = int(fluid_loop.get("max_iterations", self.MAX_FLUID_ITERATIONS))
            history = list(fluid_loop.get("history") or [])

            if has_gate_failure:
                err_list = list(ver_report.get("errors") or [])
                for ft in val_failed_tasks:
                    err_list.append(f"Validation task '{ft.title}' failed")
                    ft.status = TaskStatus.COMPLETED  # Mark handled by fluid self-healing iteration
                err_summary = "; ".join(err_list) or "Verification check failed"
                history.append({
                    "iteration": iteration,
                    "status": "FAILED",
                    "errors": err_list,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                fluid_loop["history"] = history

                if iteration < max_iter:
                    next_iter = iteration + 1
                    fluid_loop["iteration"] = next_iter
                    fluid_loop["status"] = "SELF_HEALING"
                    m_meta["fluid_loop"] = fluid_loop
                    self._save_mission_meta(mission, m_meta)

                    scope_triage = self._get_scope_triage(db, mission)
                    fix_role = self._select_primary_coding_role(scope_triage)
                    if any(any(ext in e.lower() for ext in (".tsx:", ".jsx:", ".css:", ".html:", "node --test")) for e in err_list):
                        fix_role = "FRONTEND_ENGINEER"
                    elif any(".sql:" in e.lower() for e in err_list):
                        fix_role = "DATABASE_ENGINEER"

                    fix_task = Task(
                        id=str(uuid.uuid4()),
                        mission_id=mission.id,
                        project_id=mission.project_id,
                        created_by=mission.created_by,
                        title=f"Fluid Loop Iteration #{next_iter}: Fix Verification & Test Failures",
                        description=(
                            f"FLUID VERIFICATION & TEST RUNNER FAILED (Iteration #{iteration}): {err_summary}. "
                            "Inspect the failing files/tests and fix the root cause in the sandbox using repository.edit or repository.write."
                        ),
                        status=TaskStatus.PENDING,
                        phase=WorkflowPhase.IMPLEMENTATION,
                        is_correction=True,
                        metadata_json=json.dumps({
                            "agent_role": fix_role,
                            "fluid_iteration": next_iter,
                            "verification_errors": err_list,
                        }),
                        dependencies_json="[]",
                    )
                    db.add(fix_task)
                    mission.phase = WorkflowPhase.IMPLEMENTATION
                    mission.current_stage = f"Fluid Loop Iteration #{next_iter}: Self-Healing Fix"
                    mission.progress = 55
                    await db.commit()
                    await self._broadcast("fluid_loop_iteration", {
                        "mission_id": mission.id,
                        "iteration": next_iter,
                        "max_iterations": max_iter,
                        "errors": err_list,
                        "fix_task_id": fix_task.id,
                    })
                    return "CONTINUE"
                else:
                    fluid_loop["status"] = "EXHAUSTED"
                    m_meta["fluid_loop"] = fluid_loop
                    self._save_mission_meta(mission, m_meta)
                    mission.current_stage = f"Verification failed after {iteration} fluid iterations: {err_summary[:160]}"
                    await db.commit()
                    return "ERROR"

            # 3. Check if any new HUMAN_STEERING_OVERRIDE or correction tasks arrived in IMPLEMENTATION/any phase
            pending_corrections = (
                await db.execute(
                    select(Task).where(
                        Task.mission_id == mission.id,
                        Task.status == TaskStatus.PENDING,
                    )
                )
            ).scalars().all()
            if pending_corrections:
                for pc in pending_corrections:
                    pc.phase = WorkflowPhase.IMPLEMENTATION
                mission.phase = WorkflowPhase.IMPLEMENTATION
                mission.current_stage = "Fluid Loop: Applying Live Steering"
                mission.progress = 60
                await db.commit()
                return "CONTINUE"

            # 4. Fluid Verification & Test Gate PASSED! Complete the sprint immediately without ceremonial waterfall stages.
            history.append({
                "iteration": iteration,
                "status": "PASSED",
                "verified_files": ver_report.get("verified_files", []),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            fluid_loop["history"] = history
            fluid_loop["status"] = "PASSED"
            fluid_loop["verification"] = ver_report
            m_meta["fluid_loop"] = fluid_loop
            m_meta["persistent_state"] = {
                "last_completed_phase": WorkflowPhase.VALIDATION.value,
                "current_phase": "COMPLETED",
                "active_branch": mission.active_branch,
                "pre_review_gate": ver_report.get("status", "PASSED"),
                "fluid_iterations": iteration,
                "checkpoint_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save_mission_meta(mission, m_meta)

            deliverable_res = {}
            try:
                from backend.services.sandbox_review import persist_mission_deliverable
                deliverable_res = await persist_mission_deliverable(db, mission, push_and_open_pr=True)
            except Exception:
                deliverable_res = {}

            mission.phase = WorkflowPhase.DELIVERY_REVIEW
            mission.status = MissionStatus.COMPLETED
            mission.progress = 100
            mission.completed_at = datetime.now(timezone.utc)
            mission.current_stage = "Mission Completed (Fluid Loop Verified)"
            await db.commit()

            try:
                from backend.api.v1.missions import dispatch_outbound_webhook_notification
                await dispatch_outbound_webhook_notification(
                    db, mission, "MISSION_COMPLETED", extra=deliverable_res
                )
            except Exception:
                pass
            await self._broadcast("mission_complete", {
                "message": "Mission completed and verified in fluid loop!",
                "fluid_iterations": iteration,
                "pr": deliverable_res.get("pr"),
                "changeset": deliverable_res.get("changeset"),
            })
            try:
                from backend.services.billing_policy import CreditPolicy
                await CreditPolicy.settle_reservation(db, mission.organization_id, mission.id)
            except Exception:
                await db.rollback()
            await db.commit()
            return "HALT"

        tasks_to_spawn = [{
            "title": "QA Testing", 
            "agent": "QA_ENGINEER",
            "description": "Execute the test suite and perform quality assurance to ensure the implementation meets the requirements."
        }]
        
        # Red-Team / Specialist Critic insertion for High-Risk
        risk_profile = {}
        if mission.risk_profile_json:
            try:
                risk_profile = json.loads(mission.risk_profile_json)
            except:
                pass
                
        high_risk = risk_profile.get("overall_risk") in ["HIGH", "CRITICAL"]
        if high_risk:
            tasks_to_spawn.append({
                "title": "Red-Team Specialist Critic", 
                "agent": "SECURITY_ENGINEER", # Or a specific CRITIC agent
                "description": "Perform an adversarial red-team review of the implementation to identify high-risk logic flaws."
            })

        # KYROS PARALLEL VERIFICATION WAVE (Live LLM Mode):
        # Concurrently execute read-only verification agents (QA_ENGINEER + SECURITY_ENGINEER + CODE_REVIEWER)
        # against the completed implementation sandbox in a single parallel wave (~5s instead of ~18s).
        from backend.core.config import settings
        provider_name = (settings.LLM_PROVIDER or os.environ.get("LLM_PROVIDER") or "mock").lower()
        if provider_name != "mock" and not high_risk:
            existing_val = (
                await db.execute(select(Task).where(Task.mission_id == mission.id, Task.phase == WorkflowPhase.VALIDATION))
            ).scalars().all()
            if not existing_val:
                scope_triage = self._get_scope_triage(db, mission)
                wave_specs = [
                    (WorkflowPhase.VALIDATION, "QA Testing", "QA_ENGINEER", "Execute the test suite and perform quality assurance to ensure the implementation meets the requirements."),
                ]
                if scope_triage.get("requires_security_audit", True):
                    wave_specs.append((
                        WorkflowPhase.SECURITY,
                        "Security Audit",
                        "SECURITY_ENGINEER",
                        "Audit the implementation for security vulnerabilities, injection flaws, and compliance issues.",
                    ))
                wave_specs.append((
                    WorkflowPhase.CODE_REVIEW,
                    "Code Review",
                    "CODE_REVIEWER",
                    "Review the code for maintainability, style, and architectural alignment before final delivery.",
                ))

                wave_ids = []
                pre_spawned_downstream_ids = []
                for ph, t_title, t_agent, t_desc in wave_specs:
                    tid = str(uuid.uuid4())
                    t_obj = Task(
                        id=tid,
                        mission_id=mission.id,
                        project_id=mission.project_id,
                        created_by=mission.created_by,
                        title=t_title,
                        description=t_desc,
                        status=TaskStatus.IN_PROGRESS,
                        phase=ph,
                        metadata_json=json.dumps({"agent_role": t_agent}),
                        dependencies_json="[]",
                    )
                    db.add(t_obj)
                    wave_ids.append(tid)
                    if ph != WorkflowPhase.VALIDATION:
                        pre_spawned_downstream_ids.append(tid)
                await db.commit()

                await asyncio.gather(*(self.execute_task(tid) for tid in wave_ids))

                # Refresh session state and check if QA created any correction tasks
                await db.rollback()
                mission = await db.get(Mission, self.mission_id)
                val_tasks = (
                    await db.execute(select(Task).where(Task.mission_id == mission.id, Task.phase == WorkflowPhase.VALIDATION))
                ).scalars().all()
                has_qa_issue = any(
                    t.is_correction or t.status in (TaskStatus.FAILED, TaskStatus.PENDING, TaskStatus.BLOCKED)
                    for t in val_tasks
                )
                if has_qa_issue and pre_spawned_downstream_ids:
                    for dtid in pre_spawned_downstream_ids:
                        dt_obj = await db.get(Task, dtid)
                        if dt_obj:
                            await db.delete(dt_obj)
                    await db.commit()

        return await self._execute_phase_tasks(
            db, mission, WorkflowPhase.VALIDATION,
            next_phase=WorkflowPhase.SECURITY,
            stage_name="Security Review",
            tasks_to_spawn=tasks_to_spawn
        )

    async def _handle_security(self, db: AsyncSession, mission: Mission) -> str:
        scope_triage = self._get_scope_triage(db, mission)
        risk_profile = {}
        if mission.risk_profile_json:
            try:
                risk_profile = json.loads(mission.risk_profile_json)
            except Exception:
                pass
        high_risk = risk_profile.get("overall_risk") in ("HIGH", "CRITICAL")
        security_tasks = []
        if scope_triage.get("requires_security_audit", True) or high_risk:
            security_tasks.append({
                "title": "Security Audit",
                "agent": "SECURITY_ENGINEER",
                "description": "Audit the implementation for security vulnerabilities, injection flaws, and compliance issues.",
            })
        return await self._execute_phase_tasks(
            db, mission, WorkflowPhase.SECURITY,
            next_phase=WorkflowPhase.CODE_REVIEW,
            stage_name="Code Review",
            tasks_to_spawn=security_tasks
        )
        
    async def _handle_code_review(self, db: AsyncSession, mission: Mission) -> str:
        return await self._execute_phase_tasks(
            db, mission, WorkflowPhase.CODE_REVIEW,
            next_phase=WorkflowPhase.DELIVERY_REVIEW,
            stage_name="Awaiting Delivery Approval",
            tasks_to_spawn=[
                {
                    "title": "Code Review", 
                    "agent": "CODE_REVIEWER",
                    "description": "Review the code for maintainability, style, and architectural alignment before final delivery."
                }
            ]
        )
        
    async def _handle_deployment(self, db: AsyncSession, mission: Mission) -> str:
        scope_triage = self._get_scope_triage(db, mission)
        deployment_tasks = []
        if scope_triage.get("requires_devops", False):
            deployment_tasks.append({
                "title": "Infrastructure Updates",
                "agent": "DEVOPS_ENGINEER",
                "description": "Prepare Dockerfiles, update CI/CD scripts, Helm charts, and environment secret configs.",
            })
        if scope_triage.get("requires_release_manager", True):
            deployment_tasks.append({
                "title": "Prepare Release",
                "agent": "RELEASE_MANAGER",
                "description": "Coordinate the deployment rollout plan and prepare the final artifacts.",
            })
        return await self._execute_phase_tasks(
            db, mission, WorkflowPhase.DEPLOYMENT,
            next_phase=WorkflowPhase.LEARNING,
            stage_name="Learning & Documentation",
            tasks_to_spawn=deployment_tasks
        )
        
    async def _handle_learning(self, db: AsyncSession, mission: Mission) -> str:
        scope_triage = self._get_scope_triage(db, mission)
        learning_tasks = []
        if scope_triage.get("requires_docs", True):
            learning_tasks.append({
                "title": "Documentation",
                "agent": "DOCUMENTATION_ENGINEER",
                "description": "Generate OpenAPI specifications, update README.md, and create changelogs based on the finalized PR.",
            })
        learning_tasks.append({
            "title": "Post-Mortem & Memory Sync",
            "agent": "SOLUTION_ARCHITECT",
            "description": "Document lessons learned and update the project memory graph.",
        })
        return await self._execute_phase_tasks(
            db, mission, WorkflowPhase.LEARNING,
            next_phase=None,
            stage_name="Mission Completed",
            tasks_to_spawn=learning_tasks
        )

    # =========================================================================
    # TASK EXECUTION LOGIC
    # =========================================================================

    async def _execute_phase_tasks(self, db: AsyncSession, mission: Mission, current_phase: WorkflowPhase, next_phase: WorkflowPhase, stage_name: str, tasks_to_spawn: list) -> str:
        # Validate phase team capabilities
        from backend.services.intelligence.adaptive_planning import AdaptivePlanner
        planner = AdaptivePlanner(db)
        current_team = json.loads(mission.team_composition_json or "[]")
        validated_team = planner.validate_phase_team(current_phase.value, current_team)
        if set(validated_team) != set(current_team):
            if os.environ.get("KOBITS_VERBOSE") == "1":
                print(f"Safety Rule Triggered: Planner proposed invalid team {current_team} for {current_phase.value}. Falling back to {validated_team}.")
            mission.team_composition_json = json.dumps(validated_team)
            await db.commit()
            
        # 1. Idempotently spawn tasks if they don't exist
        if tasks_to_spawn:
            stmt = select(Task).where(Task.mission_id == mission.id, Task.phase == current_phase)
            existing_tasks = (await db.execute(stmt)).scalars().all()


            if not existing_tasks:
                prev_task_id = None
                for spec in tasks_to_spawn:
                    t_id = str(uuid.uuid4())
                    t = Task(
                        id=t_id,
                        mission_id=mission.id,
                        project_id=mission.project_id,
                        created_by=mission.created_by,
                        title=spec["title"],
                        description=spec.get("description"),
                        status=TaskStatus.PENDING,
                        phase=current_phase,
                        metadata_json=json.dumps({"agent_role": spec["agent"]}),
                        dependencies_json=json.dumps([prev_task_id]) if prev_task_id else "[]"
                    )
                    db.add(t)
                    prev_task_id = t_id
                await db.commit()
                
        # 2. Get all tasks for this phase
        stmt = select(Task).where(Task.mission_id == mission.id, Task.phase == current_phase)
        tasks = (await db.execute(stmt)).scalars().all()
        if os.environ.get("KOBITS_VERBOSE") == "1":
            print(f"Found {len(tasks)} tasks for phase {current_phase.value}")
        
        pending = [t for t in tasks if t.status in [TaskStatus.PENDING, TaskStatus.IN_PROGRESS]]
        failed = [t for t in tasks if t.status == TaskStatus.FAILED]
        blocked = [t for t in tasks if t.status == TaskStatus.BLOCKED]
        
        if failed:
            print(f"Phase {current_phase.value} failed due to failed tasks.")
            return "ERROR"
            
        if blocked:
            print(f"Phase {current_phase.value} is halted awaiting human intervention (BLOCKED task).")
            return "HALT"
            
        if not pending:
            # In waterfall mode, run the legacy one-shot Pre-Review Gate before leaving IMPLEMENTATION.
            # In Fluid Single-Loop mode (default), _handle_validation runs the multi-iteration
            # Verification & Test-Runner self-healing loop (IMPLEMENTATION <-> VALIDATION).
            pre_review_report = None
            if not self._is_fluid_mode(mission) and current_phase == WorkflowPhase.IMPLEMENTATION and mission.active_branch:
                from backend.services.sandbox_manager import SandboxManager
                sb_session = SandboxManager.get_session_by_branch(mission.active_branch)
                if sb_session:
                    scope_triage = self._get_scope_triage(db, mission)
                    t_files = scope_triage.get("target_files", []) if isinstance(scope_triage, dict) else []
                    pre_review_report = self._verify_sandbox_code(sb_session.sandbox_dir, t_files)
                    already_fixed = any(
                        (t.title or "").startswith("Pre-Review Gate Fix:") for t in tasks
                    )
                    if pre_review_report.get("status") == "FAILED" and not already_fixed:
                        err_list = pre_review_report.get("errors") or []
                        err_summary = "; ".join(err_list)
                        domains = scope_triage.get("domains", []) if isinstance(scope_triage, dict) else []
                        fix_role = "BACKEND_ENGINEER"
                        if any(any(ext in e.lower() for ext in (".tsx:", ".jsx:", ".css:", ".html:")) for e in err_list) or (
                            domains == ["frontend"]
                        ):
                            fix_role = "FRONTEND_ENGINEER"
                        elif any(".sql:" in e.lower() for e in err_list) or (domains == ["database"]):
                            fix_role = "DATABASE_ENGINEER"
                        fix_task = Task(
                            id=str(uuid.uuid4()),
                            mission_id=mission.id,
                            project_id=mission.project_id,
                            created_by=mission.created_by,
                            title="Pre-Review Gate Fix: Resolve Compiler/Import/Test Errors",
                            description=(
                                f"AUTOMATED PRE-REVIEW GATE FAILED: {err_summary}. "
                                "Fix the syntax, import, or test errors in the modified files using repository.edit (or repository.write)."
                            ),
                            status=TaskStatus.PENDING,
                            phase=WorkflowPhase.IMPLEMENTATION,
                            is_correction=True,
                            metadata_json=json.dumps({
                                "agent_role": fix_role,
                                "pre_review_gate_errors": err_list,
                            }),
                            dependencies_json="[]",
                        )
                        db.add(fix_task)
                        await db.commit()
                        return "CONTINUE"

            # All done! Advance phase and persist durable mission checkpoint
            phase_order = [
                WorkflowPhase.INTAKE,
                WorkflowPhase.ANALYSIS,
                WorkflowPhase.PLANNING,
                WorkflowPhase.ARCHITECTURE_REVIEW,
                WorkflowPhase.IMPLEMENTATION,
                WorkflowPhase.VALIDATION,
                WorkflowPhase.SECURITY,
                WorkflowPhase.CODE_REVIEW,
                WorkflowPhase.DELIVERY_REVIEW,
                WorkflowPhase.DEPLOYMENT,
                WorkflowPhase.LEARNING,
            ]
            if next_phase is not None:
                mission.phase = next_phase
                if next_phase in phase_order:
                    mission.progress = int(round((phase_order.index(next_phase) / len(phase_order)) * 100))
            mission.current_stage = stage_name

            # Persist Mission State Checkpoint in risk_profile_json / metadata_json
            m_meta = self._load_mission_meta(mission)
            m_meta["persistent_state"] = {
                "last_completed_phase": current_phase.value,
                "current_phase": next_phase.value if next_phase else "COMPLETED",
                "active_branch": mission.active_branch,
                "completed_task_ids": [t.id for t in tasks if t.status == TaskStatus.COMPLETED],
                "pre_review_gate": pre_review_report.get("status") if pre_review_report else m_meta.get("persistent_state", {}).get("pre_review_gate"),
                "checkpoint_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save_mission_meta(mission, m_meta)

            # Persist durable Changeset + PullRequest when IMPLEMENTATION completes
            if current_phase == WorkflowPhase.IMPLEMENTATION:
                try:
                    from backend.services.sandbox_review import persist_mission_deliverable
                    await persist_mission_deliverable(db, mission, push_and_open_pr=True)
                except Exception:
                    pass

            if next_phase is None:
                mission.status = MissionStatus.COMPLETED
                mission.progress = 100
                mission.completed_at = datetime.now(timezone.utc)
                deliverable_res = {}
                try:
                    from backend.services.sandbox_review import persist_mission_deliverable
                    deliverable_res = await persist_mission_deliverable(db, mission, push_and_open_pr=True)
                except Exception:
                    deliverable_res = {}
                try:
                    from backend.api.v1.missions import dispatch_outbound_webhook_notification
                    await dispatch_outbound_webhook_notification(
                        db, mission, "MISSION_COMPLETED", extra=deliverable_res
                    )
                except Exception:
                    pass
                await self._broadcast("mission_complete", {
                    "message": "Mission completed successfully!",
                    "pr": deliverable_res.get("pr"),
                    "changeset": deliverable_res.get("changeset"),
                })
                try:
                    from backend.services.billing_policy import CreditPolicy
                    await CreditPolicy.settle_reservation(db, mission.organization_id, mission.id)
                except Exception as e:
                    print(f"Failed to settle reservation: {e}")
                    await db.rollback()
            else:
                await self._broadcast("phase_complete", {
                    "phase": current_phase.value,
                    "next_phase": next_phase.value if next_phase else None,
                    "stage": stage_name
                })
            await db.commit()
            return "CONTINUE"
            
        # 3. Find ready tasks
        ready_tasks = []
        for t in tasks:
            if t.status == TaskStatus.PENDING:
                deps = json.loads(t.dependencies_json or "[]")
                deps_met = True
                for dep_id in deps:
                    dep_task = next((dt for dt in tasks if dt.id == dep_id or dt.title == dep_id), None)
                    if dep_task and dep_task.status != TaskStatus.COMPLETED:
                        deps_met = False
                        break
                if deps_met:
                    ready_tasks.append(t)
                    
        if not ready_tasks:
            in_progress = [t for t in tasks if t.status == TaskStatus.IN_PROGRESS]
            if not in_progress:
                print(f"Deadlock in phase {current_phase.value}")
                return "ERROR"
            
            # GAP 3: CRASH RECOVERY
            # Since this is a single execution loop holding the mission lock, 
            # ANY task that is currently IN_PROGRESS was left over from a previous server crash.
            # We resume them by treating them as ready tasks!
            print(f"CRASH RECOVERY: Resuming {len(in_progress)} IN_PROGRESS tasks.")
            ready_tasks = in_progress
            
        # 4. Execute ready tasks
        for t in ready_tasks:
            t.status = TaskStatus.IN_PROGRESS
            await db.commit()
            
        from backend.core.config import settings
        provider_name = (settings.LLM_PROVIDER or os.environ.get("LLM_PROVIDER") or "mock").lower()
        if len(ready_tasks) > 1 and provider_name != "mock":
            await asyncio.gather(*(self.execute_task(t.id) for t in ready_tasks))
        else:
            for t in ready_tasks:
                await self.execute_task(t.id)
        
        # After executing, the state machine will loop again and re-evaluate this phase
        return "CONTINUE"


    async def execute_task(self, task_id: str):
        async with AsyncSessionLocal() as db:
            task = await db.get(Task, task_id)
            if not task: return
            
            # Idempotency: Don't execute completed or failed tasks again
            if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                return

            mission = await db.get(Mission, task.mission_id)
            if not mission or mission.status != MissionStatus.ACTIVE:
                return

            metadata = json.loads(task.metadata_json or "{}")
            agent_role_str = metadata.get("agent_role", "BACKEND_ENGINEER")
            
            try:
                agent_type = AgentType(agent_role_str)
            except ValueError:
                agent_type = AgentType.BACKEND_ENGINEER

            # Crash Recovery: Fail any existing non-terminal runs for this task to avoid orphans
            stmt_orphans = select(AgentRun).where(
                AgentRun.task_id == task.id,
                AgentRun.status.in_([AgentRunStatus.QUEUED, AgentRunStatus.RUNNING])
            )
            orphaned_runs = (await db.execute(stmt_orphans)).scalars().all()
            for orun in orphaned_runs:
                orun.status = AgentRunStatus.FAILED
                orun.error = "Orphaned by system restart/crash"
                orun.completed_at = datetime.now(timezone.utc)

            stmt = select(Agent).where(Agent.type == agent_type)
            agent_record = (await db.execute(stmt)).scalars().first()
            agent_id = agent_record.id if agent_record else "system"

            run = AgentRun(
                agent_id=agent_id,
                project_id=task.project_id,
                task_id=task.id,
                organization_id=self.organization_id,
                status=AgentRunStatus.QUEUED,
                started_at=datetime.now(timezone.utc)
            )
            async with self._db_lock:
                db.add(run)
                await db.commit()
                await db.refresh(run)

            # Load upstream artifacts using Bounded Context (pinned foundational + sliding window)
            stmt_dep = (
                select(AgentRun, Task)
                .join(Task)
                .where(Task.mission_id == mission.id, AgentRun.status == AgentRunStatus.COMPLETED)
                .order_by(AgentRun.created_at.asc())
            )
            dep_runs = (await db.execute(stmt_dep)).all()
            upstream_artifacts = self._build_bounded_upstream_context(dep_runs)
            
            # Setup sandbox branch if not set (for IMPLEMENTATION tasks)
            if not mission.active_branch:
                from backend.services.sandbox_manager import SandboxManager
                branch_name = f"kobits/mission/{self.mission_id[:8]}"
                
                # Respect KOBITS_TARGET_WORKSPACE when targeting a local or cloned GitHub repository
                project_root = os.environ.get("KOBITS_TARGET_WORKSPACE") or os.getcwd()
                
                created = False
                
                # Support Multi-Repository Choreography
                from backend.models.mission import MissionRepository
                from backend.models.github import Repository
                
                stmt = select(MissionRepository).where(MissionRepository.mission_id == mission.id)
                res = await db.execute(stmt)
                m_repos = res.scalars().all()
                
                if m_repos:
                    # Multi-repo mode
                    repos_payload = []
                    for mr in m_repos:
                        repo = await db.get(Repository, mr.repository_id)
                        if repo and repo.clone_url:
                            repos_payload.append({
                                "clone_url": repo.clone_url,
                                "mount_path": mr.mount_path,
                                "base_commit_sha": mission.base_commit_sha
                            })
                    if repos_payload:
                        SandboxManager.create_sandbox(
                            project_root="",
                            branch_name=branch_name,
                            repositories=repos_payload
                        )
                        created = True
                elif getattr(mission, "repository_id", None):
                    # Legacy single-repo mode
                    repo = await db.get(Repository, mission.repository_id)
                    if repo and repo.clone_url:
                        SandboxManager.create_sandbox(
                            project_root="",
                            clone_url=repo.clone_url,
                            base_commit_sha=mission.base_commit_sha,
                            branch_name=branch_name
                        )
                        created = True
                        
                if not created:
                    # LOCAL SANDBOX - Fallback to local project dir
                    SandboxManager.create_sandbox(
                        project_root=project_root,
                        branch_name=branch_name
                    )
                    
                mission.active_branch = branch_name
                async with self._db_lock:
                    await db.commit()

                # Auto-index repository AST chunks & symbol graph when sandbox attaches
                try:
                    from backend.services.indexer import RepositoryIndexer
                    from backend.services.graph_indexer import GraphIndexerService
                    sb_sess = SandboxManager.get_session_by_branch(branch_name)
                    target_index_dir = sb_sess.sandbox_dir if sb_sess else project_root
                    if task.project_id and target_index_dir and os.path.isdir(target_index_dir):
                        async with self._db_lock:
                            await RepositoryIndexer.index_repository(db, task.project_id, target_index_dir)
                            await GraphIndexerService.index_repository(db, task.project_id, target_index_dir)
                except Exception:
                    pass

            task_meta = {}
            if task.metadata_json:
                try:
                    task_meta = json.loads(task.metadata_json)
                except:
                    pass

            mission_meta = self._load_mission_meta(mission)
            scope_triage = mission_meta.get("scope_triage") or AdaptivePlanner(db).classify_scope(
                mission.objective or "", mission.title or ""
            )
            complexity_map = {"trivial": "LOW", "standard": "DEFAULT", "complex": "HIGH"}

            adv_ctx = "Standard Context"
            try:
                from backend.services.context_builder import build_agent_context
                from backend.models.project import Project
                from backend.models.memory import CodeDocument
                from backend.services.indexer import RepositoryIndexer
                from backend.services.graph_indexer import GraphIndexerService
                from backend.services.sandbox_manager import SandboxManager
                from types import SimpleNamespace
                async with self._db_lock:
                    if task.project_id:
                        existing_doc = (
                            await db.execute(
                                select(CodeDocument.id).where(CodeDocument.project_id == task.project_id).limit(1)
                            )
                        ).scalar_one_or_none()
                        if not existing_doc:
                            sb_sess = SandboxManager.get_session_by_branch(mission.active_branch) if mission.active_branch else None
                            warm_dir = sb_sess.sandbox_dir if sb_sess else os.getcwd()
                            if warm_dir and os.path.isdir(warm_dir):
                                await RepositoryIndexer.index_repository(db, task.project_id, warm_dir)
                                await GraphIndexerService.index_repository(db, task.project_id, warm_dir)
                    proj_record = await db.get(Project, task.project_id) if task.project_id else None
                    proj_obj = proj_record or SimpleNamespace(id=task.project_id or "default")
                    ag_obj = agent_record or SimpleNamespace(
                        id=agent_id,
                        name=agent_type.value,
                        type=agent_type,
                        system_prompt="",
                    )
                    adv_ctx = await build_agent_context(
                        db, ag_obj, proj_obj, mission, task, self.organization_id
                    )
            except Exception:
                adv_ctx = "Standard Context"

            input_data = {
                "mission_id": task.mission_id,
                "project_id": task.project_id,
                "task_title": task.title,
                "task_description": task.description,
                "mission_objective": mission.objective,
                "upstream_artifacts": upstream_artifacts,
                "advanced_intelligence_context": adv_ctx,
                "risk_profile": (mission.risk_profile_json or "")[:600],
                "complexity": complexity_map.get(scope_triage.get("complexity", "standard"), "DEFAULT"),
                "scope_triage": scope_triage,
                "human_override_comment": task_meta.get("human_override_comment")
            }
            
            read_only_roles = {
                "QA_ENGINEER", "SECURITY_ENGINEER", "CODE_REVIEWER",
                "TECHNICAL_LEAD", "PRODUCT_MANAGER", "SOLUTION_ARCHITECT",
                "RELEASE_MANAGER"
            }
            is_read_only_task = (
                agent_role_str in read_only_roles
                or task.title in ("Post-Mortem & Memory Sync", "Planning & Architecture", "Task Decomposition", "Release & Deployment")
            ) and not task.is_correction

            task_sandbox_session_id = None
            if mission.active_branch:
                from backend.services.sandbox_manager import SandboxManager
                session = SandboxManager.get_session_by_branch(mission.active_branch)
                if not session:
                    session = SandboxManager.create_sandbox(
                        project_root=os.getcwd(),
                        branch_name=mission.active_branch
                    )
                if session:
                    if is_read_only_task:
                        input_data["sandbox_session_id"] = session.session_id
                        input_data["sandbox_dir"] = session.sandbox_dir
                        if agent_role_str in ("QA_ENGINEER", "SECURITY_ENGINEER", "CODE_REVIEWER"):
                            verification_report = self._verify_sandbox_code(
                                session.sandbox_dir,
                                scope_triage.get("target_files", []) if isinstance(scope_triage, dict) else []
                            )
                            input_data["sandbox_verification"] = verification_report
                            task_meta["sandbox_verification"] = verification_report
                            task.metadata_json = json.dumps(task_meta)
                            if agent_role_str == "QA_ENGINEER":
                                await self._broadcast("sandbox_verification", {
                                    "task_id": task.id,
                                    "status": verification_report["status"],
                                    "changed_files": verification_report["changed_files"],
                                    "verified_py_files": verification_report["verified_py_files"],
                                    "errors": verification_report["errors"],
                                    "message": (
                                        f"✓ AST & Syntax Smoke Check PASSED ({len(verification_report['verified_py_files'])} Python files verified)"
                                        if verification_report["status"] == "PASSED"
                                        else f"✗ AST & Syntax Smoke Check FAILED: {'; '.join(verification_report['errors'])}"
                                    )
                                })
                    else:
                        task_session = SandboxManager.create_task_worktree(session.session_id, task.id)
                        task_sandbox_session_id = task_session.session_id
                        input_data["sandbox_session_id"] = task_session.session_id
                        input_data["sandbox_dir"] = task_session.sandbox_dir
                else:
                    # CRITICAL SAFETY RULE: Do not execute task without isolation
                    task.status = TaskStatus.FAILED
                    run.status = AgentRunStatus.FAILED
                    run.completed_at = datetime.now(timezone.utc)
                    run.error = "CRITICAL RECOVERY ERROR: Sandbox context lost. Execution blocked to prevent unisolated writes on the host file system."
                    async with self._db_lock:
                        await db.commit()
                    
                    await self._broadcast("task_fail", {
                        "task_id": task.id,
                        "task_title": task.title,
                        "message": "Task blocked due to missing sandbox isolation context after restart."
                    })
                    return False

            if task.is_correction:
                input_data["is_correction"] = True
                
            run.input_text = json.dumps(input_data)
            async with self._db_lock:
                await db.commit()

            await self._broadcast("task_start", {
                "task_id": task.id,
                "task_title": task.title,
                "agent": agent_role_str,
                "message": f"Agent {agent_role_str} starting: {task.title}"
            })

            try:
                result = await execute_agent_run(run, agent_type, input_data, db=db)
                run.completed_at = datetime.now(timezone.utc)
                from backend.models.agent import classify_model_result, ResultClassification
                classification, run_status, classification_reason = classify_model_result(result)
                run.status = run_status
                async with self._db_lock:
                    await db.commit()
                
                # Check for sandbox changes
                if mission.active_branch and task_sandbox_session_id:
                    from backend.services.sandbox_manager import SandboxManager
                    from backend.models.github import Changeset, ChangesetStatus
                    task_session = SandboxManager.get_session(task_sandbox_session_id)
                    mission_session = SandboxManager.get_session_by_branch(mission.active_branch)
                    if task_session and mission_session:
                        diff_data = SandboxManager.get_diff(task_session.session_id)
                        if diff_data.get("diff") or diff_data.get("files_changed"):
                            commit_msg = f"Task {task.title} (Agent: {run.agent_id})"
                            SandboxManager.commit_changes(task_session.session_id, commit_msg)
                            merge_res = SandboxManager.merge_task_worktree(mission_session.session_id, task_session.session_id)
                            if "error" not in merge_res:
                                cs = Changeset(
                                    id=str(uuid.uuid4()),
                                    mission_id=task.mission_id,
                                    task_id=task.id,
                                    agent_id=run.agent_id,
                                    repository_id=mission.repository_id,
                                    branch=mission.active_branch,
                                    base_commit_sha=mission.base_commit_sha,
                                    head_commit_sha=merge_res.get("commit_sha"),
                                    files_changed=len(task_session.files_changed),
                                    diff_summary=diff_data.get("summary", ""),
                                    status=ChangesetStatus.COMMITTED
                                )
                                async with self._db_lock:
                                    db.add(cs)
                                    await db.commit()
                                # Incrementally re-index modified files and refresh code graph in the mission sandbox
                                try:
                                    from backend.services.indexer import RepositoryIndexer
                                    from backend.services.graph_indexer import GraphIndexerService
                                    if task.project_id and task_session.files_changed:
                                        async with self._db_lock:
                                            await RepositoryIndexer.index_repository(
                                                db,
                                                task.project_id,
                                                mission_session.sandbox_dir,
                                                only_files=list(task_session.files_changed),
                                            )
                                            await GraphIndexerService.index_repository(
                                                db,
                                                task.project_id,
                                                mission_session.sandbox_dir,
                                            )
                                except Exception:
                                    pass

                async with self._db_lock:
                    decision = await OrchestratorDecision.evaluate_task_result(db, task, run)
                
                if decision["action"] == "CREATE_CORRECTION_TASK":
                    task.status = TaskStatus.COMPLETED 
                    reval_id = decision.get("revalidation_task_id")
                    if reval_id:
                        stmt_downstream = select(Task).where(Task.mission_id == task.mission_id)
                        all_t = (await db.execute(stmt_downstream)).scalars().all()
                        for dt in all_t:
                            d_deps = json.loads(dt.dependencies_json or "[]")
                            if task.id in d_deps:
                                d_deps.remove(task.id)
                                d_deps.append(reval_id)
                                dt.dependencies_json = json.dumps(d_deps)
                    async with self._db_lock:
                        await db.commit()
                elif decision["action"] == "ESCALATE":
                    task.status = TaskStatus.FAILED
                elif decision["action"] == "BLOCK":
                    task.status = TaskStatus.FAILED
                elif decision["action"] == "INTERRUPT":
                    task.status = TaskStatus.BLOCKED
                    mission.status = MissionStatus.BLOCKED
                    await self._broadcast("task_blocked", {
                        "task_id": task.id,
                        "task_title": task.title,
                        "message": "🚨 HUMAN INTERVENTION REQUIRED: " + decision.get("reason", "Pipeline blocked.")
                    })
                    async with self._db_lock:
                        await db.commit()
                else:
                    if run.status == AgentRunStatus.FAILED:
                        task.status = TaskStatus.FAILED
                    else:
                        # INTELLIGENCE LAYER: Policy Engine Evidence Validation
                        policy_engine = PolicyEngine(db)
                        contract = TaskContract(
                            task_id=task.id,
                            success_criteria_json=json.dumps([{"type": "generic", "description": "Code compiles"}]),
                            failure_criteria_json=json.dumps([])
                        )
                        # Evidence-First Completion: Agent MUST provide actual artifacts/evidence
                        actual_evidence = []
                        if classification in (ResultClassification.COMPLETED, ResultClassification.COMPLETED_WITH_INFERRED_STATUS):
                            if isinstance(result, dict) and "artifacts" in result:
                                if isinstance(result["artifacts"], dict):
                                    for k, v in result["artifacts"].items():
                                        actual_evidence.append({"type": "artifact", "name": k, "value": str(v)})
                                elif isinstance(result["artifacts"], list):
                                    for idx, item in enumerate(result["artifacts"]):
                                        if isinstance(item, dict) and "type" in item:
                                            actual_evidence.append({"type": "artifact", "name": item.get("type", str(idx)), "value": str(item.get("content", item))})
                                        else:
                                            actual_evidence.append({"type": "artifact", "name": f"item_{idx}", "value": str(item)})
                                            
                            if isinstance(result, dict) and "findings" in result and isinstance(result["findings"], list):
                                for f in result["findings"]:
                                    actual_evidence.append({"type": "finding", "value": str(f)})
                        
                        is_valid = policy_engine.evaluate_task_contract(contract, actual_evidence)
                        
                        # High-Risk tasks require strict evidence
                        risk_profile = {}
                        if mission.risk_profile_json:
                            try:
                                risk_profile = json.loads(mission.risk_profile_json)
                            except: pass
                        if risk_profile.get("overall_risk") in ["HIGH", "CRITICAL"] and len(actual_evidence) == 0:
                            is_valid = False
                            
                        if os.environ.get("KOBITS_VERBOSE"):
                            print(f"[DEBUG] Task {task.title} (Agent: {agent_role_str}) -> classification={classification}, run_status={run_status}, result={result}, evidence={actual_evidence}, is_valid={is_valid}")
                            
                        if is_valid:
                            task.status = TaskStatus.COMPLETED
                            await self._broadcast("task_complete", {
                                "task_id": task.id,
                                "task_title": task.title,
                                "agent": agent_role_str,
                                "message": f"✓ {task.title} completed by {agent_role_str}"
                            })
                            
                            # --- TASK CREATION: Create dynamic IMPLEMENTATION tasks from TECHNICAL_LEAD output ---
                            if task.title == "Task Decomposition":
                                from backend.services.intelligence.task_parser import parse_and_validate_tasks
                                try:
                                    parsed_tasks, telemetry = parse_and_validate_tasks(result)
                                    prev_impl_task = None
                                    created_count = 0
                                    for pt in parsed_tasks:
                                        t_id = str(uuid.uuid4())
                                        impl_task = Task(
                                            id=t_id,
                                            mission_id=mission.id,
                                            project_id=mission.project_id,
                                            created_by=mission.created_by,
                                            title=pt.title,
                                            description=pt.description,
                                            status=TaskStatus.PENDING,
                                            phase=WorkflowPhase.IMPLEMENTATION,
                                            metadata_json=json.dumps({"agent_role": pt.agent}),
                                            dependencies_json="[]" # NO DEPENDENCIES - RUN IN PARALLEL!
                                        )
                                        db.add(impl_task)
                                        created_count += 1
                                        
                                    telemetry["CREATED_TASK_COUNT"] = created_count
                                    print(f"TELEMETRY: {telemetry}")
                                    
                                    if telemetry["PARSED_TASK_COUNT"] > 0 and telemetry["CREATED_TASK_COUNT"] == 0:
                                        raise ValueError("Parsed tasks exist but zero implementation tasks were created!")
                                        
                                except Exception as e:
                                    task.status = TaskStatus.FAILED
                                    run.error = f"Failed to normalize and create implementation tasks: {str(e)}"
                                    run.status = AgentRunStatus.FAILED
                        else:
                            task.status = TaskStatus.FAILED
                            run.error = "PolicyEngine: Task evidence did not satisfy contract (insufficient evidence provided)"
                async with self._db_lock:
                    await db.commit()

            except Exception as e:
                import traceback
                traceback.print_exc()
                async with self._db_lock:
                    await db.rollback() # Fix PendingRollbackError
                    task.status = TaskStatus.FAILED
                    run.status = AgentRunStatus.FAILED
                    run.error = str(e)
                    run.completed_at = datetime.now(timezone.utc)
                    await db.commit()
