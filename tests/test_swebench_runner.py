"""
Automated Test Suite for SWE-bench Official Evaluation Harness (benchmarks/swebench_runner.py).
"""

import os
import json
import pytest
import tempfile
import subprocess
from pathlib import Path

from benchmarks.swebench_runner import (
    SWEBenchInstance,
    SWEBenchPrediction,
    SWEBenchEvaluationReport,
    SWEBenchRunner,
    BUILTIN_SWEBENCH_INSTANCES,
)


def test_swebench_instance_parsing():
    """Tests parsing varying SWE-bench dataset schemas (stringified JSON vs lists)."""
    # Case 1: Stringified JSON lists
    raw_dict_1 = {
        "instance_id": "django__django-11099",
        "repo": "django/django",
        "base_commit": "4f48866734c7407a50ca82747bb70cf1e29082ec",
        "problem_statement": "UsernameValidator allows trailing newline in usernames.",
        "FAIL_TO_PASS": '["test_ascii_validator_rejects_trailing_newline"]',
        "PASS_TO_PASS": '["test_ascii_validator_accepts_valid"]',
    }
    inst1 = SWEBenchInstance.from_dict(raw_dict_1)
    assert inst1.instance_id == "django__django-11099"
    assert inst1.repo_name == "django"
    assert inst1.repo_owner == "django"
    assert inst1.fail_to_pass == ["test_ascii_validator_rejects_trailing_newline"]
    assert inst1.pass_to_pass == ["test_ascii_validator_accepts_valid"]

    # Case 2: Native Python lists
    raw_dict_2 = {
        "instance_id": "pallets__flask-4045",
        "repo": "pallets/flask",
        "base_commit": "3cf7583ee91f09322254050d53c306d860d5dd6c",
        "problem_statement": "Raise error if blueprint name contains a dot.",
        "fail_to_pass": ["test_blueprint_dot_name_raises"],
        "pass_to_pass": ["test_blueprint_valid_name"],
    }
    inst2 = SWEBenchInstance.from_dict(raw_dict_2)
    assert inst2.instance_id == "pallets__flask-4045"
    assert inst2.repo_name == "flask"
    assert inst2.repo_owner == "pallets"
    assert inst2.fail_to_pass == ["test_blueprint_dot_name_raises"]


def test_builtin_instances():
    """Verifies that built-in reference instances are valid and complete."""
    instances = SWEBenchRunner.get_builtin_instances()
    assert len(instances) >= 4
    instance_ids = [i.instance_id for i in instances]
    assert "django__django-11099" in instance_ids
    assert "pallets__flask-4045" in instance_ids
    assert "psf__requests-2674" in instance_ids
    assert "sympy__sympy-20590" in instance_ids

    for inst in instances:
        assert len(inst.fail_to_pass) > 0
        assert len(inst.base_commit) > 0
        assert len(inst.problem_statement) > 0


def test_load_instances_from_json_and_jsonl(tmp_path):
    """Tests loading instances from both JSON array and JSONL files."""
    # Write JSON array
    json_path = tmp_path / "instances.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(BUILTIN_SWEBENCH_INSTANCES[:2], f)

    loaded_json = SWEBenchRunner.load_instances_from_file(json_path)
    assert len(loaded_json) == 2
    assert loaded_json[0].instance_id == "django__django-11099"

    # Write JSONL
    jsonl_path = tmp_path / "instances.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for item in BUILTIN_SWEBENCH_INSTANCES[:2]:
            f.write(json.dumps(item) + "\n")

    loaded_jsonl = SWEBenchRunner.load_instances_from_file(jsonl_path)
    assert len(loaded_jsonl) == 2
    assert loaded_jsonl[1].instance_id == "pallets__flask-4045"


def test_synthesize_reproducer_script():
    """Verifies synthesizer produces valid python script structure."""
    runner = SWEBenchRunner()
    django_inst = SWEBenchInstance.from_dict(BUILTIN_SWEBENCH_INSTANCES[0])
    script = runner.synthesize_reproducer_script(django_inst)
    assert "ASCIIUsernameValidator" in script
    assert "sys.exit(1)" in script
    assert "sys.exit(0)" in script

    flask_inst = SWEBenchInstance.from_dict(BUILTIN_SWEBENCH_INSTANCES[1])
    flask_script = runner.synthesize_reproducer_script(flask_inst)
    assert "Blueprint" in flask_script


def test_evaluate_instance_end_to_end(tmp_path):
    """
    Tests end-to-end instance evaluation:
    1. Base repo initialization
    2. Negative baseline verification (reproducer asserts failure on unpatched code)
    3. Best-of-N candidate exploration samples candidate patch
    4. Reproducer test passes after patch
    5. Clean diff extraction without reproducer test contamination
    """
    runner = SWEBenchRunner(model_name="kobits-devin-parity", candidates_n=2)
    inst = SWEBenchInstance.from_dict(BUILTIN_SWEBENCH_INSTANCES[0])  # django__django-11099

    worktree = str(tmp_path / "eval_worktree")
    prediction = runner.evaluate_instance(inst, worktree_dir=worktree)

    assert prediction.instance_id == "django__django-11099"
    assert prediction.reproducer_passed is True
    assert prediction.is_resolved is True
    assert prediction.model_name_or_path == "kobits-devin-parity"
    assert "diff --git" in prediction.model_patch
    assert "validators.py" in prediction.model_patch
    # Verify the reproducer script itself was NOT included in the git diff!
    assert "reproduce_issue.py" not in prediction.model_patch


def test_run_suite_and_export_predictions(tmp_path):
    """
    Tests running an evaluation suite and exporting standard predictions.json.
    """
    runner = SWEBenchRunner(model_name="kobits-sota", candidates_n=2)
    instances = [
        SWEBenchInstance.from_dict(BUILTIN_SWEBENCH_INSTANCES[0]),
        SWEBenchInstance.from_dict(BUILTIN_SWEBENCH_INSTANCES[1]),
    ]

    out_file = str(tmp_path / "predictions.json")
    report: SWEBenchEvaluationReport = runner.run_suite(instances, output_file=out_file)

    assert report.total_instances == 2
    assert report.resolved_instances == 2
    assert report.solve_rate_pct == 100.0
    assert os.path.exists(out_file)

    # Verify official SWE-bench submission schema
    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "django__django-11099" in data
    assert "pallets__flask-4045" in data
    assert data["django__django-11099"]["model_name_or_path"] == "kobits-sota"
    assert "diff --git" in data["django__django-11099"]["model_patch"]

    # Verify evaluation report JSON
    report_file = str(tmp_path / "predictions_report.json")
    assert os.path.exists(report_file)
    with open(report_file, "r", encoding="utf-8") as rf:
        rep_data = json.load(rf)
    assert rep_data["total_instances"] == 2
    assert rep_data["solve_rate_pct"] == 100.0
    assert "django" in rep_data["repo_breakdown"]
    assert "flask" in rep_data["repo_breakdown"]
