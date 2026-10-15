#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_owasp_audit.sh — OWASP Top 10 Security Gate
# Mission: 0100ff5f-48c5-458a-b9c0-d8d233618d92
# Project: ab7aa6f5-2480-4b56-98cd-5ba2969d4a18
#
# Exit codes:
#   0 — All checks passed (no HIGH severity findings)
#   1 — HIGH severity finding(s) detected → CI gate FAILS build
#   2 — Tool installation/setup error
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPORT_DIR="$REPO_ROOT/security/reports"
BANDIT_REPORT="$REPORT_DIR/bandit_report.json"
SAFETY_REPORT="$REPORT_DIR/safety_report.json"
OWASP_REPORT="$REPORT_DIR/owasp_audit_report.json"

mkdir -p "$REPORT_DIR"

echo "════════════════════════════════════════════════════════════"
echo "  OWASP Top 10 Security Gate"
echo "  Project: ab7aa6f5-2480-4b56-98cd-5ba2969d4a18"
echo "  $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "════════════════════════════════════════════════════════════"

# ── Step 1: Install tools ────────────────────────────────────────────────────
echo ""
echo "[SETUP] Installing security tools..."
pip install --quiet bandit safety 2>&1 || {
    echo "[ERROR] Failed to install security tools" >&2
    exit 2
}

# ── Step 2: Bandit SAST ──────────────────────────────────────────────────────
echo ""
echo "[BANDIT] Running SAST analysis on Python sources..."
BANDIT_EXIT=0
bandit -r "$REPO_ROOT" \
    --exclude "$REPO_ROOT/.venv,$REPO_ROOT/venv,$REPO_ROOT/security" \
    -f json \
    -o "$BANDIT_REPORT" \
    --severity-level medium \
    --confidence-level medium \
    2>&1 || BANDIT_EXIT=$?

if [ -f "$BANDIT_REPORT" ]; then
    HIGH_COUNT=$(python3 -c "
import json, sys
try:
    r = json.load(open('$BANDIT_REPORT'))
    highs = [i for i in r.get('results', []) if i.get('issue_severity') == 'HIGH']
    print(len(highs))
except Exception:
    print(0)
")
    echo "[BANDIT] HIGH findings: $HIGH_COUNT"
    echo "[BANDIT] Full report: $BANDIT_REPORT"
else
    HIGH_COUNT=0
fi

# ── Step 3: Safety dependency check ─────────────────────────────────────────
echo ""
echo "[SAFETY] Checking Python dependencies for known CVEs..."
SAFETY_EXIT=0
if [ -f "$REPO_ROOT/requirements.txt" ]; then
    safety check \
        -r "$REPO_ROOT/requirements.txt" \
        --output json \
        --save-json "$SAFETY_REPORT" \
        2>&1 || SAFETY_EXIT=$?
    echo "[SAFETY] Report: $SAFETY_REPORT"
else
    echo "[SAFETY] No requirements.txt found, skipping dependency check"
fi

# ── Step 4: OWASP Pattern Checks ────────────────────────────────────────────
echo ""
echo "[OWASP]  Running OWASP Top 10 pattern audit..."
cd "$REPO_ROOT"
OWASP_EXIT=0
python3 "$SCRIPT_DIR/owasp_audit_checks.py" || OWASP_EXIT=$?

# ── Step 5: Gate Evaluation ──────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  SECURITY GATE EVALUATION"
echo "════════════════════════════════════════════════════════════"

GATE_FAILED=0

if [ "$BANDIT_EXIT" -ne 0 ] && [ "${HIGH_COUNT:-0}" -gt 0 ]; then
    echo "[FAIL]  Bandit found $HIGH_COUNT HIGH severity issues"
    GATE_FAILED=1
else
    echo "[PASS]  Bandit: no HIGH severity SAST findings"
fi

if [ "$OWASP_EXIT" -ne 0 ]; then
    echo "[FAIL]  OWASP pattern checks found HIGH severity findings"
    GATE_FAILED=1
else
    echo "[PASS]  OWASP pattern checks: gate passed"
fi

echo ""
if [ "$GATE_FAILED" -eq 1 ]; then
    echo "  ❌  SECURITY GATE: FAILED — Build blocked"
    echo "  Review reports in: $REPORT_DIR"
    exit 1
else
    echo "  ✅  SECURITY GATE: PASSED"
    exit 0
fi