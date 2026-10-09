"""
Kobits Turbo Scale Harvester (100% Free Public Enterprise Data)
Extracts 3,000+ elite trajectories across:
1. Claude Fable 5 Premium Agent Traces (saidutta69/fable-5-premium)
2. Modern Theme UI, React 19, Next.js 15, Tailwind v4 & Full-Stack Systems
   (m-a-p/CodeFeedback-Filtered-Instruction)
Utilizes asynchronous streaming for ultra-fast throughput ($0.00 API Cost).
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
TURBO_FILE = OUTPUT_DIR / "kobits_turbo_harvest.jsonl"
FRONTIER_FILE = OUTPUT_DIR / "kobits_frontier_v2.jsonl"

FABLE_URL = "https://huggingface.co/datasets/saidutta69/fable-5-premium/resolve/main/agent_traces/train.jsonl"
CODEFEEDBACK_URL = "https://huggingface.co/datasets/m-a-p/CodeFeedback-Filtered-Instruction/resolve/main/CodeFeedback-Filtered-Instruction.jsonl"

UI_THEME_KEYWORDS = [
    "react", "next.js", "nextjs", "tailwind", "shadcn", "radix", "lucide",
    "theme", "dark mode", "dashboard", "component", "hook", "css", "flexbox",
    "grid", "responsive", "modal", "sidebar", "navbar", "card", "layout",
    "animation", "dialog", "drawer", "dropdown", "button", "table", "palette",
    "framer", "monaco", "terminal", "diff", "zustand", "tanstack", "zod"
]

SYS_PROMPT_FABLE = (
    "You are Kobits, an autonomous senior full-stack AI engineering agent specializing in "
    "modern UI/UX architecture, clean theme systems, strict type safety, robust backend pipelines, "
    "and complete production-grade implementations with zero placeholders."
)

SYS_PROMPT_UI = (
    "You are Kobits, an autonomous senior frontend & full-stack architect specializing in "
    "React 19, Next.js 15 App Router, Tailwind CSS v4, dark-mode design systems, and responsive "
    "component engineering with complete implementations and zero placeholders."
)


def load_existing_queries() -> Set[str]:
    queries = set()
    for filepath in [FRONTIER_FILE, TURBO_FILE]:
        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            d = json.loads(line)
                            for m in d.get("messages", []):
                                if m.get("role") == "user":
                                    queries.add(m.get("content", "").strip()[:80])
            except Exception:
                pass
    return queries


async def harvest_fable_bulk(client: httpx.AsyncClient, existing_queries: Set[str], limit: int = 1500) -> int:
    print("\n[1/2] 🎭 Streaming Claude Fable 5 Trajectories...", flush=True)
    collected = 0
    buffer = ""

    async with client.stream("GET", FABLE_URL, follow_redirects=True) as resp:
        if resp.status_code != 200:
            print(f"[Error] Fable stream HTTP {resp.status_code}", flush=True)
            return 0

        async for chunk in resp.aiter_text():
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.strip():
                    continue

                try:
                    row = json.loads(line)
                    qs = row.get("quality_scores", {})
                    if qs.get("overall", 0) < 0.70:
                        continue

                    user_msg = next((m.get("content", "") for m in row.get("messages", []) if m.get("role") == "user"), "")
                    asst_msg = next((m.get("content", "") for m in row.get("messages", []) if m.get("role") == "assistant"), "")

                    if len(user_msg) < 25 or len(asst_msg) < 300:
                        continue
                    if "// todo" in asst_msg.lower() or "# todo" in asst_msg.lower():
                        continue

                    q_key = user_msg.strip()[:80]
                    if q_key in existing_queries:
                        continue

                    record = {
                        "messages": [
                            {"role": "system", "content": SYS_PROMPT_FABLE},
                            {"role": "user", "content": user_msg.strip()},
                            {"role": "assistant", "content": asst_msg.strip()}
                        ],
                        "metadata": {
                            "source": "claude_fable_5_turbo",
                            "model": row.get("model", "claude-fable-5"),
                            "quality_score": qs.get("overall", 0.8),
                            "cost_usd": 0.0
                        }
                    }

                    with open(TURBO_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps(record) + "\n")

                    existing_queries.add(q_key)
                    collected += 1

                    if collected % 100 == 0 or collected <= 5:
                        print(f"  ✓ [Fable 5] {collected}/{limit}: {user_msg[:60]}...", flush=True)

                    if collected >= limit:
                        break
                except Exception:
                    continue

            if collected >= limit:
                break

    print(f"[*] Completed Fable 5 harvest: {collected} trajectories.", flush=True)
    return collected


async def harvest_modern_ui_bulk(client: httpx.AsyncClient, existing_queries: Set[str], limit: int = 1500) -> int:
    print("\n[2/2] 🎨 Streaming Modern Theme UI & Full-Stack Trajectories...", flush=True)
    collected = 0
    buffer = ""

    async with client.stream("GET", CODEFEEDBACK_URL, follow_redirects=True) as resp:
        if resp.status_code != 200:
            print(f"[Error] CodeFeedback stream HTTP {resp.status_code}", flush=True)
            return 0

        async for chunk in resp.aiter_text():
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.strip():
                    continue

                try:
                    row = json.loads(line)
                    query = row.get("query", "").strip()
                    answer = row.get("answer", "").strip()
                    lang = (row.get("lang") or "").lower()

                    if len(query) < 25 or len(answer) < 350:
                        continue
                    if "// todo" in answer.lower() or "# todo" in answer.lower():
                        continue

                    # Filter: Must match UI/theme keywords or be modern frontend
                    combined = (query + " " + answer).lower()
                    matches = sum(1 for k in UI_THEME_KEYWORDS if k in combined)
                    if matches < 2 and lang not in ["typescript", "javascript"]:
                        continue

                    q_key = query[:80]
                    if q_key in existing_queries:
                        continue

                    record = {
                        "messages": [
                            {"role": "system", "content": SYS_PROMPT_UI},
                            {"role": "user", "content": query},
                            {"role": "assistant", "content": answer}
                        ],
                        "metadata": {
                            "source": "modern_theme_ui_turbo",
                            "lang": lang,
                            "cost_usd": 0.0
                        }
                    }

                    with open(TURBO_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps(record) + "\n")

                    existing_queries.add(q_key)
                    collected += 1

                    if collected % 100 == 0 or collected <= 5:
                        print(f"  ✓ [Modern UI] {collected}/{limit}: {query[:60]}...", flush=True)

                    if collected >= limit:
                        break
                except Exception:
                    continue

            if collected >= limit:
                break

    print(f"[*] Completed Modern UI harvest: {collected} trajectories.", flush=True)
    return collected


async def main():
    print("=================================================================", flush=True)
    print("⚡ Kobits Turbo Scale Harvester (Claude Fable 5 + Modern UI)", flush=True)
    print(f"   Output File: {TURBO_FILE}", flush=True)
    print("=================================================================", flush=True)

    existing = load_existing_queries()
    print(f"[*] Loaded {len(existing)} existing queries in master to prevent duplication.", flush=True)

    t0 = time.time()
    async with httpx.AsyncClient(timeout=180) as client:
        c1 = await harvest_fable_bulk(client, existing, limit=1500)
        c2 = await harvest_modern_ui_bulk(client, existing, limit=1500)

    elapsed = time.time() - t0
    total = c1 + c2

    print("\n=================================================================", flush=True)
    print(f"🎉 Turbo Harvest Complete in {elapsed:.1f}s!", flush=True)
    print(f"   Total Fresh Trajectories: {total:,}", flush=True)
    print(f"   Claude Fable 5 Traces    : {c1:,}", flush=True)
    print(f"   Modern Theme UI & Web    : {c2:,}", flush=True)
    print(f"   API Cost                 : $0.00 FREE", flush=True)
    print(f"   File Saved               : {TURBO_FILE}", flush=True)
    print("=================================================================\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
