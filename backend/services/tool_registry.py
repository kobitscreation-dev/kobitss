import os
import subprocess
from typing import Dict, Any, Callable, List
import enum
import json
import logging

class RiskLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ToolDefinition:
    def __init__(self, name: str, description: str, risk_level: RiskLevel, input_schema: Dict[str, Any], handler: Callable = None):
        self.name = name
        self.description = description
        self.risk_level = risk_level
        self.input_schema = input_schema
        self.handler = handler

    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "risk_level": self.risk_level.value,
            "input_schema": self.input_schema
        }
    
    async def execute(self, context: dict = None, **kwargs) -> Any:
        if self.handler:
            if context is not None:
                return await self.handler(context=context, **kwargs)
            return await self.handler(**kwargs)
        return {"error": f"No handler implemented for tool {self.name}"}


class ToolRegistry:
    """Central registry of all tools available to agents."""
    
    _tools: Dict[str, ToolDefinition] = {}

    @classmethod
    def register(cls, tool: ToolDefinition):
        cls._tools[tool.name] = tool

    @classmethod
    def get_tool(cls, name: str) -> ToolDefinition:
        return cls._tools.get(name)

    @classmethod
    def get_all_tools(cls) -> List[ToolDefinition]:
        return list(cls._tools.values())

    @classmethod
    def unregister(cls, name: str):
        """Remove a tool from the registry (used for cache invalidation on delete/disable)."""
        cls._tools.pop(name, None)
        
    @classmethod
    async def execute_tool(cls, name: str, args: dict, context: dict = None) -> Any:
        tool = cls.get_tool(name)
        if not tool:
            # Fallback to normalized match (e.g. repository_write -> repository.write)
            norm = name.replace(".", "").replace("_", "").lower()
            for t in cls.get_all_tools():
                if t.name.replace(".", "").replace("_", "").lower() == norm:
                    tool = t
                    break
                    
        if not tool:
            return {"error": f"Tool {name} not found"}
        return await tool.execute(context=context, **args)


# ── Tool Implementations ──

async def handle_repository_read(
    path: str,
    start_line: int = None,
    end_line: int = None,
    context: dict = None,
) -> dict:
    """Reads a file (or line range) from the repository inside a safe sandbox path."""
    try:
        if context and context.get("sandbox_session_id"):
            from backend.services.sandbox_manager import SandboxManager
            return SandboxManager.read_file(
                context["sandbox_session_id"],
                path,
                start_line=start_line,
                end_line=end_line,
            )
            
        # Fallback if no sandbox (should not happen in real execution)
        if not os.path.exists(path):
            return {"error": f"File not found: {path}"}
        if os.path.isdir(path):
            files = os.listdir(path)
            return {"type": "directory", "files": files}
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"type": "file", "content": content}
    except Exception as e:
        return {"error": str(e)}

async def handle_repository_write(path: str, content: str, context: dict = None) -> dict:
    """Writes a file to the repository safely using sandbox."""
    try:
        if context and context.get("sandbox_session_id"):
            from backend.services.sandbox_manager import SandboxManager
            return SandboxManager.write_file(context["sandbox_session_id"], path, content)
            
        return {"error": "No sandbox context provided."}
    except Exception as e:
        return {"error": str(e)}

async def handle_repository_edit(
    path: str,
    old_string: str = "",
    new_string: str = "",
    replace_all: bool = False,
    edits: list = None,
    context: dict = None,
) -> dict:
    """Surgically patches an existing file inside the sandbox (single or atomic multi-chunk)."""
    try:
        if context and context.get("sandbox_session_id"):
            from backend.services.sandbox_manager import SandboxManager
            return SandboxManager.edit_file(
                context["sandbox_session_id"],
                path,
                old_string=old_string,
                new_string=new_string,
                replace_all=bool(replace_all),
                edits=edits,
            )
        return {"error": "No sandbox context provided."}
    except Exception as e:
        return {"error": str(e)}

async def handle_repository_delete(path: str, context: dict = None) -> dict:
    """Safely deletes a file inside the sandboxed repository."""
    try:
        if context and context.get("sandbox_session_id"):
            from backend.services.sandbox_manager import SandboxManager
            return SandboxManager.delete_file(context["sandbox_session_id"], path)
        return {"error": "No sandbox context provided."}
    except Exception as e:
        return {"error": str(e)}

