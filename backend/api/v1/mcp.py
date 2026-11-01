"""
Built-in JSON-RPC 2.0 Model Context Protocol (MCP) Server for Kobits Memory OS.

Exposes the 3-tier Biologically Inspired Memory OS and SHA-256 Cryptographic
Mutation Ledger over stateless HTTP JSON-RPC 2.0 (`POST /api/v1/mcp`) so external
IDEs, CLI coding agents, and autonomous subagents can read and mutate tenant-scoped
long-term project memory.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.memory import resolve_memory_tenant
from backend.core.database import get_db
from backend.models.memory_os import GENESIS_HASH, MemoryLedgerEntry
from backend.services.memory_os_service import MemoryOSService
from sqlalchemy import and_, select

router = APIRouter()

MCP_PROTOCOL_VERSION = "2024-11-05"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


MCP_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "store_episodic_memory",
        "description": (
            "Store a tenant-scoped episodic experience with initial Ebbinghaus "
            "memory stability S (seconds) and cryptographic ledger signing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target project identifier."},
                "content": {"type": "string", "description": "Episodic experience narrative."},
                "stability": {
                    "type": "number",
                    "description": "Initial Ebbinghaus stability S in seconds (default 86400).",
                    "default": 86400.0,
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional categorical retrieval tags.",
                },
            },
            "required": ["project_id", "content"],
        },
    },
    {
        "name": "query_episodic_memory",
        "description": (
            "Retrieve and rank episodic memories filtered by live Ebbinghaus retention "
            "R(t) = exp(-t/S) >= min_retention and hybrid semantic similarity."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target project identifier."},
                "query": {"type": "string", "description": "Optional semantic search query."},
                "min_retention": {
                    "type": "number",
                    "description": "Minimum live Ebbinghaus retention threshold [0.0, 1.0].",
                    "default": 0.10,
                },
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "recall_episodic_memory",
        "description": (
            "Reinforce an episodic memory upon active recall, monotonically increasing "
            "stability S_after = S_before * (1 + alpha) and resetting elapsed decay time."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target project identifier."},
                "memory_id": {"type": "string", "description": "Episodic memory record UUID."},
                "alpha": {
                    "type": "number",
                    "description": "Positive reinforcement growth factor (> 0).",
                    "default": 0.5,
                },
            },
            "required": ["project_id", "memory_id"],
        },
    },
    {
        "name": "store_semantic_memory",
        "description": (
            "Store a semantic architectural invariant or domain constraint with "
            "hybrid symbolic/vector contradiction detection and belief propagation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target project identifier."},
                "subject": {"type": "string", "description": "Architectural domain or subsystem."},
                "key": {"type": "string", "description": "Canonical constraint key."},
                "statement": {"type": "string", "description": "Declarative constraint statement."},
                "confidence": {"type": "number", "default": 1.0},
                "base_confidence": {"type": "number"},
                "depends_on_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional upstream semantic constraint IDs.",
                },
            },
            "required": ["project_id", "subject", "key", "statement"],
        },
    },
    {
        "name": "query_semantic_memory",
        "description": (
            "Query semantic constraints filtered by lifecycle status "
            "(ACTIVE, CONFLICTING, SUPERSEDED, or ALL), subject, and key."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target project identifier."},
                "status": {
                    "type": "string",
                    "description": "Lifecycle filter: ACTIVE, CONFLICTING, SUPERSEDED, or ALL.",
                    "default": "ACTIVE",
                },
                "subject": {"type": "string"},
                "key": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "resolve_semantic_conflict",
        "description": (
            "Resolve a semantic contradiction by promoting winning_id to ACTIVE, "
            "transitioning superseded_id to SUPERSEDED, and propagating belief attenuation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target project identifier."},
                "winning_id": {"type": "string", "description": "Winning constraint record ID."},
                "superseded_id": {"type": "string", "description": "Superseded constraint record ID."},
                "reason": {"type": "string", "description": "Audit rationale for resolution."},
            },
            "required": ["project_id", "winning_id", "superseded_id"],
        },
    },
    {
        "name": "store_procedural_playbook",
        "description": (
            "Create or version a reusable procedural execution playbook with ordered steps, "
            "trigger tags, and preconditions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target project identifier."},
                "name": {"type": "string", "description": "Playbook title."},
                "description": {"type": "string", "description": "Playbook summary."},
                "steps": {
                    "type": "array",
                    "items": {},
                    "description": "Ordered executable steps (strings or step objects).",
                },
                "trigger_tags": {"type": "array", "items": {"type": "string"}},
                "preconditions": {"type": "array", "items": {"type": "string"}},
                "parent_playbook_id": {"type": "string"},
            },
            "required": ["project_id", "name", "steps"],
        },
    },
    {
        "name": "query_procedural_playbooks",
        "description": (
            "Query procedural playbooks ranked by tag relevance and Laplace-smoothed "
            "empirical reliability (s+1)/(s+f+2)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target project identifier."},
                "query": {"type": "string", "description": "Optional task context query."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "record_playbook_outcome",
        "description": (
            "Record a procedural playbook execution outcome (SUCCESS or FAILURE) "
            "to update Laplace reliability telemetry and append a signed ledger entry."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target project identifier."},
                "playbook_id": {"type": "string", "description": "Playbook UUID."},
                "outcome": {
                    "type": "string",
                    "enum": ["SUCCESS", "FAILURE"],
                    "description": "Execution outcome.",
                },
            },
            "required": ["playbook_id", "outcome"],
        },
    },
    {
        "name": "verify_memory_integrity",
        "description": (
            "Verify the SHA-256 cryptographic mutation hash chain and detect any "
            "out-of-band tampering, sequence gaps, or broken hash pointers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Target project identifier (or omit to verify all org chains).",
                },
            },
        },
    },
]

_TOOL_ALIASES: Dict[str, str] = {
    "reinforce_episodic_memory": "recall_episodic_memory",
    "resolve_semantic_contradiction": "resolve_semantic_conflict",
    "record_playbook_execution": "record_playbook_outcome",
}


def _jsonrpc_ok(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_err(req_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _wrap_tool_result(payload: Dict[str, Any], is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _require_str(args: Dict[str, Any], field: str, allow_empty: bool = False) -> str:
    val = args.get(field)
    if not isinstance(val, str) or (not allow_empty and not val.strip()):
        raise ValueError(f"Missing or invalid required string parameter '{field}'")
    return val


async def _execute_mcp_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    request: Request,
    db: AsyncSession,
) -> Dict[str, Any]:
    canonical = _TOOL_ALIASES.get(tool_name, tool_name)
    valid_names = {t["name"] for t in MCP_TOOLS}
    if canonical not in valid_names:
        raise KeyError(f"Unknown MCP tool '{tool_name}'")

    raw_proj = arguments.get("project_id")
    project_id: Optional[str] = str(raw_proj).strip() if isinstance(raw_proj, str) and raw_proj.strip() else None

    _, org_id, _ = await resolve_memory_tenant(request, db, project_id)

    if canonical == "store_episodic_memory":
        pid = _require_str(arguments, "project_id")
        content = _require_str(arguments, "content")
        stability = float(arguments.get("stability", 86400.0))
        tags = arguments.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError("Parameter 'tags' must be a list of strings")
        rec = await MemoryOSService.store_episodic(
            db=db,
            org_id=org_id,
            project_id=pid,
            content=content,
            stability=stability,
            tags=[str(t) for t in tags],
        )
        return _wrap_tool_result(rec.to_dict())

    if canonical == "query_episodic_memory":
        pid = _require_str(arguments, "project_id")
        query_text = arguments.get("query") or arguments.get("query_text")
        min_retention = float(arguments.get("min_retention", 0.10))
        limit = int(arguments.get("limit", 10))
        pairs = await MemoryOSService.query_episodic(
            db=db,
            org_id=org_id,
            project_id=pid,
            query_text=str(query_text) if query_text is not None else None,
            min_retention=min_retention,
            limit=limit,
        )
        items = []
        for rec, live_r in pairs:
            d = rec.to_dict()
            d["retention"] = round(float(live_r), 6)
            items.append(d)
        return _wrap_tool_result({"results": items, "count": len(items), "project_id": pid})

    if canonical == "recall_episodic_memory":
        pid = _require_str(arguments, "project_id")
        memory_id = _require_str(arguments, "memory_id")
        alpha = float(arguments.get("alpha", 0.5))
        rec = await MemoryOSService.recall_episodic(
            db=db,
            org_id=org_id,
            project_id=pid,
            memory_id=memory_id,
            alpha=alpha,
        )
        return _wrap_tool_result(rec.to_dict())

    if canonical == "store_semantic_memory":
        pid = _require_str(arguments, "project_id")
        subject = _require_str(arguments, "subject")
        key = _require_str(arguments, "key")
        statement = _require_str(arguments, "statement")
        confidence = float(arguments.get("confidence", 1.0))
        base_conf = arguments.get("base_confidence")
        deps = arguments.get("depends_on_ids") or []
        if not isinstance(deps, list):
            raise ValueError("Parameter 'depends_on_ids' must be a list of IDs")
        rec, conflict = await MemoryOSService.store_semantic(
            db=db,
            org_id=org_id,
            project_id=pid,
            subject=subject,
            key=key,
            statement=statement,
            confidence=confidence,
            base_confidence=float(base_conf) if base_conf is not None else None,
            depends_on_ids=[str(d) for d in deps],
        )
        rec_dict = rec.to_dict()
        return _wrap_tool_result(
            {
                **rec_dict,
                "record": rec_dict,
                "conflict": conflict.to_dict() if conflict is not None else None,
            }
        )

    if canonical == "query_semantic_memory":
        pid = _require_str(arguments, "project_id")
        raw_status = arguments.get("status", "ACTIVE")
        eff_status: Optional[str] = None
        if raw_status is not None and str(raw_status).strip().upper() not in ("ALL", "*", ""):
            eff_status = str(raw_status).strip().upper()
        subject = arguments.get("subject")
        key = arguments.get("key")
        limit = int(arguments.get("limit", 20))
        records = await MemoryOSService.query_semantic(
            db=db,
            org_id=org_id,
            project_id=pid,
            status=eff_status,
            subject=str(subject) if subject else None,
            key=str(key) if key else None,
            limit=limit,
        )
        items = [r.to_dict() for r in records]
        return _wrap_tool_result({"results": items, "count": len(items), "project_id": pid})

    if canonical == "resolve_semantic_conflict":
        pid = _require_str(arguments, "project_id")
        winning_id = _require_str(arguments, "winning_id")
        superseded_id = _require_str(arguments, "superseded_id")
        reason = str(arguments.get("reason") or "Resolved via MCP")
        resolution = await MemoryOSService.resolve_conflict(
            db=db,
            org_id=org_id,
            project_id=pid,
            winning_id=winning_id,
            superseded_id=superseded_id,
            reason=reason,
        )
        return _wrap_tool_result(resolution)

    if canonical == "store_procedural_playbook":
        pid = _require_str(arguments, "project_id")
        name = _require_str(arguments, "name")
        description = str(arguments.get("description") or "")
        steps = arguments.get("steps") if "steps" in arguments else []
        if not isinstance(steps, list):
            raise ValueError("Parameter 'steps' must be a list")
        trigger_tags = arguments.get("trigger_tags") or []
        preconditions = arguments.get("preconditions") or []
        parent_id = arguments.get("parent_playbook_id")
        pb = await MemoryOSService.store_playbook(
            db=db,
            org_id=org_id,
            project_id=pid,
            name=name,
            description=description,
            steps=steps,
            trigger_tags=[str(t) for t in trigger_tags],
            preconditions=[str(p) for p in preconditions],
            parent_playbook_id=str(parent_id) if parent_id else None,
        )
        return _wrap_tool_result(pb.to_dict())

    if canonical == "query_procedural_playbooks":
        pid = _require_str(arguments, "project_id")
        query_text = arguments.get("query") or arguments.get("context_query")
        raw_tags = arguments.get("tags")
        tags_list: Optional[List[str]] = None
        if isinstance(raw_tags, list):
            tags_list = [str(t) for t in raw_tags if str(t).strip()]
        elif isinstance(raw_tags, str) and raw_tags.strip():
            tags_list = [t.strip() for t in raw_tags.split(",") if t.strip()]
        limit = int(arguments.get("limit", 5))
        pbs = await MemoryOSService.query_playbooks(
            db=db,
            org_id=org_id,
            project_id=pid,
            context_query=str(query_text) if query_text else None,
            tags=tags_list,
            limit=limit,
        )
        items = [p.to_dict() for p in pbs]
        return _wrap_tool_result({"results": items, "count": len(items), "project_id": pid})

    if canonical == "record_playbook_outcome":
        playbook_id = _require_str(arguments, "playbook_id")
        outcome = _require_str(arguments, "outcome").strip().upper()
        pb = await MemoryOSService.record_playbook_execution(
            db=db,
            org_id=org_id,
            project_id=project_id,
            playbook_id=playbook_id,
            outcome=outcome,  # type: ignore[arg-type]
        )
        return _wrap_tool_result(pb.to_dict())

    # verify_memory_integrity
    is_valid, corrupted_id, error_msg = await MemoryOSService.verify_integrity(
        db=db,
        org_id=org_id,
        project_id=project_id,
    )
    conditions = [MemoryLedgerEntry.organization_id == org_id]
    if project_id is not None:
        conditions.append(MemoryLedgerEntry.project_id == project_id)
    stmt = (
        select(MemoryLedgerEntry)
        .where(and_(*conditions))
        .order_by(MemoryLedgerEntry.sequence_number.desc())
    )
    res = await db.execute(stmt)
    entries = list(res.scalars().all())
    head_entry = entries[0] if entries else None

    return _wrap_tool_result(
        {
            "is_valid": is_valid,
            "valid": is_valid,
            "corrupted_entry_id": corrupted_id,
            "error_message": error_msg,
            "entry_count": len(entries),
            "latest_sequence": int(head_entry.sequence_number) if head_entry else 0,
            "head_hash": str(head_entry.entry_hash) if head_entry else GENESIS_HASH,
            "project_id": project_id,
        }
    )


async def _dispatch_single_rpc(
    payload: Any,
    request: Request,
    db: AsyncSession,
) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return _jsonrpc_err(None, INVALID_REQUEST, "Invalid Request: expected JSON object")

    is_notification = "id" not in payload
    req_id = payload.get("id")

    if payload.get("jsonrpc") != "2.0":
        return _jsonrpc_err(req_id, INVALID_REQUEST, "Invalid Request: jsonrpc version must be '2.0'")

    method = payload.get("method")
    if not isinstance(method, str) or not method.strip():
        return _jsonrpc_err(req_id, INVALID_REQUEST, "Invalid Request: missing or non-string 'method'")

    params = payload.get("params")
    if params is not None and not isinstance(params, dict):
        return _jsonrpc_err(req_id, INVALID_PARAMS, "Invalid params: 'params' must be an object when provided")
    params_dict: Dict[str, Any] = params or {}

    if method.startswith("notifications/"):
        if is_notification:
            return None
        return _jsonrpc_ok(req_id, {"acknowledged": True})

    if method == "initialize":
        return _jsonrpc_ok(
            req_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "kobits-memory-os",
                    "version": "1.0.0",
                },
            },
        )

    if method == "ping":
        return _jsonrpc_ok(req_id, {})

    if method == "tools/list":
        # Verify caller authentication before exposing workspace tools
        await resolve_memory_tenant(request, db, None)
        return _jsonrpc_ok(req_id, {"tools": MCP_TOOLS})

    if method == "tools/call":
        tool_name = params_dict.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return _jsonrpc_err(req_id, INVALID_PARAMS, "Invalid params: missing tool 'name'")
        raw_args = params_dict.get("arguments")
        if raw_args is not None and not isinstance(raw_args, dict):
            return _jsonrpc_err(req_id, INVALID_PARAMS, "Invalid params: 'arguments' must be an object")

        try:
            tool_res = await _execute_mcp_tool(
                tool_name=tool_name.strip(),
                arguments=raw_args or {},
                request=request,
                db=db,
            )
            return _jsonrpc_ok(req_id, tool_res)
        except HTTPException:
            # Propagate 401/403 HTTP authentication and cross-tenant boundaries directly
            raise
        except KeyError as exc:
            return _jsonrpc_err(req_id, INVALID_PARAMS, str(exc))
        except (ValueError, TypeError) as exc:
            return _jsonrpc_err(req_id, INVALID_PARAMS, f"Invalid params: {exc}")
        except Exception as exc:
            return _jsonrpc_err(req_id, INTERNAL_ERROR, f"Internal error: {exc}")

    if is_notification:
        return None
    return _jsonrpc_err(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")


@router.post("")
@router.post("/", include_in_schema=False)
async def handle_mcp_jsonrpc(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Stateless JSON-RPC 2.0 MCP endpoint supporting single and batch requests."""
    raw_body = await request.body()
    if not raw_body or not raw_body.strip():
        return JSONResponse(
            status_code=200,
            content=_jsonrpc_err(None, PARSE_ERROR, "Parse error: empty request body"),
        )

    try:
        parsed: Union[Dict[str, Any], List[Any]] = json.loads(raw_body.decode("utf-8"))
    except Exception:
        return JSONResponse(
            status_code=200,
            content=_jsonrpc_err(None, PARSE_ERROR, "Parse error: malformed JSON payload"),
        )

    if isinstance(parsed, list):
        if len(parsed) == 0:
            return JSONResponse(
                status_code=200,
                content=_jsonrpc_err(None, INVALID_REQUEST, "Invalid Request: empty batch array"),
            )
        responses: List[Dict[str, Any]] = []
        for item in parsed:
            resp = await _dispatch_single_rpc(item, request, db)
            if resp is not None:
                responses.append(resp)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(status_code=200, content=responses)

    single_resp = await _dispatch_single_rpc(parsed, request, db)
    if single_resp is None:
        return Response(status_code=202)
    return JSONResponse(status_code=200, content=single_resp)
