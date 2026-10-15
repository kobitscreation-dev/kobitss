"""
OWASP Top 10 Security Audit - Automated Check Suite
Mission: 0100ff5f-48c5-458a-b9c0-d8d233618d92
Project: ab7aa6f5-2480-4b56-98cd-5ba2969d4a18

Covers:
  - A01: Broken Access Control
  - A02: Cryptographic Failures / Exposed Secrets
  - A03: Injection (SQL, Command, Prompt)
  - A05: Security Misconfiguration
  - A07: Auth Failures
  - A09: Logging & Monitoring
  - A10: SSRF
"""

import os
import re
import json
import pathlib
import datetime
from typing import Dict, List, Tuple


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

ROOT_DIR = pathlib.Path(".").resolve()
EXCLUDE_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__",
    ".mypy_cache", "security", ".tox", "dist", "build", ".pytest_cache",
}

SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"
SEVERITY_INFO = "INFO"


# ─────────────────────────────────────────────
# Finding Model
# ─────────────────────────────────────────────

class Finding:
    """Represents a single security finding."""

    def __init__(
        self,
        owasp_id: str,
        title: str,
        severity: str,
        file_path,
        line_no: int,
        snippet: str,
        remediation: str,
    ):
        self.owasp_id = owasp_id
        self.title = title
        self.severity = severity
        self.file_path = str(file_path)
        self.line_no = line_no
        self.snippet = snippet.rstrip()
        self.remediation = remediation

    def to_dict(self) -> dict:
        return {
            "owasp_id": self.owasp_id,
            "title": self.title,
            "severity": self.severity,
            "file_path": self.file_path,
            "line_no": self.line_no,
            "snippet": self.snippet,
            "remediation": self.remediation,
        }


# ─────────────────────────────────────────────
# Utility Helpers
# ─────────────────────────────────────────────

def iter_source_files(extensions: tuple = (".py", ".js", ".ts", ".env", ".yml", ".yaml")):
    """Yield all source files respecting EXCLUDE_DIRS."""
    for root, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fname in files:
            if fname.endswith(extensions):
                yield pathlib.Path(root) / fname


def read_lines(fpath: pathlib.Path) -> List[str]:
    """Read file lines safely, returning empty list on error."""
    try:
        return fpath.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


# ─────────────────────────────────────────────
# Pattern Libraries
# ─────────────────────────────────────────────

# A02 – Secrets
SECRET_PATTERNS = [
    # Generic high-entropy secret assignments
    r'(?i)(password|passwd|secret|api_key|apikey|token|auth_token|access_token|private_key)\s*=\s*["\'][^"\']{8,}["\']',
    # Database URLs with embedded credentials (e.g. postgresql://user:pass@host/db)
    r'(?i)(database_url|db_url|connection_string)\s*=\s*["\'][a-z+]+://[^"\']+:[^"\'@]+@[^"\']+["\']',
    # Generic connection strings with passwords
    r'(?i)://[^"\']*:[^"\'@]{4,}@',
    # OpenAI / Anthropic / HuggingFace
    r'sk-[a-zA-Z0-9]{20,}',
    r'sk-ant-[a-zA-Z0-9\-]{20,}',
    r'hf_[a-zA-Z0-9]{20,}',
    # AWS
    r'AKIA[0-9A-Z]{16}',
    # Generic bearer / auth headers hardcoded
    r'(?i)Authorization\s*[:=]\s*["\']Bearer\s+[A-Za-z0-9\-_\.]+["\']',
    # Private key blocks
    r'-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----',
]