async def handle_tests_run(test_command: str, context: dict = None) -> dict:
    """Runs a test command inside the isolated Kyros container / MicroVM / virtual-rootfs sandbox."""
    try:
        if context and context.get("sandbox_session_id"):
            from backend.services.sandbox_manager import SandboxManager
            return SandboxManager.run_command(context["sandbox_session_id"], test_command, timeout=60)
        if context and context.get("sandbox_dir") and os.path.isdir(context["sandbox_dir"]):
            from backend.services.environment_bootstrapper import EnvironmentBootstrapper
            return EnvironmentBootstrapper.execute_isolated(
                sandbox_dir=context["sandbox_dir"],
                command=test_command,
                timeout=60,
            )
        return {"error": "No sandbox context provided."}
    except Exception as e:
        return {"error": str(e)}

async def handle_database_migrate(migration_script: str) -> dict:
    """Applies a database migration. Mocked for safety."""
    return {"success": True, "message": "Migration simulated successfully. Schema updated."}

async def handle_deployment_preview(**kwargs) -> dict:
    """Spins up a preview deployment URL."""
    return {"success": True, "preview_url": "https://preview-deploy.kobits.dev/pr-123", "message": "Preview environment is live."}

async def handle_deployment_deploy(environment: str, **kwargs) -> dict:
    """Deploys to environment. Mocked for safety."""
    return {"success": True, "message": f"Successfully deployed to {environment}."}

async def handle_deployment_rollback(environment: str) -> dict:
    """Rolls back the deployment."""
    return {"success": True, "message": f"Successfully rolled back {environment} to the previous stable release."}

async def handle_terminal_execute(command: str, timeout: int = 60, context: dict = None) -> dict:
    """Executes a shell command inside the isolated Kyros container / MicroVM / virtual-rootfs sandbox."""
    try:
        if context and context.get("sandbox_session_id"):
            from backend.services.sandbox_manager import SandboxManager
            return SandboxManager.run_command(context["sandbox_session_id"], command, timeout=timeout)
        if context and context.get("sandbox_dir") and os.path.isdir(context["sandbox_dir"]):
            from backend.services.environment_bootstrapper import EnvironmentBootstrapper
            return EnvironmentBootstrapper.execute_isolated(
                sandbox_dir=context["sandbox_dir"],
                command=command,
                timeout=timeout,
            )
        return {"error": "No sandbox context provided."}
    except Exception as e:
        return {"error": str(e)}

async def handle_sandbox_exec(command: str, timeout: int = 120, context: dict = None) -> dict:
    """First-class Kyros MicroVM / Container shell execution tool (`sandbox.exec`)."""
    return await handle_terminal_execute(command=command, timeout=timeout, context=context)

async def handle_git_run(command: str, context: dict = None) -> dict:
    """Runs a git command in the repository."""
    if not command.startswith("git "):
        command = f"git {command}"
    if context and context.get("sandbox_dir"):
        from backend.services.sandbox_manager import _run_git
        cmd_args = command.split(" ")[1:]
        out = _run_git(context["sandbox_dir"], *cmd_args)
        return {"stdout": out, "exit_code": 0}
    return await handle_terminal_execute(command, context=context)

async def handle_browser_navigate(url: str) -> dict:
    """Mocks browser navigation or fetch."""
    try:
        # In a real system, we'd use Playwright or Puppeteer
        # Here we just use a simple curl/request representation
        import urllib.request
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8')
            return {"status": response.status, "content": html[:5000] + "...(truncated)" if len(html)>5000 else html}
    except Exception as e:
        return {"error": str(e)}

async def handle_database_query(query: str, connection_string: str = None) -> dict:
    """Runs a SQL query against the database (mocked/sandboxed)."""
    # In a real app this would execute safely against a test DB
    return {"success": True, "columns": ["id", "result"], "rows": [[1, "Mocked result for query: " + query[:50]]]}

