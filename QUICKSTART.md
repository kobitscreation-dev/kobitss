# Kobits Quickstart Guide 🚀

**Autonomous Multi-Agent Software Engineering Engine**  
Run full-lifecycle engineering missions in an isolated sandbox, verify code with tests & AST gates, and review diffs before applying changes to your repository.

---

## ⚡ 30-Second Setup

### 1. Run Setup & Diagnostics
Configure your environment and choose your preferred LLM provider in 10 seconds:
```bash
kobits setup
# Or: py -3.12 kobits_cli.py setup
```
Supported providers:
- **DeepSeek** (Recommended: High-reasoning, 80% cheaper)
- **Anthropic Claude** (Claude 3.5 Sonnet / Opus)
- **Google Gemini** (Gemini 1.5 Pro / Flash)
- **OpenAI** (GPT-4o)
- **AWS Bedrock**

---

## 🛠️ Usage Mode 1: Terminal Native CLI

Kobits works directly inside any repository without needing a web browser or cloud dashboard.

### Run an Autonomous Mission
```bash
# In your project folder:
kobits "Add a sliding window rate limiter in backend/limiter.py with pytest tests"
```
**What happens:**
1. Kobits creates an isolated Git worktree (`sandboxes/sandbox-<id>`).
2. Axiom (Solution Architect) designs the specification.
3. Backend Engineer writes the code into the sandbox.
4. Sentinel QA runs unit tests and isolates errors.
5. Aegis Security performs static and AST vulnerability scans.
6. Your working directory stays 100% untouched until you approve!

### Inspect the Sandbox Diff
```bash
kobits diff
```
View clean unified diffs of all created and modified files.

### Apply Verified Changes
```bash
kobits apply
```
Merges the verified code from the sandbox directly into your local workspace.

### Interactive Terminal Chat (REPL)
```bash
kobits repl
```
Starts an interactive terminal session (like Claude Code / Kyros) to pair-program, explore code, and run missions.

---

## 🖥️ Usage Mode 2: Web Dashboard

Launch the full engineering workspace with real-time visual phase pipelines:

```bash
# Option A: Windows 1-Click
run_dashboard.bat

# Option B: Terminal
py -3.12 main.py
```
Open **`http://localhost:8000/portal.html`** in your browser.

- **Overview**: Track active missions, agent throughput, and health scores.
- **Missions**: Monitor agents collaborating across 11 lifecycle phases.
- **Sandbox Review**: Inspect side-by-side file diffs and apply changes.
- **Approvals**: Review high-risk security gates and architectural decisions.

---

## 🔄 The Post-Training Flywheel

Every completed mission that passes test and AST verification is automatically captured and added to your proprietary training dataset:

```bash
kobits dataset
```
Shows total harvested golden trajectories, token count, and exported dataset paths for fine-tuning custom 200k context models.

---

## 📋 Command Cheat Sheet

| Command | Description |
| :--- | :--- |
| `kobits setup` | Interactive environment diagnostic and API key configuration |
| `kobits "<objective>"` | Launch an autonomous engineering mission |
| `kobits repl` | Interactive multi-turn terminal pair programming |
| `kobits status` | Show current workspace control center and active missions |
| `kobits diff` | Inspect sandbox code modifications and git diff |
| `kobits apply` | Merge verified sandbox changes into your project |
| `kobits dataset` | View harvested golden trajectories and training stats |
| `kobits missions` | List historical and active missions |
| `kobits agents` | View all 21 specialized engineering agents and stats |