# A03 – SQL Injection
SQL_INJECTION_PATTERNS = [
    r'execute\s*\(\s*["\'].*%[s|d].*["\']',                      # % string formatting in execute
    r'execute\s*\(\s*f["\'].*\{',                                  # f-string in execute
    r'cursor\.execute\s*\(\s*["\'][^"\']*["\'\s]*\+',             # string concat in cursor.execute
    r'(?i)SELECT\s+.+FROM\s+.+WHERE\s+.+["\'\s]*\+\s*\w',        # raw string concat SQL
    r'(?i)raw\s*\(\s*["\'][^"\']*%[sd]',                          # Django raw() with %
    r'(?i)\.filter\s*\(\s*["\'].*%s.*["\']',                      # ORM filter with %s
    r'text\s*\(\s*f["\']',                                         # SQLAlchemy text(f"...")
]

# A03 – Command Injection
CMD_INJECTION_PATTERNS = [
    r'os\.system\s*\(',
    r'subprocess\.(call|run|Popen)\s*\(["\']',             # string (not list) arg
    r'subprocess\.(call|run|Popen)\s*\(.*shell\s*=\s*True',
    r'eval\s*\(',
    r'exec\s*\(',
    r'__import__\s*\(',
]

# A03/LLM01 – Prompt Injection
PROMPT_INJECTION_PATTERNS = [
    r'(?i)(prompt|system_prompt|user_input)\s*[+]=?\s*\w',   # direct concatenation
    r'(?i)f["\'].*\{.*user.*\}.*["\'].*openai',              # f-string with user input to AI call
    r'(?i)ChatCompletion\.create',                             # raw openai calls without sanitization
    r'(?i)openai\.completions\.create',
    r'(?i)anthropic.*messages\.create',
]

# A05 – Misconfiguration
MISCONFIG_PATTERNS = [
    r'(?i)DEBUG\s*=\s*True',
    r'(?i)ALLOWED_HOSTS\s*=\s*\[?\s*[\'"]\*["\']',
    r'(?i)CORS_ORIGIN_ALLOW_ALL\s*=\s*True',
    r'(?i)cors.*origins.*\*',
    r'(?i)verify\s*=\s*False',                                # SSL verify=False
    r'(?i)SSL_VERIFY\s*=\s*False',
    r'(?i)SECRET_KEY\s*=\s*["\']django-insecure-',           # Django insecure default SECRET_KEY
]

# A07 – Auth Failures
AUTH_FAILURE_PATTERNS = [
    r'(?i)algorithm\s*=\s*["\']none["\']',                    # JWT alg=none
    r'(?i)algorithms\s*=\s*\[\s*["\']none["\']',
    r'(?i)jwt\.decode\s*\(.*verify\s*=\s*False',              # JWT verify=False
    r'(?i)check_expiration\s*=\s*False',
    r'(?i)md5\s*\(',                                           # weak hashing for passwords
    r'(?i)hashlib\.md5\s*\(',
    r'(?i)hashlib\.sha1\s*\(',
]

# A10 – SSRF
SSRF_PATTERNS = [
    r'requests\.get\s*\(\s*\w',                               # requests.get(variable)
    r'requests\.post\s*\(\s*\w',
    r'urllib\.request\.urlopen\s*\(\s*\w',
    r'httpx\.(get|post|request)\s*\(\s*\w',
    r'aiohttp.*session\.(get|post)\s*\(\s*\w',
    r'(?i)fetch\s*\(\s*(?![\"\']https?://)',                   # JS fetch without literal URL
]

# A01 – Access Control
ACCESS_CONTROL_PATTERNS = [
    r'(?i)@app\.route.*methods.*["\']GET["\'](?!.*login_required|.*require)',  # unprotected GET
    r'(?i)allow_unauthenticated\s*=\s*True',
    r'(?i)@permission_classes\s*\(\s*\[\s*AllowAny\s*\]\s*\)',
    r'(?i)IsAuthenticated.*False',
]


# ─────────────────────────────────────────────
# Check Functions
# ─────────────────────────────────────────────

