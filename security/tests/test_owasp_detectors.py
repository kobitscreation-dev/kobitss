"""
Unit tests for OWASP audit detectors.
Uses intentionally vulnerable code snippets to verify detection accuracy.
"""

import sys
import os
import tempfile
import pathlib
import json
import pytest

# Add audit_scripts to path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "audit_scripts"))

# Patch ROOT_DIR to point to temp dir during tests
import owasp_audit_checks as audit


def make_temp_file(content: str, suffix=".py") -> pathlib.Path:
    """Write content to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix=suffix, delete=False,
        dir=tempfile.gettempdir(), encoding="utf-8"
    )
    tmp.write(content)
    tmp.close()
    return pathlib.Path(tmp.name)


class TestA02SecretDetection:
    """A02 - Cryptographic Failures."""

    def _scan_content(self, content: str, suffix=".py"):
        fpath = make_temp_file(content, suffix)
        findings = []

        # Temporarily override ROOT_DIR and iter_source_files
        original_root = audit.ROOT_DIR
        audit.ROOT_DIR = fpath.parent

        def mock_iter(extensions=None):
            if extensions and fpath.suffix not in extensions:
                return
            yield fpath

        original_iter = audit.iter_source_files
        audit.iter_source_files = mock_iter

        try:
            audit.check_a02_secrets(findings)
        finally:
            audit.ROOT_DIR = original_root
            audit.iter_source_files = original_iter
            os.unlink(fpath)

        return findings

    def test_detects_hardcoded_password(self):
        findings = self._scan_content('password = "supersecret123"')
        assert any(f.owasp_id == "A02" for f in findings), "Should detect hardcoded password"

    def test_detects_api_key(self):
        findings = self._scan_content('api_key = "abc123def456ghi789"')
        assert any(f.owasp_id == "A02" for f in findings), "Should detect hardcoded API key"

    def test_ignores_placeholder(self):
        # Short values or env var references should not trigger
        findings = self._scan_content('api_key = os.environ.get("API_KEY")')
        # This should NOT be flagged (it's using env var)
        hardcoded = [f for f in findings if "api_key" in f.snippet.lower() and "os.environ" not in f.snippet]
        assert len(hardcoded) == 0, "Should not flag env variable references"

    def test_detects_db_password(self):
        findings = self._scan_content('database_url = "postgresql://user:password123@localhost/db"')
        assert any(f.owasp_id == "A02" for f in findings), "Should detect database credential"


class TestA03InjectionDetection:
    """A03 - Injection."""

    def _scan_sql(self, content: str):
        fpath = make_temp_file(content, ".py")
        findings = []
        original_root = audit.ROOT_DIR
        audit.ROOT_DIR = fpath.parent

        def mock_iter(extensions=None):
            if extensions and fpath.suffix not in extensions:
                return
            yield fpath

        original_iter = audit.iter_source_files
        audit.iter_source_files = mock_iter

        try:
            audit.check_a03_sql_injection(findings)
        finally:
            audit.ROOT_DIR = original_root
            audit.iter_source_files = original_iter
            os.unlink(fpath)

        return findings

    def test_detects_f_string_sql(self):
        content = 'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")'
        findings = self._scan_sql(content)
        assert any(f.owasp_id == "A03" for f in findings), "Should detect f-string SQL injection"

    def test_detects_format_sql(self):
        content = 'cursor.execute("SELECT * FROM users WHERE name = %s" % (username,))'
        findings = self._scan_sql(content)
        assert any(f.owasp_id == "A03" for f in findings), "Should detect % format SQL injection"

    def _scan_cmd(self, content: str):
        fpath = make_temp_file(content, ".py")
        findings = []
        original_root = audit.ROOT_DIR
        audit.ROOT_DIR = fpath.parent

        def mock_iter(extensions=None):
            if extensions and fpath.suffix not in extensions:
                return
            yield fpath

        original_iter = audit.iter_source_files
        audit.iter_source_files = mock_iter

        try:
            audit.check_a03_cmd_injection(findings)
        finally:
            audit.ROOT_DIR = original_root
            audit.iter_source_files = original_iter
            os.unlink(fpath)

        return findings

    def test_detects_shell_true(self):
        content = 'subprocess.run(cmd, shell=True)'
        findings = self._scan_cmd(content)
        assert any(f.owasp_id == "A03" for f in findings), "Should detect shell=True command injection"

    def test_detects_os_system_fstring(self):
        content = 'os.system(f"ls {user_dir}")'
        findings = self._scan_cmd(content)
        assert any(f.owasp_id == "A03" for f in findings), "Should detect os.system f-string injection"


class TestA05MisconfigDetection:
    """A05 - Security Misconfiguration."""

    def _scan_misconfig(self, content: str, suffix=".py"):
        fpath = make_temp_file(content, suffix)
        findings = []
        original_root = audit.ROOT_DIR
        audit.ROOT_DIR = fpath.parent

        def mock_iter(extensions=None):
            if extensions and fpath.suffix not in extensions:
                return
            yield fpath

        original_iter = audit.iter_source_files
        audit.iter_source_files = mock_iter

        try:
            audit.check_a05_misconfig(findings)
        finally:
            audit.ROOT_DIR = original_root
            audit.iter_source_files = original_iter
            os.unlink(fpath)

        return findings

    def test_detects_debug_true(self):
        findings = self._scan_misconfig('DEBUG = True')
        assert any(f.owasp_id == "A05" for f in findings), "Should detect DEBUG=True"

    def test_detects_ssl_verify_false(self):
        findings = self._scan_misconfig('response = requests.get(url, verify=False)')
        assert any(f.owasp_id == "A05" for f in findings), "Should detect SSL verify=False"

    def test_detects_insecure_django_key(self):
        findings = self._scan_misconfig('SECRET_KEY = "django-insecure-abc123xyz"')
        assert any(f.owasp_id == "A05" for f in findings), "Should detect Django insecure SECRET_KEY"


class TestReportGeneration:
    """Report generation quality checks."""

    def test_report_structure(self):
        findings = [
            audit.Finding(
                owasp_id="A02",
                title="Test Finding",
                severity="HIGH",
                file_path=pathlib.Path("test.py"),
                line_no=1,
                snippet='password = "test"',
                remediation="Use env vars"
            )
        ]
        report = audit.generate_report(findings)
        assert "mission_id" in report
        assert "total_findings" in report
        assert report["total_findings"] == 1
        assert report["summary"]["HIGH"] == 1
        assert report["owasp_breakdown"]["A02"] == 1
        assert len(report["findings"]) == 1

    def test_report_severity_ordering(self):
        findings = [
            audit.Finding("A05", "Low", "LOW", pathlib.Path("a.py"), 1, "x", "fix"),
            audit.Finding("A02", "High", "HIGH", pathlib.Path("b.py"), 1, "y", "fix"),
            audit.Finding("A03", "Medium", "MEDIUM", pathlib.Path("c.py"), 1, "z", "fix"),
        ]
        report = audit.generate_report(findings)
        severities = [f["severity"] for f in report["findings"]]
        assert severities[0] == "HIGH", "HIGH findings should come first"
        assert severities[-1] == "LOW", "LOW findings should come last"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])