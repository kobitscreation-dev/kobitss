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


def test_all_marketing_pages_navbar_structure_and_exact_spec():
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
        path = Path(page)
        assert path.exists(), f"{page} missing"
        content = path.read_text(encoding="utf-8")

        # 1. Nav tag must exist
        nav_match = re.search(r"<nav\b[^>]*>(.*?)</nav>", content, re.DOTALL)
        assert nav_match is not None, f"<nav> element missing in {page}"
        nav_html = nav_match.group(1)

        # 2. Clean logo: Must contain KOBITS, must NOT contain the green pill badge
        assert "KOBITS" in nav_html, f"Missing KOBITS logo in {page} navbar"
        assert "AUTONOMOUS SOFTWARE ENGINEER" not in nav_html, f"Navbar in {page} still has green badge text"
        assert "border-emerald" not in nav_html, f"Navbar in {page} still has green pill badge border"
        assert "bg-emerald" not in nav_html, f"Navbar in {page} still has green pill badge background"

        # 3. Center links: Exact targets, exact labels, exact order
        expected_routes = [
            "/product.html",
            "/engineering.html",
            "/how-it-works.html",
            "/comparison.html",
            "/security.html",
            "/pricing.html",
        ]
        positions = []
        for route in expected_routes:
            assert route in nav_html, f"{page} navbar missing route {route}"
            positions.append(nav_html.find(route))

        # Assert exact left-to-right order
        assert positions == sorted(positions), f"{page} navbar links are not in expected order: {positions}"

        # Disallowed legacy labels in navbar
        assert "Architecture" not in nav_html, f"{page} navbar still has legacy 'Architecture' instead of 'Engineering'"
        assert "Security & SOC2" not in nav_html, f"{page} navbar still has legacy 'Security & SOC2' instead of 'Security'"

        # 4. Right action button: Must be single clean 'Apply for Early Access', no Connect GitHub or Launch Mission
        assert "Apply for Early Access" in nav_html, f"{page} navbar missing 'Apply for Early Access' button"
        assert "Connect GitHub" not in nav_html, f"{page} navbar still contains 'Connect GitHub'"
        assert "Launch Mission" not in nav_html, f"{page} navbar still contains 'Launch Mission'"

        # 5. Mobile drawer parity
        mobile_match = re.search(r'id="kb-[a-z-]+menu"[^>]*>(.*?)</div>', content, re.DOTALL)
        if mobile_match:
            mobile_html = mobile_match.group(1)
            assert "Architecture" not in mobile_html, f"{page} mobile menu has legacy 'Architecture'"
            assert "Security & SOC2" not in mobile_html, f"{page} mobile menu has legacy 'Security & SOC2'"
            assert "/engineering.html" in mobile_html, f"{page} mobile menu missing /engineering.html"
            assert "/security.html" in mobile_html, f"{page} mobile menu missing /security.html"


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
        
        # Enforce zero occurrences of 'cloud' as a standalone word across all marketing pages
        cloud_matches = re.findall(r'\bcloud\b', content, re.IGNORECASE)
        assert len(cloud_matches) == 0, f"Found {len(cloud_matches)} 'cloud' occurrences in {page}: {cloud_matches}"

        # Enforce zero occurrences of 'ky-' competitor prefixes in HTML/CSS/JS
        ky_matches = re.findall(r'\bky-[a-z0-9_-]+', content, re.IGNORECASE)
        assert len(ky_matches) == 0, f"Found {len(ky_matches)} 'ky-' prefixed selectors in {page}: {ky_matches}"


def test_comparison_page_architecture_and_matrix_invariants():
    comp_text = Path("comparison.html").read_text(encoding="utf-8")
    assert "How Kobits Stacks Up" in comp_text, "Missing competitive matrix heading in comparison.html"
    assert "Multi-Agent Swarm" in comp_text, "Missing Multi-Agent Swarm pillar in comparison.html"
    assert "140ms MicroVM Isolation" in comp_text, "Missing 140ms MicroVM Isolation pillar in comparison.html"
    assert "Negative-to-Positive TDD" in comp_text, "Missing Negative-to-Positive TDD in comparison.html"
    assert "Persistent Knowledge Graph" in comp_text, "Missing Persistent Knowledge Graph in comparison.html"
    assert "12 Engineering Dimensions Compared" in comp_text, "Missing 12 dimensions spec in comparison.html"
    assert "KOBITS AUTONOMOUS SE" in comp_text, "Missing footer branding in comparison.html"
    assert "kb-comparison-menu-toggle" in comp_text, "Missing mobile menu toggle in comparison.html"

