from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, field_validator
from datetime import datetime, timezone
import json
import re
import shlex
import logging

from backend.core.database import get_db
from backend.api.v1.auth import get_current_active_user
from backend.services.auth_service import get_user_default_org
from backend.models.organization import User
from backend.models.tool import CustomTool, ToolExecutionType
from backend.services.tool_registry import ToolRegistry, ToolDefinition, RiskLevel

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Safe argument handling ────────────────────────────────────────────────

SAFE_ARG_RE = re.compile(r'^[a-zA-Z0-9_\-./: @#,=\[\]{}()+*?^$|\\"\' \n\t]+$')
MAX_ARG_LENGTH = 4096
MAX_CODE_LENGTH = 32768


def _validate_input_schema(schema: Dict[str, Any]) -> bool:
    """Validate that input_schema is a well-formed JSON Schema object."""
    if not isinstance(schema, dict):
        return False
    if schema.get("type") != "object":
        return False
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return False
    for k, v in props.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            return False
        if "type" not in v:
            return False
    return True


def _validate_args_against_schema(args: dict, schema: Dict[str, Any]) -> Optional[str]:
    """Validate arguments against the tool's input_schema. Returns error string or None."""
    props = schema.get("properties", {})
    required = schema.get("required", [])

    for r in required:
        if r not in args:
            return f"Missing required argument: {r}"

    for k, v in args.items():
        if k not in props:
            return f"Unknown argument: {k}"
        expected_type = props[k].get("type", "string")
        if expected_type == "string" and not isinstance(v, str):
            return f"Argument '{k}' must be a string"
        if expected_type == "number" and not isinstance(v, (int, float)):
            return f"Argument '{k}' must be a number"
        if isinstance(v, str) and len(v) > MAX_ARG_LENGTH:
            return f"Argument '{k}' exceeds max length ({MAX_ARG_LENGTH})"

    return None


def _serialize_args_for_bash(args: dict) -> str:
    """Serialize arguments into safe environment variable exports for bash execution."""
    exports = []
    for k, v in args.items():
        safe_val = shlex.quote(str(v))
        safe_key = re.sub(r'[^A-Za-z0-9_]', '_', k).upper()
        exports.append(f"export TOOL_ARG_{safe_key}={safe_val}")
    return "\n".join(exports)


def _serialize_args_for_python(args: dict) -> str:
    """Serialize arguments into a safe JSON assignment for python execution."""
    sanitized = {}
    for k, v in args.items():
        safe_key = re.sub(r'[^A-Za-z0-9_]', '_', k)
        sanitized[safe_key] = v
    return f"import json\n_tool_args = json.loads({json.dumps(json.dumps(sanitized))})\n"


# ── Request/Response schemas ──────────────────────────────────────────────

class CustomToolCreate(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any]
    execution_type: str
    execution_code: str
    assigned_agents: List[str]

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_.]{1,63}$', v):
            raise ValueError("Tool name must be 2-64 alphanumeric chars, starting with a letter")
        return v

    @field_validator("execution_type")
    @classmethod
    def validate_execution_type(cls, v):
        valid = {e.value for e in ToolExecutionType}
        if v not in valid:
            raise ValueError(f"execution_type must be one of: {valid}")
        if v == ToolExecutionType.HTTP.value:
            raise ValueError("HTTP execution type is not yet implemented")
        return v

    @field_validator("execution_code")
    @classmethod
    def validate_execution_code(cls, v):
        if len(v) > MAX_CODE_LENGTH:
            raise ValueError(f"execution_code exceeds max length ({MAX_CODE_LENGTH})")
        return v

    @field_validator("input_schema")
    @classmethod
    def validate_schema(cls, v):
        if not _validate_input_schema(v):
            raise ValueError("input_schema must be a valid JSON Schema with type=object and typed properties")
        return v


class CustomToolUpdate(BaseModel):
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    execution_code: Optional[str] = None
    assigned_agents: Optional[List[str]] = None
    enabled: Optional[bool] = None


# ── API endpoints ─────────────────────────────────────────────────────────

