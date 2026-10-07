import json
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
import pytest

import kobits_cli
from backend.services.intelligence.risk_analysis import RiskAnalyzer
from backend.services.mission_runtime import MissionRuntime
from backend.services.sandbox_manager import SandboxManager
from kobits_cli import (
    build_parser,
    _inspect_mission_sandbox,
    _resolve_mission_id,
)


def test_risk_analyzer_excludes_sandboxes_and_bounds_dependents():
    analyzer = RiskAnalyzer(db=None)
    impact = analyzer.analyze_change_impact(["backend/api/v1/org.py", "backend/main.py"])
    deps = impact.get("dependent_files", [])
    assert len(deps) <= 15
    for d in deps:
        assert "sandboxes" not in d.replace("\\", "/")


def test_verify_sandbox_code_ast_imports_and_pytest_gate():
    with tempfile.TemporaryDirectory() as tmpdir:
        backend_dir = os.path.join(tmpdir, "backend")
        os.makedirs(backend_dir, exist_ok=True)
        with open(os.path.join(backend_dir, "__init__.py"), "w", encoding="utf-8") as f:
            f.write("")
        good_py = os.path.join(backend_dir, "valid_mod.py")
        with open(good_py, "w", encoding="utf-8") as f:
            f.write("def hello() -> str:\n    return 'ok'\n")

        test_py = os.path.join(backend_dir, "test_valid_mod.py")
        with open(test_py, "w", encoding="utf-8") as f:
            f.write("from backend.valid_mod import hello\ndef test_hello():\n    assert hello() == 'ok'\n")

        res_ok = MissionRuntime._verify_sandbox_code(
            tmpdir, ["backend/valid_mod.py", "backend/test_valid_mod.py"]
        )
        assert res_ok["status"] == "PASSED"
        assert "backend/valid_mod.py" in res_ok["verified_py_files"]
        assert "backend.valid_mod" in res_ok["verified_imports"]
        assert res_ok["pytest_summary"]["passed"] is True
        assert res_ok["errors"] == []

        # Test broken local module import caught by Pre-Review Gate
        bad_import_py = os.path.join(backend_dir, "bad_import.py")
        with open(bad_import_py, "w", encoding="utf-8") as f:
            f.write("from backend.missing_ghost_module import Ghost\n")

        res_bad_imp = MissionRuntime._verify_sandbox_code(tmpdir, ["backend/bad_import.py"])
        assert res_bad_imp["status"] == "FAILED"
        assert any("Unresolved local module 'backend.missing_ghost_module'" in e for e in res_bad_imp["errors"])

        # Test broken syntax caught by Pre-Review Gate
        bad_py = os.path.join(backend_dir, "broken_mod.py")
        with open(bad_py, "w", encoding="utf-8") as f:
            f.write("def broken(\n")

        res_fail = MissionRuntime._verify_sandbox_code(tmpdir, ["backend/broken_mod.py"])
        assert res_fail["status"] == "FAILED"
        assert any("backend/broken_mod.py" in e for e in res_fail["errors"])


def test_native_git_worktree_sandbox():
    with tempfile.TemporaryDirectory() as src_repo, tempfile.TemporaryDirectory() as wt_parent:
        # Initialize a real Git repo in src_repo
        Path(src_repo, "service.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=src_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=src_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=src_repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=src_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Init"], cwd=src_repo, check=True, capture_output=True)

        wt_dir = os.path.join(wt_parent, "wt-sandbox-1")
        created = SandboxManager._create_git_worktree_sandbox(
            wt_dir, "kobits/mission/wttest01", source_dir=src_repo
        )
        assert created is True
        # In a true Git worktree, .git is a pointer file linking back to the main repository
        assert os.path.isfile(os.path.join(wt_dir, ".git"))
        assert os.path.isfile(os.path.join(wt_dir, "service.py"))


def test_bounded_context_pins_foundational_artifacts():
    dep_runs = []
    # Task 1: Repository Understanding (Foundational - must be pinned)
    dep_runs.append((
        SimpleNamespace(output_text=json.dumps({"artifacts": {"repository_understanding": "FastAPI + SQLite repo"}})),
        SimpleNamespace(title="Repository Understanding"),
    ))
    # Task 2: Planning & Architecture (Foundational - must be pinned)
    dep_runs.append((
        SimpleNamespace(output_text=json.dumps({"artifacts": {"architecture_blueprint": "AuditLog schema"}})),
        SimpleNamespace(title="Planning & Architecture"),
    ))
    # Add 8 subsequent tasks so total tasks = 10 (exceeding old 6-task window)
    for i in range(1, 9):
        dep_runs.append((
            SimpleNamespace(output_text=json.dumps({"artifacts": {f"step_{i}": f"output_{i}"}})),
            SimpleNamespace(title=f"Implementation Step {i}"),
        ))

    ctx = MissionRuntime._build_bounded_upstream_context(dep_runs, max_total_chars=8000)
    # Pinned foundational artifacts are preserved even on task #10!
    assert "repository_understanding_repository_understanding" in ctx
    assert "planning_&_architecture_architecture_blueprint" in ctx
    # Recent sliding window tasks are included
    assert "implementation_step_8_step_8" in ctx
    # Old middle tasks were evicted to keep context bounded
    assert "implementation_step_1_step_1" not in ctx


def test_cli_diff_apply_and_repl_subcommands_registered():
    parser = build_parser()
    args_diff = parser.parse_args(["diff", "03992d0b", "--stat"])
    assert args_diff.command == "diff"
    assert args_diff.mission_id == "03992d0b"
    assert args_diff.stat is True

    args_diff_latest = parser.parse_args(["diff", "--stat"])
    assert args_diff_latest.command == "diff"
    assert args_diff_latest.mission_id is None

    args_apply = parser.parse_args(["apply", "--dry-run"])
    assert args_apply.command == "apply"
    assert args_apply.mission_id is None
    assert args_apply.dry_run is True

    args_repl = parser.parse_args(["repl"])
    assert args_repl.command == "repl"


def test_pure_local_engine_no_web_client():
    assert not hasattr(kobits_cli, "KobitsAPIClient")
    assert not hasattr(kobits_cli, "poll_server_mission")


@pytest.mark.asyncio
async def test_resolve_latest_mission_id_zero_uuid_friction():
    from backend.core.database import AsyncSessionLocal
    from backend.models.mission import Mission, MissionStatus
    from backend.models.project import Project

    latest_id = await _resolve_mission_id(None)
    if latest_id is None:
        user_id, org_id = await kobits_cli._ensure_local_db_and_identity()
        async with AsyncSessionLocal() as db:
            db.add(Project(id="p_zero_uuid", name="Zero", organization_id=org_id, created_by=user_id))
            db.add(
                Mission(
                    id="03992d0b-1111-2222-3333-444455556666",
                    organization_id=org_id,
                    project_id="p_zero_uuid",
                    created_by=user_id,
                    title="Latest mission test",
                    objective="Verify zero UUID friction",
                    status=MissionStatus.COMPLETED,
                )
            )
            await db.commit()
        latest_id = await _resolve_mission_id(None)
    assert latest_id is not None
    assert len(latest_id) >= 8