async def handle_github_create_pr(title: str, body: str, head_branch: str, base_branch: str, context: dict = None) -> dict:
    """Create a Pull Request in GitHub."""
    import os
    is_dev = os.environ.get("KOBITS_DEV_MODE", "0").lower() in ("1", "true", "yes")
    if not is_dev:
        import urllib.request
        import json
        token = os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPO", "kobits-org/sandbox-repo") # Example repo
        if not token:
            return {"success": False, "error": "GITHUB_TOKEN environment variable is not set."}
        
        url = f"https://api.github.com/repos/{repo}/pulls"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }
        data = json.dumps({
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch
        }).encode('utf-8')
        
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode('utf-8'))
                return {
                    "success": True,
                    "pr_url": result.get("html_url"),
                    "pr_number": result.get("number"),
                    "message": f"Successfully created PR '{title}' from {head_branch} to {base_branch}."
                }
        except Exception as e:
            error_text = str(e)
            if hasattr(e, 'read'):
                error_text += " " + e.read().decode('utf-8')
            return {"success": False, "error": error_text}
            
    # Mock mode
    import uuid
    pr_id = str(uuid.uuid4())[:8]
    return {
        "success": True,
        "pr_url": f"https://github.com/kobits-org/sandbox-repo/pull/{pr_id}",
        "message": f"Successfully created PR '{title}' from {head_branch} to {base_branch}."
    }

# ── Register New Tools ──

ToolRegistry.register(ToolDefinition(
    name="github.create_pr",
    description="Create a Pull Request in GitHub to merge your sandboxed branch into the main branch.",
    risk_level=RiskLevel.MEDIUM,
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string", "description": "Markdown description of the changes"},
            "head_branch": {"type": "string"},
            "base_branch": {"type": "string", "default": "main"}
        },
        "required": ["title", "body", "head_branch"]
    },
    handler=handle_github_create_pr
))

ToolRegistry.register(ToolDefinition(
    name="terminal.execute",
    description="Execute a bash/shell command inside the isolated Kyros container / MicroVM sandbox. Useful for installing packages (pip, npm, apt-get), running scripts, and verifying builds.",
    risk_level=RiskLevel.MEDIUM,
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "timeout": {"type": "integer", "description": "Optional timeout in seconds", "default": 60},
        },
        "required": ["command"]
    },
    handler=handle_terminal_execute
))

ToolRegistry.register(ToolDefinition(
    name="sandbox.exec",
    description="Execute an arbitrary Linux/shell command inside the isolated per-sprint container / MicroVM / virtual-rootfs sandbox.",
    risk_level=RiskLevel.MEDIUM,
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute inside the isolated VM"},
            "timeout": {"type": "integer", "description": "Optional timeout in seconds", "default": 120},
        },
        "required": ["command"]
    },
    handler=handle_sandbox_exec
))

ToolRegistry.register(ToolDefinition(
    name="git.run",
    description="Execute a git command (e.g. 'git status', 'git checkout -b feature', 'git commit').",
    risk_level=RiskLevel.LOW,
    input_schema={
        "type": "object",
        "properties": {"command": {"type": "string", "description": "The git command (with or without 'git ' prefix)"}},
        "required": ["command"]
    },
    handler=handle_git_run
))

ToolRegistry.register(ToolDefinition(
    name="browser.navigate",
    description="Navigate to a URL to scrape HTML content or read documentation.",
    risk_level=RiskLevel.LOW,
    input_schema={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The URL to navigate to"}},
        "required": ["url"]
    },
    handler=handle_browser_navigate
))

ToolRegistry.register(ToolDefinition(
    name="database.query",
    description="Execute a raw SQL query against the connected database to inspect schema or data.",
    risk_level=RiskLevel.MEDIUM,
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The SQL query to execute"},
            "connection_string": {"type": "string", "description": "Optional specific DB connection string"}
        },
        "required": ["query"]
    },
    handler=handle_database_query
))

# ── Register Existing Core Tools ──

ToolRegistry.register(ToolDefinition(
    name="repository.read",
    description="Read files or directories from the project repository. Optionally specify 1-indexed start_line and end_line to read a surgical line slice of a large file.",
    risk_level=RiskLevel.LOW,
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file or directory"},
            "start_line": {"type": "integer", "description": "Optional 1-indexed start line for reading a specific slice of a file"},
            "end_line": {"type": "integer", "description": "Optional 1-indexed inclusive end line for reading a specific slice of a file"},
        },
        "required": ["path"]
    },
    handler=handle_repository_read
))

