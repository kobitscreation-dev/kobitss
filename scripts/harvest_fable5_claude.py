"""
Kobits Claude Fable 5 / Sonnet / Opus Harvester (100% Free Public Data)
Extracts elite coding, modern theme UI, and software engineering agent trajectories
from saidutta69/fable-5-premium on Hugging Face ($0.00 API Cost).
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Set
import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "training_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TARGET_JSONL = OUTPUT_DIR / "kobits_fable5_claude.jsonl"
FRONTIER_JSONL = OUTPUT_DIR / "kobits_frontier_v2.jsonl"

FABLE_DATASET_URL = "https://huggingface.co/datasets/saidutta69/fable-5-premium/resolve/main/agent_traces/train.jsonl"

UI_AND_ENG_KEYWORDS = [
    # Modern Theme UI & Frontend
    "react", "next.js", "nextjs", "tailwind", "shadcn", "component", "hook",
    "theme", "dark mode", "dashboard", "css", "flexbox", "grid", "responsive",
    "modal", "sidebar", "navbar", "frontend", "ui", "ux", "design", "card",
    "table", "button", "layout", "animation", "palette", "dialog", "drawer",
    # Backend & Engineering
    "python", "fastapi", "async", "database", "sqlalchemy", "sql", "redis",
    "api", "docker", "test", "pytest", "git", "diff", "patch", "refactor",
    "bug", "fix", "concurrency", "security", "token", "auth", "cache",
    "pipeline", "worker", "architecture", "algorithm", "data structure"
]

SYS_PROMPT = (
    "You are Kobits, an autonomous senior full-stack AI engineering agent specializing in "
    "modern UI/UX architecture, clean theme systems, strict type safety, robust backend pipelines, "
    "and complete production-grade implementations with zero placeholders."
)


def load_existing_queries() -> Set[str]:
    queries = set()
    if FRONTIER_JSONL.exists():
        try:
            with open(FRONTIER_JSONL, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        for m in data.get("messages", []):
                            if m.get("role") == "user":
                                queries.add(m.get("content", "").strip()[:80])
        except Exception:
            pass
    return queries


def is_high_value_fable(user_msg: str, asst_msg: str) -> bool:
    if len(user_msg) < 30 or len(asst_msg) < 350:
        return False
    if "// todo" in asst_msg.lower() or "# todo" in asst_msg.lower():
        return False

    combined = (user_msg + " " + asst_msg).lower()
    matches = sum(1 for k in UI_AND_ENG_KEYWORDS if k in combined)
    return matches >= 2


def format_fable_trajectory(row: Dict[str, Any], user_msg: str, asst_msg: str) -> Dict[str, Any]:
    qs = row.get("quality_scores", {})
    return {
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": user_msg.strip()},
            {"role": "assistant", "content": asst_msg.strip()}
        ],
        "metadata": {
            "source": "claude_fable_5_premium",
            "model": row.get("model", "claude-fable-5"),
            "quality_score": qs.get("overall", 0.85),
            "cost_usd": 0.0
        }
    }


async def harvest_fable(limit: int = 500):
    print("=================================================================", flush=True)
    print("🎭 Kobits Claude Fable 5 & Modern Theme UI Harvester ($0.00 Cost)", flush=True)
    print(f"   Source : saidutta69/fable-5-premium (Claude-Fable-5 Traces)", flush=True)
    print(f"   Target : {TARGET_JSONL}", flush=True)
    print(f"   Target Count : {limit} trajectories", flush=True)
    print("=================================================================", flush=True)

    existing = load_existing_queries()
    print(f"[*] Loaded {len(existing)} existing queries to prevent duplication.", flush=True)

    collected = 0
    scanned = 0
    ui_count = 0
    buffer = ""

    async with httpx.AsyncClient(timeout=120) as client:
        print("[*] Streaming Fable 5 dataset from Hugging Face...", flush=True)
        async with client.stream("GET", FABLE_DATASET_URL, follow_redirects=True) as resp:
            if resp.status_code != 200:
                print(f"[Error] Failed to connect: HTTP {resp.status_code}", flush=True)
                return

            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue

                    scanned += 1
                    try:
                        row = json.loads(line)
                        qs = row.get("quality_scores", {})
                        if qs.get("overall", 0) < 0.75:
                            continue

                        user_msg = next((m.get("content", "") for m in row.get("messages", []) if m.get("role") == "user"), "")
                        asst_msg = next((m.get("content", "") for m in row.get("messages", []) if m.get("role") == "assistant"), "")

                        q_key = user_msg.strip()[:80]
                        if q_key in existing:
                            continue

                        if is_high_value_fable(user_msg, asst_msg):
                            record = format_fable_trajectory(row, user_msg, asst_msg)
                            with open(TARGET_JSONL, "a", encoding="utf-8") as out_f:
                                out_f.write(json.dumps(record) + "\n")

                            existing.add(q_key)
                            collected += 1

                            is_ui = any(k in (user_msg + asst_msg).lower() for k in ["react", "tailwind", "ui", "css", "theme", "component", "dashboard"])
                            if is_ui:
                                ui_count += 1

                            if collected % 50 == 0 or collected <= 5:
                                print(f"  ✓ [{collected}/{limit}] ({ui_count} UI/Theme) {user_msg[:60]}...", flush=True)

                            if collected >= limit:
                                break
                    except Exception:
                        continue

                if collected >= limit:
                    break

    print("\n=================================================================", flush=True)
    print(f"🎉 Claude Fable 5 Harvest Complete!", flush=True)
    print(f"   Total Harvested     : {collected} trajectories", flush=True)
    print(f"   Modern Theme UI/UX  : {ui_count} trajectories", flush=True)
    print(f"   Total API Cost      : $0.00 FREE", flush=True)
    print(f"   Saved to            : {TARGET_JSONL}", flush=True)
    print("=================================================================\n", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500, help="Number of trajectories to extract")
    args = parser.parse_args()
    asyncio.run(harvest_fable(limit=args.limit))