def test_inspect_mission_sandbox_on_completed_mission():
    info = _inspect_mission_sandbox("03992d0b", "kobits/mission/03992d0b", ["backend/api/v1/org.py"])
    if info.get("sandbox_dir"):
        assert info["verification"]["status"] == "PASSED"
        paths = [f["path"] for f in info["files"]]
        assert "backend/models/audit_log.py" in paths
        assert "backend/api/v1/org.py" in paths


def test_external_workspace_sandbox_seeding_and_verification():
    orig_ws = os.environ.get("KOBITS_TARGET_WORKSPACE")
    try:
        with tempfile.TemporaryDirectory() as ext_project, tempfile.TemporaryDirectory() as sb_root:
            app_py = Path(ext_project) / "app.py"
            app_py.write_text("def main():\n    return 1\n", encoding="utf-8")

            os.environ["KOBITS_TARGET_WORKSPACE"] = ext_project
            sb_dir = os.path.join(sb_root, "sandbox-ext-test")
            os.makedirs(sb_dir, exist_ok=True)
            SandboxManager._seed_local_workspace(sb_dir)
            subprocess.run(["git", "init"], cwd=sb_dir, check=True, capture_output=True)
            SandboxManager._init_git_excludes(sb_dir)
            subprocess.run(["git", "add", "."], cwd=sb_dir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial seed"], cwd=sb_dir, check=True, capture_output=True)

            seeded_app = Path(sb_dir) / "app.py"
            assert seeded_app.is_file()

            feature_py = Path(sb_dir) / "feature.py"
            feature_py.write_text("def compute(x: int) -> int:\n    return x * 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=sb_dir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Add feature.py"], cwd=sb_dir, check=True, capture_output=True)

            ver = MissionRuntime._verify_sandbox_code(sb_dir, ["feature.py"])
            assert ver["status"] == "PASSED"
            assert "feature.py" in ver["verified_py_files"]
    finally:
        if orig_ws is not None:
            os.environ["KOBITS_TARGET_WORKSPACE"] = orig_ws


def test_persistent_sandbox_session_rehydration_from_disk():
    # Verify that even if SandboxManager._sessions is cleared (simulating a process restart),
    # get_session(session_id) and read_file(session_id, ...) rehydrate seamlessly from disk.
    info = _inspect_mission_sandbox("03992d0b", "kobits/mission/03992d0b")
    if info.get("sandbox_dir"):
        session_id = info["sandbox_dir"].split("sandbox-", 1)[1]
        SandboxManager._sessions.pop(session_id, None)
        rehydrated = SandboxManager.get_session(session_id)
        assert rehydrated is not None
        assert rehydrated.branch_name == "kobits/mission/03992d0b"
        read_res = SandboxManager.read_file(session_id, "backend/models/audit_log.py")
        assert read_res.get("type") == "file"
        assert "AuditLog" in read_res.get("content", "")


@pytest.mark.asyncio
async def test_clean_cli_package_infrastructure():
    root = Path(kobits_cli.ROOT_DIR)
    assert (root / "pyproject.toml").is_file()
    assert (root / "README.md").is_file()
    assert not (root / "portal.html").exists()
    assert not (root / "js").exists()

    parser = build_parser()
    parsed = parser.parse_args(["agents"])
    assert parsed.command == "agents"
    rc = await kobits_cli.cmd_agents(parsed)
    assert rc == 0


def test_full_e2e_sandbox_diff_and_apply_workflow():
    import shutil
    with tempfile.TemporaryDirectory() as target_ws, tempfile.TemporaryDirectory() as sb_root:
        # 1. Initialize a clean Git repository as the user's target workspace
        subprocess.run(["git", "init"], cwd=target_ws, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "dev@kobits.local"], cwd=target_ws, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Kobits Dev"], cwd=target_ws, check=True, capture_output=True)
        (Path(target_ws) / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=target_ws, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=target_ws, check=True, capture_output=True)

        # 2. Create a native Git worktree sandbox via SandboxManager
        sb_dir = os.path.join(sb_root, "sandbox-e2e-apply")
        ok = SandboxManager._create_git_worktree_sandbox(
            sb_dir, "kobits/mission/e2e-apply", source_dir=target_ws
        )
        assert ok is True

        # 3. Write new feature + unit test inside the sandbox (leaving target_ws untouched)
        (Path(sb_dir) / "calc.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n\ndef multiply(a: int, b: int) -> int:\n    return a * b\n",
            encoding="utf-8",
        )
        (Path(sb_dir) / "test_calc.py").write_text(
            "from calc import add, multiply\n\ndef test_ops():\n    assert add(2, 3) == 5\n    assert multiply(3, 4) == 12\n",
            encoding="utf-8",
        )
        assert "multiply" not in (Path(target_ws) / "calc.py").read_text(encoding="utf-8")

        # 4. Run the 4-Stage Automated Pre-Review Gate (AST + Bytecode + Imports + Pytest)
        ver = MissionRuntime._verify_sandbox_code(sb_dir, ["calc.py", "test_calc.py"])
        assert ver["status"] == "PASSED"
        assert ver["pytest_summary"]["passed"] is True

        # 5. Apply verified sandbox changes to target_ws
        for rel_p in ["calc.py", "test_calc.py"]:
            shutil.copy2(os.path.join(sb_dir, rel_p), os.path.join(target_ws, rel_p))

        assert "multiply" in (Path(target_ws) / "calc.py").read_text(encoding="utf-8")
        assert (Path(target_ws) / "test_calc.py").is_file()
        subprocess.run(["git", "worktree", "remove", "--force", sb_dir], cwd=target_ws, capture_output=True)


