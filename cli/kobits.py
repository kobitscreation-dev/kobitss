#!/usr/bin/env python3
"""
Kobits Developer CLI.

Enterprise command-line interface for autonomous software engineering:
  kobits login           Authenticate CLI with a cryptographic API key
  kobits whoami          Inspect current identity, workspace, and balance
  kobits init            Initialize project repository configuration
  kobits run             Launch an autonomous mission with live terminal streaming
  kobits status          Check real-time mission status and execution phase
  kobits diff            Inspect colorized sandbox diffs
  kobits apply           Apply validated candidate changes to local working tree
  kobits keys            Manage cryptographic API keys
  kobits logout          Clear local credentials
"""

import sys
import os
import json
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

import click
import httpx

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.syntax import Syntax
    RICH_AVAILABLE = True
    console = Console()
    err_console = Console(stderr=True)
except ImportError:
    RICH_AVAILABLE = False
    console = None
    err_console = None


CONFIG_DIR = Path.home() / ".kobits"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_API_URL = "http://127.0.0.1:8000"


def print_msg(msg: str, style: str = "white"):
    if RICH_AVAILABLE and console:
        console.print(msg, style=style)
    else:
        print(msg)


def print_err(msg: str):
    if RICH_AVAILABLE and err_console:
        err_console.print(f"[bold red]Error:[/bold red] {msg}")
    else:
        sys.stderr.write(f"Error: {msg}\n")


def print_success(msg: str):
    if RICH_AVAILABLE and console:
        console.print(f"[bold green]✓[/bold green] {msg}")
    else:
        print(f"✓ {msg}")


def load_config() -> Dict[str, Any]:
    """Load CLI configuration from ~/.kobits/config.json."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(config: Dict[str, Any]):
    """Persist CLI configuration to ~/.kobits/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def resolve_auth_context(api_key: Optional[str] = None, url: Optional[str] = None) -> tuple[str, str]:
    """
    Resolve API Key and Base URL with precedence:
    1. CLI flags (--api-key, --url)
    2. Environment variables (KOBITS_API_KEY, KOBITS_API_URL)
    3. Config file (~/.kobits/config.json)
    4. Defaults
    """
    cfg = load_config()
    final_key = api_key or os.environ.get("KOBITS_API_KEY") or cfg.get("api_key") or ""
    final_url = url or os.environ.get("KOBITS_API_URL") or cfg.get("api_url") or DEFAULT_API_URL
    return final_key.strip(), final_url.strip().rstrip("/")


