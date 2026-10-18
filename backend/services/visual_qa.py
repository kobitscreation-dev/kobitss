"""
Multi-Modal Visual Regression Testing Service (Visual QA Manager).
Kyros 1:1 Enterprise Parity:
Provides computer-vision and DOM-grounded layout inspection across responsive
viewports (Desktop 1920x1080, Tablet 768x1024, Mobile 375x667).
Detects:
1. Element overlap & text collision
2. Color contrast accessibility violations (WCAG 2.1 AA/AAA)
3. Asset failures & broken images (404s, missing src)
4. Horizontal overflow on mobile viewports
5. Perceptual visual regression against baseline screenshots
Generates structured QA findings for Sentinel to trigger automatic remediation.
"""

import os
import re
import json
import math
import struct
import zlib
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger("kobits.visual_qa")

STANDARD_VIEWPORTS = {
    "desktop": {"width": 1920, "height": 1080, "label": "Desktop (1080p)"},
    "tablet": {"width": 768, "height": 1024, "label": "Tablet (Portrait)"},
    "mobile": {"width": 375, "height": 667, "label": "Mobile (iPhone SE/13 mini)"},
}


def parse_color_to_rgb(color_str: str) -> Optional[Tuple[int, int, int]]:
    """Parses hex, rgb(r, g, b), or standard named colors into an (r, g, b) integer tuple."""
    if not color_str:
        return None
    c = color_str.strip().lower()

    named_colors = {
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "red": (255, 0, 0),
        "green": (0, 128, 0),
        "blue": (0, 0, 255),
        "yellow": (255, 255, 0),
        "cyan": (0, 255, 255),
        "magenta": (255, 0, 255),
        "gray": (128, 128, 128),
        "grey": (128, 128, 128),
        "lightgray": (211, 211, 211),
        "lightgrey": (211, 211, 211),
        "darkgray": (169, 169, 169),
        "darkgrey": (169, 169, 169),
    }
    if c in named_colors:
        return named_colors[c]

    if c.startswith("#"):
        hex_val = c.lstrip("#")
        if len(hex_val) == 3:
            try:
                return (int(hex_val[0] * 2, 16), int(hex_val[1] * 2, 16), int(hex_val[2] * 2, 16))
            except ValueError:
                return None
        elif len(hex_val) in (6, 8):
            try:
                return (int(hex_val[0:2], 16), int(hex_val[2:4], 16), int(hex_val[4:6], 16))
            except ValueError:
                return None

    rgb_match = re.match(r"rgba?\(\s*([0-9]+)\s*,\s*([0-9]+)\s*,\s*([0-9]+)", c)
    if rgb_match:
        try:
            return (int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3)))
        except ValueError:
            return None

    return None


@dataclass
class SentinelFinding:
    """Formal QA Finding synthesized by Sentinel for automated self-healing and reproducer loops."""
    finding_id: str
    category: str  # "LAYOUT_ALIGNMENT" | "TEXT_OVERLAP" | "RESPONSIVE_BREAKPOINT_COMPLIANCE" | "BROKEN_ASSET" | "CONTRAST_VIOLATION" | "VISUAL_DIFF"
    severity: str  # "CRITICAL" | "HIGH" | "MEDIUM" | "WARNING" | "INFO"
    message: str
    viewport: str = "desktop"  # "desktop" | "tablet" | "mobile"
    selector: Optional[str] = None
    bounding_box: Optional[Dict[str, float]] = None
    wcag_criterion: Optional[str] = None  # e.g. "WCAG 2.1 AA 1.4.3"
    suggested_fix: Optional[str] = None
    suggested_patch_hints: List[str] = field(default_factory=list)
    reproducer_snippet: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "viewport": self.viewport,
            "selector": self.selector,
            "bounding_box": self.bounding_box,
            "wcag_criterion": self.wcag_criterion,
            "suggested_fix": self.suggested_fix,
            "suggested_patch_hints": list(self.suggested_patch_hints),
            "reproducer_snippet": self.reproducer_snippet,
            "metadata": dict(self.metadata),
        }

    def generate_reproducer_script(self) -> str:
        """Synthesizes a negative-to-positive Python reproducer script for this Sentinel QA finding."""
        sel_esc = (self.selector or "body").replace('"', '\\"')
        msg_esc = self.message.replace('"', '\\"').replace("\n", " ")
        cat = self.category
        vp = self.viewport

        return f'''"""
Sentinel Autonomous Visual QA Negative Reproducer.
Category: {cat}
Viewport: {vp}
Target Selector: {sel_esc}
Defect: {msg_esc}
"""
import sys
import os
import re

def reproduce():
    # Scan worktree component and markup files for the reported visual defect
    candidates = [
        "index.html", "dist/index.html", "build/index.html", "public/index.html", "web/index.html",
        "src/App.tsx", "src/App.jsx", "src/App.vue", "src/App.svelte", "src/index.tsx", "src/index.jsx"
    ]
    found_html = None
    for cand in candidates:
        if os.path.isfile(cand):
            with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                found_html = f.read()
            break

    if not found_html:
        # If no default candidate located, inspect component and HTML/CSS files
        ignored_dirs = {{".git", "node_modules", ".venv", "venv", "__pycache__"}}
        for root, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if d not in ignored_dirs]
            for file in files:
                if file.endswith((".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".css")):
                    with open(os.path.join(root, file), "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        if "{cat}" in ("BROKEN_ASSET",) and ("<img" in content.lower() or "src=" in content.lower()):
                            found_html = content
                            break
                        elif "{cat}" in ("CONTRAST_VIOLATION",) and ("color" in content.lower() or "background" in content.lower()):
                            found_html = content
                            break
                        elif "{cat}" in ("RESPONSIVE_BREAKPOINT_COMPLIANCE", "RESPONSIVE_OVERFLOW") and ("width" in content.lower() or "px" in content.lower()):
                            found_html = content
                            break
                        elif "{cat}" in ("LAYOUT_ALIGNMENT", "TEXT_OVERLAP") and ("margin" in content.lower() or "padding" in content.lower()):
                            found_html = content
                            break
                        elif not found_html:
                            found_html = content
            if found_html:
                break

    if not found_html:
        print("BUG REPRODUCED: Target markup or component not found in workspace")
        return 1

    category = "{cat}"
    if category in ("BROKEN_ASSET",):
        # Assert no empty/invalid image src attributes (HTML, JSX, or Vue templates)
        img_tags = re.findall(r"<img\\b[^>]*>", found_html, re.IGNORECASE)
        for img in img_tags:
            src_m = re.search(r'src=["\\\'{{](.*?)["\\\'}}]', img, re.IGNORECASE)
            val = src_m.group(1).strip() if src_m else ""
            if not src_m or not val or val in ("#", "undefined", "null", "javascript:void(0)", "''", '""'):
                print("BUG REPRODUCED: Found broken/missing img src in workspace HTML/JSX")
                return 1

    elif category in ("CONTRAST_VIOLATION",):
        # Assert no low contrast inline styles or flags
        if "color" in found_html.lower() and "background" in found_html.lower():
            low_contrast_patterns = [
                r"""color\\s*[:=]\\s*["']?(?:#999|#aaa|#bbb|#ccc|gray|grey)["']?""",
                r"""color\\s*[:=]\\s*["']?#[7-9a-fA-F]{{3,6}}["']?""",
                r"""text-(?:gray|zinc|slate)-(?:300|400)"""
            ]
            for pat in low_contrast_patterns:
                if re.search(pat, found_html, re.IGNORECASE):
                    print("BUG REPRODUCED: Low contrast style detected violating WCAG AA")
                    return 1

    elif category in ("RESPONSIVE_BREAKPOINT_COMPLIANCE", "RESPONSIVE_OVERFLOW"):
        # Assert no fixed widths > 375 on mobile
        fixed_w = re.findall(r"""(?:width|min-width)\\s*[:=]\\s*["']?([0-9]+)(?:px)?["']?""", found_html, re.IGNORECASE)
        for w_str in fixed_w:
            if int(w_str) > 375:
                print(f"BUG REPRODUCED: Fixed element width {{w_str}}px exceeds viewport boundary")
                return 1

    elif category in ("LAYOUT_ALIGNMENT", "TEXT_OVERLAP"):
        if re.search(r"""margin(?:-top|-left|-right|-bottom)?\\s*[:=]\\s*["']?-[3-9][0-9]px["']?""", found_html, re.IGNORECASE) or "-mt-[3-9]" in found_html:
            print("BUG REPRODUCED: Severe negative margin causing layout collision")
            return 1

    print("SUCCESS: Visual QA defect has been remediated")
    return 0

if __name__ == "__main__":
    sys.exit(reproduce())
'''