@pytest.mark.asyncio
async def test_surgical_repository_edit_tool_and_safety_guards():
    from backend.services.sandbox_manager import SandboxSession
    from backend.services.tool_registry import ToolRegistry
    from backend.services.agent_registry import AGENT_REGISTRY
    from backend.models.agent import AgentType
    from backend.services.intelligence.consensus_engine import ConsensusEngine

    # Verify coding agents have repository.edit in allowed_tools
    assert "repository.edit" in AGENT_REGISTRY[AgentType.BACKEND_ENGINEER].allowed_tools
    assert "repository.edit" in AGENT_REGISTRY[AgentType.FRONTEND_ENGINEER].allowed_tools
    assert "repository.edit" in AGENT_REGISTRY[AgentType.DATABASE_ENGINEER].allowed_tools

    with tempfile.TemporaryDirectory() as sb_dir:
        session_id = "surgical-edit-test"
        session = SandboxSession(
            session_id=session_id,
            project_root=sb_dir,
            branch_name="kobits/mission/surgical",
            sandbox_dir=sb_dir,
        )
        SandboxManager._sessions[session_id] = session

        # Create a multi-function file with CRLF line endings and trailing spaces
        service_path = Path(sb_dir) / "service.py"
        initial_code = (
            "class PaymentService:\r\n"
            "    def charge(self, amount: int) -> bool:   \r\n"
            "        return amount > 0\r\n"
            "\r\n"
            "    def refund(self, amount: int) -> bool:\r\n"
            "        return amount > 0\r\n"
        )
        service_path.write_bytes(initial_code.encode("utf-8"))

        # 0. Surgical line-range read (start_line=2, end_line=3) returns numbered lines + metadata
        slice_res = await ToolRegistry.execute_tool(
            "repository.read",
            {"path": "service.py", "start_line": 2, "end_line": 3},
            context={"sandbox_session_id": session_id},
        )
        assert slice_res["type"] == "file"
        assert slice_res["start_line"] == 2
        assert slice_res["end_line"] == 3
        assert slice_res["total_lines"] == 6
        assert "def charge" in slice_res["content"]
        assert "2:     def charge" in slice_res["numbered_content"]
        assert "def refund" not in slice_res["content"]

        # 1. Ambiguous edit (matches both charge and refund `return amount > 0`) MUST be blocked when replace_all=False
        ambig_res = await ToolRegistry.execute_tool(
            "repository.edit",
            {
                "path": "service.py",
                "old_string": "        return amount > 0",
                "new_string": "        return amount >= 1",
                "replace_all": False,
            },
            context={"sandbox_session_id": session_id},
        )
        assert "error" in ambig_res
        assert "Ambiguous edit" in ambig_res["error"]

        # 2. Tier-2 (trailing whitespace difference) surgical edit + compact snippet + AST syntax check
        edit_res = await ToolRegistry.execute_tool(
            "repository.edit",
            {
                "path": "service.py",
                "old_string": "    def charge(self, amount: int) -> bool:\n        return amount > 0",
                "new_string": "    def charge(self, amount: int, currency: str = 'USD') -> bool:\n        return amount > 0 and bool(currency)",
            },
            context={"sandbox_session_id": session_id},
        )
        assert edit_res.get("success") is True
        assert edit_res["replacements"] == 1
        assert edit_res["syntax_valid"] is True
        assert "content" not in edit_res  # Compact snippet instead of full-file echo
        assert "snippet" in edit_res
        assert "currency: str = 'USD'" in edit_res["snippet"]
        assert "service.py" in session.files_changed

        # Verify CRLF line endings were preserved and only `charge` was modified while `refund` is untouched
        raw_after = service_path.read_bytes()
        assert b"\r\n" in raw_after
        text_after = raw_after.decode("utf-8")
        assert "currency: str = 'USD'" in text_after
        assert "def refund(self, amount: int) -> bool:" in text_after

        # 3. Tier-3 Relative-Indentation-Aware Matching:
        # LLM omits the 4-space class indentation in both old_string and new_string;
        # engine must match via Tier-3 and shift new_string by +4 spaces automatically!
        indent_res = await ToolRegistry.execute_tool(
            "repository.edit",
            {
                "path": "service.py",
                "old_string": "def refund(self, amount: int) -> bool:\n    return amount > 0",
                "new_string": "def refund(self, amount: int, reason: str = 'user_request') -> bool:\n    return amount > 0 and bool(reason)",
            },
            context={"sandbox_session_id": session_id},
        )
        assert indent_res.get("success") is True
        assert indent_res["syntax_valid"] is True
        text_after_indent = service_path.read_text(encoding="utf-8")
        assert "    def refund(self, amount: int, reason: str = 'user_request') -> bool:" in text_after_indent
        assert "        return amount > 0 and bool(reason)" in text_after_indent

        # 4. Atomic Multi-Chunk Batching (`edits` list) + Atomic Rollback on second chunk failure
        atomic_fail = await ToolRegistry.execute_tool(
            "repository.edit",
            {
                "path": "service.py",
                "edits": [
                    {
                        "old_string": "class PaymentService:",
                        "new_string": "class EnterprisePaymentService:",
                    },
                    {
                        "old_string": "def non_existent_method():",
                        "new_string": "def should_rollback():",
                    },
                ],
            },
            context={"sandbox_session_id": session_id},
        )
        assert "error" in atomic_fail
        assert "chunk #2 failed" in atomic_fail["error"].lower()
        # Verify chunk #1 was NOT written to disk (atomic rollback)
        assert "EnterprisePaymentService" not in service_path.read_text(encoding="utf-8")

        # Valid multi-chunk atomic edit
        atomic_ok = await ToolRegistry.execute_tool(
            "repository.edit",
            {
                "path": "service.py",
                "edits": [
                    {
                        "old_string": "class PaymentService:",
                        "new_string": "class PaymentService:\n    PROVIDER = 'stripe'",
                    },
                    {
                        "old_string": "reason: str = 'user_request'",
                        "new_string": "reason: str = 'dispute'",
                    },
                ],
            },
            context={"sandbox_session_id": session_id},
        )
        assert atomic_ok.get("success") is True
        assert atomic_ok["chunks_applied"] == 2
        assert atomic_ok["syntax_valid"] is True

        # 5. Instant AST Syntax Error Detection on broken Python edit
        bad_syntax = await ToolRegistry.execute_tool(
            "repository.edit",
            {
                "path": "service.py",
                "old_string": "    PROVIDER = 'stripe'",
                "new_string": "    PROVIDER = 'stripe'(\n    def broken_syntax(:",
            },
            context={"sandbox_session_id": session_id},
        )
        assert bad_syntax.get("success") is True
        assert bad_syntax["syntax_valid"] is False
        assert bad_syntax["syntax_error"] is not None
        assert "warning" in bad_syntax

        # 6. Security path traversal check MUST block escaping sandbox
        sec_res = SandboxManager.edit_file(session_id, "../escape.py", "a", "b")
        assert "Security Block" in sec_res.get("error", "")

        # 7. ConsensusEngine.extract_code_artifacts MUST capture repository.edit compact snippet
        artifacts = ConsensusEngine.extract_code_artifacts([
            {
                "type": "tool_call",
                "name": "repository.edit",
                "args": {"path": "service.py", "old_string": "old", "new_string": "new"},
                "result": edit_res,
            }
        ])
        assert len(artifacts) == 1
        assert artifacts[0]["path"] == "service.py"
        assert artifacts[0]["action"] == "edit"
        assert "currency: str = 'USD'" in artifacts[0]["content"]


