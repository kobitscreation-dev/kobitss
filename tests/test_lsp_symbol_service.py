"""
Comprehensive Test Suite for HeadlessLSPService, Symbol Graph Engine,
Compiler Diagnostics, and Tool/Agent Registry Integration.
"""

import os
import shutil
import tempfile
import pytest
import pytest_asyncio

from backend.services.lsp_service import HeadlessLSPService, LSPService
from backend.services.tool_registry import ToolRegistry
from backend.services.agent_registry import AGENT_REGISTRY
from backend.models.agent import AgentType


@pytest.fixture
def temp_workspace(tmp_path):
    """
    Creates an isolated multi-file mock project workspace to test:
    - Jump to definition across relative and absolute imports
    - Cross-file reference tracking and caller identification
    - Real-time compiler diagnostics and cross-file ripple errors
    - Circular import resilience
    - In-memory symbol graph generation
    """
    ws = tmp_path / "test_repo"
    ws.mkdir()

    # Package structure:
    # test_repo/
    #   pkg/
    #     __init__.py
    #     models.py
    #     service.py
    #     utils.py
    #   frontend/
    #     api.js
    #     component.ts
    #   circular_a.py
    #   circular_b.py

    pkg = ws / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("# package root\n", encoding="utf-8")

    (pkg / "models.py").write_text(
        '''"""Data models module."""

class OrderModel:
    """Represents a customer order."""
    def __init__(self, order_id: str, amount: float):
        self.order_id = order_id
        self.amount = amount

    def calculate_tax(self, rate: float) -> float:
        """Calculate sales tax on the order."""
        return self.amount * rate


def create_default_order() -> OrderModel:
    """Factory helper to build a default order."""
    return OrderModel("ord_001", 100.0)
''',
        encoding="utf-8",
    )

    (pkg / "service.py").write_text(
        '''"""Business logic service module."""
from .models import OrderModel, create_default_order
from .utils import log_transaction

class OrderService:
    """Service handling high-volume order processing."""

    def process_order(self, order: OrderModel, tax_rate: float = 0.05) -> float:
        """Processes order and returns total with tax."""
        tax = order.calculate_tax(tax_rate)
        log_transaction(order.order_id, order.amount + tax)
        return order.amount + tax

    async def async_dispatch(self, order_id: str) -> bool:
        """Asynchronously dispatch order to fulfillment."""
        return True


def execute_batch() -> None:
    svc = OrderService()
    ord1 = create_default_order()
    svc.process_order(ord1)
''',
        encoding="utf-8",
    )

    (pkg / "utils.py").write_text(
        '''"""Utility functions."""

def log_transaction(tx_id: str, amount: float) -> None:
    """Log financial transaction."""
    pass
''',
        encoding="utf-8",
    )

    frontend = ws / "frontend"
    frontend.mkdir()

    (frontend / "api.js").write_text(
        '''// JavaScript API Client
export class ApiClient {
    constructor(baseUrl) {
        this.baseUrl = baseUrl;
    }

    async getOrders() {
        return fetch(`${this.baseUrl}/orders`);
    }
}

export function createClient(url) {
    return new ApiClient(url);
}
''',
        encoding="utf-8",
    )

    (frontend / "component.ts").write_text(
        '''import { ApiClient, createClient } from './api';

export interface UserProps {
    id: string;
    username: string;
}

export class OrderView {
    private client: ApiClient;

    constructor() {
        this.client = createClient("https://api.kobits.dev");
    }

    render(): void {
        this.client.getOrders();
    }
}
''',
        encoding="utf-8",
    )

    # Circular import files
    (ws / "circular_a.py").write_text(
        '''from circular_b import func_b

def func_a():
    return func_b()
''',
        encoding="utf-8",
    )

    (ws / "circular_b.py").write_text(
        '''from circular_a import func_a

def func_b():
    return "ok"
''',
        encoding="utf-8",
    )

    return str(ws)


# ══════════════════════════════════════════════════════════════════════════
# 1. DEFINITION JUMPING TESTS
# ══════════════════════════════════════════════════════════════════════════

def test_find_definition_local_class_and_methods(temp_workspace):
    res = HeadlessLSPService.find_definition(
        symbol="OrderModel",
        file_path="pkg/models.py",
        repo_path=temp_workspace,
    )
    assert res.get("found") is True
    assert res.get("kind") == "class"
    assert "models.py" in res.get("file_path")
    assert res.get("line") == 3
    assert "class OrderModel" in res.get("signature")
    assert "Represents a customer order." in res.get("docstring")


