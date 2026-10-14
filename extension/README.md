# Kobits — Autonomous Multi-Agent Software Engineer for VS Code

Bring the full power of the Kobits 21-specialist autonomous engineering swarm directly into Visual Studio Code and Cursor.

---

## Capabilities

* **Autonomous Mission Dispatch**: Launch end-to-end features, bugfixes, or refactors from the command palette (`Ctrl+Shift+P` -> `Kobits: Autonomous Engineering Mission...`).
* **Inline Code Refactoring**: Right-click any highlighted code block and select `Kobits: Fix or Refactor Selected Code`. Kobits analyzes the context, reproduces regressions in a sandbox, and proposes atomic diffs.
* **Live Status Bar Telemetry**: Always know what phase the swarm is in (`[PLANNING]`, `[IMPLEMENTATION]`, `[VALIDATION]`, `[SECURITY]`) and your active workspace balance.
* **Side-by-Side Solution Diff**: Inspect clean, colorized sandbox diffs with one click before applying them to your workspace.
* **Secure Enterprise Key Vault**: Authenticates via high-entropy `kb_live_...` API keys stored inside VS Code's encrypted secret store (`context.secrets`).

---

## Setup & Authentication

1. Open your Kobits Web Dashboard -> **Settings** -> **API Keys & CLI**.
2. Click **Generate New Key** and copy your `kb_live_...` token.
3. In VS Code, open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) and run:
   ```
   Kobits: Set API Key
   ```
4. Paste your key. The status bar will immediately display `$(sparkle) Kobits: Ready` connected to your enterprise workspace.

---

## Configuration Settings

| Setting | Default | Description |
| :--- | :--- | :--- |
| `kobits.apiUrl` | `http://127.0.0.1:8000` | Kobits Backend server URL (or your cloud instance URL). |
| `kobits.autoStream` | `true` | Show progress notifications when autonomous missions execute. |

---

## Supported Commands

* `Kobits: Set API Key` (`kobits.login`)
* `Kobits: Autonomous Engineering Mission...` (`kobits.dispatchPrompt`)
* `Kobits: Fix or Refactor Selected Code` (`kobits.dispatchSelection`)
* `Kobits: View Sandbox Solution Diff` (`kobits.viewDiff`)
* `Kobits: Apply Validated Diff to Workspace` (`kobits.applyDiff`)
* `Kobits: Show Swarm Status` (`kobits.showStatus`)
