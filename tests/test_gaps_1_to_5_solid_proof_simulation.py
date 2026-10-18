"""
End-to-End Solid Proof Simulation: Frontier Gaps 1 through 5
Validates the unified execution pipeline across realistic high-stress scenarios:
- Gap 1: Sub-800ms Warm MicroVM restore
- Gap 2: Multi-Hop LSP symbol jumping & TypeScript semantic ripple detection
- Gap 3: Long-horizon Prompt Caching (>90% KV hit rate across 25 turns)
- Gap 4: Interactive 30fps CDP screencast stream & event dispatch
- Gap 5: UCT MCTS hypothesis tree search with dead-end branch pruning
- Unified Agency Telemetry verification
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.services.cloud_sandbox import CloudMicroVMSandbox, WarmSnapshotPoolManager
from backend.services.lsp_service import HeadlessLSPService, LSPJsonRpcDaemonClient
from backend.services.prompt_cache import PromptCacheOptimizer
from backend.services.browser_engine import CDPStreamManager
from backend.services.best_of_n import BestOfNExplorer, MCTSHypothesisExplorer


@pytest.mark.asyncio
async def test_solid_proof_simulation_gaps_1_to_5_unified_pipeline():
    """
    Simulates a complete enterprise mission lifecycle chaining Gaps 1 to 5:
    1. Instant Warm MicroVM Provisioning (<800ms SLA)
    2. Zero-grep LSP Codebase Navigation & Semantic Type Checking
    3. Multi-Turn Prompt Cache Optimization with >90% KV Hit Rate
    4. Live CDP Interactive Screencast Streaming & User Input Dispatch
    5. MCTS Hypothesis Tree Search with Circular/Compiler Error Pruning
    6. System-Wide Telemetry Verification via Agency Admin API
    """
    mission_id = f"sim-mission-{uuid.uuid4().hex[:8]}"

    with tempfile.TemporaryDirectory() as repo_root:
        # =====================================================================
        # STAGE 1: Real-World Workspace Setup & Warm-Pool Provisioning (<800ms)
        # =====================================================================
        # Create lockfiles and multi-file fullstack repo structure
        with open(os.path.join(repo_root, "package-lock.json"), "w", encoding="utf-8") as f:
            f.write('{"name": "enterprise-billing", "version": "2.4.0", "lockfileVersion": 3}')
        with open(os.path.join(repo_root, "requirements.txt"), "w", encoding="utf-8") as f:
            f.write("fastapi>=0.115.0\npydantic>=2.9.0\npytest>=8.0.0\n")

        # Initialize git for rollback checkpoints
        subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "sim@kobits.ai"], cwd=repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Kobits Simulator"], cwd=repo_root, check=True, capture_output=True)

        # Pre-warm snapshot pool
        pool = WarmSnapshotPoolManager.prewarm_pool(pool_size=2, repo_path=repo_root)
        assert len(pool) >= 2

        # Measure warm restore latency
        t0 = time.perf_counter()
        sandbox, restore_meta = WarmSnapshotPoolManager.restore_from_warm_pool(
            session_id=mission_id,
            repo_path=repo_root,
        )
        t_restore_ms = (time.perf_counter() - t0) * 1000.0

        assert restore_meta["warm_cache_hit"] is True
        assert restore_meta["sla_met"] is True
        assert t_restore_ms < 800.0, f"Warm restore exceeded SLA: {t_restore_ms:.2f}ms"
        assert sandbox.sandbox_id.startswith("fc-vm-warm-")

        # =====================================================================
        # STAGE 2: LSP JSON-RPC Framing & Multi-Hop TS Semantic Navigation
        # =====================================================================
        # Verify JSON-RPC daemon framing protocol
        init_seq = LSPJsonRpcDaemonClient.build_initialize_sequence(repo_root)
        decoded_init = LSPJsonRpcDaemonClient.decode_jsonrpc_frame(b"".join(init_seq))
        assert len(decoded_init) == 2
        assert decoded_init[0]["method"] == "initialize"

        # Build 3-hop barrel export structure:
        # api/client.ts -> services/index.ts -> services/billing/index.ts -> services/billing/calculator.ts
        services_dir = os.path.join(repo_root, "services", "billing")
        os.makedirs(services_dir, exist_ok=True)
        api_dir = os.path.join(repo_root, "api")
        os.makedirs(api_dir, exist_ok=True)

        calc_file = os.path.join(services_dir, "calculator.ts")
        with open(calc_file, "w", encoding="utf-8") as f:
            f.write(
                "export interface InvoiceConfig {\n"
                "  taxRate: number;\n"
                "  currency: string;\n"
                "}\n\n"
                "export function computeTotal(subtotal: number, config: InvoiceConfig): number {\n"
                "  return subtotal * (1 + config.taxRate);\n"
                "}\n"
            )

        with open(os.path.join(services_dir, "index.ts"), "w", encoding="utf-8") as f:
            f.write("export * from './calculator';\n")

        with open(os.path.join(repo_root, "services", "index.ts"), "w", encoding="utf-8") as f:
            f.write("export { computeTotal, type InvoiceConfig } from './billing';\n")

        client_file = os.path.join(api_dir, "client.ts")
        with open(client_file, "w", encoding="utf-8") as f:
            f.write(
                "import { computeTotal, InvoiceConfig } from '../services';\n\n"
                "const cfg: InvoiceConfig = { taxRate: 0.1, currency: 'USD' };\n"
                "const finalVal = computeTotal(500, cfg);\n"
            )

        # Jump to definition across 3 barrels without regex grep
        def_match = HeadlessLSPService.find_definition(
            symbol="computeTotal",
            file_path="api/client.ts",
            workspace_dir=repo_root,
        )
        assert def_match["found"] is True
        assert def_match["file_path"].endswith("services/billing/calculator.ts")

        # Test semantic type error detection (TS2741 missing required field)
        broken_client = os.path.join(api_dir, "broken_client.ts")
        with open(broken_client, "w", encoding="utf-8") as f:
            f.write(
                "import { computeTotal, InvoiceConfig } from '../services';\n\n"
                "const badCfg: InvoiceConfig = { taxRate: 0.05 };\n"  # Missing 'currency'
            )
        diag = HeadlessLSPService.get_diagnostics(file_path="api/broken_client.ts", workspace_dir=repo_root)
        diag_codes = {d["code"] for d in diag.get("diagnostics", [])}
        assert "TS2741" in diag_codes

        # =====================================================================
        # STAGE 3: Long-Horizon Prompt Caching (25 Turns Simulation)
        # =====================================================================
        optimizer = PromptCacheOptimizer(uncached_tail_turns=1)
        system_prompt = "You are Kobits Principal Autonomous SWE Agent.\n" + ("SYSTEM_POLICIES_CONVENTION\n" * 400)
        repo_ast = "\n".join([f"class ServiceModule_{i}: def execute_{i}()" for i in range(300)])
        tools = [{"name": f"tool_{i}", "description": f"Tool #{i}", "input_schema": {}} for i in range(12)]

        messages = [{"role": "user", "content": "Execute full test-driven refactoring of billing module."}]
        optimizer.prewarm_session_prefix(mission_id, system_prompt, tools, repo_ast, messages[0]["content"])

        turn_metrics = []
        for turn in range(1, 26):
            messages.append({"role": "assistant", "content": f"Turn {turn}: Analyzing symbol references."})
            messages.append({"role": "user", "content": f"Turn {turn} observation: OK."})
            payload, metrics = optimizer.build_cached_payload(
                session_id=mission_id,
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                repo_ast_map=repo_ast,
            )
            turn_metrics.append(metrics)
            assert metrics.prefix_invariant_verified is True

        avg_hit_rate = sum(m.cache_hit_rate_pct for m in turn_metrics) / len(turn_metrics)
        assert avg_hit_rate >= 90.0, f"Cache hit rate too low: {avg_hit_rate:.1f}%"
        assert turn_metrics[-1].estimated_ttft_cached_ms < 1500.0

        # =====================================================================
        # STAGE 4: Interactive 30fps CDP Screencast & Input Event Dispatch
        # =====================================================================
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Get live screencast frame
            stream_res = await client.get(f"/api/v1/preview/{mission_id}/cdp-stream")
            assert stream_res.status_code == 200
            frame_data = stream_res.json()
            assert frame_data["target_fps"] == 30
            assert frame_data["frame_data_uri"].startswith("data:image/png;base64,")

            # Dispatch interactive click and text input
            input_res = await client.post(
                f"/api/v1/preview/{mission_id}/cdp-input",
                json={"type": "type", "selector": "input[name='email']", "text": "test@kobits.ai"},
            )
            assert input_res.status_code == 200
            assert input_res.json()["action"]["text"] == "test@kobits.ai"

        # =====================================================================
        # STAGE 5: UCT MCTS Hypothesis Search & Dead-End Pruning
        # =====================================================================
        # Commit baseline code
        with open(os.path.join(repo_root, "discount.py"), "w", encoding="utf-8") as f:
            f.write(
                "def calculate_discount(price: float, rate: float) -> float:\n"
                "    # BUGGY BASELINE: returns negative discount\n"
                "    return price + (price * rate)\n"
            )
        with open(os.path.join(repo_root, "reproduce_bug.py"), "w", encoding="utf-8") as f:
            f.write(
                "from discount import calculate_discount\n"
                "assert abs(calculate_discount(100.0, 0.2) - 80.0) < 1e-6\n"
            )
        subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline with discount bug"], cwd=repo_root, check=True, capture_output=True)

        # 3 Competing MCTS Hypotheses
        def gen_circular(ws):
            with open(os.path.join(ws, "discount_helper.py"), "w", encoding="utf-8") as f:
                f.write("from discount import calculate_discount\ndef helper(): return 1\n")
            with open(os.path.join(ws, "discount.py"), "w", encoding="utf-8") as f:
                f.write("from discount_helper import helper\ndef calculate_discount(p, r): return p\n")

        def gen_broken_syntax(ws):
            with open(os.path.join(ws, "discount.py"), "w", encoding="utf-8") as f:
                f.write("import nonexistent_module_xyz\ndef calculate_discount(p, r): return 0\n")

        def gen_valid_fix(ws):
            with open(os.path.join(ws, "discount.py"), "w", encoding="utf-8") as f:
                f.write("def calculate_discount(price: float, rate: float) -> float:\n    return price * (1.0 - rate)\n")

        hypotheses = [
            {"name": "H1: Circular Import", "description": "Extracts helper with circular import", "generator": gen_circular},
            {"name": "H2: Broken Import", "description": "Imports missing package", "generator": gen_broken_syntax},
            {"name": "H3: In-Place Math Fix", "description": "Correct algebraic formula", "generator": gen_valid_fix},
        ]

        mcts_res = await BestOfNExplorer.explore_mcts(
            sandbox_dir=repo_root,
            hypotheses=hypotheses,
            reproducer_script="reproduce_bug.py",
            max_iterations=6,
        )

        assert mcts_res.success is True
        assert mcts_res.winning_node is not None
        assert mcts_res.winning_node.node_id == "h_3"
        assert mcts_res.winning_node.reproducer_passed is True
        assert len(mcts_res.pruned_nodes) >= 2

        # Verify winning code in sandbox
        with open(os.path.join(repo_root, "discount.py"), "r", encoding="utf-8") as f:
            assert "price * (1.0 - rate)" in f.read()

        # =====================================================================
        # STAGE 6: Deep Console Unified Telemetry Verification
        # =====================================================================
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            telem_res = await client.get("/api/v1/agency/console/telemetry")
            assert telem_res.status_code == 200
            telem_data = telem_res.json()

            assert "warm_snapshot_pool" in telem_data
            assert telem_data["warm_snapshot_pool"]["sla_compliant"] is True
            assert telem_data["warm_snapshot_pool"]["avg_restore_latency_ms"] < 800.0

            assert "lsp_daemon_status" in telem_data
            assert telem_data["lsp_daemon_status"]["protocol_version"] == "LSP 3.17 / JSON-RPC 2.0"
            assert telem_data["lsp_daemon_status"]["barrel_reexport_resolution"] is True
            assert telem_data["lsp_daemon_status"]["typescript_semantic_checker"] is True

            assert "prompt_cache_economics" in telem_data
            assert telem_data["prompt_cache_economics"]["kv_cache_hit_rate_pct"] >= 90.0
            assert telem_data["prompt_cache_economics"]["cost_savings_pct"] >= 80.0

            assert "cdp_browser_stream" in telem_data
            assert telem_data["cdp_browser_stream"]["target_fps"] == 30

            assert "mcts_search_tree" in telem_data
            assert telem_data["mcts_search_tree"]["total_searches"] >= 1
            assert telem_data["mcts_search_tree"]["total_branches_pruned"] >= 2
