"""
Tests for VS Code Extension Scaffold & Manifest Integrity.
Verifies package.json contributes all required commands, menus, configuration,
and that extension.ts contains correct activate/deactivate bindings.
"""

import json
from pathlib import Path


def test_vscode_package_json_manifest():
    manifest_path = Path("extension/package.json")
    assert manifest_path.exists(), "extension/package.json must exist"

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["name"] == "kobits-autonomous-engineer"
    assert "publisher" in data
    assert "version" in data
    assert data["engines"]["vscode"].startswith("^")

    commands = data["contributes"]["commands"]
    command_ids = [c["command"] for c in commands]

    expected_commands = [
        "kobits.login",
        "kobits.dispatchPrompt",
        "kobits.dispatchSelection",
        "kobits.viewDiff",
        "kobits.applyDiff",
        "kobits.showStatus",
    ]
    for exp in expected_commands:
        assert exp in command_ids, f"Command {exp} missing from package.json"

    # Context menu verification
    menus = data["contributes"]["menus"]
    assert "editor/context" in menus
    ctx_commands = [m["command"] for m in menus["editor/context"]]
    assert "kobits.dispatchSelection" in ctx_commands

    # Configuration verification
    props = data["contributes"]["configuration"]["properties"]
    assert "kobits.apiUrl" in props
    assert "kobits.autoStream" in props


def test_vscode_extension_source_structure():
    src_path = Path("extension/src/extension.ts")
    assert src_path.exists(), "extension/src/extension.ts must exist"

    content = src_path.read_text(encoding="utf-8")
    assert "export async function activate(" in content
    assert "export function deactivate(" in content

    # Check key endpoints and commands are implemented
    assert "/api/v1/extension/status" in content
    assert "/api/v1/extension/dispatch" in content
    assert "/api/v1/extension/missions/" in content
    assert "kobits.login" in content
    assert "kobits.dispatchPrompt" in content
    assert "kobits.dispatchSelection" in content
    assert "kobits.viewDiff" in content
    assert "kobits.applyDiff" in content
    assert "context.secrets" in content