def test_multi_language_pre_review_gate_ts_js_tsx_go_rust_json_toml():
    with tempfile.TemporaryDirectory() as sb_dir:
        # 1. Valid multi-language project (TypeScript + TSX + JS + Go + Rust + JSON + TOML + Node TS unit test)
        (Path(sb_dir) / "package.json").write_text(
            '{\n  "name": "multi-lang-demo",\n  "type": "module"\n}\n',
            encoding="utf-8",
        )
        (Path(sb_dir) / "Cargo.toml").write_text(
            '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8",
        )
        (Path(sb_dir) / "go.mod").write_text(
            "module example.com/demo\n\ngo 1.22\n",
            encoding="utf-8",
        )
        os.makedirs(Path(sb_dir) / "pkg" / "calc", exist_ok=True)
        (Path(sb_dir) / "pkg" / "calc" / "calc.go").write_text(
            "package calc\n\nfunc Add(a int, b int) int {\n\treturn a + b\n}\n",
            encoding="utf-8",
        )
        (Path(sb_dir) / "main.go").write_text(
            'package main\n\nimport (\n\t"fmt"\n\t"example.com/demo/pkg/calc"\n)\n\nfunc main() {\n\tfmt.Println(calc.Add(2, 3))\n}\n',
            encoding="utf-8",
        )
        (Path(sb_dir) / "engine.rs").write_text(
            "pub fn compute<'a>(label: &'a str, x: i32) -> (&'a str, i32) {\n    let raw = r#\"ok\"#;\n    (if raw.is_empty() { label } else { label }, x * 2)\n}\n",
            encoding="utf-8",
        )
        (Path(sb_dir) / "lib.rs").write_text(
            "pub mod engine;\n\npub fn run() -> i32 {\n    let (_, v) = engine::compute(\"test\", 21);\n    v\n}\n",
            encoding="utf-8",
        )
        (Path(sb_dir) / "math.ts").write_text(
            "export interface MathResult {\n  value: number;\n}\n\nexport function multiply(a: number, b: number): MathResult {\n  return { value: a * b };\n}\n",
            encoding="utf-8",
        )
        (Path(sb_dir) / "math.test.ts").write_text(
            "import { multiply } from './math.ts';\n\ndescribe('TypeScript multiply', () => {\n  it('multiplies numbers accurately', () => {\n    expect(multiply(6, 7).value).toBe(42);\n  });\n});\n",
            encoding="utf-8",
        )
        (Path(sb_dir) / "Widget.tsx").write_text(
            "import { multiply } from './math';\n\nexport function Widget() {\n  const res = multiply(3, 4);\n  return (\n    <div className=\"widget\">\n      <span>{res.value}</span>\n    </div>\n  );\n}\n",
            encoding="utf-8",
        )

        target_list = [
            "package.json",
            "Cargo.toml",
            "pkg/calc/calc.go",
            "main.go",
            "engine.rs",
            "lib.rs",
            "math.ts",
            "math.test.ts",
            "Widget.tsx",
        ]
        res_ok = MissionRuntime._verify_sandbox_code(sb_dir, target_list)
        assert res_ok["status"] == "PASSED", f"Expected PASSED, got errors: {res_ok['errors']}"
        assert "typescript" in res_ok["verified_by_language"]
        assert "tsx" in res_ok["verified_by_language"]
        assert "go" in res_ok["verified_by_language"]
        assert "rust" in res_ok["verified_by_language"]
        assert "json" in res_ok["verified_by_language"]
        assert "toml" in res_ok["verified_by_language"]
        assert "./math" in res_ok["verified_imports"]
        assert "example.com/demo/pkg/calc" in res_ok["verified_imports"]
        assert "mod::engine" in res_ok["verified_imports"]
        assert res_ok["node_test_summary"] is not None
        assert res_ok["node_test_summary"]["passed"] is True

        # 2. Broken TypeScript syntax + unresolved relative import MUST fail gate
        (Path(sb_dir) / "broken.ts").write_text(
            "import { ghost } from './does_not_exist';\nexport const x: number = ;\n",
            encoding="utf-8",
        )
        res_bad_ts = MissionRuntime._verify_sandbox_code(sb_dir, ["broken.ts"])
        assert res_bad_ts["status"] == "FAILED"
        assert any("broken.ts" in e for e in res_bad_ts["errors"])

        # 3. Unresolved relative import in syntactically valid TS file MUST fail gate
        (Path(sb_dir) / "bad_import.ts").write_text(
            "import { ghost } from './missing_module';\nexport const y: number = 10;\n",
            encoding="utf-8",
        )
        res_bad_imp = MissionRuntime._verify_sandbox_code(sb_dir, ["bad_import.ts"])
        assert res_bad_imp["status"] == "FAILED"
        assert any("Unresolved local relative import './missing_module'" in e for e in res_bad_imp["errors"])

        # 4. Mismatched JSX tags in .tsx MUST fail gate
        (Path(sb_dir) / "BrokenWidget.tsx").write_text(
            "export function BrokenWidget() {\n  return <div><span>Hello</div></span>;\n}\n",
            encoding="utf-8",
        )
        res_bad_tsx = MissionRuntime._verify_sandbox_code(sb_dir, ["BrokenWidget.tsx"])
        assert res_bad_tsx["status"] == "FAILED"
        assert any("Mismatched JSX closing tag" in e for e in res_bad_tsx["errors"])

        # 5. Broken Go file (missing package or unresolved local Go package) MUST fail gate
        (Path(sb_dir) / "bad.go").write_text(
            'package main\n\nimport "example.com/demo/pkg/nonexistent"\n\nfunc broken() {\n}\n',
            encoding="utf-8",
        )
        res_bad_go = MissionRuntime._verify_sandbox_code(sb_dir, ["bad.go"])
        assert res_bad_go["status"] == "FAILED"
        assert any("Unresolved local Go package" in e for e in res_bad_go["errors"])

        # 6. Broken Rust external module (`mod missing_rs;`) MUST fail gate
        (Path(sb_dir) / "bad_mod.rs").write_text(
            "pub mod missing_rs;\n",
            encoding="utf-8",
        )
        res_bad_rs = MissionRuntime._verify_sandbox_code(sb_dir, ["bad_mod.rs"])
        assert res_bad_rs["status"] == "FAILED"
        assert any("Unresolved Rust module" in e for e in res_bad_rs["errors"])

        # 7. Broken TypeScript interface inside a .tsx file MUST fail gate via SWC type check
        (Path(sb_dir) / "BadInterfaceWidget.tsx").write_text(
            "export interface BadProps {\n  count: ;\n}\nexport function W() {\n  return <div>ok</div>;\n}\n",
            encoding="utf-8",
        )
        res_bad_tsx_type = MissionRuntime._verify_sandbox_code(sb_dir, ["BadInterfaceWidget.tsx"])
        assert res_bad_tsx_type["status"] == "FAILED"
        assert any("TypeScript SyntaxError" in e for e in res_bad_tsx_type["errors"])

        # 8. Failing TypeScript unit test assertion in `node --test` MUST fail gate
        (Path(sb_dir) / "failing.test.ts").write_text(
            "import { multiply } from './math.ts';\ndescribe('failing suite', () => {\n  it('detects wrong result', () => {\n    expect(multiply(2, 3).value).toBe(999);\n  });\n});\n",
            encoding="utf-8",
        )
        res_fail_test = MissionRuntime._verify_sandbox_code(sb_dir, ["failing.test.ts"])
        assert res_fail_test["status"] == "FAILED"
        assert res_fail_test["node_test_summary"]["passed"] is False
        assert any("node --test failed" in e for e in res_fail_test["errors"])


