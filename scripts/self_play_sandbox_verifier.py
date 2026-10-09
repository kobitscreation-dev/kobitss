"""
Kobits Self-Play Sandbox Verifier (The DeepSeek-R1 Style Training Engine)
Runs code generation against isolated sandboxes, executes Pytest,
and saves ONLY 100% verified test-passing trajectories ($0.00 Cost).
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional
import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "training_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TARGET_JSONL = OUTPUT_DIR / "kobits_selfplay_verified.jsonl"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
MODEL_NAME = os.environ.get("KOBITS_MODEL", "kobits-coder")

# Benchmark verification tasks with ground-truth test assertions
VERIFICATION_TASKS = [
    {
        "id": "task_hmac_stripe",
        "title": "Stripe Webhook HMAC Verification",
        "prompt": "Write a pure Python function `verify_stripe_signature(payload: bytes, signature_header: str, secret: str, tolerance: int = 300) -> bool` that verifies Stripe webhook signatures, rejects expired timestamps, and uses hmac.compare_digest for constant-time comparison.",
        "test_code": """
import time, hmac, hashlib
def test_valid_signature():
    payload = b'{"id": "evt_test"}'
    secret = "whsec_test_secret"
    t = int(time.time())
    signed_payload = f"{t}.".encode() + payload
    v1_sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    header = f"t={t},v1={v1_sig}"
    assert verify_stripe_signature(payload, header, secret) is True

def test_tampered_payload():
    payload = b'{"id": "evt_test"}'
    secret = "whsec_test_secret"
    t = int(time.time())
    header = f"t={t},v1=fake_signature"
    assert verify_stripe_signature(payload, header, secret) is False

def test_expired_timestamp():
    payload = b'{"id": "evt_test"}'
    secret = "whsec_test_secret"
    t = int(time.time()) - 500  # Older than tolerance
    signed_payload = f"{t}.".encode() + payload
    v1_sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    header = f"t={t},v1={v1_sig}"
    assert verify_stripe_signature(payload, header, secret, tolerance=300) is False
"""
    },
    {
        "id": "task_sliding_rate_limiter",
        "title": "In-Memory Sliding Window Rate Limiter",
        "prompt": "Write a thread-safe Python class `SlidingWindowRateLimiter(max_requests: int, window_seconds: float)` with a method `allow_request(client_id: str) -> bool` using collections.deque and threading.Lock to implement an exact sliding window log.",
        "test_code": """
import time
def test_rate_limiter():
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=0.1)
    assert limiter.allow_request("user1") is True
    assert limiter.allow_request("user1") is True
    assert limiter.allow_request("user1") is False  # Limit reached
    assert limiter.allow_request("user2") is True  # Different user
    time.sleep(0.12)
    assert limiter.allow_request("user1") is True  # Window expired
"""
    },
    {
        "id": "task_retry_with_backoff",
        "title": "Exponential Backoff Decorator",
        "prompt": "Write a Python decorator `@retry_with_backoff(max_retries=3, base_delay=0.01, backoff_factor=2)` that catches specified exceptions, retries the function call with exponential delays, and raises the final exception if retries are exhausted.",
        "test_code": """
def test_retry_success():
    attempts = 0
    @retry_with_backoff(max_retries=3, base_delay=0.01)
    def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("Temp failure")
        return "success"
    assert flaky() == "success"
    assert attempts == 3

def test_retry_exhausted():
    @retry_with_backoff(max_retries=2, base_delay=0.01)
    def always_fail():
        raise RuntimeError("Fatal")
    try:
        always_fail()
        assert False, "Should have raised RuntimeError"
    except RuntimeError:
        pass
"""
    }
]


def run_test_in_sandbox(code: str, test_code: str) -> tuple[bool, str]:
    """Writes code + test to a temp sandbox and executes pytest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        test_file = tmp_path / "test_verification.py"
        test_file.write_text(f"{code}\n\n{test_code}", encoding="utf-8")

        res = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-q"],
            capture_output=True,
            text=True,
            timeout=15
        )
        passed = (res.returncode == 0)
        output = res.stdout + "\n" + res.stderr
        return passed, output


async def generate_solution(prompt: str) -> str:
    """Calls Ollama or fallback local generator."""
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={"model": MODEL_NAME, "prompt": prompt, "stream": False}
            )
            if resp.status_code == 200:
                return resp.json().get("response", "")
    except Exception:
        pass
    return ""


async def verify_and_harvest():
    print("=================================================================", flush=True)
    print("🔬 Kobits Self-Play Sandbox Verifier (DeepSeek-R1 Method)", flush=True)
    print("   Executes Pytest in Isolated Sandbox ($0.00 Cost)", flush=True)
    print("=================================================================", flush=True)

    verified_count = 0
    for task in VERIFICATION_TASKS:
        print(f"\n[*] Testing Task: {task['title']}...", flush=True)

        # For demonstration or local run, generate and verify
        # Code generation prompt
        agent_prompt = f"Write clean, type-safe Python code with zero placeholders.\nTask: {task['prompt']}"
        solution_code = await generate_solution(agent_prompt)

        if not solution_code:
            print("  [Note] Ollama model not currently running; skipping live generation.", flush=True)
            continue

        passed, logs = run_test_in_sandbox(solution_code, task["test_code"])
        if passed:
            print("  ✓ Pytest Passed 100%! Saving verified trajectory...", flush=True)
            record = {
                "messages": [
                    {"role": "user", "content": task["prompt"]},
                    {"role": "assistant", "content": solution_code}
                ],
                "metadata": {
                    "source": "self_play_sandbox_verified",
                    "task_id": task["id"],
                    "verification": "pytest_passed",
                    "cost_usd": 0.0
                }
            }
            with open(TARGET_JSONL, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            verified_count += 1
        else:
            print(f"  ✗ Pytest Failed. Traceback:\n{logs[:200]}...", flush=True)

    print("\n=================================================================", flush=True)
    print(f"🎉 Verification Batch Complete! Saved {verified_count} verified trajectories.", flush=True)
    print("=================================================================\n", flush=True)


if __name__ == "__main__":
    asyncio.run(verify_and_harvest())