ToolRegistry.register(ToolDefinition(
    name="repository.write",
    description="Write a brand-new file (or full file replacement) to the sandboxed branch. Prefer repository.edit when modifying existing files.",
    risk_level=RiskLevel.MEDIUM,
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to write to"}, 
            "content": {"type": "string", "description": "Full content of the file"}
        },
        "required": ["path", "content"]
    },
    handler=handle_repository_write
))

ToolRegistry.register(ToolDefinition(
    name="repository.edit",
    description="Surgically edit an existing file in the sandboxed branch by replacing an exact `old_string` block with `new_string` (or passing an atomic `edits` list of multiple non-contiguous chunks). Performs 3-tier matching (exact, trailing-whitespace-insensitive, and relative-indentation-aware) and instant AST syntax verification.",
    risk_level=RiskLevel.MEDIUM,
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the existing file to modify"},
            "old_string": {"type": "string", "description": "Exact contiguous block of existing lines to replace (include 2-3 surrounding context lines for uniqueness)"},
            "new_string": {"type": "string", "description": "New replacement lines to insert in place of old_string"},
            "replace_all": {"type": "boolean", "description": "Set true only to replace all occurrences of old_string in the file", "default": False},
            "edits": {
                "type": "array",
                "description": "Optional list of multiple surgical edits [{old_string, new_string, replace_all}] to apply atomically to the same file",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean", "default": False},
                    },
                    "required": ["old_string", "new_string"],
                },
            },
        },
        "required": ["path"]
    },
    handler=handle_repository_edit
))

ToolRegistry.register(ToolDefinition(
    name="repository.delete",
    description="Delete an obsolete or temporary file from the sandboxed repository branch.",
    risk_level=RiskLevel.MEDIUM,
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file to delete inside the sandbox"},
        },
        "required": ["path"]
    },
    handler=handle_repository_delete
))

ToolRegistry.register(ToolDefinition(
    name="tests.run",
    description="Run automated tests.",
    risk_level=RiskLevel.LOW,
    input_schema={
        "type": "object",
        "properties": {"test_command": {"type": "string", "description": "Terminal command to run tests (e.g. pytest)"}},
        "required": ["test_command"]
    },
    handler=handle_tests_run
))

ToolRegistry.register(ToolDefinition(
    name="database.migrate",
    description="Apply database schema migrations. Modifies live database.",
    risk_level=RiskLevel.HIGH,
    input_schema={
        "type": "object",
        "properties": {"migration_script": {"type": "string"}},
        "required": ["migration_script"]
    },
    handler=handle_database_migrate
))

ToolRegistry.register(ToolDefinition(
    name="deployment.preview",
    description="Generate a preview deployment link for the current codebase.",
    risk_level=RiskLevel.LOW,
    input_schema={
        "type": "object",
        "properties": {},
        "required": []
    },
    handler=handle_deployment_preview
))

ToolRegistry.register(ToolDefinition(
    name="deployment.deploy",
    description="Deploy the application to the production environment.",
    risk_level=RiskLevel.CRITICAL,
    input_schema={
        "type": "object",
        "properties": {"environment": {"type": "string"}},
        "required": ["environment"]
    },
    handler=handle_deployment_deploy
))