@pytest.mark.asyncio
async def test_three_way_git_apply_merges_concurrent_edits_and_deletions():
    """
    Kyros Parity Feature #15: 3-Way Git Patch (`git apply --3way` / `git merge-file`) & File Deletions (`[DELETED]`)
    Verifies:
    1. `SandboxManager.delete_file` / `repository.delete` safely deletes files in sandbox, blocks path traversal
       and `.git`/`.kobits_` metadata deletion, and prunes empty parent directories.
    2. `inspect_sandbox_directory` accurately detects `ADDED`, `MODIFIED`, and `DELETED` files against `root_sha`.
    3. `apply_sandbox_to_workspace` performs a true 3-way merge when a file (`service.py`) was concurrently edited
       on non-overlapping lines in both the live host workspace and the sandbox (`3WAY_MERGED`), preserving BOTH edits!
    4. `apply_sandbox_to_workspace` deletes `[DELETED]` files (`legacy/old_helper.py`) from the host workspace
       and prunes empty parent directories (`legacy/`).
    5. `apply_sandbox_to_workspace` detects overlapping 3-way line conflicts (`config.py`) and modify/delete conflicts
       (`keep_me.py`), blocking destructive overwrites unless `force=True`.
    """
    import subprocess
    from backend.services.sandbox_manager import SandboxManager, SandboxSession
    from backend.services.tool_registry import ToolRegistry
    from kobits_cli import inspect_sandbox_directory, apply_sandbox_to_workspace

    with tempfile.TemporaryDirectory() as host_ws, tempfile.TemporaryDirectory() as sb_dir:
        host_path = Path(host_ws)
        sb_path = Path(sb_dir)

        # 1. Seed initial base files in both host workspace and sandbox
        base_service = "\n".join([
            "def header_config():",
            "    return {'env': 'prod', 'timeout': 30}",
            "",
            "def middle_section():",
            "    x = 10",
            "    y = 20",
            "    return x + y",
            "",
            "def footer_handler():",
            "    return 'v1-original'",
            "",
        ])
        base_config = "MAX_RETRIES = 3\nLOG_LEVEL = 'INFO'\n"
        base_legacy = "# Obsolete legacy helper\ndef legacy_fn():\n    return False\n"
        base_keep = "# File that sandbox deletes but user edits locally\nVALUE = 1\n"

        for root in (host_path, sb_path):
            (root / "service.py").write_text(base_service, encoding="utf-8")
            (root / "config.py").write_text(base_config, encoding="utf-8")
            (root / "legacy").mkdir(parents=True, exist_ok=True)
            (root / "legacy" / "old_helper.py").write_text(base_legacy, encoding="utf-8")
            (root / "keep_me.py").write_text(base_keep, encoding="utf-8")

        # Initialize git repo in both host and sandbox (committing the base state = root_sha)
        for root in (host_path, sb_path):
            subprocess.run(["git", "init"], cwd=str(root), capture_output=True, check=True)
            subprocess.run(["git", "config", "user.email", "test@kobits.local"], cwd=str(root), capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Kobits Test"], cwd=str(root), capture_output=True, check=True)
            subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "Initial base snapshot"], cwd=str(root), capture_output=True, check=True)

        session_id = "test-3way-session"
        SandboxManager._sessions[session_id] = SandboxSession(
            session_id=session_id,
            project_root=str(host_path),
            branch_name="kobits/mission/3waytest",
            sandbox_dir=str(sb_path),
        )

        try:
            # 2. Agent modifies bottom of `service.py`, creates `new_feature.py`, and deletes `legacy/old_helper.py`
            res_edit = await ToolRegistry.execute_tool(
                "repository.edit",
                {
                    "path": "service.py",
                    "old_string": "def footer_handler():\n    return 'v1-original'",
                    "new_string": "def footer_handler():\n    return 'v2-from-sandbox-agent'",
                },
                context={"sandbox_session_id": session_id},
            )
            assert res_edit.get("success") is True

            res_write = await ToolRegistry.execute_tool(
                "repository.write",
                {
                    "path": "new_feature.py",
                    "content": "def feature_flag() -> bool:\n    return True\n",
                },
                context={"sandbox_session_id": session_id},
            )
            assert res_write.get("success") is True

            # Security checks on repository.delete
            res_del_traversal = await ToolRegistry.execute_tool(
                "repository.delete",
                {"path": "../outside.py"},
                context={"sandbox_session_id": session_id},
            )
            assert "Security Block" in res_del_traversal.get("error", "")

            res_del_git = await ToolRegistry.execute_tool(
                "repository.delete",
                {"path": ".git/HEAD"},
                context={"sandbox_session_id": session_id},
            )
            assert "Security Block" in res_del_git.get("error", "")

            # Valid deletion of `legacy/old_helper.py` in sandbox
            res_del = await ToolRegistry.execute_tool(
                "repository.delete",
                {"path": "legacy/old_helper.py"},
                context={"sandbox_session_id": session_id},
            )
            assert res_del.get("success") is True
            assert res_del.get("deleted") is True
            assert not (sb_path / "legacy" / "old_helper.py").exists()
            # Empty parent dir `legacy/` should be pruned automatically in sandbox
            assert not (sb_path / "legacy").exists()

            # 3. Inspect sandbox directory -> verify ADDED, MODIFIED, DELETED statuses
            inspected = inspect_sandbox_directory(sb_path)
            status_by_path = {f["path"]: f["status"] for f in inspected["files"]}
            assert status_by_path.get("service.py") == "MODIFIED"
            assert status_by_path.get("new_feature.py") == "ADDED"
            assert status_by_path.get("legacy/old_helper.py") == "DELETED"

            # 4. Simulate CONCURRENT NON-CONFLICTING user edit on top of `service.py` in the live host workspace!
            concurrent_host_service = base_service.replace(
                "return {'env': 'prod', 'timeout': 30}",
                "return {'env': 'staging-live-user-edit', 'timeout': 60}",
            )
            (host_path / "service.py").write_text(concurrent_host_service, encoding="utf-8")

            # Apply sandbox to workspace (clean 3-way merge + file deletion + file creation)
            apply_res = apply_sandbox_to_workspace(
                abs_sb=sb_path,
                workspace_dir=host_path,
                files=inspected["files"],
                dry_run=False,
                force=False,
            )
            assert apply_res["status"] == "APPLIED", f"Expected APPLIED, got: {apply_res}"
            assert apply_res["conflicts"] == []
            assert "service.py" in apply_res["merged_3way"]
            assert "new_feature.py" in apply_res["created"]
            assert "legacy/old_helper.py" in apply_res["deleted"]

            # Verify BOTH the user's concurrent top-of-file edit AND the agent's bottom-of-file edit exist in host `service.py`!
            merged_service_text = (host_path / "service.py").read_text(encoding="utf-8")
            assert "'env': 'staging-live-user-edit', 'timeout': 60" in merged_service_text
            assert "return 'v2-from-sandbox-agent'" in merged_service_text

            # Verify `legacy/old_helper.py` and its empty `legacy/` parent dir were removed from host workspace!
            assert not (host_path / "legacy" / "old_helper.py").exists()
            assert not (host_path / "legacy").exists()
            assert (host_path / "new_feature.py").is_file()

            # 5. Now test CONFLICT protection:
            #    (a) Overlapping edit on the exact same line in `config.py`
            #    (b) Deletion in sandbox of `keep_me.py` while user modified `keep_me.py` on host
            await ToolRegistry.execute_tool(
                "repository.edit",
                {
                    "path": "config.py",
                    "old_string": "MAX_RETRIES = 3",
                    "new_string": "MAX_RETRIES = 10  # sandbox agent change",
                },
                context={"sandbox_session_id": session_id},
            )
            await ToolRegistry.execute_tool(
                "repository.delete",
                {"path": "keep_me.py"},
                context={"sandbox_session_id": session_id},
            )
            await ToolRegistry.execute_tool(
                "repository.write",
                {
                    "path": "atomic_batch_file.py",
                    "content": "BATCH_OK = True\n",
                },
                context={"sandbox_session_id": session_id},
            )
            # User concurrently edits the exact same line in `config.py` AND modifies `keep_me.py` on host!
            (host_path / "config.py").write_text("MAX_RETRIES = 999  # live user edit\nLOG_LEVEL = 'INFO'\n", encoding="utf-8")
            (host_path / "keep_me.py").write_text("# User added critical local work\nVALUE = 42\n", encoding="utf-8")

            inspected_conflict = inspect_sandbox_directory(sb_path)
            conflict_files = [
                f for f in inspected_conflict["files"]
                if f["path"] in ("atomic_batch_file.py", "config.py", "keep_me.py")
            ]
            conflict_res = apply_sandbox_to_workspace(
                abs_sb=sb_path,
                workspace_dir=host_path,
                files=conflict_files,
                dry_run=False,
                force=False,
            )
            assert conflict_res["status"] == "CONFLICT"
            conflict_paths = {c["path"] for c in conflict_res["conflicts"]}
            assert "config.py" in conflict_paths
            assert "keep_me.py" in conflict_paths

            # Transactional guarantee: `atomic_batch_file.py` MUST NOT be partially written when batch has conflicts!
            assert not (host_path / "atomic_batch_file.py").exists()
            # Verify host files were NOT overwritten or deleted!
            assert "MAX_RETRIES = 999  # live user edit" in (host_path / "config.py").read_text(encoding="utf-8")
            assert (host_path / "keep_me.py").exists()
            assert "VALUE = 42" in (host_path / "keep_me.py").read_text(encoding="utf-8")

            # Verify `--force` explicitly overrides when requested and commits the entire batch
            force_res = apply_sandbox_to_workspace(
                abs_sb=sb_path,
                workspace_dir=host_path,
                files=conflict_files,
                dry_run=False,
                force=True,
            )
            assert force_res["status"] == "APPLIED"
            assert (host_path / "atomic_batch_file.py").is_file()
            assert "MAX_RETRIES = 10  # sandbox agent change" in (host_path / "config.py").read_text(encoding="utf-8")
            assert not (host_path / "keep_me.py").exists()
        finally:
            SandboxManager._sessions.pop(session_id, None)