def test_find_definition_method_in_class(temp_workspace):
    res = HeadlessLSPService.find_definition(
        symbol="OrderModel.calculate_tax",
        file_path="pkg/models.py",
        repo_path=temp_workspace,
    )
    assert res.get("found") is True
    assert res.get("kind") == "method"
    assert "calculate_tax" in res.get("signature")
    assert res.get("line") == 9


def test_find_definition_relative_import(temp_workspace):
    # OrderModel is imported in pkg/service.py from .models
    res = HeadlessLSPService.find_definition(
        symbol="OrderModel",
        file_path="pkg/service.py",
        repo_path=temp_workspace,
    )
    assert res.get("found") is True
    assert res.get("source") == "imported_module"
    assert "models.py" in res.get("file_path")
    assert res.get("line") == 3


def test_find_definition_imported_function(temp_workspace):
    res = HeadlessLSPService.find_definition(
        symbol="log_transaction",
        file_path="pkg/service.py",
        repo_path=temp_workspace,
    )
    assert res.get("found") is True
    assert "utils.py" in res.get("file_path")
    assert res.get("kind") == "function"
    assert "def log_transaction" in res.get("signature")


def test_find_definition_by_cursor_position(temp_workspace):
    # In pkg/service.py line 2: from .models import OrderModel, create_default_order
    res = HeadlessLSPService.find_definition(
        symbol="",
        file_path="pkg/service.py",
        line=2,
        col=25,  # points to OrderModel
        repo_path=temp_workspace,
    )
    assert res.get("found") is True
    assert res.get("symbol") == "OrderModel"
    assert "models.py" in res.get("file_path")


def test_find_definition_js_ts(temp_workspace):
    # Local JS class definition
    js_res = HeadlessLSPService.find_definition(
        symbol="ApiClient",
        file_path="frontend/api.js",
        repo_path=temp_workspace,
    )
    assert js_res.get("found") is True
    assert js_res.get("kind") == "class"
    assert "api.js" in js_res.get("file_path")

    # Imported in component.ts
    ts_res = HeadlessLSPService.find_definition(
        symbol="ApiClient",
        file_path="frontend/component.ts",
        repo_path=temp_workspace,
    )
    assert ts_res.get("found") is True
    assert "api.js" in ts_res.get("file_path")


# ══════════════════════════════════════════════════════════════════════════
# 2. REFERENCE RESOLUTION & CALLER TRACING TESTS
# ══════════════════════════════════════════════════════════════════════════

def test_find_references_with_callers(temp_workspace):
    res = HeadlessLSPService.find_references(
        symbol="calculate_tax",
        repo_path=temp_workspace,
        max_results=10,
    )
    assert res.get("count") >= 2
    refs = res.get("references", [])

    # Definition in models.py
    def_ref = next((r for r in refs if r["kind"] == "definition"), None)
    assert def_ref is not None
    assert "models.py" in def_ref["file"]

    # Call inside OrderService.process_order in service.py
    call_ref = next((r for r in refs if r["kind"] == "call"), None)
    assert call_ref is not None
    assert "service.py" in call_ref["file"]
    assert "OrderService" in call_ref.get("caller", "")


def test_find_references_word_boundary(temp_workspace):
    # Ensure 'order' doesn't match 'OrderModel' as a symbol
    res = HeadlessLSPService.find_references(
        symbol="OrderModel",
        repo_path=temp_workspace,
    )
    assert res.get("count") > 0
    for r in res["references"]:
        assert "OrderModel" in r["text"]


# ══════════════════════════════════════════════════════════════════════════
# 3. COMPILER DIAGNOSTICS & RIPPLE CHECK TESTS
# ══════════════════════════════════════════════════════════════════════════

def test_diagnostics_clean_workspace(temp_workspace):
    res = HeadlessLSPService.get_diagnostics(
        file_path="pkg/models.py",
        repo_path=temp_workspace,
    )
    assert res.get("status") == "clean"
    assert res.get("error_count") == 0


