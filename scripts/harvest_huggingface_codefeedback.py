"""
Kobits Hugging Face Enterprise Harvester (100% Free Public Datasets)
Streams and filters high-quality coding & debugging trajectories
from m-a-p/CodeFeedback-Filtered-Instruction on Hugging Face ($0.00 Cost).
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, List, Set
import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "training_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TARGET_JSONL = OUTPUT_DIR / "kobits_huggingface_enterprise.jsonl"
FRONTIER_JSONL = OUTPUT_DIR / "kobits_frontier_v2.jsonl"

DATASET_URL = "https://huggingface.co/datasets/m-a-p/CodeFeedback-Filtered-Instruction/resolve/main/CodeFeedback-Filtered-Instruction.jsonl"

HIGH_VALUE_TOPICS = [
    "fastapi", "async", "redis", "database", "sqlalchemy", "jwt",
    "authentication", "concurrency", "lock", "thread", "stream",
    "webhook", "rate limit", "cache", "token", "pytest", "test",
    "pydantic", "docker", "postgres", "sql", "api", "security",
    "exception", "error", "refactor", "bug", "fix", "deadlock",
    "pipeline", "orchestration", "worker", "queue", "schema",
    "migration", "service", "model", "architecture", "automation"
]


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
                                queries.add(m.get("content", "").strip())
        except Exception:
            pass
    return queries


def is_high_value_enterprise(query: str, answer: str) -> bool:
    query_lower = query.lower()
    answer_lower = answer.lower()

    if len(query) < 40 or len(answer) < 300:
        return False
    if "todo" in answer_lower and ("// todo" in answer_lower or "# todo" in answer_lower):
        return False

    matches = sum(1 for t in HIGH_VALUE_TOPICS if t in query_lower or t in answer_lower)
    return matches >= 2


def format_kobits_trajectory(query: str, answer: str, lang: str) -> Dict[str, Any]:
    sys_prompt = (
        "You are Kobits, an autonomous senior full-stack AI engineering agent. "
        "You design and implement enterprise production-grade software with explicit architectural decomposition, "
        "strict type safety, comprehensive error handling, and complete implementations with zero placeholders."
    )

    return {
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": query.strip()},
            {"role": "assistant", "content": answer.strip()}
        ],
        "metadata": {
            "source": "huggingface_codefeedback_enterprise",
            "lang": lang,
            "cost_usd": 0.0
        }
    }


async def harvest_huggingface(limit: int = 800):
    print("=================================================================", flush=True)
    print("🤗 Kobits Hugging Face Enterprise Harvester ($0.00 Cost)", flush=True)
    print(f"   Source : m-a-p/CodeFeedback-Filtered-Instruction", flush=True)
    print(f"   Target : {TARGET_JSONL}", flush=True)
    print(f"   Limit  : {limit} fresh enterprise trajectories", flush=True)
    print("=================================================================", flush=True)

    existing = load_existing_queries()
    print(f"[*] Loaded {len(existing)} existing queries to prevent duplicates.", flush=True)

    collected = 0
    buffer = ""

    async with httpx.AsyncClient(timeout=60) as client:
        print("[*] Streaming dataset from Hugging Face...", flush=True)
        async with client.stream("GET", DATASET_URL, follow_redirects=True) as resp:
            if resp.status_code != 200:
                print(f"[Error] Failed to connect: HTTP {resp.status_code}", flush=True)
                return

            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue

                    try:
                        data = json.loads(line)
                        lang = (data.get("lang") or "").lower()
                        if lang not in ["python", "typescript", "javascript", "sql"]:
                            continue

                        query = data.get("query", "").strip()
                        answer = data.get("answer", "")

                        if query in existing:
                            continue

                        if is_high_value_enterprise(query, answer):
                            record = format_kobits_trajectory(query, answer, lang)
                            with open(TARGET_JSONL, "a", encoding="utf-8") as out_f:
                                out_f.write(json.dumps(record) + "\n")

                            existing.add(query)
                            collected += 1
                            if collected % 50 == 0 or collected <= 5:
                                print(f"  ✓ [{collected}/{limit}] Harvested: {query[:60]}...", flush=True)

                            if collected >= limit:
                                break
                    except Exception:
                        continue

                if collected >= limit:
                    break

    print("\n=================================================================", flush=True)
    print(f"🎉 Hugging Face Harvest Complete!", flush=True)
    print(f"   Total Fresh Harvested : {collected} elite trajectories", flush=True)
    print(f"   Total Cost            : $0.00 FREE", flush=True)
    print("=================================================================\n", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=800, help="Number of trajectories to extract")
    args = parser.parse_args()
    asyncio.run(harvest_huggingface(limit=args.limit))