@pytest.mark.asyncio
async def test_ripgrep_regex_glob_and_semantic_token_search_in_sandbox():
    """
    Kyros Parity Feature #16: Ripgrep-Style Regex, Glob, Smart-Case, Context Snippets & Multi-Token Search
    Verifies:
    1. Regular expression matching (`def\\s+verify_\\w+`) across sandbox files with line-numbered context snippets.
    2. File glob filtering (`file_pattern="*.{ts,tsx}"`) to scope results strictly to matching extensions.
    3. Case-insensitive fallback when `case_sensitive=True` has 0 exact-case matches.
    4. Multi-token BM25 identifier window fallback (`match_mode == "semantic_token_window"`) when a natural-language
       query (`"validate bearer jwt signature expiration"`) has no contiguous substring match.
    """
    from backend.services.sandbox_manager import SandboxManager, SandboxSession
    from backend.services.tool_registry import ToolRegistry

    with tempfile.TemporaryDirectory() as sb_dir:
        sb_path = Path(sb_dir)
        (sb_path / "auth").mkdir(parents=True, exist_ok=True)
        (sb_path / "frontend").mkdir(parents=True, exist_ok=True)

        (sb_path / "auth" / "jwt_handler.py").write_text(
            "\n".join([
                "import time",
                "",
                "class TokenVerifier:",
                "    def verify_bearer_signature(self, raw_jwt: str, exp_timestamp: int) -> bool:",
                "        '''Validates the cryptographic bearer token and checks expiration.'''",
                "        if exp_timestamp < time.time():",
                "            return False",
                "        return raw_jwt.startswith('eyJ')",
            ]) + "\n",
            encoding="utf-8",
        )
        (sb_path / "frontend" / "AuthBanner.tsx").write_text(
            "\n".join([
                "export interface AuthBannerProps { username: string; }",
                "export function renderAuthBanner(props: AuthBannerProps) {",
                "  return `<div class='banner'>Welcome ${props.username}</div>`;",
                "}",
            ]) + "\n",
            encoding="utf-8",
        )

        session_id = "test_search_session"
        SandboxManager._sessions[session_id] = SandboxSession(
            session_id=session_id,
            project_root=sb_dir,
            branch_name="kobits/mission/search123",
            sandbox_dir=sb_dir,
        )
        try:
            # 1. Regex search (`def\s+verify_\w+`) + column + matched_text + enclosing_symbol + `> line |` context
            res_regex = await ToolRegistry.execute_tool(
                "repository.search",
                {"query": r"def\s+verify_\w+", "is_regex": True, "before_context": 1, "after_context": 2},
                context={"sandbox_session_id": session_id},
            )
            assert res_regex["total_matches"] == 1
            hit = res_regex["results"][0]
            assert hit["file"] == "auth/jwt_handler.py"
            assert hit["line"] == 4
            assert hit["column"] == 5
            assert hit["matched_text"] == "def verify_bearer_signature"
            assert "def verify_bearer_signature" in (hit["enclosing_symbol"] or "")
            assert "    3 | class TokenVerifier:" in hit["snippet"]
            assert ">    4 |     def verify_bearer_signature" in hit["snippet"]
            assert "    6 |         if exp_timestamp < time.time():" in hit["snippet"]

            # 2. True Ripgrep Smart-Case (`case_sensitive=None`):
            #    - All-lowercase query -> `smart_case_insensitive`
            #    - Mixed-case exact query -> `smart_case_sensitive`
            #    - All-uppercase mismatched query -> tries case-sensitive first, then `case_insensitive_fallback`
            res_smart_lower = SandboxManager.search_files(session_id, query="tokenverifier")
            assert res_smart_lower["match_mode"] == "smart_case_insensitive"
            assert res_smart_lower["total_matches"] == 1

            res_smart_upper = SandboxManager.search_files(session_id, query="TokenVerifier")
            assert res_smart_upper["match_mode"] == "smart_case_sensitive"
            assert res_smart_upper["total_matches"] == 1

            res_smart_fallback = SandboxManager.search_files(session_id, query="TOKENVERIFIER")
            assert res_smart_fallback["match_mode"] == "case_insensitive_fallback"
            assert res_smart_fallback["total_matches"] == 1

            # 3. Whole-word (`whole_word=True`) & Multiline (`multiline=True`) regex
            res_word = await ToolRegistry.execute_tool(
                "repository.search",
                {"query": "token", "whole_word": True, "case_sensitive": True},
                context={"sandbox_session_id": session_id},
            )
            assert res_word["total_matches"] == 1
            assert res_word["results"][0]["line"] == 5  # matches 'bearer token' in docstring, not 'TokenVerifier'

            res_multi = await ToolRegistry.execute_tool(
                "repository.search",
                {"query": r"class TokenVerifier:\n\s+def verify_bearer_signature", "multiline": True, "context_lines": 1},
                context={"sandbox_session_id": session_id},
            )
            assert res_multi["total_matches"] == 1
            m_hit = res_multi["results"][0]
            assert m_hit["line"] == 3
            assert m_hit["end_line"] == 4
            assert ">    3 | class TokenVerifier:" in m_hit["snippet"]
            assert ">    4 |     def verify_bearer_signature" in m_hit["snippet"]

            # 4. Brace & negated glob filter (`*.{ts,tsx},!*.spec.tsx`)
            (sb_path / "frontend" / "AuthBanner.spec.tsx").write_text(
                "export const testProps: AuthBannerProps = { username: 'test' };\n",
                encoding="utf-8",
            )
            res_glob = await ToolRegistry.execute_tool(
                "repository.search",
                {"query": "AuthBannerProps", "file_pattern": "*.{ts,tsx},!*.spec.tsx"},
                context={"sandbox_session_id": session_id},
            )
            assert res_glob["total_matches"] == 2
            assert all(r["file"] == "frontend/AuthBanner.tsx" for r in res_glob["results"])

            # 5. Case-insensitive fallback when `case_sensitive=True` has no exact-case match
            res_ci = SandboxManager.search_files(
                session_id,
                query="tokenverifier",
                case_sensitive=True,
            )
            assert res_ci["total_matches"] == 1
            assert res_ci["results"][0]["match_mode"] == "case_insensitive_fallback"

            # 6. Natural-language / multi-token query fallback (no contiguous substring match)
            res_nl = await ToolRegistry.execute_tool(
                "repository.search",
                {"query": "validate cryptographic bearer jwt expiration"},
                context={"sandbox_session_id": session_id},
            )
            assert res_nl["match_mode"] == "semantic_token_window"
            assert res_nl["total_matches"] >= 1
            assert res_nl["results"][0]["file"] == "auth/jwt_handler.py"

            # 7. Minified bundle (.min.js) and .gitignore directory filtering
            (sb_path / "frontend" / "vendor.min.js").write_text("function AuthBannerProps(){return 1;}\n", encoding="utf-8")
            (sb_path / ".gitignore").write_text("generated_cache/\n", encoding="utf-8")
            (sb_path / "generated_cache").mkdir(parents=True, exist_ok=True)
            (sb_path / "generated_cache" / "leak.ts").write_text("export const AuthBannerProps = 123;\n", encoding="utf-8")
            res_filtered = await ToolRegistry.execute_tool(
                "repository.search",
                {"query": "AuthBannerProps", "exclude_pattern": "*.spec.tsx"},
                context={"sandbox_session_id": session_id},
            )
            matched_files = {r["file"] for r in res_filtered["results"]}
            assert "frontend/vendor.min.js" not in matched_files
            assert "generated_cache/leak.ts" not in matched_files
            assert "frontend/AuthBanner.spec.tsx" not in matched_files

            # 8. Verify CLI `kobits search` parser registration
            import kobits_cli
            parser = kobits_cli.build_parser()
            parsed = parser.parse_args(["search", r"def\s+verify_\w+", "-g", "*.py", "-C", "1", "-W"])
            assert parsed.command == "search"
            assert parsed.glob == "*.py"
            assert parsed.context == 1
            assert parsed.word_regexp is True
        finally:
            SandboxManager._sessions.pop(session_id, None)