def test_diagnostics_syntax_error(temp_workspace):
    broken_file = os.path.join(temp_workspace, "pkg", "syntax_err.py")
    with open(broken_file, "w", encoding="utf-8") as f:
        f.write("def broken_syntax(:\n    pass\n")

    res = HeadlessLSPService.get_diagnostics(
        file_path="pkg/syntax_err.py",
        repo_path=temp_workspace,
    )
    assert res.get("status") == "errors_found"
    assert res.get("error_count") >= 1
    diag = res["diagnostics"][0]
    assert diag.get("code") == "SYNTAX_ERROR"
    assert diag.get("severity") == "error"
    assert diag.get("line") == 1


def test_diagnostics_broken_import_symbol(temp_workspace):
    bad_import_file = os.path.join(temp_workspace, "pkg", "bad_import.py")
    with open(bad_import_file, "w", encoding="utf-8") as f:
        f.write("from .models import NonExistentSymbol\n")

    res = HeadlessLSPService.get_diagnostics(
        file_path="pkg/bad_import.py",
        repo_path=temp_workspace,
    )
    assert res.get("status") == "errors_found"
    assert res.get("error_count") >= 1
    assert any("NonExistentSymbol" in d["message"] for d in res["diagnostics"])


def test_diagnostics_broken_module_import(temp_workspace):
    bad_mod_file = os.path.join(temp_workspace, "pkg", "bad_mod.py")
    with open(bad_mod_file, "w", encoding="utf-8") as f:
        f.write("from .non_existent_module import something\n")

    res = HeadlessLSPService.get_diagnostics(
        file_path="pkg/bad_mod.py",
        repo_path=temp_workspace,
    )
    assert res.get("status") == "errors_found"
    assert res.get("error_count") >= 1
    assert any("Cannot resolve import" in d["message"] for d in res["diagnostics"])


def test_diagnostics_call_signature_mismatch(temp_workspace):
    sig_mismatch_file = os.path.join(temp_workspace, "pkg", "mismatch.py")
    with open(sig_mismatch_file, "w", encoding="utf-8") as f:
        f.write(
            "def calculate(a, b, c):\n"
            "    return a + b + c\n\n"
            "calculate(1)\n"  # missing 2 arguments
        )

    res = HeadlessLSPService.get_diagnostics(
        file_path="pkg/mismatch.py",
        repo_path=temp_workspace,
    )
    assert res.get("status") == "errors_found"
    assert any(d.get("code") == "SIGNATURE_MISMATCH" for d in res["diagnostics"])


