"""
Comprehensive Verification Suite for Frontier Engineering Gaps 1 to 5:
1. Gap 1: MicroVM Warm-Pool Snapshotting (<800ms Instant Resume vs. Cold Boot)
2. Gap 2: Native JSON-RPC 2.0 Language Server Daemons + Multi-Hop TS Barrel & Semantic Type-Checker
3. Gap 3: Long-Horizon Prompt Caching & Token Economics (90%+ KV Cache Hit Rate)
4. Gap 4: Live Interactive Browser Canvas (30fps CDP Screencast Stream & Multimodal Vision Loop)
5. Gap 5: Monte Carlo Tree Search (MCTS) & Hypothesis Branching with Automatic Dead-End Pruning
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.services.cloud_sandbox import CloudMicroVMSandbox, WarmSnapshotPoolManager
from backend.services.lsp_service import HeadlessLSPService, LSPJsonRpcDaemonClient
from backend.services.prompt_cache import PromptCacheOptimizer
from backend.services.browser_engine import CDPStreamManager
from backend.services.best_of_n import BestOfNExplorer, MCTSHypothesisExplorer


# ---------------------------------------------------------------------------
# GAP 1: MicroVM Warm-Pool Snapshotting (<800ms Instant Resume)
# ---------------------------------------------------------------------------

def test_gap_1_microvm_warm_pool_snapshotting_under_800ms():
    with tempfile.TemporaryDirectory() as source_repo:
        # 1. Create realistic lockfiles and source code in source_repo
        with open(os.path.join(source_repo, "requirements.txt"), "w", encoding="utf-8") as f:
            f.write("fastapi==0.115.0\npydantic==2.9.2\nsqlalchemy==2.0.35\npytest==8.3.3\n")
        with open(os.path.join(source_repo, "package.json"), "w", encoding="utf-8") as f:
            f.write('{"name": "kobits-web", "dependencies": {"vite": "^5.4.0", "typescript": "^5.6.0"}}')
        os.makedirs(os.path.join(source_repo, "src"), exist_ok=True)
        with open(os.path.join(source_repo, "src", "app.py"), "w", encoding="utf-8") as f:
            f.write("def health() -> str:\n    return 'ok'\n")

        # 2. Pre-warm a pool of 3 MicroVM snapshots keyed by repo + lockfile hash
        pool_list = WarmSnapshotPoolManager.prewarm_pool(
            pool_size=3,
            repo_path=source_repo,
        )
        assert len(pool_list) >= 3
        snap = WarmSnapshotPoolManager.capture_snapshot(repo_path=source_repo, tag="acme-platform-main")
        assert snap.snapshot_id.startswith("snap-vm-")

        # 3. Provision a new mission sandbox using the warm pool and verify <800ms SLA
        session_id = f"warm-mission-{uuid.uuid4().hex[:6]}"
        sb, restore_meta = WarmSnapshotPoolManager.restore_from_warm_pool(
            session_id=session_id,
            repo_path=source_repo,
            snapshot_id=snap.snapshot_id,
        )

        assert sb.sandbox_id.startswith("fc-vm-warm-")
        assert restore_meta["warm_cache_hit"] is True
        assert restore_meta["sla_met"] is True
        assert restore_meta["restore_latency_ms"] < 800.0
        assert "/workspace/requirements.txt" in sb._sandbox.files._storage
        assert "/workspace/package.json" in sb._sandbox.files._storage

        # Also test CloudMicroVMSandbox.restore_warm_snapshot convenience entrypoint
        sb2 = CloudMicroVMSandbox.restore_warm_snapshot(
            session_id=f"{session_id}-2",
            repo_path=source_repo,
            snapshot_id=snap.snapshot_id,
        )
        assert sb2.sandbox_id.startswith("fc-vm-warm-")

        telemetry = CloudMicroVMSandbox.get_warm_pool_telemetry()
        assert telemetry["total_restores"] >= 2
        assert telemetry["sla_target_ms"] == 800.0
        assert telemetry["avg_restore_latency_ms"] < 800.0
        assert telemetry["sla_compliant"] is True


# ---------------------------------------------------------------------------
# GAP 2: Native JSON-RPC 2.0 LSP Framing + Multi-Hop TS Barrels + TS Semantics
# ---------------------------------------------------------------------------

def test_gap_2_lsp_jsonrpc_daemon_multi_hop_barrels_and_typescript_semantics():
    # 1. Verify JSON-RPC 2.0 Content-Length framing (initialize / didOpen / definition)
    init_frames = LSPJsonRpcDaemonClient.build_initialize_sequence("/workspace/repo")
    assert len(init_frames) == 2
    assert init_frames[0].startswith(b"Content-Length: ")
    decoded = LSPJsonRpcDaemonClient.decode_jsonrpc_frame(b"".join(init_frames))
    assert len(decoded) == 2
    assert decoded[0]["jsonrpc"] == "2.0"
    assert decoded[0]["method"] == "initialize"
    assert decoded[1]["method"] == "initialized"

    with tempfile.TemporaryDirectory() as ws:
        # 2. Create 3-hop TypeScript barrel hierarchy:
        # src/consumer.ts -> ./components (index.ts) -> ./widgets (index.ts) -> ./PaymentCard.tsx
        comp_dir = os.path.join(ws, "src", "components")
        wid_dir = os.path.join(comp_dir, "widgets")
        os.makedirs(wid_dir, exist_ok=True)

        payment_card_path = os.path.join(wid_dir, "PaymentCard.tsx")
        with open(payment_card_path, "w", encoding="utf-8") as f:
            f.write(
                "export interface PaymentProps {\n"
                "  amount: number;\n"
                "  currency: string;\n"
                "  memo?: string;\n"
                "}\n\n"
                "export function renderPaymentCard(amount: number, currency: string): string {\n"
                "  return `${currency} ${amount}`;\n"
                "}\n"
            )

        with open(os.path.join(wid_dir, "index.ts"), "w", encoding="utf-8") as f:
            f.write("export * from './PaymentCard';\n")

        with open(os.path.join(comp_dir, "index.ts"), "w", encoding="utf-8") as f:
            f.write("export { renderPaymentCard, type PaymentProps } from './widgets';\n")

        consumer_path = os.path.join(ws, "src", "consumer.ts")
        with open(consumer_path, "w", encoding="utf-8") as f:
            f.write(
                "import { renderPaymentCard, PaymentProps } from './components';\n\n"
                "const validProps: PaymentProps = {\n"
                "  amount: 250,\n"
                "  currency: 'USD'\n"
                "};\n"
                "const res = renderPaymentCard(250, 'USD');\n"
            )

        # Jump to definition of `renderPaymentCard` from `src/consumer.ts` across 2 barrel index.ts hops
        def_res = HeadlessLSPService.find_definition(
            symbol="renderPaymentCard",
            file_path="src/consumer.ts",
            workspace_dir=ws,
        )
        assert def_res["found"] is True
        assert def_res["file_path"].endswith("src/components/widgets/PaymentCard.tsx")
        assert def_res["line"] == 7

        # Clean consumer.ts should have 0 compiler diagnostics
        clean_diag = HeadlessLSPService.get_diagnostics(file_path="src/consumer.ts", workspace_dir=ws)
        assert clean_diag["error_count"] == 0

        # 3. Introduce TypeScript semantic errors in broken_consumer.ts (TS2305, TS2322, TS2741, TS2554)
        broken_path = os.path.join(ws, "src", "broken_consumer.ts")
        with open(broken_path, "w", encoding="utf-8") as f:
            f.write(
                "import { renderPaymentCard, PaymentProps, NonExistentSymbol } from './components';\n\n"
                "const badCount: number = 'not-a-number';\n"
                "const missingCurrencyProps: PaymentProps = {\n"
                "  amount: 100\n"
                "};\n"
                "const wrongFieldTypeProps: PaymentProps = {\n"
                "  amount: 'wrong-type',\n"
                "  currency: 'EUR'\n"
                "};\n"
                "function computeTax(subtotal: number, rate: number): number {\n"
                "  return subtotal * rate;\n"
                "}\n"
                "const tax = computeTax(100);\n"
            )

        broken_diag = HeadlessLSPService.get_diagnostics(file_path="src/broken_consumer.ts", workspace_dir=ws)
        codes = {d["code"] for d in broken_diag["diagnostics"]}
        assert "TS2305" in codes  # NonExistentSymbol not exported
        assert "TS2322" in codes  # Type mismatch ('not-a-number' -> number, 'wrong-type' -> number)
        assert "TS2741" in codes  # Property 'currency' missing in missingCurrencyProps
        assert "TS2554" in codes  # computeTax(100) missing 2nd argument

        # 4. Ripple Diagnostic Test: Add a required field `merchantId: string` to `PaymentProps` in PaymentCard.tsx
        # and verify `src/consumer.ts` immediately flags TS2741 via compiler_ripple!
        with open(payment_card_path, "w", encoding="utf-8") as f:
            f.write(
                "export interface PaymentProps {\n"
                "  amount: number;\n"
                "  currency: string;\n"
                "  merchantId: string;\n"
                "}\n\n"
                "export function renderPaymentCard(amount: number, currency: string): string {\n"
                "  return `${currency} ${amount}`;\n"
                "}\n"
            )

        ripple_diag = HeadlessLSPService.get_diagnostics(
            file_path="src/components/widgets/PaymentCard.tsx",
            workspace_dir=ws,
        )
        ripple_errors = [
            d for d in ripple_diag["diagnostics"]
            if d["file"].endswith("src/consumer.ts") and d["code"] == "TS2741"
        ]
        assert len(ripple_errors) >= 1
        assert "merchantId" in ripple_errors[0]["message"]


# ---------------------------------------------------------------------------
# GAP 3: Long-Horizon Prompt Caching & Token Economics (90%+ KV Hit Rate)
# ---------------------------------------------------------------------------

def test_gap_3_long_horizon_prompt_caching_90_percent_kv_hit_rate():
    optimizer = PromptCacheOptimizer(uncached_tail_turns=1)
    session_id = f"mission-kv-{uuid.uuid4().hex[:8]}"

    # Realistic large system prompt + repository AST map + 18 tool schemas (~20,000+ static prefix tokens)
    system_prompt = "You are Kobits Axiom/Forge Autonomous Engineer.\n" + ("# Core Architecture Invariant Rule\n" * 600)
    repo_ast_map = "\n".join(
        [f"module backend.services.module_{i}: class Service_{i}(execute, validate, rollback, serialize)" for i in range(450)]
    )
    tools = [
        {
            "name": f"tool_{i}",
            "description": f"Deterministic sandbox tool #{i} for AST navigation, file editing, and test execution.",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "line": {"type": "integer"}}},
        }
        for i in range(18)
    ]

    messages = [
        {"role": "user", "content": "Fix the cross-tenant authentication bug in backend/api/v1/webhooks.py and verify tests."}
    ]

    # Pre-warm session prefix
    optimizer.prewarm_session_prefix(
        session_id=session_id,
        system_prompt=system_prompt,
        tools=tools,
        repo_ast_map=repo_ast_map,
        initial_user_prompt=messages[0]["content"],
    )

    turn_metrics = []
    for turn in range(1, 31):
        # Append concise compacted assistant + tool observation turn
        messages.append({
            "role": "assistant",
            "content": f"Turn {turn}: Inspecting symbol references and verifying hypothesis #{turn}.",
        })
        messages.append({
            "role": "user",
            "content": f"[Observation Turn {turn}]: LSP check clean (0 errors); pytest 14/14 passed.",
        })
        payload, metrics = optimizer.build_cached_payload(
            session_id=session_id,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            repo_ast_map=repo_ast_map,
        )
        turn_metrics.append(metrics)
        assert metrics.prefix_invariant_verified is True
        # Verify cache_control breakpoints exist on system, repo map, and last tool
        assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert payload["system"][1]["cache_control"] == {"type": "ephemeral"}
        assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}

    avg_hit_rate = sum(m.cache_hit_rate_pct for m in turn_metrics) / len(turn_metrics)
    total_uncached_usd = sum(m.uncached_cost_usd for m in turn_metrics)
    total_cached_usd = sum(m.cached_cost_usd for m in turn_metrics)
    savings_pct = ((total_uncached_usd - total_cached_usd) / total_uncached_usd) * 100.0
    last_turn = turn_metrics[-1]

    assert avg_hit_rate >= 90.0
    assert savings_pct >= 80.0
    assert last_turn.estimated_ttft_cached_ms < 1500.0
    assert last_turn.estimated_ttft_uncached_ms > 4000.0
    assert last_turn.estimated_ttft_uncached_ms / last_turn.estimated_ttft_cached_ms > 5.0


# ---------------------------------------------------------------------------
# GAP 4: Live Interactive Browser Canvas (30fps CDP Stream & Input Dispatch)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gap_4_live_interactive_cdp_browser_canvas_30fps_and_input_dispatch():
    mission_id = f"cdp-mission-{uuid.uuid4().hex[:8]}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Fetch 30fps CDP Screencast frame
        res_frame = await client.get(f"/api/v1/preview/{mission_id}/cdp-stream")
        assert res_frame.status_code == 200
        frame_data = res_frame.json()
        assert frame_data["protocol"] == "CDP Page.screencastFrame"
        assert frame_data["target_fps"] == 30
        assert frame_data["frame_data_uri"].startswith("data:image/png;base64,")
        assert len(frame_data["dom_bounding_boxes"]) >= 3
        assert frame_data["agent_cursor"]["visible"] is True

        # 2. Dispatch interactive click on #hero-cta-btn (x=150, y=255)
        res_click = await client.post(
            f"/api/v1/preview/{mission_id}/cdp-input",
            json={"type": "click", "x": 150, "y": 255},
        )
        assert res_click.status_code == 200
        click_data = res_click.json()
        assert click_data["status"] == "dispatched"
        assert click_data["hit_element"] is not None
        assert click_data["hit_element"]["selector"] == "#hero-cta-btn"

        # 3. Dispatch interactive typing into email input
        res_type = await client.post(
            f"/api/v1/preview/{mission_id}/cdp-input",
            json={"type": "type", "selector": "input[name='email']", "text": "founder@acme.io"},
        )
        assert res_type.status_code == 200
        type_data = res_type.json()
        assert type_data["action"]["selector"] == "input[name='email']"
        assert type_data["action"]["text"] == "founder@acme.io"

        telemetry = CDPStreamManager.get_telemetry()
        assert telemetry["active_streams"] >= 1
        assert telemetry["target_fps"] == 30


# ---------------------------------------------------------------------------
# GAP 5: Monte Carlo Tree Search (MCTS) & Hypothesis Branching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gap_5_mcts_hypothesis_tree_search_and_dead_end_pruning():
    with tempfile.TemporaryDirectory() as sbox:
        # Initialize git repository in sandbox
        subprocess.run(["git", "init"], cwd=sbox, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "kobits@kobits.space"], cwd=sbox, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Kobits MCTS"], cwd=sbox, check=True, capture_output=True)

        # Create initial buggy billing module + reproducer + regression test
        with open(os.path.join(sbox, "billing.py"), "w", encoding="utf-8") as f:
            f.write(
                "def calculate_invoice_total(subtotal: float, discount_pct: float = 0.0) -> float:\n"
                "    # BUG: adds discount instead of subtracting and ignores negative cap\n"
                "    return subtotal + (subtotal * discount_pct)\n"
            )

        with open(os.path.join(sbox, "reproduce_issue.py"), "w", encoding="utf-8") as f:
            f.write(
                "from billing import calculate_invoice_total\n"
                "assert abs(calculate_invoice_total(200.0, 0.25) - 150.0) < 1e-6\n"
                "assert calculate_invoice_total(100.0, 1.5) == 0.0\n"
            )

        with open(os.path.join(sbox, "test_regression.py"), "w", encoding="utf-8") as f:
            f.write(
                "from billing import calculate_invoice_total\n"
                "assert calculate_invoice_total(100.0, 0.0) == 100.0\n"
            )

        subprocess.run(["git", "add", "-A"], cwd=sbox, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial buggy baseline"], cwd=sbox, check=True, capture_output=True)

        # Define 3 competing MCTS architectural hypotheses:
        # Hypothesis 1: Introduces a circular dependency (billing.py <-> tax_helper.py) -> Must be pruned!
        def apply_hypothesis_circular(ws: str):
            with open(os.path.join(ws, "tax_helper.py"), "w", encoding="utf-8") as f:
                f.write("from billing import calculate_invoice_total\ndef apply_discount(x, d): return x * (1 - d)\n")
            with open(os.path.join(ws, "billing.py"), "w", encoding="utf-8") as f:
                f.write(
                    "from tax_helper import apply_discount\n"
                    "def calculate_invoice_total(subtotal: float, discount_pct: float = 0.0) -> float:\n"
                    "    return apply_discount(subtotal, discount_pct)\n"
                )

        # Hypothesis 2: Introduces broken syntax / cascading LSP errors -> Must be pruned!
        def apply_hypothesis_broken_lsp(ws: str):
            with open(os.path.join(ws, "billing.py"), "w", encoding="utf-8") as f:
                f.write(
                    "from missing_pkg_1 import foo\n"
                    "from missing_pkg_2 import bar\n"
                    "from missing_pkg_3 import baz\n"
                    "def calculate_invoice_total(subtotal: float, discount_pct: float = 0.0) -> float:\n"
                    "    return subtotal\n"
                )

        # Hypothesis 3 (Parent): Fixes subtraction but forgets negative floor -> expands Child Refinement 3.1 which fixes negative floor!
        def apply_hypothesis_partial(ws: str):
            with open(os.path.join(ws, "billing.py"), "w", encoding="utf-8") as f:
                f.write(
                    "def calculate_invoice_total(subtotal: float, discount_pct: float = 0.0) -> float:\n"
                    "    return subtotal - (subtotal * discount_pct)\n"
                )

        def apply_refinement_clamp_zero(ws: str):
            with open(os.path.join(ws, "billing.py"), "w", encoding="utf-8") as f:
                f.write(
                    "def calculate_invoice_total(subtotal: float, discount_pct: float = 0.0) -> float:\n"
                    "    discounted = subtotal - (subtotal * discount_pct)\n"
                    "    return max(0.0, round(discounted, 2))\n"
                )

        hypotheses = [
            {
                "name": "Hypothesis 1: External Helper Split (Circular Import Risk)",
                "description": "Extract discount math into tax_helper.py",
                "generator": apply_hypothesis_circular,
            },
            {
                "name": "Hypothesis 2: Third-Party Dependency Rewrite",
                "description": "Import external pricing modules",
                "generator": apply_hypothesis_broken_lsp,
            },
            {
                "name": "Hypothesis 3: In-Place Algebraic Fix + Boundary Clamping",
                "description": "Subtract discount percentage and refine boundary clamping",
                "generator": apply_hypothesis_partial,
                "refinements": [
                    {
                        "name": "Refinement 3.1: Clamp negative totals at 0.0",
                        "description": "Ensure discount > 100% clamps cleanly to 0.0",
                        "generator": apply_refinement_clamp_zero,
                    }
                ],
            },
        ]

        import sys
        mcts_result = await BestOfNExplorer.explore_mcts(
            sandbox_dir=sbox,
            hypotheses=hypotheses,
            reproducer_script="reproduce_issue.py",
            regression_cmd=[sys.executable, "test_regression.py"],
            max_iterations=10,
        )

        assert mcts_result.success is True
        assert mcts_result.winning_node is not None
        assert mcts_result.winning_node.node_id == "h_3_r1"
        assert mcts_result.winning_node.depth == 2
        assert mcts_result.winning_node.reproducer_passed is True
        assert mcts_result.winning_node.regression_passed is True
        assert len(mcts_result.pruned_nodes) >= 2

        # Verify Hypothesis 1 was specifically pruned for circular dependency
        h1_node = next(n for n in mcts_result.all_nodes if n.node_id == "h_1")
        assert h1_node.pruned is True
        assert h1_node.circular_dependency_detected is True
        assert "Circular dependency" in h1_node.prune_reason

        # Verify winning code is active in workspace
        with open(os.path.join(sbox, "billing.py"), "r", encoding="utf-8") as f:
            final_code = f.read()
        assert "max(0.0, round(discounted, 2))" in final_code