@pytest.mark.asyncio
async def test_ast_chunking_deterministic_embeddings_and_hybrid_rag_with_context_builder():
    """
    Kyros Parity Feature #17: AST-Chunked Semantic Vector + BM25 Hybrid RAG & Automatic ContextBuilder Injection
    Verifies:
    1. `EmbeddingUtils.generate_embedding` is 100% deterministic (CRC32 CountSketch) and understands
       camelCase / snake_case sub-tokens (`verifyBearerToken` <-> `verify_bearer_token`).
    2. `RepositoryIndexer.index_repository` chunks Python classes/methods via `ast.parse` and TypeScript
       declarations with exact `[start_line, end_line]`, `symbol`, and `kind` metadata, plus SHA-256 delta indexing.
    3. `GraphIndexerService.index_repository` extracts `FILE`, `CLASS`, `FUNCTION` nodes (including TS class methods)
       and `DEFINES`, `IMPORTS`, and `CALLS` edges across languages.
    4. `MemoryService.search_codebase` hybrid ranking (Dense + BM25 IDF + Symbol Boost) ranks the exact
       target AST method #1.
    5. `build_agent_context` automatically injects `RELEVANT CODEBASE CONTEXT (AST & SEMANTIC RAG)` into the
       agent prompt with matched graph symbols, 1-hop `CALLS`/`DEFINES` dependency edges, and AST code chunks.
    6. All engineering, QA, security, and review agents in `AGENT_REGISTRY` have `repository.search` enabled.
    """
    import uuid
    import numpy as np
    from types import SimpleNamespace
    from backend.core.database import engine, AsyncSessionLocal
    from backend.models.base import Base
    from backend.services.embedding import EmbeddingUtils
    from backend.services.indexer import RepositoryIndexer
    from backend.services.graph_indexer import GraphIndexerService
    from backend.services.memory_service import MemoryService
    from backend.services.context_builder import build_agent_context
    from backend.services.agent_registry import AGENT_REGISTRY
    from backend.models.agent import AgentType

    # Verify all code-facing roles in AGENT_REGISTRY have repository.search enabled
    for role in (
        AgentType.BACKEND_ENGINEER,
        AgentType.FRONTEND_ENGINEER,
        AgentType.QA_ENGINEER,
        AgentType.SECURITY_ENGINEER,
        AgentType.CODE_REVIEWER,
        AgentType.SOLUTION_ARCHITECT,
        AgentType.TECHNICAL_LEAD,
    ):
        assert "repository.search" in AGENT_REGISTRY[role].allowed_tools, f"{role} missing repository.search"

    # 1. Verify deterministic code embeddings & camelCase/snake_case semantic overlap
    vec_snake = np.array(EmbeddingUtils.generate_embedding("def verify_jwt_expiration_token(user_id):"))
    vec_camel = np.array(EmbeddingUtils.generate_embedding("verifyJwtExpirationToken for userId"))
    vec_unrelated = np.array(EmbeddingUtils.generate_embedding("render_css_grid_layout_animation"))
    sim_related = float(np.dot(vec_snake, vec_camel))
    sim_unrelated = float(np.dot(vec_snake, vec_unrelated))
    assert sim_related > 0.45, f"Expected high sub-token similarity, got {sim_related}"
    assert sim_related > sim_unrelated + 0.30

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with tempfile.TemporaryDirectory() as repo_dir:
        repo_path = Path(repo_dir)
        (repo_path / "services").mkdir(parents=True, exist_ok=True)
        (repo_path / "ui").mkdir(parents=True, exist_ok=True)

        (repo_path / "services" / "payment_ledger.py").write_text(
            "\n".join([
                "import decimal",
                "",
                "def calculate_tax_cents(amount_cents: int) -> int:",
                "    return int(amount_cents * 0.08)",
                "",
                "class PaymentLedgerService:",
                "    '''Handles double-entry ledger settlements and refunds.'''",
                "    def settle_invoice_transaction(self, invoice_id: str, amount_cents: int) -> dict:",
                "        tax = calculate_tax_cents(amount_cents)",
                "        return {'invoice_id': invoice_id, 'total': amount_cents + tax}",
                "",
                "    def issue_customer_refund(self, refund_id: str) -> bool:",
                "        return True",
            ]) + "\n",
            encoding="utf-8",
        )
        (repo_path / "services" / "checkout_router.py").write_text(
            "\n".join([
                "from services.payment_ledger import calculate_tax_cents",
                "",
                "async def process_checkout_request(cart_total: int) -> int:",
                "    return cart_total + calculate_tax_cents(cart_total)",
            ]) + "\n",
            encoding="utf-8",
        )
        (repo_path / "ui" / "InvoiceCard.tsx").write_text(
            "\n".join([
                "export class InvoiceViewModel {",
                "  formatCurrency(cents: number): string { return `$${(cents / 100).toFixed(2)}`; }",
                "}",
            ]) + "\n",
            encoding="utf-8",
        )

        project_id = f"proj_rag_{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as db:
            # 2. Run AST RepositoryIndexer & GraphIndexerService
            created_count = await RepositoryIndexer.index_repository(db, project_id, repo_dir)
            assert created_count >= 3

            # Delta indexing check: re-running without file modifications creates 0 new docs
            delta_count = await RepositoryIndexer.index_repository(db, project_id, repo_dir)
            assert delta_count == 0

            graph_stats = await GraphIndexerService.index_repository(db, project_id, repo_dir)
            assert graph_stats["nodes"] >= 7
            assert graph_stats["edges"] >= 6  # DEFINES + IMPORTS + CALLS edges

            # Verify TypeScript class method was extracted into the Code Graph
            ts_graph_hits = await MemoryService.search_graph_nodes(
                db, project_id, "formatCurrency InvoiceViewModel"
            )
            assert any(g["name"] == "InvoiceViewModel.formatCurrency" for g in ts_graph_hits)

            # 3. Search codebase via Hybrid RAG
            hits = await MemoryService.search_codebase(
                db, project_id, "settle invoice transaction tax calculation", limit=5
            )
            assert len(hits) >= 1
            top_hit = hits[0]
            assert top_hit["file"] == "services/payment_ledger.py"
            assert "PaymentLedgerService" in top_hit["symbol"] or "settle_invoice_transaction" in top_hit["content"]
            assert top_hit["start_line"] >= 1
            assert top_hit["end_line"] >= top_hit["start_line"]

            # 4. Search Code Graph symbols with multi-word natural language query
            graph_hits = await MemoryService.search_graph_nodes(
                db, project_id, "settle invoice transaction in PaymentLedgerService"
            )
            assert any("PaymentLedgerService" in g["name"] for g in graph_hits)

            # 5. Verify build_agent_context injects AST & Semantic RAG + 1-hop graph edges automatically
            dummy_agent = SimpleNamespace(
                id="agent_backend",
                name="Core",
                type=AgentType.BACKEND_ENGINEER,
                system_prompt="You are Core, the Backend Engineer.",
            )
            dummy_project = SimpleNamespace(id=project_id)
            dummy_mission = SimpleNamespace(
                id=f"mission_{uuid.uuid4().hex[:8]}",
                objective="Update invoice settlement tax calculation in PaymentLedgerService",
            )
            dummy_task = SimpleNamespace(
                title="Modify settle_invoice_transaction in PaymentLedgerService",
                description="Ensure tax calculation is included in ledger settlement output",
                input_context_json=None,
            )
            assembled_ctx = await build_agent_context(
                db, dummy_agent, dummy_project, dummy_mission, dummy_task, "org_default"
            )
            assert "RELEVANT CODEBASE CONTEXT (AST & SEMANTIC RAG)" in assembled_ctx
            assert "Matched Code Graph Symbols:" in assembled_ctx
            assert "PaymentLedgerService" in assembled_ctx
            assert "services/payment_ledger.py" in assembled_ctx
            assert "CALLS [FUNCTION] calculate_tax_cents" in assembled_ctx or "DEFINES" in assembled_ctx