def test_cross_file_ripple_diagnostics(temp_workspace):
    """
    Kyros / Devin 1:1 Parity:
    If a developer modifies a function signature or removes an export in models.py,
    get_diagnostics('pkg/models.py') immediately identifies that service.py is broken
    before running tests!
    """
    models_file = os.path.join(temp_workspace, "pkg", "models.py")
    with open(models_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Rename OrderModel to RenamedOrderModel in models.py
    modified_content = content.replace("class OrderModel:", "class RenamedOrderModel:")
    with open(models_file, "w", encoding="utf-8") as f:
        f.write(modified_content)

    # Invalidate AST cache for models.py
    HeadlessLSPService._ast_cache.pop(os.path.realpath(models_file), None)

    # Diagnostic on models.py should detect the ripple effect in service.py!
    res = HeadlessLSPService.get_diagnostics(
        file_path="pkg/models.py",
        repo_path=temp_workspace,
    )
    assert res.get("status") == "errors_found"
    assert res.get("error_count") >= 1
    ripple_err = next((d for d in res["diagnostics"] if d.get("source") == "compiler_ripple"), None)
    assert ripple_err is not None
    assert "service.py" in ripple_err["file"]
    assert "OrderModel" in ripple_err["message"]


# ══════════════════════════════════════════════════════════════════════════
# 4. SYMBOL GRAPH TESTS
# ══════════════════════════════════════════════════════════════════════════

def test_get_symbol_graph(temp_workspace):
    graph = HeadlessLSPService.get_symbol_graph(repo_path=temp_workspace)
    assert "nodes" in graph
    assert "edges" in graph
    assert "summary" in graph

    summary = graph["summary"]
    assert summary["classes"] >= 2
    assert summary["functions"] >= 2
    assert summary["total_files"] >= 4

    # Verify node structure
    nodes = {n["name"]: n for n in graph["nodes"]}
    assert "OrderService" in nodes or "ApiClient" in nodes

    # Verify callers / callees mapping
    callers = graph.get("callers", {})
    assert "calculate_tax" in callers or "log_transaction" in callers

    # Test cache retrieval speed (< 20ms)
    import time
    t0 = time.perf_counter()
    cached_graph = HeadlessLSPService.get_symbol_graph(repo_path=temp_workspace)
    dur_ms = (time.perf_counter() - t0) * 1000
    assert dur_ms < 50
    assert cached_graph["summary"] == summary


# ══════════════════════════════════════════════════════════════════════════
# 5. EDGE CASES
# ══════════════════════════════════════════════════════════════════════════

def test_edge_case_missing_file(temp_workspace):
    res = HeadlessLSPService.find_definition(
        symbol="Foo",
        file_path="non_existent_file.py",
        repo_path=temp_workspace,
    )
    assert res.get("found") is False

    diag = HeadlessLSPService.get_diagnostics(
        file_path="non_existent_file.py",
        repo_path=temp_workspace,
    )
    assert diag.get("status") == "errors_found"
    assert diag.get("error_count") == 1
    assert diag["diagnostics"][0]["code"] == "FILE_NOT_FOUND"


def test_edge_case_empty_input(temp_workspace):
    res = HeadlessLSPService.find_definition(symbol="", repo_path=temp_workspace)
    assert res.get("found") is False

    refs = HeadlessLSPService.find_references(symbol="", repo_path=temp_workspace)
    assert refs.get("count") == 0


def test_edge_case_circular_imports(temp_workspace):
    # circular_a.py imports circular_b, and circular_b imports circular_a
    # This must resolve cleanly without RecursionError or infinite loop
    res = HeadlessLSPService.find_definition(
        symbol="func_b",
        file_path="circular_a.py",
        repo_path=temp_workspace,
    )
    assert res.get("found") is True
    assert "circular_b.py" in res.get("file_path")


# ══════════════════════════════════════════════════════════════════════════
# 6. TOOL REGISTRY & AGENT REGISTRY INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_all_lsp_tools_registered_and_executable(temp_workspace):
    tools = [
        "lsp.find_definition",
        "lsp.goto_definition",
        "lsp.find_references",
        "lsp.get_diagnostics",
        "lsp.get_symbol_graph",
        "lsp.document_symbols",
    ]
    for t_name in tools:
        tool = ToolRegistry.get_tool(t_name)
        assert tool is not None, f"Tool {t_name} not found in ToolRegistry"

    # Test find_definition execution
    t_def = ToolRegistry.get_tool("lsp.find_definition")
    res_def = await t_def.execute(
        symbol="OrderService",
        file_path="pkg/service.py",
        repo_path=temp_workspace,
    )
    assert res_def.get("found") is True
    assert res_def.get("kind") == "class"

    # Test find_references execution
    t_ref = ToolRegistry.get_tool("lsp.find_references")
    res_ref = await t_ref.execute(
        symbol="OrderModel",
        repo_path=temp_workspace,
    )
    assert res_ref.get("count") > 0

    # Test diagnostics execution
    t_diag = ToolRegistry.get_tool("lsp.get_diagnostics")
    res_diag = await t_diag.execute(
        file_path="pkg/models.py",
        repo_path=temp_workspace,
    )
    assert "diagnostics" in res_diag

    # Test symbol graph execution
    t_graph = ToolRegistry.get_tool("lsp.get_symbol_graph")
    res_graph = await t_graph.execute(repo_path=temp_workspace)
    assert "nodes" in res_graph
    assert "edges" in res_graph


def test_agent_registry_grants_lsp_tools():
    """Verify Forge, Sentinel, Nexus, and Axiom all have LSP tools granted."""
    expected_tools = {
        "lsp.find_definition",
        "lsp.find_references",
        "lsp.get_diagnostics",
        "lsp.get_symbol_graph",
        "lsp.goto_definition",
        "lsp.document_symbols",
    }

    target_agent_types = [
        AgentType.ORCHESTRATOR,       # Nexus
        AgentType.PRODUCT_MANAGER,    # Forge
        AgentType.TECHNICAL_LEAD,     # Forge
        AgentType.SOLUTION_ARCHITECT, # Axiom
        AgentType.QA_ENGINEER,        # Sentinel
    ]

    for ag_type in target_agent_types:
        agent_def = AGENT_REGISTRY.get(ag_type)
        assert agent_def is not None, f"Agent {ag_type} not found in AGENT_REGISTRY"
        for t in expected_tools:
            assert t in agent_def.allowed_tools, (
                f"Agent {agent_def.name} ({ag_type}) missing required LSP tool: {t}"
            )


# ══════════════════════════════════════════════════════════════════════════
# 7. EXPANDED REPRODUCER TESTS FOR EDGE CASES & REGRESSION DEFENSE
# ══════════════════════════════════════════════════════════════════════════

def test_submodule_import_definition_and_diagnostics(temp_workspace):
    c1 = os.path.join(temp_workspace, "pkg", "consumer.py")
    with open(c1, "w", encoding="utf-8") as f:
        f.write("from . import models\n\nval = models.create_default_order()\n")

    c2 = os.path.join(temp_workspace, "consumer2.py")
    with open(c2, "w", encoding="utf-8") as f:
        f.write("from pkg import models\n\nval = models.create_default_order()\n")

    res1 = HeadlessLSPService.find_definition(
        symbol="models",
        file_path="pkg/consumer.py",
        repo_path=temp_workspace,
    )
    assert res1.get("found") is True
    assert "models.py" in res1.get("file_path")

    res2 = HeadlessLSPService.find_definition(
        symbol="models",
        file_path="consumer2.py",
        repo_path=temp_workspace,
    )
    assert res2.get("found") is True
    assert "models.py" in res2.get("file_path")

    diag1 = HeadlessLSPService.get_diagnostics(file_path="pkg/consumer.py", repo_path=temp_workspace)
    assert diag1.get("status") == "clean", f"Unexpected diagnostics: {diag1}"

    diag2 = HeadlessLSPService.get_diagnostics(file_path="consumer2.py", repo_path=temp_workspace)
    assert diag2.get("status") == "clean", f"Unexpected diagnostics: {diag2}"


def test_star_import_resolution_and_reexports(temp_workspace):
    star_file = os.path.join(temp_workspace, "pkg", "star_consumer.py")
    with open(star_file, "w", encoding="utf-8") as f:
        f.write("from .models import *\n\norder = create_default_order()\n")

    res = HeadlessLSPService.find_definition(
        symbol="create_default_order",
        file_path="pkg/star_consumer.py",
        repo_path=temp_workspace,
    )
    assert res.get("found") is True
    assert "models.py" in res.get("file_path")
    assert res.get("kind") == "function"

    diag = HeadlessLSPService.get_diagnostics(file_path="pkg/star_consumer.py", repo_path=temp_workspace)
    assert diag.get("status") == "clean"


def test_dotted_method_resolution(temp_workspace):
    res = HeadlessLSPService.find_definition(
        symbol="OrderModel.calculate_tax",
        file_path="pkg/service.py",
        repo_path=temp_workspace,
    )
    assert res.get("found") is True
    assert res.get("kind") == "method"
    assert "models.py" in res.get("file_path")
    assert "calculate_tax" in res.get("signature")

    js_res = HeadlessLSPService.find_definition(
        symbol="ApiClient.getOrders",
        file_path="frontend/component.ts",
        repo_path=temp_workspace,
    )
    assert js_res.get("found") is True
    assert "api.js" in js_res.get("file_path")
    assert "getOrders" in js_res.get("signature")


def test_find_references_attributes_and_assignments(temp_workspace):
    const_file = os.path.join(temp_workspace, "pkg", "constants.py")
    with open(const_file, "w", encoding="utf-8") as f:
        f.write("MAX_RETRIES = 5\nTIMEOUT = 30\n")

    use_file = os.path.join(temp_workspace, "pkg", "client.py")
    with open(use_file, "w", encoding="utf-8") as f:
        f.write("from .constants import MAX_RETRIES\n\ndef call():\n    for _ in range(MAX_RETRIES):\n        pass\n")

    refs = HeadlessLSPService.find_references(
        symbol="MAX_RETRIES",
        repo_path=temp_workspace,
    )
    assert refs.get("count") >= 2
    kinds = {r["kind"] for r in refs["references"]}
    assert "assignment" in kinds or "definition" in kinds
    assert "usage" in kinds or "call" in kinds

    attr_refs = HeadlessLSPService.find_references(
        symbol="amount",
        repo_path=temp_workspace,
    )
    assert attr_refs.get("count") >= 2
    assert any("service.py" in r["file"] for r in attr_refs["references"])


def test_init_package_ripple_diagnostics(temp_workspace):
    init_file = os.path.join(temp_workspace, "pkg", "__init__.py")
    with open(init_file, "w", encoding="utf-8") as f:
        f.write("from .models import OrderModel\n__all__ = ['OrderModel']\n")

    user_file = os.path.join(temp_workspace, "user.py")
    with open(user_file, "w", encoding="utf-8") as f:
        f.write("from pkg import OrderModel\n\no = OrderModel('1', 10.0)\n")

    HeadlessLSPService.clear_cache()

    diag = HeadlessLSPService.get_diagnostics(file_path="user.py", repo_path=temp_workspace)
    assert diag.get("status") == "clean"

    with open(init_file, "w", encoding="utf-8") as f:
        f.write("# OrderModel removed\n")
    HeadlessLSPService.invalidate_cache(init_file)

    ripple_res = HeadlessLSPService.get_diagnostics(file_path="pkg/__init__.py", repo_path=temp_workspace)
    assert ripple_res.get("status") == "errors_found"
    assert any("user.py" in d["file"] for d in ripple_res["diagnostics"])


def test_js_ts_ripple_diagnostics(temp_workspace):
    api_file = os.path.join(temp_workspace, "frontend", "api.js")
    with open(api_file, "r", encoding="utf-8") as f:
        api_code = f.read()

    broken_api = api_code.replace("export function createClient", "function internalCreateClient")
    with open(api_file, "w", encoding="utf-8") as f:
        f.write(broken_api)

    HeadlessLSPService.clear_cache()

    res = HeadlessLSPService.get_diagnostics(file_path="frontend/api.js", repo_path=temp_workspace)
    assert res.get("status") == "errors_found"
    assert any("component.ts" in d["file"] and "createClient" in d["message"] for d in res["diagnostics"])


def test_symbol_graph_no_method_duplication_and_multi_lang(temp_workspace):
    go_file = os.path.join(temp_workspace, "server.go")
    with open(go_file, "w", encoding="utf-8") as f:
        f.write("package main\n\ntype Config struct {\n    Port int\n}\n\nfunc StartServer() {\n}\n")

    rs_file = os.path.join(temp_workspace, "engine.rs")
    with open(rs_file, "w", encoding="utf-8") as f:
        f.write("pub struct Worker {\n    id: u32,\n}\n\npub fn run_worker() {\n}\n")

    HeadlessLSPService.clear_cache()
    graph = HeadlessLSPService.get_symbol_graph(repo_path=temp_workspace)

    function_nodes = [n for n in graph["nodes"] if n["kind"] == "function"]
    func_names = [n["name"] for n in function_nodes]
    assert "calculate_tax" not in func_names, "Class method calculate_tax was duplicated as top-level function!"

    node_names = {n["name"] for n in graph["nodes"]}
    assert "StartServer" in node_names
    assert "Config" in node_names
    assert "run_worker" in node_names
    assert "Worker" in node_names


def test_cache_invalidation_and_content_hash(temp_workspace):
    HeadlessLSPService.clear_cache()
    test_f = os.path.join(temp_workspace, "cache_test.py")
    with open(test_f, "w", encoding="utf-8") as f:
        f.write("x = 1\n")

    tree1 = HeadlessLSPService._get_cached_ast(test_f, "x = 1\n")
    assert tree1 is not None

    tree2 = HeadlessLSPService._get_cached_ast(test_f, "x = 'hello'\n")
    assert tree2 is not None
    assert tree1 is not tree2

    HeadlessLSPService.invalidate_cache(test_f)
    assert os.path.realpath(test_f) not in HeadlessLSPService._ast_cache


@pytest.mark.asyncio
async def test_document_symbols_fallbacks_and_context(temp_workspace):
    tool = ToolRegistry.get_tool("lsp.document_symbols")
    assert tool is not None

    res = await tool.execute(
        path="pkg/models.py",
        repo_path=temp_workspace,
    )
    assert "symbols" in res
    sym_names = [s["name"] for s in res["symbols"]]
    assert "OrderModel" in sym_names

    res2 = await tool.execute(
        filepath="frontend/api.js",
        repo_path=temp_workspace,
    )
    assert "symbols" in res2
    assert any("ApiClient" in s["name"] for s in res2["symbols"])

    res_err = await tool.execute(file_path=None, repo_path=temp_workspace)
    assert "error" in res_err
