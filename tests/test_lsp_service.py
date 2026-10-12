import os
import pytest
from backend.services.lsp_service import LSPService
from backend.services.tool_registry import ToolRegistry


def test_lsp_goto_definition():
    # Test resolving a known class definition via import
    res = LSPService.goto_definition("CreditPolicy", current_file="backend/api/v1/billing.py")
    assert res.get("found") is True
    assert res.get("kind") == "class"
    assert "billing_policy.py" in res.get("file_path")
    assert res.get("line") > 0
    assert "class CreditPolicy" in res.get("signature")


def test_lsp_find_references():
    res = LSPService.find_references("CreditPolicy", max_results=10)
    assert res.get("count") > 0
    refs = res.get("references", [])
    assert any("billing" in r["file"] for r in refs)


def test_lsp_document_symbols():
    res = LSPService.get_document_symbols("backend/models/billing.py")
    assert res.get("language") == "python"
    symbols = res.get("symbols", [])
    names = [s["name"] for s in symbols]
    assert "LedgerTransaction" in names or "CreditPackage" in names


@pytest.mark.asyncio
async def test_lsp_tools_in_registry():
    t_def = ToolRegistry.get_tool("lsp.goto_definition")
    assert t_def is not None

    res = await t_def.execute(symbol="CreditPolicy", current_file="backend/api/v1/billing.py")
    assert res.get("found") is True

    t_refs = ToolRegistry.get_tool("lsp.find_references")
    assert t_refs is not None
    res_refs = await t_refs.execute(symbol="CreditPolicy", max_results=5)
    assert res_refs.get("count") > 0