@router.get("/")
async def list_tools(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """List all tools: base + org-scoped custom tools."""
    org_member = await get_user_default_org(db, current_user.id)

    base_tools = [
        {"id": "base-" + t.name, "name": t.name, "description": t.description, "is_custom": False}
        for t in ToolRegistry.get_all_tools()
        if not getattr(t, '_is_custom', False)
    ]

    stmt = select(CustomTool).where(CustomTool.organization_id == org_member.organization_id)
    res = await db.execute(stmt)
    custom_tools = res.scalars().all()

    c_tools = [{
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "execution_type": t.execution_type,
        "assigned_agents": t.get_assigned_agents(),
        "enabled": t.enabled,
        "is_custom": True
    } for t in custom_tools]

    return {"tools": base_tools + c_tools}


@router.post("/")
async def create_tool(
    tool: CustomToolCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a custom tool scoped to the user's organization."""
    org_member = await get_user_default_org(db, current_user.id)

    # Check for duplicate name within org
    stmt = select(CustomTool).where(
        CustomTool.organization_id == org_member.organization_id,
        CustomTool.name == tool.name
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Tool '{tool.name}' already exists in this organization")

    new_tool = CustomTool(
        organization_id=org_member.organization_id,
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        execution_type=tool.execution_type,
        execution_code=tool.execution_code,
        assigned_agents_json=json.dumps(tool.assigned_agents),
        enabled=True,
    )
    db.add(new_tool)
    await db.commit()
    await db.refresh(new_tool)

    _register_custom_tool_in_memory(new_tool)
    logger.info(f"Custom tool created: name={tool.name} org={org_member.organization_id}")

    return {
        "message": "Custom tool created",
        "tool_id": new_tool.id,
        "name": new_tool.name,
    }


@router.put("/{tool_id}")
async def update_tool(
    tool_id: str,
    update: CustomToolUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update a custom tool. Invalidates cache."""
    org_member = await get_user_default_org(db, current_user.id)
    tool = await db.get(CustomTool, tool_id)
    if not tool or tool.organization_id != org_member.organization_id:
        raise HTTPException(status_code=404, detail="Tool not found")

    if update.description is not None:
        tool.description = update.description
    if update.input_schema is not None:
        if not _validate_input_schema(update.input_schema):
            raise HTTPException(status_code=422, detail="Invalid input_schema")
        tool.input_schema = update.input_schema
    if update.execution_code is not None:
        if len(update.execution_code) > MAX_CODE_LENGTH:
            raise HTTPException(status_code=422, detail="execution_code too long")
        tool.execution_code = update.execution_code
    if update.assigned_agents is not None:
        tool.assigned_agents_json = json.dumps(update.assigned_agents)
    if update.enabled is not None:
        tool.enabled = update.enabled

    tool.updated_at = datetime.now(timezone.utc).isoformat()
    await db.commit()

    # Invalidate + re-register (or unregister if disabled)
    ToolRegistry.unregister(tool.name)
    if tool.enabled:
        _register_custom_tool_in_memory(tool)

    return {"message": "Tool updated", "tool_id": tool.id}


@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete a custom tool. Removes from cache."""
    org_member = await get_user_default_org(db, current_user.id)
    tool = await db.get(CustomTool, tool_id)
    if not tool or tool.organization_id != org_member.organization_id:
        raise HTTPException(status_code=404, detail="Tool not found")

    ToolRegistry.unregister(tool.name)
    await db.delete(tool)
    await db.commit()

    logger.info(f"Custom tool deleted: name={tool.name} org={org_member.organization_id}")
    return {"message": "Tool deleted"}


# ── Runtime registration ──────────────────────────────────────────────────

def _register_custom_tool_in_memory(tool: CustomTool):
    """
    Register a CustomTool into the in-memory ToolRegistry.

    CRITICAL SECURITY INVARIANT:
    ALL custom tool code (bash AND python) executes inside the existing
    SandboxManager boundary. Python code is written to a temp file inside
    the sandbox directory and executed via SandboxManager.run_command().
    No exec()/eval() on the backend host.
    """

    tool_id = tool.id
    tool_name = tool.name
    tool_org_id = tool.organization_id
    execution_type = tool.execution_type
    execution_code = tool.execution_code
    tool_input_schema = tool.input_schema
    assigned_agents = tool.get_assigned_agents()
    tool_enabled = tool.enabled

    async def custom_handler(context: dict = None, **kwargs):
        # ── Gate 1: Enabled check ──
        if not tool_enabled:
            return {"error": f"Tool '{tool_name}' is disabled"}

        # ── Gate 2: Sandbox required ──
        if not context or not context.get("sandbox_session_id"):
            return {"error": "Custom tools require an active sandbox session"}

        # ── Gate 3: Agent authorization ──
        calling_agent = context.get("agent_type")
        if assigned_agents and calling_agent and calling_agent not in assigned_agents:
            logger.warning(
                f"AUTHORIZATION DENIED: agent={calling_agent} tool={tool_name} "
                f"allowed={assigned_agents} org={tool_org_id}"
            )
            return {"error": f"Agent '{calling_agent}' is not authorized to use tool '{tool_name}'"}

        # ── Gate 4: Organization isolation ──
        ctx_org_id = context.get("organization_id")
        if ctx_org_id and ctx_org_id != tool_org_id:
            logger.warning(
                f"ORG ISOLATION VIOLATION: ctx_org={ctx_org_id} tool_org={tool_org_id} tool={tool_name}"
            )
            return {"error": "Tool not available in this organization"}

        # ── Gate 5: Argument validation ──
        validation_error = _validate_args_against_schema(kwargs, tool_input_schema)
        if validation_error:
            return {"error": f"Argument validation failed: {validation_error}"}

        session_id = context["sandbox_session_id"]
        from backend.services.sandbox_manager import SandboxManager

        try:
            if execution_type == ToolExecutionType.BASH.value:
                # Safe arg serialization via env vars
                env_exports = _serialize_args_for_bash(kwargs)
                full_script = f"{env_exports}\n{execution_code}"
                result = SandboxManager.run_command(session_id, full_script, timeout=30)
                _log_execution(context, tool_name, tool_id, kwargs, result)
                return {"success": True, "output": result}

            elif execution_type == ToolExecutionType.PYTHON.value:
                # Write python to sandbox temp file, execute via run_command
                arg_preamble = _serialize_args_for_python(kwargs)
                full_script = arg_preamble + execution_code
                # Write to sandbox fs, then execute
                tmp_path = f".kobits_tool_{tool_name}.py"
                SandboxManager.write_file(session_id, tmp_path, full_script)
                result = SandboxManager.run_command(session_id, f"python {tmp_path}", timeout=30)
                _log_execution(context, tool_name, tool_id, kwargs, result)
                return {"success": True, "output": result}

            elif execution_type == ToolExecutionType.HTTP.value:
                return {"error": "HTTP execution type is UNIMPLEMENTED. Not yet available."}

            return {"error": f"Unsupported execution type: {execution_type}"}

        except Exception as e:
            logger.error(f"Custom tool execution error: tool={tool_name} error={e}")
            # Never expose full tracebacks to agents
            return {"error": f"Tool execution failed: {type(e).__name__}"}

    tool_def = ToolDefinition(
        name=tool_name,
        description=tool.description,
        risk_level=RiskLevel.MEDIUM,
        input_schema=tool_input_schema,
        handler=custom_handler
    )
    tool_def.assigned_agents = assigned_agents
    tool_def.organization_id = tool_org_id
    tool_def._is_custom = True

    ToolRegistry.register(tool_def)


def _log_execution(context: dict, tool_name: str, tool_id: str, args: dict, result):
    """
    Audit log for custom tool execution. Persists to ActivityLog AND Python logger.
    Never persists secrets or sensitive arguments.
    """
    safe_args = {k: ("***" if "secret" in k.lower() or "key" in k.lower() or "token" in k.lower() else v)
                 for k, v in args.items()}

    is_success = "error" not in str(result)
    description = (
        f"tool={tool_name} tool_id={tool_id} "
        f"task={context.get('task_id', '?')} "
        f"agent={context.get('agent_type', '?')} "
        f"args={json.dumps(safe_args, default=str)[:500]} "
        f"success={is_success}"
    )

    logger.info(f"TOOL_EXEC: mission={context.get('mission_id', '?')} {description}")

    # Persist to ActivityLog asynchronously (best-effort, never blocks tool execution)
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_persist_tool_activity(context, tool_name, tool_id, description))
    except RuntimeError:
        pass  # No event loop — running outside async context (e.g. tests)


async def _persist_tool_activity(context: dict, tool_name: str, tool_id: str, description: str):
    """Persist a TOOL_EXECUTED activity to the database."""
    try:
        from backend.core.database import AsyncSessionLocal
        from backend.services.activity_service import log_activity
        from backend.models.project import ActivityType

        org_id = context.get("organization_id")
        if not org_id:
            return  # Cannot persist without org scope

        async with AsyncSessionLocal() as db:
            await log_activity(
                db=db,
                organization_id=org_id,
                activity_type=ActivityType.TOOL_EXECUTED,
                title=f"Custom tool executed: {tool_name}",
                description=description,
                project_id=None,  # Tools are org-scoped, not project-scoped
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"Failed to persist tool execution activity: {e}")


# ── Startup loader ────────────────────────────────────────────────────────

async def load_custom_tools_on_startup():
    """
    Called during server lifespan startup.
    Loads all enabled CustomTool records from the database and registers
    them into the in-memory ToolRegistry.
    Disabled/deleted tools are NOT loaded.
    """
    try:
        from backend.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            stmt = select(CustomTool).where(CustomTool.enabled == True)
            res = await db.execute(stmt)
            tools = res.scalars().all()

            loaded = 0
            for tool in tools:
                _register_custom_tool_in_memory(tool)
                loaded += 1

            logger.info(f"Startup: loaded {loaded} custom tools into ToolRegistry")
            return loaded
    except Exception as e:
        logger.warning(f"Startup: failed to load custom tools: {e}")
        return 0