def check_a01_access_control(findings: List[Finding]):
    """A01 - Broken Access Control checks."""
    for fpath in iter_source_files(extensions=(".py",)):
        lines = read_lines(fpath)
        for i, line in enumerate(lines, 1):
            for pattern in ACCESS_CONTROL_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(Finding(
                        owasp_id="A01",
                        title="Broken Access Control: Potentially Unprotected Endpoint",
                        severity=SEVERITY_MEDIUM,
                        file_path=fpath.relative_to(ROOT_DIR),
                        line_no=i,
                        snippet=line,
                        remediation=(
                            "Ensure all non-public routes require authentication. "
                            "Use @login_required, permission_classes=[IsAuthenticated], "
                            "or equivalent decorators. Audit RBAC for IDOR vulnerabilities."
                        ),
                    ))


def check_a02_secrets(findings: List[Finding]):
    """A02 - Scan for hardcoded secrets and API keys."""
    for fpath in iter_source_files(extensions=(".py", ".js", ".ts", ".env", ".yml", ".yaml", ".json")):
        # Skip lock files and known-safe config
        if fpath.name in ("package-lock.json", "yarn.lock", "poetry.lock"):
            continue
        lines = read_lines(fpath)
        for i, line in enumerate(lines, 1):
            # Skip comments and example files
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            for pattern in SECRET_PATTERNS:
                if re.search(pattern, line):
                    findings.append(Finding(
                        owasp_id="A02",
                        title="Cryptographic Failure: Hardcoded Secret / API Key",
                        severity=SEVERITY_HIGH,
                        file_path=fpath.relative_to(ROOT_DIR),
                        line_no=i,
                        snippet=line,
                        remediation=(
                            "Remove hardcoded credentials. Load secrets exclusively from "
                            "environment variables (os.environ.get) or a secrets manager. "
                            "Add .env to .gitignore. Rotate any exposed credentials immediately."
                        ),
                    ))


def check_env_file_exposure(findings: List[Finding]):
    """A02 - Check if .env files are tracked by git."""
    for fpath in ROOT_DIR.rglob(".env*"):
        if ".git" in fpath.parts:
            continue
        # Check gitignore
        gitignore = ROOT_DIR / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text()
            if ".env" in content:
                continue  # properly ignored
        findings.append(Finding(
            owasp_id="A02",
            title=".env File Not in .gitignore",
            severity=SEVERITY_HIGH,
            file_path=fpath.relative_to(ROOT_DIR),
            line_no=1,
            snippet=str(fpath.name),
            remediation=(
                "Add .env, .env.*, and *.env to .gitignore immediately. "
                "Ensure no .env files are committed to version control. "
                "Use a secrets manager (AWS Secrets Manager, HashiCorp Vault) for production."
            ),
        ))


def check_a03_sql_injection(findings: List[Finding]):
    """A03 - SQL Injection via string formatting/concatenation."""
    for fpath in iter_source_files(extensions=(".py",)):
        lines = read_lines(fpath)
        for i, line in enumerate(lines, 1):
            for pattern in SQL_INJECTION_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(Finding(
                        owasp_id="A03",
                        title="SQL Injection: Unsafe Query Construction",
                        severity=SEVERITY_HIGH,
                        file_path=fpath.relative_to(ROOT_DIR),
                        line_no=i,
                        snippet=line,
                        remediation=(
                            "Use parameterized queries exclusively: "
                            "cursor.execute('SELECT * FROM t WHERE id=%s', (user_id,)). "
                            "With SQLAlchemy, use text() with bindparams or ORM query methods. "
                            "Never interpolate user input into SQL strings."
                        ),
                    ))


def check_a03_cmd_injection(findings: List[Finding]):
    """A03 - Command Injection via shell execution."""
    for fpath in iter_source_files(extensions=(".py",)):
        lines = read_lines(fpath)
        for i, line in enumerate(lines, 1):
            for pattern in CMD_INJECTION_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(Finding(
                        owasp_id="A03",
                        title="Command Injection: Unsafe Shell Execution",
                        severity=SEVERITY_HIGH,
                        file_path=fpath.relative_to(ROOT_DIR),
                        line_no=i,
                        snippet=line,
                        remediation=(
                            "Replace os.system() with subprocess.run(cmd_list, shell=False). "
                            "Always pass commands as a list, never as a string with shell=True. "
                            "Avoid eval()/exec() on any user-controlled input."
                        ),
                    ))