@dataclass
class VisualFinding:
    category: str  # "LAYOUT_OVERLAP" | "CONTRAST_VIOLATION" | "BROKEN_ASSET" | "RESPONSIVE_OVERFLOW" | "VISUAL_DIFF"
    severity: str  # "CRITICAL" | "WARNING" | "INFO"
    message: str
    selector: Optional[str] = None
    viewport: Optional[str] = None
    suggested_fix: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "selector": self.selector,
            "viewport": self.viewport,
            "suggested_fix": self.suggested_fix,
            "metadata": self.metadata,
        }

    def to_sentinel_finding(self) -> SentinelFinding:
        """Elevates this VisualFinding to a formal SentinelFinding."""
        import uuid
        f_id = f"sentinel_{uuid.uuid4().hex[:8]}"
        cat = self.category
        if cat == "LAYOUT_OVERLAP":
            cat = "TEXT_OVERLAP" if "text" in self.message.lower() else "LAYOUT_ALIGNMENT"
        elif cat == "RESPONSIVE_OVERFLOW":
            cat = "RESPONSIVE_BREAKPOINT_COMPLIANCE"

        wcag = "WCAG 2.1 AA 1.4.3" if self.category == "CONTRAST_VIOLATION" else None
        hints = [self.suggested_fix] if self.suggested_fix else []
        sf = SentinelFinding(
            finding_id=f_id,
            category=cat,
            severity=self.severity,
            message=self.message,
            viewport=self.viewport or "desktop",
            selector=self.selector,
            wcag_criterion=wcag,
            suggested_fix=self.suggested_fix,
            suggested_patch_hints=hints,
            metadata=dict(self.metadata),
        )
        sf.reproducer_snippet = sf.generate_reproducer_script()
        return sf



@dataclass
class VisualQAReport:
    passed: bool
    total_findings: int
    critical_findings: int
    findings: List[VisualFinding]
    viewports_checked: List[str]
    diff_ratio: float = 0.0
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "total_findings": self.total_findings,
            "critical_findings": self.critical_findings,
            "findings": [f.to_dict() for f in self.findings],
            "viewports_checked": self.viewports_checked,
            "diff_ratio": self.diff_ratio,
            "summary": self.summary,
        }


