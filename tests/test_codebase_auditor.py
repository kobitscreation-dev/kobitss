import json
from pathlib import Path
import pytest
from backend.services.codebase_auditor import CodebaseAuditor, CodebaseAuditResult


def test_codebase_auditor_on_sample_repo(tmp_path: Path):
    # Setup mock repository structure
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)

    # 1. Main service file
    main_py = src_dir / "main.py"
    main_py.write_text("""
import os
from src.utils import helper_func

def run_app():
    print("App running")
    return helper_func()
""", encoding="utf-8")

    # 2. Helper utils file
    utils_py = src_dir / "utils.py"
    utils_py.write_text("""
def helper_func():
    return 42
""", encoding="utf-8")

    # 3. Orphan file (never imported)
    orphan_py = src_dir / "dead_module.py"
    orphan_py.write_text("""
def unused_dead_function():
    return "dead"
""", encoding="utf-8")

    # 4. Insecure file with dangerous call
    insecure_py = src_dir / "insecure.py"
    insecure_py.write_text("""
import subprocess

def run_cmd(user_input):
    return subprocess.run(user_input, shell=True)
""", encoding="utf-8")

    # 5. Test file
    test_py = tests_dir / "test_main.py"
    test_py.write_text("""
from src.main import run_app

def test_run_app():
    assert run_app() == 42
""", encoding="utf-8")

    # 6. package.json with dependencies
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({
        "name": "sample-project",
        "dependencies": {
            "react": "^19.0.0",
            "next": "^15.0.0",
            "tailwindcss": "^3.4.0"
        }
    }), encoding="utf-8")

    # 7. requirements.txt with python deps
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("""
fastapi>=0.110.0
sqlalchemy>=2.0.0
pytest>=8.0.0
""", encoding="utf-8")

    # Run auditor
    auditor = CodebaseAuditor(str(tmp_path), project_name="Acme Corp SaaS")
    result: CodebaseAuditResult = auditor.audit()

    # Assertions
    assert result.project_name == "Acme Corp SaaS"
    assert result.audit_id.startswith("audit_")
    assert 0 <= result.health_score <= 100
    assert result.metrics.total_files >= 6
    assert result.metrics.total_loc > 20
    assert result.metrics.test_files_count >= 1

    # Check framework detections
    detected_names = [t["name"] for t in result.tech_stack]
    assert "Next.js" in detected_names
    assert "React" in detected_names
    assert "FastAPI" in detected_names
    assert "SQLAlchemy" in detected_names
    assert "Pytest" in detected_names

    # Check orphan detection
    assert any("dead_module.py" in f for f in result.metrics.orphan_files)

    # Check security findings (detected shell=True)
    assert any("shell=True" in f.title for f in result.security_findings)

    # Check Mermaid diagram
    assert "flowchart TD" in result.mermaid_diagram
    assert "Axiom" in result.mermaid_diagram

    # Check 4-Week Milestone Roadmap
    assert len(result.milestones) == 4
    assert result.milestones[0]["week"] == 1
    assert result.milestones[3]["week"] == 4

    # Check Markdown report formatting
    assert "# Executive Architecture & Codebase Discovery Audit" in result.markdown_report
    assert "OVERALL CODEBASE HEALTH SCORE" in result.markdown_report
    assert "Next.js" in result.markdown_report
