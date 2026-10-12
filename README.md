# Kobits (`kobits`)

**Pure Local Multi-Agent Software Engineering Terminal Engine**

Kobits is a standalone, terminal-native multi-agent engineering engine designed around the **Safe Sandbox $\rightarrow$ Diff $\rightarrow$ Apply** workflow. It runs directly in your local repository with zero web server or cloud dashboard dependencies.

---

## Core Architecture

1. **Isolated Git Worktree Sandboxes (`SandboxManager`)**
   - Every mission creates an isolated Git worktree (`git worktree add --force -B <branch> sandboxes/sandbox-<id> HEAD`) or copy-on-write fallback sandbox.
   - Your active working directory is never modified until you explicitly run `kobits apply`.
   - Sandbox metadata (`.kobits_sandbox.json`) is persisted on disk and automatically rehydrated across process restarts.

2. **14-Stage Multi-Agent Pipeline & Scope Triage (`MissionRuntime`)**
   - **21 Specialized Engineering Agents** (`Nexus` Orchestrator, `Axiom` Solution Architect, `Core` Backend Engineer, `Sentinel` QA, `Aegis` Security, `Critique` Code Reviewer, etc.).
   - **Dynamic Scope Triage:** Automatically classifies missions (`MICRO`, `STANDARD`, `FULL`) to fast-track focused engineering changes while keeping full multi-agent rigor for complex architectures.
   - **Bounded Context & Pinned Artifacts:** Pins foundational architectural artifacts (`Repository Understanding`, `Planning & Architecture`, `Database Architecture`, `Task Decomposition`) while bounding upstream context to save ~350k tokens per mission.

3. **4-Stage Automated Pre-Review Gate (`_verify_sandbox_code`)**
   - Enforced deterministically at the end of `IMPLEMENTATION` before advancing to `VALIDATION` / `CODE_REVIEW`:
     1. **AST Parse Gate (`ast.parse`)**
     2. **Bytecode Compilation Gate (`compile()`)**
     3. **Local Module Import Resolution Gate (`ast.ImportFrom`)**
     4. **Automated Pytest Smoke Gate (`pytest -q --tb=short`)**
   - If any gate fails, `MissionRuntime` automatically blocks phase progression and spawns a targeted `"Pre-Review Gate Fix"` correction task with compiler/import/test diagnostics.

4. **Persistent ACID Mission State (`SQLite`)**
   - Every task and phase transition checkpoints `persistent_state` into local SQLite (`kobits.db`), allowing `kobits retry` to resume from the exact incomplete task after any interruption.

---

## Quick Start (Under 60 Seconds)

See [QUICKSTART.md](file:///C:/Users/Arvind%20Kumar/.gemini/antigravity/scratch/kobits/QUICKSTART.md) for the full guide.

### 1. Setup & Environment Doctor
Configure your LLM credentials (DeepSeek, Claude, Gemini, OpenAI) in 10 seconds:
```bash
kobits setup
# Or: kobits doctor
```

### 2. Run an Engineering Mission
```bash
# 1. Execute an objective in an isolated Git worktree sandbox
kobits "Add a rate limiter utility with unit tests"

# 2. Inspect the verified unified git diff
kobits diff

# 3. Apply verified changes to your live working tree
kobits apply
```

### 3. Interactive Terminal Pair Programming (REPL)
```bash
kobits repl
```

### 4. Launch Web Visual Dashboard
```bash
# Windows 1-Click
run_dashboard.bat

# Or terminal:
py -3.12 main.py
```
Open **`http://localhost:8000/portal.html`** to track agents, visually review sandboxes, and manage multi-project repositories.

### 5. Inspect 200k Training Flywheel
```bash
kobits dataset
```

---

## Verification Suite
```bash
py -3.12 -m pytest backend/tests/test_kyros_parity.py -v
```
