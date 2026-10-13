import os
import re
import mimetypes
import logging
import json
import httpx
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.core.database import get_db
from backend.models.mission import Mission
from backend.services.sandbox_manager import SandboxManager

logger = logging.getLogger("kobits.preview")

router = APIRouter(tags=["preview"])

# Register essential web MIME types
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("text/html", ".html")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("application/wasm", ".wasm")


def find_sandbox_directory(mission: Mission) -> Optional[str]:
    """Resolve the physical filesystem sandbox worktree directory for a mission."""
    # 1. By active branch
    if mission.active_branch:
        sess = SandboxManager.get_session_by_branch(mission.active_branch)
        if sess and os.path.isdir(sess.sandbox_dir):
            return sess.sandbox_dir

    # 2. By mission ID session lookup
    sess = SandboxManager.get_session(mission.id)
    if sess and os.path.isdir(sess.sandbox_dir):
        return sess.sandbox_dir

    # 3. Direct candidate paths under SANDBOX_ROOT
    candidates = [
        os.path.join(SandboxManager.SANDBOX_ROOT, f"sandbox-{mission.id}"),
        os.path.join(SandboxManager.SANDBOX_ROOT, f"sandbox-{mission.id[:8]}"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c

    # 4. Scan SANDBOX_ROOT for metadata matches
    if os.path.isdir(SandboxManager.SANDBOX_ROOT):
        try:
            for entry in os.listdir(SandboxManager.SANDBOX_ROOT):
                sdir = os.path.join(SandboxManager.SANDBOX_ROOT, entry)
                meta_path = os.path.join(sdir, ".kobits_sandbox.json")
                if os.path.isdir(sdir) and os.path.isfile(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if mission.active_branch and data.get("branch_name") == mission.active_branch:
                            return sdir
                        if mission.id in (data.get("session_id", ""), data.get("mission_id", "")):
                            return sdir
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Error scanning sandboxes for preview: {e}")

    return None


def get_preview_config(sandbox_dir: str) -> Dict[str, Any]:
    """Read .kobits_preview.json configuration if present."""
    cfg_file = os.path.join(sandbox_dir, ".kobits_preview.json")
    if os.path.isfile(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def set_preview_config(sandbox_dir: str, config: Dict[str, Any]) -> None:
    """Save .kobits_preview.json configuration."""
    cfg_file = os.path.join(sandbox_dir, ".kobits_preview.json")
    try:
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write preview config: {e}")


def find_entry_file(sandbox_dir: str) -> Optional[str]:
    """Locate primary HTML entry file (index.html)."""
    check_paths = [
        "index.html",
        os.path.join("dist", "index.html"),
        os.path.join("build", "index.html"),
        os.path.join("public", "index.html"),
        os.path.join("web", "index.html"),
        os.path.join("src", "index.html"),
    ]
    for rel in check_paths:
        candidate = os.path.join(sandbox_dir, rel)
        if os.path.isfile(candidate):
            return candidate
    return None


def inject_base_tag(content: str, mission_id: str) -> str:
    """Inject <base href="..."> into HTML to ensure relative asset URLs resolve to the preview root."""
    if "<base " in content.lower():
        return content
    base_tag = f'\n  <base href="/api/v1/preview/{mission_id}/">'
    if re.search(r"<head[^>]*>", content, flags=re.IGNORECASE):
        return re.sub(r"(<head[^>]*>)", r"\1" + base_tag, content, count=1, flags=re.IGNORECASE)
    elif re.search(r"<html[^>]*>", content, flags=re.IGNORECASE):
        return re.sub(r"(<html[^>]*>)", r"\1\n<head>" + base_tag + "\n</head>", content, count=1, flags=re.IGNORECASE)
    else:
        return f"<head>{base_tag}\n</head>\n" + content


def render_placeholder_html(mission_id: str, status: str, phase: str) -> HTMLResponse:
    """Render a responsive placeholder page when sandbox assets are not ready."""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="4">
  <title>Kobits Live Preview — Building...</title>
  <style>
    body {{
      margin: 0;
      background: #09090b;
      color: #f4f4f5;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 16px;
      box-sizing: border-box;
    }}
    .card {{
      background: #141417;
      border: 1px solid #27272a;
      border-radius: 12px;
      padding: 32px;
      max-width: 480px;
      text-align: center;
      box-shadow: 0 10px 40px rgba(0,0,0,0.5);
    }}
    .spinner {{
      border: 3px solid #27272a;
      border-top: 3px solid #38bdf8;
      border-radius: 50%;
      width: 40px;
      height: 40px;
      animation: spin 0.9s linear infinite;
      margin: 0 auto 20px;
    }}
    @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
    h2 {{ margin: 0 0 10px; font-size: 20px; color: #ffffff; font-weight: 600; }}
    p {{ margin: 0 0 20px; color: #a1a1aa; font-size: 14px; line-height: 1.6; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 12px;
      background: rgba(56, 189, 248, 0.1);
      border: 1px solid rgba(56, 189, 248, 0.3);
      border-radius: 999px;
      font-size: 12px;
      color: #38bdf8;
      font-family: monospace;
    }}
    .footer {{ margin-top: 16px; font-size: 11px; color: #71717a; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="spinner"></div>
    <h2>Ephemeral Web Preview Initializing</h2>
    <p>Kobits autonomous engineers are generating and validating web assets in an isolated sandbox. This preview will refresh automatically once build artifacts are created.</p>
    <div class="badge">
      <span>Mission: {mission_id[:8]}</span> &bull;
      <span>Status: {status}</span> &bull;
      <span>Phase: {phase}</span>
    </div>
    <div class="footer">Listening on MicroVM Port Forwarding &bull; Kobits Preview Gateway</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200, headers={"Cache-Control": "no-cache"})


@router.get("/{mission_id}/info")
async def get_preview_info(
    mission_id: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve preview status, detected files, dev server configuration, and entry URL."""
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    sandbox_dir = find_sandbox_directory(mission)
    if not sandbox_dir or not os.path.isdir(sandbox_dir):
        return {
            "mission_id": mission_id,
            "sandbox_found": False,
            "status": "pending_creation",
            "entry_point": None,
            "files": [],
            "preview_url": f"/api/v1/preview/{mission_id}/",
        }

    preview_cfg = get_preview_config(sandbox_dir)
    entry_file = find_entry_file(sandbox_dir)
    entry_rel = os.path.relpath(entry_file, sandbox_dir).replace("\\", "/") if entry_file else None

    # Collect up to 50 files for inspector
    files = []
    try:
        for root, _, filenames in os.walk(sandbox_dir):
            if ".git" in root or "node_modules" in root or "__pycache__" in root:
                continue
            for fn in filenames:
                full_p = os.path.join(root, fn)
                rel_p = os.path.relpath(full_p, sandbox_dir).replace("\\", "/")
                files.append(rel_p)
                if len(files) >= 50:
                    break
            if len(files) >= 50:
                break
    except Exception:
        pass

    return {
        "mission_id": mission_id,
        "sandbox_found": True,
        "status": "ready" if entry_file or preview_cfg.get("port") else "waiting_for_assets",
        "sandbox_dir": sandbox_dir,
        "entry_point": entry_rel,
        "files_count": len(files),
        "files_sample": files[:20],
        "preview_url": f"/api/v1/preview/{mission_id}/",
        "dev_server": {
            "active": bool(preview_cfg.get("active")),
            "port": preview_cfg.get("port"),
        },
    }


@router.post("/{mission_id}/port")
async def configure_preview_port(
    mission_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Configure or update local dev server port proxy for a mission's sandbox."""
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    body = await request.json()
    port = body.get("port")
    active = body.get("active", True)

    sandbox_dir = find_sandbox_directory(mission)
    if not sandbox_dir or not os.path.isdir(sandbox_dir):
        raise HTTPException(status_code=400, detail="Sandbox not found for mission")

    cfg = get_preview_config(sandbox_dir)
    cfg["port"] = int(port) if port else None
    cfg["active"] = bool(active)
    set_preview_config(sandbox_dir, cfg)

    return {
        "status": "configured",
        "mission_id": mission_id,
        "port": port,
        "active": active,
    }


@router.get("/{mission_id}")
@router.get("/{mission_id}/")
@router.get("/{mission_id}/{file_path:path}")
async def serve_preview_asset(
    mission_id: str,
    file_path: str = "",
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Serve an ephemeral web preview for the given mission.
    1. Proxies to local dev server (e.g. Vite/Next.js/Webpack) if port is active.
    2. Serves static files directly from the sandbox directory with correct MIME types.
    3. Provides Single-Page Application (SPA) index.html fallback for client-side routing.
    """
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    sandbox_dir = find_sandbox_directory(mission)
    if not sandbox_dir or not os.path.isdir(sandbox_dir):
        return render_placeholder_html(
            mission_id=mission_id,
            status=str(mission.status.value if hasattr(mission.status, "value") else mission.status),
            phase=str(mission.phase.value if hasattr(mission.phase, "value") else mission.phase),
        )

    # Redirect root requests without trailing slash to ensure relative browser assets resolve correctly
    if request is not None and not file_path and not request.url.path.endswith("/"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"{request.url.path}/", status_code=307)

    # Clean & normalize relative path
    import urllib.parse
    raw_path = urllib.parse.unquote(file_path or "").strip().lstrip("/").replace("\\", "/")
    parts = [p.strip() for p in raw_path.split("/")]
    if ".." in parts or any(".." in p for p in parts):
        raise HTTPException(status_code=403, detail="Directory traversal prohibited")
    clean_path = "/".join(parts)

    # 1. Dev Server Proxy check (MicroVM Port Forwarding)
    preview_cfg = get_preview_config(sandbox_dir)
    proxy_port = preview_cfg.get("port")
    if proxy_port and preview_cfg.get("active", True):
        proxy_url = f"http://127.0.0.1:{proxy_port}/{clean_path}"
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                query_str = str(request.url.query) if request and request.url.query else ""
                full_target = f"{proxy_url}?{query_str}" if query_str else proxy_url
                resp = await client.get(
                    full_target,
                    headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
                )
                headers = {
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-cache",
                }
                if "content-type" in resp.headers:
                    headers["Content-Type"] = resp.headers["content-type"]
                body_content = resp.content
                if "text/html" in resp.headers.get("content-type", "").lower():
                    try:
                        body_content = inject_base_tag(body_content.decode("utf-8", errors="ignore"), mission_id).encode("utf-8")
                    except Exception:
                        pass
                return Response(
                    content=body_content,
                    status_code=resp.status_code,
                    headers=headers,
                )
        except Exception as proxy_err:
            logger.debug(f"Dev server proxy to port {proxy_port} inactive, falling back to static worktree: {proxy_err}")

    # 2. Static File Resolution
    candidate_subdirs = ["", "dist", "build", "public", "web", "src"]
    target_abs = None

    if not clean_path or clean_path.endswith("/"):
        # Look for index.html
        for sub in candidate_subdirs:
            p = os.path.join(sandbox_dir, sub, "index.html") if sub else os.path.join(sandbox_dir, "index.html")
            if os.path.isfile(p):
                target_abs = p
                break
    else:
        # Look for the exact requested file
        for sub in candidate_subdirs:
            p = os.path.join(sandbox_dir, sub, clean_path) if sub else os.path.join(sandbox_dir, clean_path)
            if os.path.isfile(p):
                target_abs = p
                break

    # 3. SPA Fallback: If route has no file extension, fallback to index.html
    has_extension = bool(os.path.splitext(clean_path)[1])
    if not target_abs:
        if not has_extension:
            target_abs = find_entry_file(sandbox_dir)
        else:
            raise HTTPException(status_code=404, detail=f"Asset '{clean_path}' not found in sandbox")

    if not target_abs or not os.path.isfile(target_abs):
        return render_placeholder_html(
            mission_id=mission_id,
            status=str(mission.status.value if hasattr(mission.status, "value") else mission.status),
            phase=str(mission.phase.value if hasattr(mission.phase, "value") else mission.phase),
        )

    # Verify resolved file is strictly contained within sandbox_dir
    if target_abs:
        real_target = os.path.realpath(target_abs)
        real_sandbox = os.path.realpath(sandbox_dir)
        try:
            if os.path.commonpath([real_target, real_sandbox]) != real_sandbox:
                raise HTTPException(status_code=403, detail="Directory traversal prohibited")
        except ValueError:
            raise HTTPException(status_code=403, detail="Directory traversal prohibited")

    # Determine media type
    ext = os.path.splitext(target_abs)[1].lower()
    guessed_type, _ = mimetypes.guess_type(target_abs)
    media_type = guessed_type or "application/octet-stream"
    if ext in (".html", ".htm"):
        media_type = "text/html; charset=utf-8"
    elif ext in (".js", ".mjs"):
        media_type = "application/javascript; charset=utf-8"
    elif ext == ".css":
        media_type = "text/css; charset=utf-8"
    elif ext == ".json":
        media_type = "application/json; charset=utf-8"

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache",
        "X-Kobits-Preview": "sandbox-microvm",
    }

    if ext in (".html", ".htm") and os.path.isfile(target_abs):
        try:
            with open(target_abs, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            content = inject_base_tag(content, mission_id)
            return HTMLResponse(content=content, status_code=200, headers=headers)
        except Exception:
            pass

    return FileResponse(
        path=target_abs,
        media_type=media_type,
        headers=headers,
    )
