import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = str(ROOT / "kobits_cli.py")


def test_cli_help():
    res = subprocess.run([sys.executable, CLI, "--help"], capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    assert res.returncode == 0
    assert "Kobits Native CLI" in res.stdout
    for cmd in ("run", "status", "missions", "projects", "approve", "reject", "cancel", "steer"):
        assert cmd in res.stdout


def test_cli_status_and_missions():
    res = subprocess.run([sys.executable, CLI, "status", "--local"], capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    assert res.returncode == 0
    assert "KOBITS ENGINEERING CONTROL CENTER" in res.stdout

    res_m = subprocess.run([sys.executable, CLI, "missions", "--local"], capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    assert res_m.returncode == 0
