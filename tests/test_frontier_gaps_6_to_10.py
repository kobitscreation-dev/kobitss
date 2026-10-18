"""
Comprehensive Verification & Solid Proof Simulation Suite for Frontier Gaps 6 to 10:
- Gap 6: Dynamic Active Context Window & Sliding Working-Set Budgeting (AST Skeletons + Tombstones)
- Gap 7: Speculative Multi-Tool Call DAG Parallelism (Wave-Based Sub-Turn Execution)
- Gap 8: Atomic Multi-File Co-Editing & 3-Way AST Merge Conflict Healing
- Gap 9: Autonomous Build Toolchain & Polyglot Monorepo Auto-Detection (22+ Ecosystems)
- Gap 10: Multi-Modal Perceptual Visual Diffing (SSIM, 64-Bit dHash, Pixelmatch Heatmap & CLS Invariants)
- Unified 10-Gap Deep Console Telemetry Verification
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.services.context_compactor import WorkingSetBudgetManager
from backend.services.tool_registry import SpeculativeToolDAGExecutor, ToolRegistry
from backend.services.atomic_changeset import AtomicMultiFileTransaction, ASTThreeWayMergeHealer
from backend.services.toolchain_detector import AutonomousToolchainDetector
from backend.services.visual_qa import PerceptualVisualDiffEngine, VisualQAManager


# ---------------------------------------------------------------------------
# GAP 6: Dynamic Active Context Window & Sliding Working-Set Budgeting
# ---------------------------------------------------------------------------

def test_gap_6_working_set_budgeting_ast_skeleton_demotion_and_tombstone_digests():
    budgeter = WorkingSetBudgetManager(
        max_active_files=3,
        max_working_set_tokens=12000,
        tombstone_turn_ttl=3,
    )

    # Simulate loading 14 multi-hundred-line Python & TypeScript files across 14 turns
    for turn in range(1, 15):
        if turn % 2 == 1:
            fpath = f"backend/services/service_module_{turn}.py"
            body_lines = "\n".join([f"        x_{j} = value + {j} * 2" for j in range(60)])
            content = (
                f"import os\nfrom typing import Dict, Any\n\n"
                f"class PaymentProcessor_{turn}:\n"
                f'    """Handles enterprise settlement batch #{turn}."""\n'
                f"    def settle_batch(self, value: int, currency: str = 'USD') -> int:\n"
                f"{body_lines}\n"
                f"        return x_59\n"
            )
        else:
            fpath = f"web/src/components/Widget_{turn}.tsx"
            body_lines = "\n".join([f"  const step_{j} = props.amount * {j};" for j in range(60)])
            content = (
                f"import React from 'react';\n\n"
                f"export interface WidgetProps_{turn} {{\n"
                f"  amount: number;\n"
                f"  label: string;\n"
                f"}}\n\n"
                f"export function renderWidget_{turn}(props: WidgetProps_{turn}): string {{\n"
                f"{body_lines}\n"
                f"  return `${{props.label}}: ${{step_59}}`;\n"
                f"}}\n"
            )

        budgeter.touch_file(file_path=fpath, content=content, current_turn=turn)

        # Also record a large grep/test observation on each turn
        raw_grep = "\n".join([f"backend/services/service_module_{turn}.py:{line}: match_token" for line in range(1, 45)])
        budgeter.record_tool_observation(
            turn=turn,
            tool_name="code.search",
            args={"query": f"PaymentProcessor_{turn}"},
            raw_output=raw_grep,
        )

    # Render context at Turn 14
    ctx = budgeter.render_working_set_context(current_turn=14)

    # 1. Exactly 3 files remain in ACTIVE_FULL; 11 older files were demoted to COLLAPSED_AST_SKELETON
    assert ctx["active_file_count"] == 3
    assert ctx["skeleton_file_count"] == 11
    assert ctx["within_budget"] is True

    # 2. Verify AST skeletons preserve class/function/interface signatures while collapsing bodies
    prompt_block = ctx["prompt_block"]
    assert "class PaymentProcessor_1:" in prompt_block
    assert "def settle_batch(self, value: int, currency: str = 'USD') -> int:" in prompt_block
    assert "export interface WidgetProps_2" in prompt_block
    assert "[AST body collapsed:" in prompt_block

    # 3. Verify observations from Turns 1..11 (age >= 3 at Turn 14) are converted to Tombstone Digests
    assert ctx["tombstoned_observations"] == 11
    assert "[Semantic Digest Turn 1 | Tool: code.search('PaymentProcessor_1')]" in prompt_block

    # 4. Verify massive context bloat reduction (>65% tokens saved)
    assert ctx["token_reduction_pct"] >= 65.0
    assert ctx["budgeted_tokens"] < ctx["raw_tokens_without_budgeting"] * 0.35