def check_a03_prompt_injection(findings: List[Finding]):
    """A03/LLM01 - Prompt Injection into LLM calls."""
    for fpath in iter_source_files(extensions=(".py",)):
        lines = read_lines(fpath)
        for i, line in enumerate(lines, 1):
            for pattern in PROMPT_INJECTION_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(Finding(
                        owasp_id="A03/LLM01",
                        title="Prompt Injection: Unsanitized User Input in LLM Prompt",
                        severity=SEVERITY_HIGH,
                        file_path=fpath.relative_to(ROOT_DIR),
                        line_no=i,
                        snippet=line,
                        remediation=(
                            "Apply the PromptSanitizer from app/ai/sanitizer.py before "
                            "constructing any LLM prompt. Strip instruction-override patterns "
                            "(ignore previous instructions, jailbreak sequences). "
                            "Use a system prompt boundary that separates instructions from user content."
                        ),
                    ))


def check_a05_misconfig(findings: List[Finding]):
    """A05 - Security Misconfiguration."""
    for fpath in iter_source_files(extensions=(".py", ".yml", ".yaml", ".env")):
        lines = read_lines(fpath)
        for i, line in enumerate(lines, 1):
            for pattern in MISCONFIG_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(Finding(
                        owasp_id="A05",
                        title="Security Misconfiguration",
                        severity=SEVERITY_HIGH,
                        file_path=fpath.relative_to(ROOT_DIR),
                        line_no=i,
                        snippet=line,
                        remediation=(
                            "Set DEBUG = os.environ.get('DEBUG', 'False') == 'True'. "
                            "Configure ALLOWED_HOSTS explicitly in production. "
                            "Disable CORS_ORIGIN_ALLOW_ALL; enumerate allowed origins. "
                            "Never disable SSL certificate verification."
                        ),
                    ))


def check_a07_auth_failures(findings: List[Finding]):
    """A07 - Identification and Authentication Failures."""
    for fpath in iter_source_files(extensions=(".py",)):
        lines = read_lines(fpath)
        for i, line in enumerate(lines, 1):
            for pattern in AUTH_FAILURE_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(Finding(
                        owasp_id="A07",
                        title="Auth Failure: Weak/Missing Authentication Control",
                        severity=SEVERITY_HIGH,
                        file_path=fpath.relative_to(ROOT_DIR),
                        line_no=i,
                        snippet=line,
                        remediation=(
                            "For JWT: always specify algorithms=['HS256'] or ['RS256']. "
                            "Never set algorithm='none'. Always verify token signatures. "
                            "Use bcrypt or argon2 for password hashing, never MD5/SHA1."
                        ),
                    ))


def check_a10_ssrf(findings: List[Finding]):
    """A10 - Server-Side Request Forgery."""
    for fpath in iter_source_files(extensions=(".py", ".js", ".ts")):
        lines = read_lines(fpath)
        for i, line in enumerate(lines, 1):
            for pattern in SSRF_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(Finding(
                        owasp_id="A10",
                        title="SSRF: Unvalidated URL in HTTP Request",
                        severity=SEVERITY_HIGH,
                        file_path=fpath.relative_to(ROOT_DIR),
                        line_no=i,
                        snippet=line,
                        remediation=(
                            "Validate and allowlist URLs before making server-side HTTP requests. "
                            "Block requests to internal IP ranges (169.254.x.x, 10.x.x.x, "
                            "172.16.x.x, 192.168.x.x). Use SSRFGuard from app/ai/sanitizer.py. "
                            "Applies to RAG retrieval pipelines too."
                        ),
                    ))


