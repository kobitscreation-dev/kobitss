"""
Automated Verification Suite for Kobits Autonomous Software Engineer Landing Page.
Tests:
1. Route integrity for root (/), /landing, /pricing, /comparison, /product, /engineering, /security, /how-it-works.
2. Content invariants for Autonomous SE positioning:
   - Hero Headline: "The Autonomous Software Engineer" / "Assign an outcome. Kobits delivers a verified PR."
   - The 6 Core Pillars:
     * Zero-Local Footprint & Remote MicroVMs
     * Negative-to-Positive TDD Reproducer (Red -> Green)
     * Self-Healing CI Pipelines & PR Review Loops
     * Live Ephemeral Web Preview & HMR Tunneling
     * Persistent Team Memory & Knowledge Graph
     * Enterprise Governance & 5% Operator Gate (SOC2 Audit + 3 Consensus Seals: Aegis, Sentinel, Nexus)
   - CTAs: "Launch Mission", "Connect GitHub", "Executive Portal".
3. HTML structural integrity and syntax validation.
4. Compiler / LSP diagnostics across frontend modules.
5. Strict verification that 'Kyros' and competitor references are completely purged from public pages.
"""

import os
import re
from pathlib import Path
import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.services.lsp_service import HeadlessLSPService


@pytest.mark.asyncio
async def test_landing_page_routes_and_status_codes():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Root route
        res_root = await ac.get("/")
        assert res_root.status_code == 200, f"Root / returned {res_root.status_code}"
        assert "text/html" in res_root.headers.get("content-type", "")

        # 2. /landing route
        res_landing = await ac.get("/landing")
        assert res_landing.status_code == 200, f"/landing returned {res_landing.status_code}"
        assert "text/html" in res_landing.headers.get("content-type", "")

        # 3. Static marketing routes
        for route in ("/pricing", "/comparison", "/product", "/engineering", "/security", "/how-it-works"):
            res = await ac.get(route)
            assert res.status_code == 200, f"{route} returned {res.status_code}"
            assert "text/html" in res.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_landing_page_kyros_devin_content_invariants():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/")
        assert res.status_code == 200
        html = res.text

        # 1. Brand & Positioning Headline: Autonomous Software Engineer (NO Cloud in H1)
        assert "The Autonomous" in html
        assert "Software Engineer" in html
        assert "The Autonomous Cloud Software Engineer" not in html
        assert "Assign an outcome. Kobits delivers a verified PR." in html

        # 2. Key Pillars
        assert "Zero-Local Footprint" in html
        assert "Negative-to-Positive TDD" in html
        assert "Self-Healing CI" in html
        assert "Ephemeral Web Preview" in html
        assert "Persistent Team Memory" in html
        assert "5% Operator Gate" in html

        # 3. The 3 Consensus Review Seals
        assert "Aegis Security" in html or "Aegis" in html
        assert "Sentinel QA" in html or "Sentinel" in html
        assert "Nexus Architecture" in html or "Nexus" in html

        # 4. CTAs
        assert "Launch Mission" in html
        assert "Connect GitHub" in html
        assert "Executive Portal" in html

        # 5. MicroVM and Telemetry mentions
        assert "MicroVM" in html
        assert "Firecracker" in html
        assert "45,509" in html
        assert "183" in html


def test_index_and_landing_html_parity_and_syntax():
    index_path = Path("index.html")
    landing_path = Path("landing.html")

    assert index_path.exists(), "index.html missing"
    assert landing_path.exists(), "landing.html missing"

    index_text = index_path.read_text(encoding="utf-8")
    landing_text = landing_path.read_text(encoding="utf-8")

    # Invariants on index.html
    assert "<!DOCTYPE html>" in index_text
    assert "<title>Kobits — The Autonomous Software Engineer</title>" in index_text
    assert 'id="workspace-showcase"' in index_text
    assert 'id="modal-early-access"' in index_text
    assert 'id="demo-diff-modal"' in index_text
    assert 'id="hero-orb-canvas"' in index_text

    # Both landing and index must have the new positioning
    assert "The Autonomous" in landing_text
    assert "Software Engineer" in landing_text
    assert "Negative-to-Positive TDD" in landing_text
    assert "The Autonomous Cloud Software Engineer" not in landing_text
    assert "The Autonomous Cloud Software Engineer" not in index_text

    # Check for basic unclosed HTML tags
    open_body = len(re.findall(r"<body\b", index_text, re.IGNORECASE))
    close_body = len(re.findall(r"</body>", index_text, re.IGNORECASE))
    assert open_body == close_body == 1

    open_html = len(re.findall(r"<html\b", index_text, re.IGNORECASE))
    close_html = len(re.findall(r"</html>", index_text, re.IGNORECASE))
    assert open_html == close_html == 1


def test_lsp_diagnostics_on_critical_frontend_and_backend_modules():
    target_files = [
        "web/js/views/client_portal.js",
        "js/views/client_portal.js",
        "backend/services/lsp_service.py",
        "backend/api/v1/client_portal.py",
    ]
    for rel_path in target_files:
        diag = HeadlessLSPService.get_diagnostics(file_path=rel_path, repo_path=".")
        assert diag["error_count"] == 0, f"Diagnostics error in {rel_path}: {diag['diagnostics']}"


def test_marketing_satellite_pages_navigation_and_footer_parity():
    pages = [
        "engineering.html",
        "how-it-works.html",
        "pricing.html",
        "product.html",
        "security.html",
        "comparison.html",
    ]
    for page in pages:
        path = Path(page)
        assert path.exists(), f"{page} missing"
        content = path.read_text(encoding="utf-8")
        assert "/comparison.html" in content, f"{page} is missing /comparison.html link"
        assert "/product.html" in content, f"{page} is missing /product.html link"
        assert "/engineering.html" in content, f"{page} is missing /engineering.html link"
        assert "/how-it-works.html" in content, f"{page} is missing /how-it-works.html link"
        assert "/security.html" in content, f"{page} is missing /security.html link"
        assert "/pricing.html" in content, f"{page} is missing /pricing.html link"
        assert "KOBITS AUTONOMOUS SE" in content, f"{page} is missing KOBITS AUTONOMOUS SE branding"


def test_no_kyros_or_devin_or_cloud_headlines_in_marketing_pages():
    pages = [
        "index.html",
        "landing.html",
        "comparison.html",
        "engineering.html",
        "how-it-works.html",
        "pricing.html",
        "product.html",
        "security.html",
    ]
    for page in pages:
        content = Path(page).read_text(encoding="utf-8")
        assert "kyros" not in content.lower(), f"Competitor 'Kyros' found in customer-facing page {page}"
        assert "devin" not in content.lower(), f"Competitor 'Devin' found in customer-facing page {page}"
        assert "The Autonomous Cloud Software Engineer" not in content, f"Headline still contains 'Cloud' in {page}"