# ---------------------------------------------------------------------------
# GAP 7: Speculative Multi-Tool Call DAG Parallelism
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gap_7_speculative_multi_tool_call_dag_parallelism():
    # Define realistic async handlers with 35ms simulated I/O latency
    async def mock_read(path: str):
        await asyncio.sleep(0.035)
        return {"path": path, "content": f"// source of {path}"}

    async def mock_lsp_def(symbol: str):
        await asyncio.sleep(0.035)
        return {"symbol": symbol, "found": True, "line": 42}

    async def mock_write(path: str, content: str):
        await asyncio.sleep(0.035)
        return {"path": path, "written": len(content)}

    async def mock_bash(command: str):
        await asyncio.sleep(0.035)
        return {"command": command, "exit_code": 0}

    custom_handlers = {
        "file.read": mock_read,
        "lsp.find_definition": mock_lsp_def,
        "file.write": mock_write,
        "bash.run": mock_bash,
    }

    # Submit a 9-call batch in a single turn:
    # - 5 independent reads/LSP queries -> Wave 0 (parallel)
    # - 2 writes to disjoint files (`a.ts`, `b.ts`) -> Wave 1 (parallel)
    # - 1 second write to `a.ts` (Write-After-Write hazard on `a.ts`!) -> Wave 2
    # - 1 global test runner barrier (`bash.run`) -> Wave 3 (barrier)
    tool_calls = [
        {"id": "r1", "name": "file.read", "args": {"path": "src/auth.ts"}},
        {"id": "r2", "name": "file.read", "args": {"path": "src/models.ts"}},
        {"id": "r3", "name": "file.read", "args": {"path": "src/config.ts"}},
        {"id": "l1", "name": "lsp.find_definition", "args": {"symbol": "UserSession"}},
        {"id": "l2", "name": "lsp.find_definition", "args": {"symbol": "TokenPayload"}},
        {"id": "w1", "name": "file.write", "args": {"path": "src/a.ts", "content": "export const a = 1;"}},
        {"id": "w2", "name": "file.write", "args": {"path": "src/b.ts", "content": "export const b = 2;"}},
        {"id": "w3", "name": "file.write", "args": {"path": "src/a.ts", "content": "export const a = 3;"}},
        {"id": "b1", "name": "bash.run", "args": {"command": "pytest -q"}},
    ]

    report = await ToolRegistry.execute_batch_dag(
        tool_calls=tool_calls,
        custom_handlers=custom_handlers,
    )

    assert report.total_calls == 9
    # Wave 0 holds all 5 read-only calls + disjoint writes w1, w2 (since a.ts and b.ts are not read in Wave 0!)
    # Wave 1 holds w3 (serialized after w1 on src/a.ts)
    # Wave 2 holds b1 (global shell barrier after all file reads/writes)
    assert report.total_waves == 3
    assert set(report.waves[0]) == {"r1", "r2", "r3", "l1", "l2", "w1", "w2"}
    assert report.waves[1] == ["w3"]
    assert report.waves[2] == ["b1"]

    # Verify wall-clock speedup and >65% latency reduction
    assert report.speedup_factor >= 2.5
    assert report.latency_reduction_pct >= 65.0
    assert all(r["status"] == "COMPLETED" for r in report.results)


# ---------------------------------------------------------------------------
# GAP 8: Atomic Multi-File Co-Editing & 3-Way AST Merge Conflict Healing
# ---------------------------------------------------------------------------

