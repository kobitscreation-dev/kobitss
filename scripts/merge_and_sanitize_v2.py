"""
Kobits Master Dataset Merger & Secret Sanitizer (v2.2)
Safely merges SWE-bench Verified and Modern UI Opus trajectories into
training_data/kobits_frontier_v2.jsonl with strict secret sanitization
to prevent GitHub push protection triggers.
"""

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "training_data"
MASTER_FILE = DATA_DIR / "kobits_frontier_v2.jsonl"
SWEBENCH_FILE = DATA_DIR / "kobits_swebench_verified.jsonl"
MODERN_UI_FILE = DATA_DIR / "kobits_modern_ui_opus.jsonl"
FABLE_FILE = DATA_DIR / "kobits_fable5_claude.jsonl"


def sanitize_text(text: str) -> str:
    # Mask any realistic mock API keys that could trigger GitHub secret scanning
    text = re.sub(r'sk_test_[a-zA-Z0-9]{20,}', 'sk_test_demo_placeholder_sanitized_token_00', text)
    text = re.sub(r'sk_live_[a-zA-Z0-9]{20,}', 'sk_live_demo_placeholder_sanitized_token_00', text)
    text = re.sub(r'sk-proj-[a-zA-Z0-9_-]{20,}', 'sk-proj-demo-placeholder-sanitized-key-00', text)
    text = re.sub(r'whsec_[a-zA-Z0-9]{20,}', 'whsec_demo_placeholder_sanitized_webhook_00', text)
    text = re.sub(r'ghp_[a-zA-Z0-9]{20,}', 'ghp_demo_placeholder_sanitized_pat_00', text)
    return text


def sanitize_record(record: dict) -> dict:
    for msg in record.get("messages", []):
        if "content" in msg:
            msg["content"] = sanitize_text(msg["content"])
    return record


def main():
    print("=================================================================", flush=True)
    print("🚀 Kobits Master Dataset Merger & Sanitizer (v2.2)", flush=True)
    print(f"   Master File  : {MASTER_FILE}", flush=True)
    print("=================================================================", flush=True)

    existing_samples = []
    seen_queries = set()

    if MASTER_FILE.exists():
        with open(MASTER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    rec = sanitize_record(rec)
                    existing_samples.append(rec)
                    for m in rec.get("messages", []):
                        if m.get("role") == "user":
                            seen_queries.add(m.get("content", "").strip()[:80])

    print(f"[*] Loaded {len(existing_samples)} existing samples from master.", flush=True)

    added_swebench = 0
    if SWEBENCH_FILE.exists():
        with open(SWEBENCH_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    q = ""
                    for m in rec.get("messages", []):
                        if m.get("role") == "user":
                            q = m.get("content", "").strip()[:80]
                    if q not in seen_queries:
                        existing_samples.append(sanitize_record(rec))
                        seen_queries.add(q)
                        added_swebench += 1

    print(f"[*] Added {added_swebench} verified SWE-bench problem instances.", flush=True)

    added_modern_ui = 0
    if MODERN_UI_FILE.exists():
        with open(MODERN_UI_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    q = ""
                    for m in rec.get("messages", []):
                        if m.get("role") == "user":
                            q = m.get("content", "").strip()[:80]
                    if q not in seen_queries:
                        existing_samples.append(sanitize_record(rec))
                        seen_queries.add(q)
                        added_modern_ui += 1

    print(f"[*] Added {added_modern_ui} Modern UI Opus design trajectories.", flush=True)

    added_fable = 0
    if FABLE_FILE.exists():
        with open(FABLE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    q = ""
                    for m in rec.get("messages", []):
                        if m.get("role") == "user":
                            q = m.get("content", "").strip()[:80]
                    if q not in seen_queries:
                        existing_samples.append(sanitize_record(rec))
                        seen_queries.add(q)
                        added_fable += 1

    print(f"[*] Added {added_fable} Claude Fable 5 agentic trajectories.", flush=True)

    # Write merged dataset
    total_chars = 0
    with open(MASTER_FILE, "w", encoding="utf-8") as f:
        for rec in existing_samples:
            line_str = json.dumps(rec)
            f.write(line_str + "\n")
            total_chars += len(line_str)

    est_tokens = total_chars // 4
    file_size_mb = MASTER_FILE.stat().st_size / (1024 * 1024)

    sources = {}
    for r in existing_samples:
        s = r.get("metadata", {}).get("source", "unknown")
        sources[s] = sources.get(s, 0) + 1

    print("\n=================================================================", flush=True)
    print("🎉 Dataset Scaling Complete!", flush=True)
    print(f"   Total Golden Samples   : {len(existing_samples):,}", flush=True)
    print(f"   Master File Size       : {file_size_mb:.2f} MB", flush=True)
    print(f"   Estimated Tokens       : ~{est_tokens:,} tokens", flush=True)
    print(f"   Source Breakdown       : {sources}", flush=True)
    print("=================================================================\n", flush=True)


if __name__ == "__main__":
    main()