def get_client(api_key: str, base_url: str) -> httpx.Client:
    """Create an HTTP client configured with API key headers."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "Kobits-CLI/1.0.0",
    }
    if api_key:
        headers["X-API-Key"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
    return httpx.Client(base_url=base_url, headers=headers, timeout=20.0)


# ═════════════════════════════════════════════════════════════════════
#  CLICK CLI ROOT
# ═════════════════════════════════════════════════════════════════════

@click.group()
@click.option("--api-key", envvar="KOBITS_API_KEY", help="Cryptographic API Key (kb_live_...)")
@click.option("--url", envvar="KOBITS_API_URL", help="Kobits API Base URL")
@click.option("--format", "output_format", type=click.Choice(["pretty", "json"]), default="pretty", help="Output format")
@click.pass_context
def cli(ctx, api_key: Optional[str], url: Optional[str], output_format: str):
    """Kobits Developer CLI — Autonomous Multi-Agent Engineering Platform."""
    ctx.ensure_object(dict)
    resolved_key, resolved_url = resolve_auth_context(api_key, url)
    ctx.obj["api_key"] = resolved_key
    ctx.obj["url"] = resolved_url
    ctx.obj["output_format"] = output_format


# ── LOGIN ─────────────────────────────────────────────────────────────
@cli.command()
@click.option("--api-key", prompt=False, help="API Key (starts with kb_live_)")
@click.option("--url", help="Kobits Server URL")
@click.pass_context
def login(ctx, api_key: Optional[str], url: Optional[str]):
    """Authenticate the CLI with a cryptographic API key."""
    target_url = url or ctx.obj["url"] or DEFAULT_API_URL
    target_key = api_key or ctx.obj["api_key"]

    if not target_key:
        if RICH_AVAILABLE and console:
            console.print("[bold cyan]Kobits Developer Login[/bold cyan]")
            console.print("Generate an API key in your workspace dashboard or via 'kobits keys create'.\n")
        target_key = click.prompt("Enter your Kobits API Key", hide_input=True)

    target_key = target_key.strip()
    if not target_key.startswith("kb_live_"):
        print_err("Invalid API key format. Expected prefix 'kb_live_'.")
        sys.exit(1)

    # Validate against server
    with get_client(target_key, target_url) as client:
        try:
            resp = client.get("/api/v1/extension/status")
            if resp.status_code == 401:
                print_err("Authentication failed: Invalid or revoked API key.")
                sys.exit(1)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print_err(f"Failed to connect to Kobits server at {target_url}: {e}")
            sys.exit(1)

    cfg = load_config()
    cfg["api_key"] = target_key
    cfg["api_url"] = target_url
    save_config(cfg)

    user = data.get("user", {})
    org = data.get("organization", {})
    user_name = user.get("full_name") or user.get("email") or "Developer"
    org_name = org.get("name") or "Workspace"

    print_success(f"Authenticated as [bold]{user_name}[/bold] in [bold]{org_name}[/bold]!")
    print_msg(f"Credentials safely stored in {CONFIG_FILE}", style="dim")


# ── WHOAMI ────────────────────────────────────────────────────────────
@cli.command()
@click.pass_context
def whoami(ctx):
    """Inspect authenticated user identity, workspace, and balance."""
    api_key = ctx.obj["api_key"]
    url = ctx.obj["url"]
    fmt = ctx.obj["output_format"]

    if not api_key:
        print_err("Not authenticated. Run 'kobits login' or set KOBITS_API_KEY.")
        sys.exit(1)

    with get_client(api_key, url) as client:
        try:
            resp = client.get("/api/v1/extension/status")
            if resp.status_code == 401:
                print_err("API Key invalid or revoked. Please run 'kobits login'.")
                sys.exit(1)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print_err(f"Server request failed: {e}")
            sys.exit(1)

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    user = data.get("user", {})
    org = data.get("organization", {})
    active = data.get("active_missions", [])

    if RICH_AVAILABLE and console:
        table = Table(title="Kobits Workspace Identity", show_header=False, box=None)
        table.add_row("[bold cyan]User:[/bold cyan]", f"{user.get('full_name')} ({user.get('email')})")
        table.add_row("[bold cyan]User ID:[/bold cyan]", user.get("id"))
        table.add_row("[bold cyan]Workspace:[/bold cyan]", org.get("name"))
        table.add_row("[bold cyan]Org ID:[/bold cyan]", org.get("id"))
        table.add_row("[bold cyan]Org Role:[/bold cyan]", org.get("role", "MEMBER"))
        table.add_row("[bold cyan]Credit Balance:[/bold cyan]", f"[bold green]{org.get('credit_balance', 0)} credits[/bold green]")
        table.add_row("[bold cyan]Server Endpoint:[/bold cyan]", url)
        table.add_row("[bold cyan]Active Missions:[/bold cyan]", str(len(active)))
        console.print(Panel(table, border_style="cyan"))
    else:
        print(f"User: {user.get('full_name')} ({user.get('email')})")
        print(f"Workspace: {org.get('name')} (Role: {org.get('role', 'MEMBER')})")
        print(f"Credit Balance: {org.get('credit_balance', 0)} credits")
        print(f"Server Endpoint: {url}")


# ── INIT ──────────────────────────────────────────────────────────────
@cli.command()
@click.option("--name", help="Project workspace name")
@click.pass_context
def init(ctx, name: Optional[str]):
    """Initialize project repository configuration in current directory."""
    cwd = Path.cwd()
    git_dir = cwd / ".git"

    repo_url = None
    default_branch = "main"

    if git_dir.exists():
        try:
            proc = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True, text=True, cwd=cwd)
            if proc.returncode == 0 and proc.stdout.strip():
                repo_url = proc.stdout.strip()
            branch_proc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=cwd)
            if branch_proc.returncode == 0 and branch_proc.stdout.strip():
                default_branch = branch_proc.stdout.strip()
        except Exception:
            pass

    proj_name = name or cwd.name
    kobits_dir = cwd / ".kobits"
    kobits_dir.mkdir(exist_ok=True)
    local_cfg = {
        "project_name": proj_name,
        "repository_url": repo_url,
        "default_branch": default_branch,
        "created_at": str(Path.cwd()),
    }
    with open(kobits_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(local_cfg, f, indent=2)

    print_success(f"Initialized Kobits repository configuration in [bold]{cwd}[/bold]")
    if repo_url:
        print_msg(f"  Remote: {repo_url} (branch: {default_branch})", style="dim")


# ── RUN ───────────────────────────────────────────────────────────────
@cli.command()
@click.argument("prompt")
@click.option("--file", "file_path", help="Target file for inline context")
@click.option("--range", "selection_range", help="Line range (e.g. L10-L40)")
@click.option("--branch", help="Active working branch")
@click.option("--mode", type=click.Choice(["autopilot", "approval_required"]), default="autopilot", help="Execution mode")
@click.option("--no-wait", is_flag=True, help="Do not wait for mission to complete; return immediately")
@click.pass_context
def run(ctx, prompt: str, file_path: Optional[str], selection_range: Optional[str], branch: Optional[str], mode: str, no_wait: bool):
    """Launch an autonomous engineering mission with live streaming output."""
    api_key = ctx.obj["api_key"]
    url = ctx.obj["url"]
    fmt = ctx.obj["output_format"]

    if not api_key:
        print_err("Not authenticated. Run 'kobits login' or set KOBITS_API_KEY.")
        sys.exit(1)

    code_context = None
    if file_path and os.path.isfile(file_path):
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                code_context = f.read(8000)
        except Exception:
            pass

    payload = {
        "prompt": prompt,
        "file_path": file_path,
        "selection_range": selection_range,
        "code_context": code_context,
        "branch": branch,
        "execution_mode": mode,
    }

    with get_client(api_key, url) as client:
        try:
            resp = client.post("/api/v1/extension/dispatch", json=payload)
            if resp.status_code == 401:
                print_err("Authentication failed. Run 'kobits login'.")
                sys.exit(1)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print_err(f"Failed to dispatch mission: {e}")
            sys.exit(1)

    mission_id = data.get("mission_id")
    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        if no_wait:
            return

    print_success(f"Mission Dispatched: [bold cyan]{mission_id[:8]}[/bold cyan] ('{data.get('title')}')")
    print_msg(f"  Branch: {data.get('active_branch')}", style="dim")
    print_msg(f"  Terminal Stream: {url}{data.get('terminal_ws_url')}", style="dim")

    if no_wait:
        print_msg(f"\nRun 'kobits status {mission_id[:8]}' to inspect progress.")
        return

    # Live streaming polling loop
    asyncio.run(_stream_mission_progress(url, api_key, mission_id))


async def _stream_mission_progress(base_url: str, api_key: str, mission_id: str):
    """Stream live mission status updates until terminal state."""
    headers = {"X-API-Key": api_key, "Authorization": f"Bearer {api_key}"}
    last_phase = None
    last_stage = None

    if RICH_AVAILABLE and console:
        console.print("\n[bold cyan]Streaming Agent Execution Trajectory...[/bold cyan]")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Initiating mission...", total=100)
            async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=10.0) as client:
                for _ in range(60):  # poll up to ~5 minutes
                    try:
                        r = await client.get(f"/api/v1/extension/missions/{mission_id}/status")
                        if r.status_code == 200:
                            s = r.json()
                            status_val = s.get("status", "ACTIVE")
                            phase_val = s.get("phase", "INTAKE")
                            stage_val = s.get("current_stage", "")
                            prog_pct = max(0, min(100, int((s.get("progress") or 0.0) * 100)))

                            progress.update(
                                task,
                                completed=prog_pct,
                                description=f"[{phase_val}] {stage_val or status_val}",
                            )

                            if status_val in ("COMPLETED", "FAILED", "CANCELLED", "AWAITING_APPROVAL"):
                                if status_val == "COMPLETED":
                                    progress.update(task, completed=100, description="[green]✓ Mission Completed!")
                                break
                    except Exception:
                        pass
                    await asyncio.sleep(2.5)
    else:
        print("\nStreaming agent execution...")
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=10.0) as client:
            for _ in range(60):
                try:
                    r = await client.get(f"/api/v1/extension/missions/{mission_id}/status")
                    if r.status_code == 200:
                        s = r.json()
                        phase = s.get("phase")
                        stage = s.get("current_stage")
                        if phase != last_phase or stage != last_stage:
                            print(f"[{phase}] {stage or s.get('status')}")
                            last_phase = phase
                            last_stage = stage
                        if s.get("status") in ("COMPLETED", "FAILED", "CANCELLED", "AWAITING_APPROVAL"):
                            print(f"Mission finished with status: {s.get('status')}")
                            break
                except Exception:
                    pass
                await asyncio.sleep(2.5)

    # Fetch and display diff summary
    with get_client(api_key, base_url) as client:
        try:
            diff_resp = client.get(f"/api/v1/extension/missions/{mission_id}/diff")
            if diff_resp.status_code == 200:
                diff_data = diff_resp.json()
                if diff_data.get("has_changes"):
                    print_success(
                        f"Solution verified: {diff_data.get('files_changed', 0)} files changed "
                        f"(+{diff_data.get('lines_added', 0)}, -{diff_data.get('lines_removed', 0)})"
                    )
                    print_msg(f"Run 'kobits diff {mission_id[:8]}' to view patch or 'kobits apply {mission_id[:8]}' to apply.")
        except Exception:
            pass


# ── STATUS ────────────────────────────────────────────────────────────
@cli.command()
@click.argument("mission_id", required=False)
@click.pass_context
def status(ctx, mission_id: Optional[str]):
    """Check real-time mission status, phase progression, and branch details."""
    api_key = ctx.obj["api_key"]
    url = ctx.obj["url"]
    fmt = ctx.obj["output_format"]

    if not api_key:
        print_err("Not authenticated. Run 'kobits login' or set KOBITS_API_KEY.")
        sys.exit(1)

    target_id = mission_id
    with get_client(api_key, url) as client:
        if not target_id:
            status_resp = client.get("/api/v1/extension/status")
            status_resp.raise_for_status()
            active = status_resp.json().get("active_missions", [])
            if not active:
                print_msg("No active missions found in current workspace.")
                return
            target_id = active[0]["id"]

        try:
            resp = client.get(f"/api/v1/extension/missions/{target_id}/status")
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print_err(f"Failed to fetch mission status: {e}")
            sys.exit(1)

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    if RICH_AVAILABLE and console:
        table = Table(title=f"Mission {data.get('id')[:8]} Status", box=None)
        table.add_row("[bold cyan]Title:[/bold cyan]", data.get("title"))
        table.add_row("[bold cyan]Status:[/bold cyan]", f"[bold green]{data.get('status')}[/bold green]")
        table.add_row("[bold cyan]Phase:[/bold cyan]", data.get("phase", "—"))
        table.add_row("[bold cyan]Progress:[/bold cyan]", f"{int((data.get('progress') or 0.0) * 100)}%")
        table.add_row("[bold cyan]Branch:[/bold cyan]", data.get("active_branch", "—"))
        table.add_row("[bold cyan]Stage:[/bold cyan]", data.get("current_stage", "—"))
        console.print(Panel(table, border_style="cyan"))
    else:
        print(f"Mission: {data.get('id')[:8]} — {data.get('title')}")
        print(f"Status: {data.get('status')} | Phase: {data.get('phase')} | Progress: {int((data.get('progress') or 0.0) * 100)}%")
        print(f"Branch: {data.get('active_branch')}")
        print(f"Stage: {data.get('current_stage')}")


# ── DIFF ──────────────────────────────────────────────────────────────
@cli.command()
@click.argument("mission_id")
@click.pass_context
def diff(ctx, mission_id: str):
    """Inspect colorized sandbox diffs for a mission."""
    api_key = ctx.obj["api_key"]
    url = ctx.obj["url"]
    fmt = ctx.obj["output_format"]

    if not api_key:
        print_err("Not authenticated. Run 'kobits login' or set KOBITS_API_KEY.")
        sys.exit(1)

    with get_client(api_key, url) as client:
        try:
            resp = client.get(f"/api/v1/extension/missions/{mission_id}/diff")
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print_err(f"Failed to fetch mission diff: {e}")
            sys.exit(1)

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    diff_content = data.get("diff_text", "")
    if RICH_AVAILABLE and console and sys.stdout.isatty():
        syntax = Syntax(diff_content, "diff", theme="monokai", line_numbers=True)
        console.print(syntax)
    else:
        click.echo(diff_content)


# ── APPLY ─────────────────────────────────────────────────────────────
@cli.command()
@click.argument("mission_id")
@click.option("--dry-run", is_flag=True, help="Simulate apply without writing files")
@click.pass_context
def apply(ctx, mission_id: str, dry_run: bool):
    """Apply validated candidate changes to local working tree."""
    api_key = ctx.obj["api_key"]
    url = ctx.obj["url"]

    if not api_key:
        print_err("Not authenticated. Run 'kobits login' or set KOBITS_API_KEY.")
        sys.exit(1)

    with get_client(api_key, url) as client:
        try:
            resp = client.post(f"/api/v1/extension/missions/{mission_id}/apply", json={"dry_run": dry_run})
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print_err(f"Failed to apply mission changes: {e}")
            sys.exit(1)

    print_success(data.get("message", "Changes applied successfully."))
    files = data.get("files", [])
    if files:
        print_msg("Files affected:")
        for f in files:
            print_msg(f"  • {f}", style="cyan")


# ── KEYS ──────────────────────────────────────────────────────────────
@cli.group()
def keys():
    """Manage cryptographic API keys for CLI and CI/CD."""
    pass


@keys.command("list")
@click.pass_context
def list_keys(ctx):
    """List API keys in current organization."""
    api_key = ctx.obj["api_key"]
    url = ctx.obj["url"]
    fmt = ctx.obj["output_format"]

    with get_client(api_key, url) as client:
        try:
            resp = client.get("/api/v1/auth/api-keys")
            resp.raise_for_status()
            keys_data = resp.json()
        except Exception as e:
            print_err(f"Failed to list API keys: {e}")
            sys.exit(1)

    if fmt == "json":
        click.echo(json.dumps(keys_data, indent=2))
        return

    if RICH_AVAILABLE and console:
        table = Table(title="Workspace API Keys")
        table.add_column("Key ID", style="dim")
        table.add_column("Name", style="bold cyan")
        table.add_column("Prefix", style="green")
        table.add_column("Scopes", style="yellow")
        table.add_column("Active", style="magenta")
        table.add_column("Created", style="dim")
        for k in keys_data:
            table.add_row(
                k["id"][:8],
                k["name"],
                k["key_prefix"],
                k["scopes"],
                "✓" if k["is_active"] else "✗",
                (k["created_at"] or "")[:10],
            )
        console.print(table)
    else:
        for k in keys_data:
            print(f"{k['id'][:8]} | {k['name']} | {k['key_prefix']} | Active: {k['is_active']}")


@keys.command("create")
@click.argument("name", default="CLI Developer Key")
@click.option("--scopes", default="read,write,cli,extension", help="Comma-separated scopes")
@click.option("--expires-days", type=int, default=None, help="Expiration in days")
@click.pass_context
def create_key(ctx, name: str, scopes: str, expires_days: Optional[int]):
    """Generate a new cryptographic API key."""
    api_key = ctx.obj["api_key"]
    url = ctx.obj["url"]
    fmt = ctx.obj["output_format"]

    with get_client(api_key, url) as client:
        try:
            resp = client.post(
                "/api/v1/auth/api-keys",
                json={"name": name, "scopes": scopes, "expires_in_days": expires_days},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print_err(f"Failed to create API key: {e}")
            sys.exit(1)

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    print_success(f"Generated new API Key '[bold]{data['name']}[/bold]'")
    if RICH_AVAILABLE and console:
        console.print(Panel(
            f"[bold green]{data['key']}[/bold green]\n\n"
            "[bold red]Important:[/bold red] Save this key now! You will NOT be able to view it again.",
            title="Raw Secret Key",
            border_style="green",
        ))
    else:
        print(f"\nSecret Key: {data['key']}\nImportant: Save this key now. You will not see it again.")


@keys.command("revoke")
@click.argument("key_id")
@click.pass_context
def revoke_key(ctx, key_id: str):
    """Revoke an API key by ID."""
    api_key = ctx.obj["api_key"]
    url = ctx.obj["url"]

    with get_client(api_key, url) as client:
        try:
            resp = client.delete(f"/api/v1/auth/api-keys/{key_id}")
            resp.raise_for_status()
        except Exception as e:
            print_err(f"Failed to revoke key: {e}")
            sys.exit(1)

    print_success(f"Revoked API key [bold]{key_id[:8]}[/bold].")


# ── LOGOUT ────────────────────────────────────────────────────────────
@cli.command()
def logout():
    """Clear local credentials from ~/.kobits/config.json."""
    if CONFIG_FILE.exists():
        try:
            CONFIG_FILE.unlink()
        except Exception:
            pass
    print_success("Logged out. Local credentials removed.")


if __name__ == "__main__":
    cli()