def test_gap_8_atomic_multi_file_coediting_and_3way_ast_merge_healing():
    with tempfile.TemporaryDirectory() as ws:
        src_dir = os.path.join(ws, "src")
        os.makedirs(src_dir, exist_ok=True)

        types_path = os.path.join(src_dir, "types.ts")
        checkout_path = os.path.join(src_dir, "checkout.ts")
        receipt_path = os.path.join(src_dir, "receipt.ts")

        orig_types = (
            "export interface OrderPayload {\n"
            "  orderId: string;\n"
            "  subtotal: number;\n"
            "}\n"
        )
        orig_checkout = (
            "import { OrderPayload } from './types';\n\n"
            "export const sampleOrder: OrderPayload = {\n"
            "  orderId: 'ORD-100',\n"
            "  subtotal: 250\n"
            "};\n"
        )
        orig_receipt = (
            "import { OrderPayload } from './types';\n\n"
            "export const receiptOrder: OrderPayload = {\n"
            "  orderId: 'ORD-101',\n"
            "  subtotal: 99\n"
            "};\n"
        )

        with open(types_path, "w", encoding="utf-8") as f:
            f.write(orig_types)
        with open(checkout_path, "w", encoding="utf-8") as f:
            f.write(orig_checkout)
        with open(receipt_path, "w", encoding="utf-8") as f:
            f.write(orig_receipt)

        # 1. Attempt a BROKEN / INCOMPLETE multi-file co-edit:
        # Adds required property `currency: string` to `OrderPayload` in `types.ts`
        # and updates `checkout.ts`, but forgets `receipt.ts` (causing TS2741 in receipt.ts!)
        broken_tx = AtomicMultiFileTransaction.apply_atomic_changeset(
            workspace_dir=ws,
            edits=[
                {
                    "file_path": "src/types.ts",
                    "content": (
                        "export interface OrderPayload {\n"
                        "  orderId: string;\n"
                        "  subtotal: number;\n"
                        "  currency: string;\n"
                        "}\n"
                    ),
                },
                {
                    "file_path": "src/checkout.ts",
                    "content": (
                        "import { OrderPayload } from './types';\n\n"
                        "export const sampleOrder: OrderPayload = {\n"
                        "  orderId: 'ORD-100',\n"
                        "  subtotal: 250,\n"
                        "  currency: 'USD'\n"
                        "};\n"
                    ),
                },
            ],
        )

        # Must abort and atomically roll back EVERY file!
        assert broken_tx.committed is False
        assert broken_tx.rolled_back is True
        assert broken_tx.new_lsp_errors >= 1
        with open(types_path, "r", encoding="utf-8") as f:
            assert f.read() == orig_types
        with open(checkout_path, "r", encoding="utf-8") as f:
            assert f.read() == orig_checkout

        # 2. Apply COMPLETE 3-file atomic co-edit (types.ts + checkout.ts + receipt.ts)
        valid_tx = AtomicMultiFileTransaction.apply_atomic_changeset(
            workspace_dir=ws,
            edits=[
                {
                    "file_path": "src/types.ts",
                    "content": (
                        "export interface OrderPayload {\n"
                        "  orderId: string;\n"
                        "  subtotal: number;\n"
                        "  currency: string;\n"
                        "}\n"
                    ),
                },
                {
                    "file_path": "src/checkout.ts",
                    "content": (
                        "import { OrderPayload } from './types';\n\n"
                        "export const sampleOrder: OrderPayload = {\n"
                        "  orderId: 'ORD-100',\n"
                        "  subtotal: 250,\n"
                        "  currency: 'USD'\n"
                        "};\n"
                    ),
                },
                {
                    "file_path": "src/receipt.ts",
                    "content": (
                        "import { OrderPayload } from './types';\n\n"
                        "export const receiptOrder: OrderPayload = {\n"
                        "  orderId: 'ORD-101',\n"
                        "  subtotal: 99,\n"
                        "  currency: 'EUR'\n"
                        "};\n"
                    ),
                },
            ],
        )
        assert valid_tx.committed is True
        assert valid_tx.rolled_back is False
        assert valid_tx.new_lsp_errors == 0
        assert len(valid_tx.files_modified) == 3

        # 3. Test 3-Way AST Merge Conflict Healer on Python & TypeScript conflicts
        conflicted_py_path = os.path.join(ws, "payments.py")
        conflicted_py = (
            "<<<<<<< HEAD\n"
            "from billing.utils import validate_card, format_cents\n"
            "=======\n"
            "from billing.utils import format_cents, compute_vat\n"
            ">>>>>>> origin/main\n\n"
            "<<<<<<< HEAD\n"
            "def authorize_payment(amount: int) -> bool:\n"
            "    return amount > 0\n"
            "=======\n"
            "def refund_payment(tx_id: str) -> str:\n"
            "    return f'refunded:{tx_id}'\n"
            ">>>>>>> origin/main\n"
        )
        heal_res = ASTThreeWayMergeHealer.heal_conflicted_file(
            file_path=conflicted_py_path,
            conflicted_content=conflicted_py,
            write_to_disk=True,
        )
        assert heal_res["healed"] is True
        assert heal_res["conflicts_detected"] == 2
        assert heal_res["conflicts_healed"] == 2
        assert heal_res["markers_remaining"] is False
        assert heal_res["syntax_valid"] is True
        healed_code = heal_res["healed_content"]
        assert "from billing.utils import validate_card, format_cents, compute_vat" in healed_code
        assert "def authorize_payment" in healed_code
        assert "def refund_payment" in healed_code