ToolRegistry.register(ToolDefinition(
    name="deployment.rollback",
    description="Roll back a failed deployment to the previous stable state.",
    risk_level=RiskLevel.HIGH,
    input_schema={
        "type": "object",
        "properties": {"environment": {"type": "string"}},
        "required": ["environment"]
    },
    handler=handle_deployment_rollback
))
async def handle_repository_search(
    query: str,
    project_id: str = None,
    file_pattern: str = None,
    exclude_pattern: str = None,
    is_regex: bool = None,
    case_sensitive: bool = None,
    whole_word: bool = False,
    multiline: bool = False,
    context_lines: int = 2,
    before_context: int = None,
    after_context: int = None,
    max_results: int = 50,
    context: dict = None,
) -> dict:
    resolved_project_id = project_id or (context.get("project_id") if context else None)

    # 1. Live sandbox ripgrep-style + semantic token window search
    sandbox_res = None
    if context and context.get("sandbox_session_id"):
        from backend.services.sandbox_manager import SandboxManager
        sandbox_res = SandboxManager.search_files(
            context["sandbox_session_id"],
            query=query,
            file_pattern=file_pattern,
            exclude_pattern=exclude_pattern,
            is_regex=is_regex,
            case_sensitive=case_sensitive,
            whole_word=bool(whole_word),
            multiline=bool(multiline),
            context_lines=context_lines,
            before_context=before_context,
            after_context=after_context,
            max_results=max_results,
        )
    elif context and context.get("sandbox_dir") and os.path.isdir(context["sandbox_dir"]):
        from backend.services.sandbox_manager import SandboxManager
        sandbox_res = SandboxManager.search_directory(
            context["sandbox_dir"],
            query=query,
            file_pattern=file_pattern,
            exclude_pattern=exclude_pattern,
            is_regex=is_regex,
            case_sensitive=case_sensitive,
            whole_word=bool(whole_word),
            multiline=bool(multiline),
            context_lines=context_lines,
            before_context=before_context,
            after_context=after_context,
            max_results=max_results,
        )

    # 2. Supplement with AST-chunked semantic RAG results if project_id is known
    semantic_results = []
    if resolved_project_id:
        try:
            from backend.services.memory_service import MemoryService
            from backend.core.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                semantic_results = await MemoryService.search_codebase(
                    db, resolved_project_id, query, limit=5
                )
        except Exception:
            semantic_results = []

    if sandbox_res is not None:
        if semantic_results:
            sandbox_res["semantic_chunks"] = semantic_results
        return sandbox_res

    return {"results": semantic_results, "match_mode": "semantic_rag"}

async def handle_memory_read(project_id: str = None, context: dict = None, **kwargs) -> dict:
    from backend.services.memory_service import MemoryService
    from backend.core.database import AsyncSessionLocal
    resolved_project_id = project_id or (context.get("project_id") if context else "default")
    async with AsyncSessionLocal() as db:
        arch_ctx = await MemoryService.get_architecture_context(db, resolved_project_id)
    return {"context": arch_ctx}

async def handle_memory_write(category: str, content: str, project_id: str = None, context: dict = None) -> dict:
    from backend.services.memory_service import MemoryService
    from backend.core.database import AsyncSessionLocal
    resolved_project_id = project_id or (context.get("project_id") if context else "default")
    async with AsyncSessionLocal() as db:
        memory = await MemoryService.add_project_memory(db, resolved_project_id, category, content)
    return {"success": True, "memory_id": memory.id}

ToolRegistry.register(ToolDefinition(
    name="repository.search",
    description=(
        "Search the codebase using Ripgrep-grade regular expressions (`is_regex`), smart-case (`case_sensitive`), "
        "brace/negated file globs (`file_pattern`, `exclude_pattern`), whole-word (`whole_word`), multiline (`multiline`), "
        "or natural-language/symbol queries. Returns `> line | code` context snippets, column numbers, enclosing AST symbols, "
        "and AST semantic chunks."
    ),
    risk_level=RiskLevel.LOW,
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Regex pattern, identifier, or natural-language search query"},
            "file_pattern": {"type": "string", "description": "Optional glob filter (e.g. '*.py', 'src/**/*.ts', '*.{js,ts},!*.test.ts')"},
            "exclude_pattern": {"type": "string", "description": "Optional glob pattern(s) to exclude (e.g. '*.spec.ts,tests/**')"},
            "is_regex": {"type": "boolean", "description": "Whether to treat query as a regular expression (auto-detected if omitted)"},
            "case_sensitive": {"type": "boolean", "description": "True for strict case-sensitive, False for case-insensitive, omit/null for Ripgrep Smart-Case (case-sensitive if query has uppercase, with automatic case-insensitive fallback)"},
            "whole_word": {"type": "boolean", "description": "Match only whole words (`-w`)", "default": False},
            "multiline": {"type": "boolean", "description": "Allow regex patterns to match across multiple lines (`-U`)", "default": False},
            "context_lines": {"type": "integer", "description": "Number of context lines before and after each match (`-C`, default 2)", "default": 2},
            "before_context": {"type": "integer", "description": "Optional number of lines before each match (`-B`)"},
            "after_context": {"type": "integer", "description": "Optional number of lines after each match (`-A`)"},
            "max_results": {"type": "integer", "description": "Maximum number of matches to return (default 50)", "default": 50},
            "project_id": {"type": "string", "description": "Optional project ID"},
        },
        "required": ["query"]
    },
    handler=handle_repository_search
))