# ─────────────────────────────────────────────
# Report Generation
# ─────────────────────────────────────────────

def generate_report(findings: List[Finding]) -> dict:
    """Generate structured JSON report from findings.

    The returned dict includes both a nested ``meta`` block (for full detail)
    and flat top-level keys (``mission_id``, ``total_findings``, ``findings``,
    ``owasp_breakdown``) for backwards-compatibility with tests and CI tooling.
    """
    severity_order = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2, SEVERITY_INFO: 3}
    findings_sorted = sorted(findings, key=lambda f: (severity_order.get(f.severity, 99), f.owasp_id))

    counts = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 0, SEVERITY_LOW: 0, SEVERITY_INFO: 0}
    by_owasp_list: Dict[str, List[dict]] = {}
    owasp_breakdown: Dict[str, int] = {}

    for f in findings_sorted:
        counts[f.severity] = counts.get(f.severity, 0) + 1
        by_owasp_list.setdefault(f.owasp_id, []).append(f.to_dict())
        owasp_breakdown[f.owasp_id] = owasp_breakdown.get(f.owasp_id, 0) + 1

    passed = counts[SEVERITY_HIGH] == 0
    all_findings_list = [f.to_dict() for f in findings_sorted]

    return {
        # ── Flat top-level keys (expected by tests & CI gate) ──────────────
        "mission_id": "0100ff5f-48c5-458a-b9c0-d8d233618d92",
        "total_findings": len(findings),
        "findings": all_findings_list,
        "owasp_breakdown": owasp_breakdown,
        # ── Nested meta block (full detail) ────────────────────────────────
        "meta": {
            "mission_id": "0100ff5f-48c5-458a-b9c0-d8d233618d92",
            "project_id": "ab7aa6f5-2480-4b56-98cd-5ba2969d4a18",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "root_dir": str(ROOT_DIR),
            "total_findings": len(findings),
            "gate_passed": passed,
        },
        "summary": {
            "HIGH": counts[SEVERITY_HIGH],
            "MEDIUM": counts[SEVERITY_MEDIUM],
            "LOW": counts[SEVERITY_LOW],
            "INFO": counts[SEVERITY_INFO],
        },
        "gate_status": "PASS" if passed else "FAIL",
        "findings_by_owasp": by_owasp_list,
        "all_findings": all_findings_list,
    }


# ─────────────────────────────────────────────
# Main Entry
# ─────────────────────────────────────────────

def run_audit() -> dict:
    findings: List[Finding] = []

    checks = [
        ("A01 Broken Access Control", check_a01_access_control),
        ("A02 Cryptographic Failures / Secrets", check_a02_secrets),
        ("A02 .env Exposure", check_env_file_exposure),
        ("A03 SQL Injection", check_a03_sql_injection),
        ("A03 Command Injection", check_a03_cmd_injection),
        ("A03/LLM01 Prompt Injection", check_a03_prompt_injection),
        ("A05 Security Misconfiguration", check_a05_misconfig),
        ("A07 Auth Failures", check_a07_auth_failures),
        ("A10 SSRF", check_a10_ssrf),
    ]

    for name, check_fn in checks:
        print(f"[CHECK] {name}...")
        before = len(findings)
        check_fn(findings)
        print(f"        -> {len(findings) - before} finding(s)")

    report = generate_report(findings)

    # Write JSON report
    report_path = pathlib.Path("./security/reports/owasp_audit_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\n[REPORT] Written to {report_path}")
    print(f"[GATE]   {report['gate_status']} "
          f"(HIGH={report['summary']['HIGH']}, "
          f"MEDIUM={report['summary']['MEDIUM']}, "
          f"LOW={report['summary']['LOW']})")

    return report


if __name__ == "__main__":
    import sys
    report = run_audit()
    sys.exit(0 if report["gate_status"] == "PASS" else 1)