# ---------------------------------------------------------------------------
# GAP 9: Autonomous Build Toolchain & Polyglot Monorepo Auto-Detection
# ---------------------------------------------------------------------------

def test_gap_9_autonomous_build_toolchain_and_polyglot_monorepo_detection():
    with tempfile.TemporaryDirectory() as mono_root:
        # 1. Root Turborepo + pnpm workspace markers
        with open(os.path.join(mono_root, "pnpm-workspace.yaml"), "w", encoding="utf-8") as f:
            f.write("packages:\n  - 'apps/*'\n  - 'services/*'\n")
        with open(os.path.join(mono_root, "turbo.json"), "w", encoding="utf-8") as f:
            f.write('{"pipeline": {"build": {}, "test": {}}}\n')

        # 2. apps/web -> TypeScript + pnpm + vitest
        web_dir = os.path.join(mono_root, "apps", "web")
        os.makedirs(web_dir, exist_ok=True)
        with open(os.path.join(web_dir, "package.json"), "w", encoding="utf-8") as f:
            f.write('{"name": "@acme/web", "scripts": {"build": "vite build", "test": "vitest run"}, "devDependencies": {"typescript": "^5.5.0"}}')
        with open(os.path.join(web_dir, "pnpm-lock.yaml"), "w", encoding="utf-8") as f:
            f.write("lockfileVersion: '9.0'\n")

        # 3. services/ai-engine -> Python + Poetry
        py_dir = os.path.join(mono_root, "services", "ai-engine")
        os.makedirs(py_dir, exist_ok=True)
        with open(os.path.join(py_dir, "pyproject.toml"), "w", encoding="utf-8") as f:
            f.write("[tool.poetry]\nname = 'ai-engine'\n")
        with open(os.path.join(py_dir, "poetry.lock"), "w", encoding="utf-8") as f:
            f.write("[[package]]\nname = 'pytest'\n")

        # 4. crates/indexer -> Rust + Cargo
        rust_dir = os.path.join(mono_root, "crates", "indexer")
        os.makedirs(rust_dir, exist_ok=True)
        with open(os.path.join(rust_dir, "Cargo.toml"), "w", encoding="utf-8") as f:
            f.write('[package]\nname = "indexer"\nversion = "0.1.0"\n')

        # 5. services/ledger -> Java + Gradle Wrapper
        java_dir = os.path.join(mono_root, "services", "ledger")
        os.makedirs(java_dir, exist_ok=True)
        with open(os.path.join(java_dir, "build.gradle.kts"), "w", encoding="utf-8") as f:
            f.write("plugins { java }\n")
        with open(os.path.join(java_dir, "gradlew"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")

        matrix = AutonomousToolchainDetector.detect_repository_toolchains(mono_root)

        assert matrix.is_monorepo is True
        assert set(matrix.detected_ecosystems) >= {"typescript", "python", "rust", "java-gradle"}
        assert set(matrix.package_managers) >= {"pnpm", "poetry", "cargo", "gradle"}
        assert "pnpm install --frozen-lockfile" in matrix.install_cmd
        assert "poetry install --no-interaction" in matrix.install_cmd
        assert "cargo test" in matrix.test_cmd
        assert "./gradlew test" in matrix.test_cmd


# ---------------------------------------------------------------------------
# GAP 10: Multi-Modal Perceptual Visual Diffing (SSIM, dHash & CLS Invariants)
# ---------------------------------------------------------------------------

def test_gap_10_perceptual_visual_diffing_ssim_dhash_and_cls_invariants():
    baseline_dom = [
        {"selector": "#navbar", "x": 0, "y": 0, "width": 1920, "height": 64, "color": "#0f172a"},
        {"selector": "#hero-cta-btn", "x": 120, "y": 240, "width": 220, "height": 52, "color": "#2563eb"},
        {"selector": "#pricing-grid", "x": 120, "y": 380, "width": 1200, "height": 420, "color": "#1e293b"},
        {"selector": "#footer", "x": 0, "y": 980, "width": 1920, "height": 100, "color": "#090d16"},
    ]

    # Scenario A: Intentional update to `#hero-cta-btn` (button color & width updated, rest untouched)
    after_intentional_dom = [
        {"selector": "#navbar", "x": 0, "y": 0, "width": 1920, "height": 64, "color": "#0f172a"},
        {"selector": "#hero-cta-btn", "x": 120, "y": 240, "width": 260, "height": 56, "color": "#10b981"},
        {"selector": "#pricing-grid", "x": 120, "y": 380, "width": 1200, "height": 420, "color": "#1e293b"},
        {"selector": "#footer", "x": 0, "y": 980, "width": 1920, "height": 100, "color": "#090d16"},
    ]

    report_ok = VisualQAManager.compare_before_after(
        before_dom_boxes=baseline_dom,
        after_dom_boxes=after_intentional_dom,
        target_selectors=["#hero-cta-btn"],
        viewport="desktop",
    )

    assert report_ok.classification == "INTENTIONAL_TARGET_UPDATE"
    assert report_ok.regression_detected is False
    assert report_ok.cumulative_layout_shift == 0.0
    assert report_ok.ssim_score > 0.85
    assert report_ok.diff_mask_data_uri.startswith("data:image/png;base64,")
    assert report_ok.changed_selectors == ["#hero-cta-btn"]
    assert report_ok.unintended_shifted_selectors == []

    # Scenario B: Unintended visual regression (footer collapses to height=0 and pricing-grid overflows viewport)
    after_broken_dom = [
        {"selector": "#navbar", "x": 0, "y": 0, "width": 1920, "height": 64, "color": "#0f172a"},
        {"selector": "#hero-cta-btn", "x": 120, "y": 240, "width": 260, "height": 56, "color": "#10b981"},
        {"selector": "#pricing-grid", "x": 450, "y": 580, "width": 1650, "height": 420, "color": "#1e293b"},
        {"selector": "#footer", "x": 0, "y": 980, "width": 1920, "height": 0, "color": "#090d16"},
    ]

    report_reg = PerceptualVisualDiffEngine.compare_before_after(
        before_dom_boxes=baseline_dom,
        after_dom_boxes=after_broken_dom,
        target_selectors=["#hero-cta-btn"],
        viewport="desktop",
    )

    assert report_reg.classification == "UNINTENDED_VISUAL_REGRESSION"
    assert report_reg.regression_detected is True
    assert report_reg.cumulative_layout_shift > 0.0
    assert "#pricing-grid" in report_reg.unintended_shifted_selectors
    assert "#footer" in report_reg.unintended_shifted_selectors
    assert len(report_reg.findings) >= 2


# ---------------------------------------------------------------------------
# UNIFIED 10-GAP TELEMETRY VERIFICATION
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unified_10_frontier_gaps_telemetry_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/agency/console/telemetry")
        assert res.status_code == 200
        data = res.json()

        # Verify all 10 frontier subsystems report live telemetry
        expected_keys = [
            "warm_snapshot_pool",
            "lsp_daemon_status",
            "prompt_cache_economics",
            "cdp_browser_stream",
            "mcts_search_tree",
            "working_set_budget",
            "speculative_tool_dag",
            "atomic_changeset_and_merge",
            "toolchain_auto_detector",
            "perceptual_visual_diff",
        ]
        for key in expected_keys:
            assert key in data, f"Missing telemetry subsystem: {key}"

        assert data["working_set_budget"]["lost_in_the_middle_mitigated"] is True
        assert data["speculative_tool_dag"]["enabled"] is True
        assert data["atomic_changeset_and_merge"]["atomic_transactions_committed"] >= 1
        assert data["toolchain_auto_detector"]["supported_ecosystems_count"] >= 20
        assert data["perceptual_visual_diff"]["comparisons_run"] >= 2