ToolRegistry.register(ToolDefinition(
    name="memory.read",
    description="Read the overarching architectural guidelines and past decisions for the project.",
    risk_level=RiskLevel.LOW,
    input_schema={
        "type": "object",
        "properties": {"project_id": {"type": "string"}},
        "required": ["project_id"]
    },
    handler=handle_memory_read
))

ToolRegistry.register(ToolDefinition(
    name="memory.write",
    description="Save a new architectural decision, pattern, or constraint to the project's long-term memory.",
    risk_level=RiskLevel.MEDIUM,
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "category": {"type": "string", "description": "e.g., ARCHITECTURE, UI_UX, DATABASE, DEPLOYMENT"},
            "content": {"type": "string", "description": "Detailed explanation of the decision"}
        },
        "required": ["project_id", "category", "content"]
    },
    handler=handle_memory_write
))
# --- Agent Intelligence Upgrade Tools ---

ToolRegistry.register(ToolDefinition(
    name="intelligence.store_agent_memory",
    risk_level=RiskLevel.LOW,
    description="Store a private memory for the current agent (e.g. lessons, conventions, recurring mistakes).",
    input_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "e.g., 'lesson', 'convention'"},
            "key": {"type": "string"},
            "value": {"type": "string", "description": "Concise structured summary. NO SECRETS."}
        },
        "required": ["category", "key", "value"]
    },
    handler=lambda kwargs, ctx: f"Stored agent memory {kwargs.get('key')}" # Actually handled by agent executor intercept
))

ToolRegistry.register(ToolDefinition(
    name="intelligence.publish_project_knowledge",
    risk_level=RiskLevel.LOW,
    description="Publish a convention or architectural knowledge to the shared project bus.",
    input_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "key": {"type": "string"},
            "value": {"type": "string"},
            "confidence": {"type": "number", "default": 1.0},
            "evidence": {"type": "string"}
        },
        "required": ["category", "key", "value"]
    },
    handler=lambda kwargs, ctx: f"Published project knowledge {kwargs.get('key')}" 
))

ToolRegistry.register(ToolDefinition(
    name="intelligence.send_message",
    risk_level=RiskLevel.LOW,
    description="Send a structured message to another agent or broadcast.",
    input_schema={
        "type": "object",
        "properties": {
            "receiver": {"type": "string", "description": "Name of receiver agent, or empty for broadcast"},
            "message_type": {"type": "string", "description": "REQUEST, INFORMATION, DECISION, WARNING, FINDING, RECOMMENDATION, HANDOFF, BLOCKER, REVIEW, CORRECTION, COMPLETION"},
            "content": {"type": "string"}
        },
        "required": ["message_type", "content"]
    },
    handler=lambda kwargs, ctx: f"Message sent" 
))

ToolRegistry.register(ToolDefinition(
    name="intelligence.publish_artifact",
    risk_level=RiskLevel.LOW,
    description="Publish an artifact pointer (not huge raw code) to downstream consumers.",
    input_schema={
        "type": "object",
        "properties": {
            "consumers": {"type": "array", "items": {"type": "string"}, "description": "List of downstream agent names"},
            "type": {"type": "string"},
            "content": {"type": "string", "description": "Path, reference, or small summary"},
            "summary": {"type": "string"}
        },
        "required": ["consumers", "type", "content", "summary"]
    },
    handler=lambda kwargs, ctx: f"Artifact published" 
))

ToolRegistry.register(ToolDefinition(
    name="intelligence.record_decision",
    risk_level=RiskLevel.LOW,
    description="Record a significant project decision.",
    input_schema={
        "type": "object",
        "properties": {
            "decision": {"type": "string"},
            "reason": {"type": "string"},
            "evidence": {"type": "string"},
            "alternatives": {"type": "string"}
        },
        "required": ["decision", "reason"]
    },
    handler=lambda kwargs, ctx: f"Decision recorded" 
))

ToolRegistry.register(ToolDefinition(
    name="intelligence.record_lesson",
    risk_level=RiskLevel.LOW,
    description="Record a failure or review rejection lesson.",
    input_schema={
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["FAILURE", "REVIEW_REJECTION"]},
            "cause": {"type": "string"},
            "lesson": {"type": "string"}
        },
        "required": ["type", "cause", "lesson"]
    },
    handler=lambda kwargs, ctx: f"Lesson recorded" 
))