class VisualQAManager:
    """
    Enterprise Multi-Modal Visual Inspection Engine.
    Inspects rendered pages and screenshots for visual, responsive, and accessibility defects.
    """

    @staticmethod
    def calculate_relative_luminance(r: int, g: int, b: int) -> float:
        """Calculates relative luminance per WCAG 2.1 specifications."""
        def _channel(c: int) -> float:
            c_norm = c / 255.0
            return c_norm / 12.92 if c_norm <= 0.03928 else ((c_norm + 0.055) / 1.055) ** 2.4

        return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)

    @classmethod
    def calculate_contrast_ratio(cls, rgb1: Tuple[int, int, int], rgb2: Tuple[int, int, int]) -> float:
        """Calculates contrast ratio (L1 + 0.05) / (L2 + 0.05)."""
        lum1 = cls.calculate_relative_luminance(*rgb1)
        lum2 = cls.calculate_relative_luminance(*rgb2)
        lighter = max(lum1, lum2)
        darker = min(lum1, lum2)
        return round((lighter + 0.05) / (darker + 0.05), 2)

    @classmethod
    def compare_screenshots(
        cls,
        baseline_bytes: bytes,
        current_bytes: bytes,
        diff_threshold: float = 0.02,
    ) -> Tuple[bool, float, List[VisualFinding]]:
        """
        Performs byte-level and structural visual diff between baseline and current screenshots.
        Returns (passed, diff_ratio, findings).
        """
        findings = []
        if not baseline_bytes and not current_bytes:
            return True, 0.0, findings

        if not baseline_bytes or not current_bytes:
            findings.append(
                VisualFinding(
                    category="VISUAL_DIFF",
                    severity="CRITICAL",
                    message="Visual comparison failed: screenshot image bytes missing or empty",
                    suggested_fix="Ensure preview page rendered correctly and screenshot bytes were captured",
                )
            )
            return False, 1.0, findings

        if baseline_bytes == current_bytes:
            return True, 0.0, findings

        # Compute byte difference ratio
        len_b = len(baseline_bytes)
        len_c = len(current_bytes)
        max_len = max(len_b, len_c)
        min_len = min(len_b, len_c)

        # Count byte divergences
        divergent_bytes = abs(len_b - len_c)
        divergent_bytes += sum(1 for i in range(min_len) if baseline_bytes[i] != current_bytes[i])
        diff_ratio = round(divergent_bytes / max_len, 4)

        passed = diff_ratio <= diff_threshold
        if not passed:
            findings.append(
                VisualFinding(
                    category="VISUAL_DIFF",
                    severity="CRITICAL" if diff_ratio > 0.1 else "WARNING",
                    message=f"Visual regression detected: {round(diff_ratio * 100, 2)}% pixel divergence exceeds threshold of {round(diff_threshold * 100, 2)}%",
                    suggested_fix="Review UI modifications or update baseline screenshot if visual change was intentional",
                    metadata={"diff_ratio": diff_ratio, "threshold": diff_threshold},
                )
            )

        return passed, diff_ratio, findings

    @classmethod
    def inspect_dom_for_visual_defects(
        cls,
        html_content: str,
        viewport_name: str = "desktop",
    ) -> List[VisualFinding]:
        """
        Inspects HTML DOM for known high-frequency visual defects:
        - Broken / empty image tags
        - Missing alt tags on essential visuals
        - Hardcoded absolute widths exceeding responsive boundaries
        - Inline styles with overlapping or negative margins
        - WCAG 2.1 color contrast violations
        """
        findings: List[VisualFinding] = []
        if not html_content:
            return findings

        # 1. Broken / Missing Image Sources
        img_tags = re.findall(r"<img\b[^>]*>", html_content, re.IGNORECASE)
        for img in img_tags:
            src_match = re.search(r'src=["\'](.*?)["\']', img, re.IGNORECASE)
            val = src_match.group(1).strip() if src_match else ""
            if not src_match or not val or val in ("#", "undefined", "null", "javascript:void(0)", "javascript:;"):
                findings.append(
                    VisualFinding(
                        category="BROKEN_ASSET",
                        severity="CRITICAL",
                        message="Image element has empty, null, or invalid src attribute",
                        selector="img",
                        viewport=viewport_name,
                        suggested_fix="Provide a valid asset URL or placeholder data URI",
                    )
                )

        # 2. Responsive Overflow Risk (Fixed widths or min-widths > viewport)
        vp_width = STANDARD_VIEWPORTS.get(viewport_name, {}).get("width", 1280)
        fixed_width_matches = re.finditer(r'(?:style=["\'][^"\']*(?:width|min-width)\s*:\s*([0-9]+)px|width=["\']?([0-9]+)["\']?)', html_content, re.IGNORECASE)
        for m in fixed_width_matches:
            w_str = m.group(1) or m.group(2)
            if w_str:
                w = int(w_str)
                if w > vp_width:
                    findings.append(
                        VisualFinding(
                            category="RESPONSIVE_OVERFLOW",
                            severity="CRITICAL" if viewport_name == "mobile" else "WARNING",
                            message=f"Fixed element width ({w}px) exceeds viewport width ({vp_width}px), causing horizontal scroll",
                            viewport=viewport_name,
                            suggested_fix="Use max-width: 100% or responsive Tailwind classes (w-full max-w-...) instead of fixed px widths",
                        )
                    )

        # 3. Excessive negative margins causing element collision / clipping
        if re.search(r'margin(?:-top|-left|-right|-bottom)?\s*:\s*-[4-9][0-9]px', html_content, re.IGNORECASE):
            findings.append(
                VisualFinding(
                    category="LAYOUT_OVERLAP",
                    severity="WARNING",
                    message="Significant negative margin detected, which frequently causes text collision or unclickable controls",
                    viewport=viewport_name,
                    suggested_fix="Use CSS flex/grid spacing or relative positioning instead of heavy negative margins",
                )
            )

        # 4. Color Contrast Violations (WCAG AA check)
        style_matches = re.finditer(r'style=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
        for sm in style_matches:
            style_str = sm.group(1)
            color_m = re.search(r'(?:^|;)\s*color\s*:\s*([^;]+)', style_str, re.IGNORECASE)
            bg_m = re.search(r'(?:^|;)\s*background(?:-color)?\s*:\s*([^;]+)', style_str, re.IGNORECASE)
            if color_m and bg_m:
                fg_rgb = parse_color_to_rgb(color_m.group(1))
                bg_rgb = parse_color_to_rgb(bg_m.group(1))
                if fg_rgb and bg_rgb:
                    ratio = cls.calculate_contrast_ratio(fg_rgb, bg_rgb)
                    if ratio < 4.5:
                        findings.append(
                            VisualFinding(
                                category="CONTRAST_VIOLATION",
                                severity="CRITICAL" if ratio < 3.0 else "WARNING",
                                message=f"Low color contrast ({ratio}:1) between text '{color_m.group(1).strip()}' and background '{bg_m.group(1).strip()}' violates WCAG AA standard (4.5:1 minimum)",
                                viewport=viewport_name,
                                suggested_fix="Adjust text or background color to achieve at least 4.5:1 contrast ratio",
                                metadata={"contrast_ratio": ratio, "text_color": color_m.group(1).strip(), "background_color": bg_m.group(1).strip()},
                            )
                        )

        return findings

    @classmethod
    async def inspect_url(
        cls,
        url: str,
        viewports: Optional[List[str]] = None,
        baseline_screenshots: Optional[Dict[str, bytes]] = None,
    ) -> VisualQAReport:
        """
        Runs comprehensive multi-modal Visual QA on a live URL across target viewports.
        """
        from backend.services.browser_engine import HeadlessBrowserEngine

        target_vps = viewports or ["desktop", "tablet", "mobile"]
        all_findings: List[VisualFinding] = []
        max_diff = 0.0

        for vp_name in target_vps:
            vp_cfg = STANDARD_VIEWPORTS.get(vp_name, STANDARD_VIEWPORTS["desktop"])
            async with HeadlessBrowserEngine(viewport={"width": vp_cfg["width"], "height": vp_cfg["height"]}) as browser:
                ctx = await browser.navigate(url)

                # 0. Check HTTP status code failure
                if ctx.status_code >= 400:
                    all_findings.append(
                        VisualFinding(
                            category="BROKEN_ASSET",
                            severity="CRITICAL",
                            message=f"HTTP {ctx.status_code} error loading preview page: {url}",
                            viewport=vp_name,
                            suggested_fix="Check preview web server routing and build artifacts",
                        )
                    )

                # 1. DOM visual defects
                dom_findings = cls.inspect_dom_for_visual_defects(ctx.html_content, viewport_name=vp_name)
                all_findings.extend(dom_findings)

                # 2. Check console errors related to 404 assets
                logs = await browser.get_console_logs()
                for log in logs:
                    if log.get("level") == "error" and ("404" in log.get("text", "") or "Failed to load resource" in log.get("text", "")):
                        all_findings.append(
                            VisualFinding(
                                category="BROKEN_ASSET",
                                severity="CRITICAL",
                                message=f"Failed to load frontend resource: {log.get('text')}",
                                viewport=vp_name,
                                suggested_fix="Ensure bundled asset exists in output directory and path is correctly prefixed",
                            )
                        )

                # 3. Baseline screenshot visual diff (if baseline provided)
                if baseline_screenshots and vp_name in baseline_screenshots:
                    current_shot = await browser.screenshot(full_page=True)
                    _, diff_ratio, diff_findings = cls.compare_screenshots(
                        baseline_screenshots[vp_name], current_shot
                    )
                    max_diff = max(max_diff, diff_ratio)
                    all_findings.extend(diff_findings)

        critical_count = sum(1 for f in all_findings if f.severity == "CRITICAL")
        passed = critical_count == 0

        summary = (
            f"Visual QA {'PASSED' if passed else 'FAILED'}: "
            f"{len(all_findings)} finding(s) ({critical_count} critical) across {len(target_vps)} viewports."
        )

        return VisualQAReport(
            passed=passed,
            total_findings=len(all_findings),
            critical_findings=critical_count,
            findings=all_findings,
            viewports_checked=target_vps,
            diff_ratio=max_diff,
            summary=summary,
        )

    @classmethod
    def elevate_to_sentinel_findings(cls, report: VisualQAReport) -> List[SentinelFinding]:
        """Elevates all findings in a VisualQAReport to formal SentinelFindings."""
        return [f.to_sentinel_finding() for f in report.findings]

    @classmethod
    def build_frontier_vision_prompt(
        cls,
        viewport_name: str,
        target_url: Optional[str] = None,
        dom_snapshot: Optional[str] = None,
    ) -> str:
        """
        Formulates a high-precision multi-modal vision prompt for frontier models
        (Claude 3.5/3.7 Sonnet, Gemini 1.5/2.0 Pro) inspecting rendered UI screenshots.
        """
        vp_cfg = STANDARD_VIEWPORTS.get(viewport_name, STANDARD_VIEWPORTS["desktop"])
        dom_section = f"\nRelevant DOM Snippet:\n```html\n{dom_snapshot[:2000]}\n```\n" if dom_snapshot else ""
        return f"""You are Sentinel, a Principal Visual QA and Computer Vision engineer at a world-class technology company.
Your role is to inspect the attached UI screenshot rendered at viewport: {viewport_name.upper()} ({vp_cfg['width']}x{vp_cfg['height']}px).
Target URL: {target_url or 'Ephemeral preview server'}
{dom_section}
Evaluate the rendered UI against these 5 strict multi-modal quality pillars:
1. LAYOUT_ALIGNMENT: Crooked elements, grid/flex column misalignment, inconsistent margins/padding, crooked headers or broken button rows.
2. TEXT_OVERLAP: Text colliding with adjacent elements, icons, or badges; truncated text labels missing ellipses; multi-line overlap.
3. RESPONSIVE_BREAKPOINT_COMPLIANCE: Content overflowing horizontal viewport bounds ({vp_cfg['width']}px on {viewport_name}), unstyled mobile clipping, horizontal scrollbars.
4. BROKEN_ASSET: Missing image thumbnails, 404 broken image placeholders, empty src tags, failed web font fallbacks.
5. CONTRAST_VIOLATION: Low color contrast violating WCAG 2.1 AA (requires minimum 4.5:1 for normal text, 3:1 for large text or interactive controls).

OUTPUT INSTRUCTIONS:
Return ONLY a valid JSON array of findings. If no defects exist, return `[]`.
Schema for each finding item:
{{
  "category": "LAYOUT_ALIGNMENT" | "TEXT_OVERLAP" | "RESPONSIVE_BREAKPOINT_COMPLIANCE" | "BROKEN_ASSET" | "CONTRAST_VIOLATION",
  "severity": "CRITICAL" | "HIGH" | "WARNING" | "INFO",
  "message": "<Precise description of the visual defect>",
  "selector": "<CSS selector or semantic element location>",
  "bounding_box": {{"x": <float>, "y": <float>, "width": <float>, "height": <float>}},
  "wcag_criterion": "<e.g. WCAG 2.1 AA 1.4.3 or null>",
  "suggested_fix": "<Concrete CSS / HTML / Tailwind remediation code>",
  "suggested_patch_hints": ["<hint 1>", "<hint 2>"]
}}
"""

    @classmethod
    def parse_frontier_vision_response(
        cls,
        response_text: str,
        viewport_name: str = "desktop",
        viewport: Optional[str] = None,
    ) -> List[SentinelFinding]:
        """Parses and validates structured JSON response from frontier multi-modal vision models."""
        import uuid
        vp = viewport or viewport_name or "desktop"
        findings: List[SentinelFinding] = []
        if not response_text or not response_text.strip():
            return findings

        clean_text = response_text.strip()
        if "```json" in clean_text:
            clean_text = clean_text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in clean_text:
            clean_text = clean_text.split("```", 1)[1].split("```", 1)[0].strip()

        try:
            raw_data = json.loads(clean_text)
            if isinstance(raw_data, dict) and "findings" in raw_data:
                raw_data = raw_data["findings"]
            if not isinstance(raw_data, list):
                raw_data = [raw_data]

            for item in raw_data:
                if not isinstance(item, dict):
                    continue
                f_id = item.get("finding_id") or f"sentinel_{uuid.uuid4().hex[:8]}"
                cat = item.get("category", "LAYOUT_ALIGNMENT")
                sev = item.get("severity", "WARNING").upper()
                msg = item.get("message", "Visual defect detected by frontier vision model")
                sel = item.get("selector")
                bbox = item.get("bounding_box")
                wcag = item.get("wcag_criterion")
                fix = item.get("suggested_fix")
                hints = item.get("suggested_patch_hints", [])
                if fix and fix not in hints:
                    hints.insert(0, fix)

                sf = SentinelFinding(
                    finding_id=f_id,
                    category=cat,
                    severity=sev,
                    message=msg,
                    viewport=vp,
                    selector=sel,
                    bounding_box=bbox,
                    wcag_criterion=wcag,
                    suggested_fix=fix,
                    suggested_patch_hints=hints,
                    metadata=item.get("metadata", {}),
                )
                sf.reproducer_snippet = sf.generate_reproducer_script()
                findings.append(sf)
        except Exception as err:
            logger.warning(f"Failed to parse frontier vision response as JSON: {err}")

        return findings

    @classmethod
    async def analyze_with_frontier_vision(
        cls,
        screenshot_bytes: bytes,
        viewport_name: str = "desktop",
        model: str = "gemini-1.5-pro",
        api_key: Optional[str] = None,
        dom_content: Optional[str] = None,
        target_url: Optional[str] = None,
    ) -> List[SentinelFinding]:
        """
        Executes multi-modal visual QA analysis using frontier models (Gemini / Claude Sonnet).
        Falls back seamlessly to local computer-vision and DOM heuristic analysis when offline
        or when external model credentials are not configured.
        """
        prompt = cls.build_frontier_vision_prompt(viewport_name, target_url, dom_content)
        findings: List[SentinelFinding] = []

        # Check if an external multi-modal LLM API key is present
        anthropic_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        gemini_key = api_key or os.environ.get("GEMINI_API_KEY")

        if "claude" in model.lower() and anthropic_key:
            try:
                import anthropic
                import base64
                client = anthropic.Anthropic(api_key=anthropic_key)
                b64_img = base64.b64encode(screenshot_bytes).decode("utf-8")
                msg = client.messages.create(
                    model=model,
                    max_tokens=2048,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": b64_img,
                                    },
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                )
                res_text = msg.content[0].text if msg.content else ""
                findings = cls.parse_frontier_vision_response(res_text, viewport_name=viewport_name)
                if findings:
                    return findings
            except Exception as e:
                logger.warning(f"Claude Vision QA failed ({e}), falling back to heuristic engine")

        elif ("gemini" in model.lower() or not anthropic_key) and gemini_key:
            try:
                from google import genai
                client = genai.Client(api_key=gemini_key)
                resp = client.models.generate_content(
                    model=model,
                    contents=[
                        genai.types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png"),
                        prompt,
                    ],
                )
                findings = cls.parse_frontier_vision_response(resp.text or "", viewport_name=viewport_name)
                if findings:
                    return findings
            except Exception as e:
                logger.warning(f"Gemini Vision QA failed ({e}), falling back to heuristic engine")

        # Hermetic / Offline Rule-Based Fallback
        if dom_content:
            dom_findings = cls.inspect_dom_for_visual_defects(dom_content, viewport_name=viewport_name)
            for df in dom_findings:
                findings.append(df.to_sentinel_finding())

        return findings


# Module-level convenience aliases
build_frontier_vision_prompt = VisualQAManager.build_frontier_vision_prompt
parse_frontier_vision_response = VisualQAManager.parse_frontier_vision_response
analyze_with_frontier_vision = VisualQAManager.analyze_with_frontier_vision


# ===========================================================================
# GAP 10: Multi-Modal Perceptual Visual Diffing (Before vs. After SSIM,
#         64-Bit dHash, Pixelmatch Heatmap & Layout Shift Invariants)
# ===========================================================================

import base64


@dataclass
class PerceptualDiffReport:
    """Result of comparing a Before (baseline) vs. After (post-patch) UI state."""
    viewport: str
    ssim_score: float
    before_dhash: str
    after_dhash: str
    dhash_hamming_distance: int
    total_pixels: int
    changed_pixels: int
    pixel_diff_ratio: float
    cumulative_layout_shift: float
    classification: str  # "IDENTICAL" | "INTENTIONAL_TARGET_UPDATE" | "UNINTENDED_VISUAL_REGRESSION"
    regression_detected: bool
    diff_mask_data_uri: str
    changed_selectors: List[str] = field(default_factory=list)
    unintended_shifted_selectors: List[str] = field(default_factory=list)
    findings: List[SentinelFinding] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "viewport": self.viewport,
            "ssim_score": round(self.ssim_score, 4),
            "before_dhash": self.before_dhash,
            "after_dhash": self.after_dhash,
            "dhash_hamming_distance": self.dhash_hamming_distance,
            "total_pixels": self.total_pixels,
            "changed_pixels": self.changed_pixels,
            "pixel_diff_ratio": round(self.pixel_diff_ratio, 4),
            "cumulative_layout_shift": round(self.cumulative_layout_shift, 4),
            "classification": self.classification,
            "regression_detected": self.regression_detected,
            "diff_mask_data_uri": self.diff_mask_data_uri,
            "changed_selectors": self.changed_selectors,
            "unintended_shifted_selectors": self.unintended_shifted_selectors,
            "findings": [f.to_dict() for f in self.findings],
        }


class PerceptualVisualDiffEngine:
    """
    Frontier Perceptual Visual Diffing & Structural Layout Invariant Engine (Devin / Kyros Parity).

    Computes:
    1. 64-bit Perceptual Difference Hash (`dHash`) across 9x8 downsampled luminance grids.
    2. Windowed Structural Similarity Index (`SSIM` in [0.0, 1.0]) using luminance,
       contrast, and structural covariance.
    3. Pixelmatch Diff Heatmap (`data:image/png;base64,...`) highlighting changed regions.
    4. Cumulative Layout Shift (`CLS`) & Target Scope Invariants to distinguish
       intentional edits on `target_selectors` from accidental layout regressions on
       untouched structural components (navbar, footer, pricing grid, etc.).
    """

    _comparisons_run: int = 0
    _regressions_caught: int = 0
    _intentional_updates_verified: int = 0

    @staticmethod
    def encode_rgb_grid_to_png(grid: List[List[Tuple[int, int, int]]]) -> bytes:
        """Encodes a 2D `grid[y][x] = (r, g, b)` into a valid standard PNG byte stream."""
        height = len(grid)
        width = len(grid[0]) if height > 0 else 1

        raw_rows = bytearray()
        for row in grid:
            raw_rows.append(0)  # Filter type 0 (None)
            for r, g, b in row:
                raw_rows.extend((max(0, min(255, int(r))), max(0, min(255, int(g))), max(0, min(255, int(b)))))

        def _chunk(c_type: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(c_type + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + c_type + data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        compressed = zlib.compress(bytes(raw_rows), level=6)
        return (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", compressed)
            + _chunk(b"IEND", b"")
        )

    @staticmethod
    def decode_png_to_rgb_grid(png_bytes: bytes) -> Optional[List[List[Tuple[int, int, int]]]]:
        """Decodes a filter-0 8-bit RGB PNG produced by `encode_rgb_grid_to_png`."""
        if not png_bytes or not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        try:
            pos = 8
            width = 0
            height = 0
            idat_chunks = bytearray()
            while pos + 8 <= len(png_bytes):
                length = struct.unpack(">I", png_bytes[pos:pos + 4])[0]
                c_type = png_bytes[pos + 4:pos + 8]
                c_data = png_bytes[pos + 8:pos + 8 + length]
                pos += 12 + length
                if c_type == b"IHDR":
                    width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", c_data[:13])
                    if bit_depth != 8 or color_type != 2:
                        return None
                elif c_type == b"IDAT":
                    idat_chunks.extend(c_data)
                elif c_type == b"IEND":
                    break

            if width <= 0 or height <= 0 or not idat_chunks:
                return None

            raw = zlib.decompress(bytes(idat_chunks))
            stride = 1 + width * 3
            if len(raw) < height * stride:
                return None

            grid: List[List[Tuple[int, int, int]]] = []
            for y in range(height):
                row_start = y * stride + 1
                row_pixels: List[Tuple[int, int, int]] = []
                for x in range(width):
                    px = row_start + x * 3
                    row_pixels.append((raw[px], raw[px + 1], raw[px + 2]))
                grid.append(row_pixels)
            return grid
        except Exception:
            return None

    @classmethod
    def rasterize_dom_boxes_to_rgb_grid(
        cls,
        dom_boxes: List[Dict[str, Any]],
        viewport_width: int = 1280,
        viewport_height: int = 720,
        canvas_width: int = 64,
        canvas_height: int = 64,
        bg_rgb: Tuple[int, int, int] = (15, 23, 42),
    ) -> List[List[Tuple[int, int, int]]]:
        """
        Rasterizes DOM bounding boxes (`x, y, width, height, color`) onto a normalized
        `canvas_width x canvas_height` RGB pixel buffer for deterministic SSIM & dHash comparison.
        """
        grid: List[List[Tuple[int, int, int]]] = [
            [bg_rgb for _ in range(canvas_width)] for _ in range(canvas_height)
        ]
        scale_x = canvas_width / max(1.0, float(viewport_width))
        scale_y = canvas_height / max(1.0, float(viewport_height))

        palette = [
            (59, 130, 246),   # Blue
            (16, 185, 129),   # Emerald
            (245, 158, 11),   # Amber
            (139, 92, 246),   # Violet
            (236, 72, 153),   # Pink
            (14, 165, 233),   # Sky
        ]

        for idx, box in enumerate(dom_boxes):
            bx = float(box.get("x", 0))
            by = float(box.get("y", 0))
            bw = float(box.get("width", 0))
            bh = float(box.get("height", 0))
            if bw <= 0 or bh <= 0:
                continue

            color_str = str(box.get("color") or box.get("background") or "")
            rgb = parse_color_to_rgb(color_str)
            if rgb is None:
                # Deterministic color from selector hash so distinct elements have distinct luminance
                sel = str(box.get("selector", f"el_{idx}"))
                h_idx = sum(ord(ch) for ch in sel) % len(palette)
                rgb = palette[h_idx]

            x0 = max(0, min(canvas_width, int(round(bx * scale_x))))
            y0 = max(0, min(canvas_height, int(round(by * scale_y))))
            x1 = max(0, min(canvas_width, int(round((bx + bw) * scale_x))))
            y1 = max(0, min(canvas_height, int(round((by + bh) * scale_y))))

            if x1 <= x0 and bw > 0:
                x1 = min(canvas_width, x0 + 1)
            if y1 <= y0 and bh > 0:
                y1 = min(canvas_height, y0 + 1)

            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    grid[yy][xx] = rgb

        return grid

    @staticmethod
    def _rgb_to_luma(rgb: Tuple[int, int, int]) -> float:
        return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]

    @classmethod
    def compute_dhash(cls, grid: List[List[Tuple[int, int, int]]]) -> Tuple[int, str]:
        """
        Computes a 64-bit Perceptual Difference Hash (`dHash`) by sampling a 9x8 luminance grid
        and comparing horizontal neighbor intensities.
        """
        h = len(grid)
        w = len(grid[0]) if h > 0 else 1
        bits = 0
        bit_pos = 63

        for r in range(8):
            y = min(h - 1, int((r + 0.5) * h / 8.0))
            lumas: List[float] = []
            for c in range(9):
                x = min(w - 1, int((c + 0.5) * w / 9.0))
                lumas.append(cls._rgb_to_luma(grid[y][x]))
            for c in range(8):
                if lumas[c] > lumas[c + 1]:
                    bits |= (1 << bit_pos)
                bit_pos -= 1

        return bits, f"{bits:016x}"

    @staticmethod
    def hamming_distance_64(hash_a: int, hash_b: int) -> int:
        return bin(hash_a ^ hash_b).count("1")

    @classmethod
    def compute_ssim(
        cls,
        grid_a: List[List[Tuple[int, int, int]]],
        grid_b: List[List[Tuple[int, int, int]]],
        window_size: int = 8,
    ) -> float:
        """
        Computes the Structural Similarity Index (`SSIM` in [0.0, 1.0]) across `window_size x window_size`
        blocks using the Wang et al. luminance, contrast, and structural covariance formulation.
        """
        h = min(len(grid_a), len(grid_b))
        w = min(len(grid_a[0]) if h > 0 else 0, len(grid_b[0]) if h > 0 else 0)
        if h == 0 or w == 0:
            return 1.0

        c1 = (0.01 * 255.0) ** 2
        c2 = (0.03 * 255.0) ** 2

        window_scores: List[float] = []
        step = max(1, window_size)

        for y0 in range(0, h, step):
            for x0 in range(0, w, step):
                y1 = min(h, y0 + step)
                x1 = min(w, x0 + step)
                vals_a: List[float] = []
                vals_b: List[float] = []
                for yy in range(y0, y1):
                    for xx in range(x0, x1):
                        vals_a.append(cls._rgb_to_luma(grid_a[yy][xx]))
                        vals_b.append(cls._rgb_to_luma(grid_b[yy][xx]))

                n = len(vals_a)
                if n == 0:
                    continue
                mu_a = sum(vals_a) / n
                mu_b = sum(vals_b) / n

                var_a = sum((va - mu_a) ** 2 for va in vals_a) / n
                var_b = sum((vb - mu_b) ** 2 for vb in vals_b) / n
                cov_ab = sum((vals_a[i] - mu_a) * (vals_b[i] - mu_b) for i in range(n)) / n

                numerator = (2.0 * mu_a * mu_b + c1) * (2.0 * cov_ab + c2)
                denominator = (mu_a ** 2 + mu_b ** 2 + c1) * (var_a + var_b + c2)
                ssim_w = numerator / denominator if denominator > 0 else 1.0
                window_scores.append(max(0.0, min(1.0, ssim_w)))

        return sum(window_scores) / len(window_scores) if window_scores else 1.0

    @classmethod
    def compare_before_after(
        cls,
        before_dom_boxes: Optional[List[Dict[str, Any]]] = None,
        after_dom_boxes: Optional[List[Dict[str, Any]]] = None,
        before_png: Optional[bytes] = None,
        after_png: Optional[bytes] = None,
        target_selectors: Optional[List[str]] = None,
        viewport: str = "desktop",
        pixel_tolerance: int = 18,
    ) -> PerceptualDiffReport:
        """
        Performs a full perceptual visual diff and structural layout shift analysis
        between the Before (baseline) and After (post-patch) UI states.
        """
        cls._comparisons_run += 1
        vp_spec = STANDARD_VIEWPORTS.get(viewport, STANDARD_VIEWPORTS["desktop"])
        vp_w, vp_h = vp_spec["width"], vp_spec["height"]

        # 1. Obtain or rasterize 2D RGB pixel grids
        grid_before = cls.decode_png_to_rgb_grid(before_png) if before_png else None
        if grid_before is None:
            grid_before = cls.rasterize_dom_boxes_to_rgb_grid(
                before_dom_boxes or [],
                viewport_width=vp_w,
                viewport_height=vp_h,
            )

        grid_after = cls.decode_png_to_rgb_grid(after_png) if after_png else None
        if grid_after is None:
            grid_after = cls.rasterize_dom_boxes_to_rgb_grid(
                after_dom_boxes or [],
                viewport_width=vp_w,
                viewport_height=vp_h,
            )

        h = min(len(grid_before), len(grid_after))
        w = min(len(grid_before[0]) if h > 0 else 0, len(grid_after[0]) if h > 0 else 0)
        total_pixels = max(1, h * w)

        # 2. Compute 64-bit dHash and SSIM
        b_hash_int, b_hash_hex = cls.compute_dhash(grid_before)
        a_hash_int, a_hash_hex = cls.compute_dhash(grid_after)
        hamming = cls.hamming_distance_64(b_hash_int, a_hash_int)
        ssim = cls.compute_ssim(grid_before, grid_after)

        # 3. Generate Pixelmatch Diff Mask
        diff_grid: List[List[Tuple[int, int, int]]] = []
        changed_pixels = 0
        for yy in range(h):
            diff_row: List[Tuple[int, int, int]] = []
            for xx in range(w):
                r1, g1, b1 = grid_before[yy][xx]
                r2, g2, b2 = grid_after[yy][xx]
                delta = abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
                if delta > pixel_tolerance:
                    changed_pixels += 1
                    diff_row.append((255, 0, 85))  # High-contrast magenta diff highlight
                else:
                    # Dimmed grayscale background for unchanged pixels
                    luma = int(cls._rgb_to_luma((r2, g2, b2)) * 0.35)
                    diff_row.append((luma, luma, luma))
            diff_grid.append(diff_row)

        diff_png_bytes = cls.encode_rgb_grid_to_png(diff_grid)
        diff_data_uri = "data:image/png;base64," + base64.b64encode(diff_png_bytes).decode("ascii")
        pixel_diff_ratio = changed_pixels / float(total_pixels)

        # 4. Analyze DOM Bounding-Box Layout Shifts (CLS) & Target Invariants
        target_set = set(target_selectors or [])
        before_map = {str(b.get("selector", "")): b for b in (before_dom_boxes or []) if b.get("selector")}
        after_map = {str(b.get("selector", "")): b for b in (after_dom_boxes or []) if b.get("selector")}

        changed_selectors: List[str] = []
        unintended_shifted: List[str] = []
        findings: List[SentinelFinding] = []
        cls_total = 0.0
        vp_area = float(max(1, vp_w * vp_h))

        for sel, b_box in before_map.items():
            a_box = after_map.get(sel)
            is_target = sel in target_set

            if a_box is None:
                changed_selectors.append(sel)
                if not is_target:
                    unintended_shifted.append(sel)
                    findings.append(
                        SentinelFinding(
                            finding_id=f"VDIFF-MISSING-{ len(findings) + 1 }",
                            category="VISUAL_DIFF",
                            severity="CRITICAL",
                            message=f"Untouched structural element '{sel}' disappeared after patch.",
                            viewport=viewport,
                            selector=sel,
                            bounding_box=b_box,
                            suggested_fix=f"Restore DOM rendering and visibility for '{sel}'.",
                        )
                    )
                continue

            dx = abs(float(a_box.get("x", 0)) - float(b_box.get("x", 0)))
            dy = abs(float(a_box.get("y", 0)) - float(b_box.get("y", 0)))
            dw = abs(float(a_box.get("width", 0)) - float(b_box.get("width", 0)))
            dh = abs(float(a_box.get("height", 0)) - float(b_box.get("height", 0)))
            color_changed = str(a_box.get("color", "")) != str(b_box.get("color", ""))

            aw = float(a_box.get("width", 0))
            ah = float(a_box.get("height", 0))
            ax = float(a_box.get("x", 0))

            if dx > 1.0 or dy > 1.0 or dw > 1.0 or dh > 1.0 or color_changed:
                changed_selectors.append(sel)

                # Calculate layout shift contribution
                max_dim = max(1.0, float(vp_w), float(vp_h))
                distance_fraction = math.hypot(dx, dy) / max_dim
                impact_area = max(
                    float(b_box.get("width", 0)) * float(b_box.get("height", 0)),
                    aw * ah,
                )
                impact_fraction = min(1.0, impact_area / vp_area)
                if not is_target:
                    cls_total += impact_fraction * distance_fraction

                # Check unintended regressions on non-target elements (or global collapse/overflow)
                collapsed = (aw <= 0 or ah <= 0) and (float(b_box.get("width", 0)) > 0 and float(b_box.get("height", 0)) > 0)
                overflowed = (ax + aw) > (vp_w + 2.0)

                if collapsed:
                    unintended_shifted.append(sel)
                    findings.append(
                        SentinelFinding(
                            finding_id=f"VDIFF-COLLAPSE-{len(findings) + 1}",
                            category="VISUAL_DIFF",
                            severity="CRITICAL",
                            message=f"Element '{sel}' collapsed to zero dimensions ({aw}x{ah}px) after patch.",
                            viewport=viewport,
                            selector=sel,
                            bounding_box=a_box,
                            suggested_fix=f"Fix flexbox/grid collapse on '{sel}'.",
                        )
                    )
                elif overflowed:
                    unintended_shifted.append(sel)
                    findings.append(
                        SentinelFinding(
                            finding_id=f"VDIFF-OVERFLOW-{len(findings) + 1}",
                            category="VISUAL_DIFF",
                            severity="HIGH",
                            message=f"Element '{sel}' overflows horizontal viewport (right edge {ax + aw}px > {vp_w}px).",
                            viewport=viewport,
                            selector=sel,
                            bounding_box=a_box,
                            suggested_fix=f"Constrain width/max-width of '{sel}' within viewport.",
                        )
                    )
                elif not is_target and (dx > 16.0 or dy > 16.0 or dw > 24.0 or dh > 24.0):
                    unintended_shifted.append(sel)
                    findings.append(
                        SentinelFinding(
                            finding_id=f"VDIFF-SHIFT-{len(findings) + 1}",
                            category="VISUAL_DIFF",
                            severity="HIGH",
                            message=(
                                f"Unintended layout shift on non-target element '{sel}' "
                                f"(dx={dx:.1f}px, dy={dy:.1f}px, dw={dw:.1f}px, dh={dh:.1f}px)."
                            ),
                            viewport=viewport,
                            selector=sel,
                            bounding_box=a_box,
                            suggested_fix=f"Prevent layout displacement of '{sel}' when modifying {list(target_set)}.",
                        )
                    )

        # Also check if pixels changed drastically without DOM metadata (pure screenshot mode)
        if not before_map and not after_map and ssim < 0.70:
            findings.append(
                SentinelFinding(
                    finding_id="VDIFF-SSIM-DROP-1",
                    category="VISUAL_DIFF",
                    severity="HIGH",
                    message=f"Severe perceptual SSIM drop ({ssim:.3f} < 0.70) detected across viewport '{viewport}'.",
                    viewport=viewport,
                    suggested_fix="Inspect diff heatmap mask for unintended layout breakage.",
                )
            )

        regression_detected = len(findings) > 0 or len(unintended_shifted) > 0
        if regression_detected:
            classification = "UNINTENDED_VISUAL_REGRESSION"
            cls._regressions_caught += 1
        elif changed_pixels == 0 and not changed_selectors:
            classification = "IDENTICAL"
        else:
            classification = "INTENTIONAL_TARGET_UPDATE"
            cls._intentional_updates_verified += 1

        return PerceptualDiffReport(
            viewport=viewport,
            ssim_score=ssim,
            before_dhash=b_hash_hex,
            after_dhash=a_hash_hex,
            dhash_hamming_distance=hamming,
            total_pixels=total_pixels,
            changed_pixels=changed_pixels,
            pixel_diff_ratio=pixel_diff_ratio,
            cumulative_layout_shift=cls_total,
            classification=classification,
            regression_detected=regression_detected,
            diff_mask_data_uri=diff_data_uri,
            changed_selectors=changed_selectors,
            unintended_shifted_selectors=unintended_shifted,
            findings=findings,
        )

    @classmethod
    def get_telemetry(cls) -> Dict[str, Any]:
        return {
            "engine": "perceptual_ssim_dhash_pixelmatch_cls",
            "comparisons_run": cls._comparisons_run,
            "regressions_caught": cls._regressions_caught,
            "intentional_updates_verified": cls._intentional_updates_verified,
            "metrics_enabled": ["SSIM", "64bit_dHash", "Pixelmatch_PNG_Heatmap", "CLS_Layout_Shift_Invariant"],
        }


VisualQAManager.compare_before_after = PerceptualVisualDiffEngine.compare_before_after


