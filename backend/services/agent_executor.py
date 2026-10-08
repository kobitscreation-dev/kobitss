from backend.services.memory_service import store_agent_memory, publish_project_knowledge, send_agent_message, publish_artifact, record_decision, record_lesson
from backend.models.communication import MessageType, AgentLessonType
import os
import json
import uuid
import asyncio
from datetime import datetime
from backend.models.agent import AgentRun, AgentType, AgentRunStatus
from backend.services.agent_registry import AGENT_REGISTRY
from backend.services.tool_registry import ToolRegistry
from backend.services.llm import get_llm_provider
from backend.services.intelligence.consensus_engine import ConsensusEngine, CODING_AGENT_TYPES

class AgentExecutor:
    """The actual Multi-Agent Execution Engine Loop."""
    
    def __init__(self, provider_scenarios=None, agent_type: AgentType = AgentType.BACKEND_ENGINEER, allowed_tools=None, db=None):
        self.provider_scenarios = provider_scenarios
        self.provider = get_llm_provider(scenarios=provider_scenarios)
        self._default_provider = self.provider
        self.agent_type = agent_type
        self.allowed_tools = allowed_tools or []
        self.db = db
        self.mission_id = None
        self.project_id = None
        self.trace_logs = []

    async def _inject_mid_turn_steering(
        self,
        result,
        context: dict,
        db,
        agent_type: AgentType,
        agent_run_id: str,
        trace_logs: list,
    ):
        """Poll SQLite for pending HUMAN_STEERING_OVERRIDE tasks and inject into the active LLM tool turn."""
        miss_id = (context or {}).get("mission_id") or self.mission_id
        active_db = db or self.db
        if active_db is None or not miss_id:
            return result
        try:
            from sqlalchemy import select
            from backend.models.project import Task, TaskStatus
            steer_rows = (
                await active_db.execute(
                    select(Task).where(
                        Task.mission_id == miss_id,
                        Task.is_correction == True,
                        Task.status == TaskStatus.PENDING,
                    )
                )
            ).scalars().all()
            injected_directives = []
            for st in steer_rows:
                desc = (st.description or "").strip()
                try:
                    s_meta = json.loads(st.metadata_json or "{}")
                except Exception:
                    s_meta = {}
                is_steer = desc.startswith("HUMAN_STEERING_OVERRIDE:") or s_meta.get("source") in ("steer", "kobits_cli")
                if is_steer and not s_meta.get("injected_mid_turn"):
                    clean_dir = desc.replace("HUMAN_STEERING_OVERRIDE:", "", 1).strip() or st.title
                    injected_directives.append(clean_dir)
                    s_meta["injected_mid_turn"] = True
                    s_meta["injected_into_run_id"] = agent_run_id
                    s_meta["injected_at"] = datetime.now().isoformat()
                    st.metadata_json = json.dumps(s_meta)
                    if agent_type in CODING_AGENT_TYPES or agent_type in (AgentType.SOLUTION_ARCHITECT, AgentType.TECHNICAL_LEAD):
                        st.status = TaskStatus.COMPLETED
            if injected_directives:
                await active_db.commit()
                joined_dir = " | ".join(injected_directives)
                if isinstance(result, dict):
                    result = dict(result)
                else:
                    result = {"output": result}
                result["_LIVE_HUMAN_STEERING_DIRECTIVE"] = (
                    f"URGENT MID-FLIGHT STEERING FROM OPERATOR: {joined_dir}. "
                    "You MUST immediately adapt your current implementation and subsequent tool calls to obey this directive."
                )
                entry = {
                    "type": "mid_turn_steering_injected",
                    "action": "mid_turn_steering_injected",
                    "directives": injected_directives,
                    "timestamp": datetime.now().isoformat(),
                }
                trace_logs.append(entry)
                if trace_logs is not self.trace_logs:
                    self.trace_logs.append(entry)
                try:
                    from backend.services.websocket_manager import manager
                    await manager.broadcast(miss_id, {
                        "type": "live_steering_injected",
                        "mission_id": miss_id,
                        "agent_run_id": agent_run_id,
                        "directives": injected_directives,
                    })
                except Exception:
                    pass
        except Exception as steer_err:
            if os.environ.get("KOBITS_VERBOSE") == "1":
                print(f"[AgentExecutor] Mid-turn steering check error: {steer_err}")
        return result

    async def execute_tool(self, name: str, args: dict, context: dict = None) -> dict:
        """Execute a tool and inject any pending mid-turn human steering directives."""
        ctx = dict(context or {})
        if self.mission_id and "mission_id" not in ctx:
            ctx["mission_id"] = self.mission_id
        if self.project_id and "project_id" not in ctx:
            ctx["project_id"] = self.project_id
        res = await ToolRegistry.execute_tool(name, args, ctx)
        res = await self._inject_mid_turn_steering(
            res,
            ctx,
            self.db,
            self.agent_type or AgentType.BACKEND_ENGINEER,
            "direct_run",
            self.trace_logs,
        )
        return res

    async def execute_run(self, agent_run: AgentRun, agent_type: AgentType, input_data: dict, db=None) -> dict:
        if self.provider_scenarios is None and self.provider is self._default_provider:
            self.provider = get_llm_provider()
            self._default_provider = self.provider
        agent_def = AGENT_REGISTRY[agent_type]
        from backend.core.config import settings
        from backend.services.intelligence.adaptive_planning import AdaptivePlanner
        
        # INTELLIGENCE LAYER: Model Routing
        planner = AdaptivePlanner(db) if db else None
        risk_level = input_data.get("risk_profile", "LOW")
        complexity = input_data.get("complexity", "DEFAULT")
        task_category = input_data.get("task_category", "general_engineering")
        model_tier = await planner.route_model(task_category, complexity, risk_level) if planner else "default"
        
        provider_name = settings.LLM_PROVIDER.lower() if settings.LLM_PROVIDER else (os.environ.get("LLM_PROVIDER") or "mock").lower()
        if provider_name in ["bedrock", "aws", "aws_bedrock"]:
            model_name = os.environ.get("BEDROCK_MODEL") or settings.BEDROCK_MODEL or "global.anthropic.claude-sonnet-4-6"
        elif provider_name == "anthropic":
            model_name = os.environ.get("ANTHROPIC_MODEL", settings.ANTHROPIC_MODEL)
        elif provider_name == "deepseek":
            cfg_ds_model = os.environ.get("DEEPSEEK_MODEL") or settings.DEEPSEEK_MODEL or "deepseek-v4.1-flash:free"
            model_name = "deepseek-v4.1-flash:free" if cfg_ds_model == "deepseek-flash" else cfg_ds_model
        elif provider_name == "gemini":
            model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro") if model_tier == "default" else f"gemini-{model_tier}"
        else:
            model_name = f"mock-model-{model_tier}"
        
        agent_run.model = model_name
        agent_run.status = "RUNNING"
        
        adv_context = input_data.pop("advanced_intelligence_context", "")
        user_prompt = f"{adv_context}\n\nTask Input:\n{json.dumps(input_data, indent=2)}\n\nPlease execute the task and return the structured response."
        
        allowed_tool_names = agent_def.allowed_tools
        tools = []
        for t_name in allowed_tool_names:
            if provider_name != "mock" and (t_name.startswith("intelligence.") or t_name.startswith("memory.")):
                continue
            t_def = ToolRegistry.get_tool(t_name)
            if t_def:
                tools.append({
                    "name": t_def.name.replace(".", "_"),
                    "description": t_def.description,
                    "input_schema": t_def.input_schema
                })

        # Standard Output Schema definition (for Anthropic structured output / function calling or JSON mode)
        artifacts_schema = {"type": "object"}
        if agent_type == AgentType.TECHNICAL_LEAD:
            artifacts_schema = {
                "type": "object",
                "required": ["task_decomposition"],
                "properties": {
                    "task_decomposition": {
                        "type": "array",
                        "description": "Non-empty list of concrete implementation tasks scoped strictly to touched domains.",
                        "items": {
                            "type": "object",
                            "required": ["title", "agent", "description"],
                            "properties": {
                                "title": {"type": "string", "description": "Short task title (min 3 chars)"},
                                "agent": {
                                    "type": "string",
                                    "enum": [
                                        "BACKEND_ENGINEER",
                                        "FRONTEND_ENGINEER",
                                        "DATABASE_ENGINEER",
                                        "AI_ML_ENGINEER",
                                        "DEVOPS_ENGINEER",
                                        "QA_ENGINEER",
                                    ],
                                },
                                "description": {"type": "string", "description": "Clear implementation instructions (min 15 chars)"},
                            },
                        },
                    }
                },
            }

        response_schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["SUCCESS", "FAILED"]},
                "summary": {"type": "string"},
                "findings": {"type": "array", "items": {"type": "string"}},
                "changes": {"type": "array", "items": {"type": "string"}},
                "recommendations": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
                "artifacts": artifacts_schema,
                "requires_approval": {"type": "boolean"}
            }
        }
        
        system_prompt = f"You are a Kobits {agent_type.value} Agent. \n{agent_def.system_prompt}\nYou MUST output your final answer as a JSON object matching the provided schema."
        scope_info = input_data.get("scope_triage") or {}
        if agent_type == AgentType.TECHNICAL_LEAD:
            scope_domains = scope_info.get("domains") or ["backend"]
            scope_complexity = scope_info.get("complexity") or "standard"
            max_tasks_hint = "1 single combined task" if scope_complexity in ("trivial", "standard") else "1 to 2 tasks"
            system_prompt += (
                "\n\nCRITICAL REQUIREMENT FOR TECHNICAL_LEAD:\n"
                '1. Your JSON response MUST include `"artifacts": {"task_decomposition": [{"title": "...", "agent": "BACKEND_ENGINEER", "description": "..."}]}`.\n'
                f"2. SCOPE TRIAGE: Touched domains are {scope_domains}, complexity is '{scope_complexity.upper()}'. Create {max_tasks_hint} (if upstream planning/database tasks already created the files in the sandbox, create 1 verification/completion task for BACKEND_ENGINEER).\n"
                "3. SELECTIVE ROUTING: Only assign agents matching the touched domains (BACKEND_ENGINEER for backend/API, FRONTEND_ENGINEER for UI/JS/CSS, DATABASE_ENGINEER for schema/SQL, AI_ML_ENGINEER for LLM/RAG, DEVOPS_ENGINEER for CI/Docker).\n"
                "4. DO NOT create separate QA/unit-test tasks in task_decomposition because QA_ENGINEER, SECURITY_ENGINEER, and CODE_REVIEWER already run automatically in the next pipeline phases."
            )
        elif agent_type in (AgentType.QA_ENGINEER, AgentType.SECURITY_ENGINEER, AgentType.CODE_REVIEWER):
            t_files = scope_info.get("target_files") or []
            system_prompt += (
                f"\n\nREPOSITORY TARGET FILES (GROUNDED BY SCOPE TRIAGE): {t_files}\n"
                "Verify ONLY whether the mission objective has been implemented in the target file(s) using `repository_read`. "
                "Do NOT fail or reject the task over pre-existing unrelated code in the repository. "
                'If the mission objective is implemented, return `"status": "SUCCESS"` immediately.'
            )
        elif scope_info.get("target_files"):
            t_files = scope_info.get("target_files")
            system_prompt += (
                f"\n\nREPOSITORY TARGET FILES (GROUNDED BY SCOPE TRIAGE): {t_files}\n"
                "Use `repository_read` directly on the target file(s) above (you can pass `start_line` and `end_line` to read surgical slices of large files). "
                "When modifying an existing file, prefer `repository_edit` (single `old_string` -> `new_string` or atomic `edits` list) to save tokens and prevent truncation; "
                "check `syntax_valid` in the edit response. "
                "When building a new application, portal, or feature, create new files using `repository_write` (e.g. `index.html`, `styles.css`, `app.js`) and output your final JSON response."
            )
        else:
            system_prompt += (
                "\n\nNEW APPLICATION / GREENFIELD IMPLEMENTATION:\n"
                "When tasked with building or creating an application, website, portal, or tool, create complete, production-grade files using `repository_write` "
                "(e.g., `index.html`, `styles.css`, `app.js` or backend scripts). Write complete implementations cleanly and then return your final JSON response with status 'SUCCESS'."
            )
        system_prompt += "\n\nCOLLABORATION:\nYou are part of a multi-agent team. Your input data may contain 'upstream_artifacts' from agents who ran before you. Use this data to inform your work. When you finish, you can output structured JSON in your 'artifacts' field for downstream agents to use."
        system_prompt += "\n\nPRE-EXECUTION REASONING:\n\nBefore executing tools for major implementation work, you MUST first establish and output a clear plan including:\n1. Requirements\n2. Assumptions\n3. Constraints\n4. Dependencies\n5. Risks\n6. Expected files to modify\n7. Verification strategy\n"
        system_prompt += "\n\nANTI-HALLUCINATION:\nYou MUST NOT claim to have modified files or run commands unless you have successfully called the corresponding tools and received a success response."

        trace_logs = []

        trace_logs.append({
            "type": "context_builder",
            "metadata": {
                "sources_used": ["agent_memory", "project_memory", "mission_memory", "upstream_artifacts", "upstream_messages", "decisions", "lessons"],
                "number_of_memories": 5, # Mock value for benchmark
                "number_of_artifacts": len(input_data.get("upstream_artifacts", [])),
                "number_of_messages": len(input_data.get("upstream_messages", [])),
                "approximate_context_size": len(adv_context)
            },
            "timestamp": datetime.now().isoformat()
        })

        tool_fingerprints = []

        async def execute_tool(name: str, args: dict):
            mapped_name = name
            # Anti-Loop Protection
            fingerprint = f"{mapped_name}:{json.dumps(args, sort_keys=True)}"
            tool_fingerprints.append(fingerprint)
            if tool_fingerprints.count(fingerprint) >= 3:
                # Signal to the provider / executor to abort
                raise RuntimeError(f"AntiLoopException: Tool {mapped_name} called 3 times with identical arguments.")
                
            t_def = ToolRegistry.get_tool(name)
            if not t_def:
                norm = name.replace(".", "").replace("_", "").lower()
                for t in ToolRegistry.get_all_tools():
                    if t.name.replace(".", "").replace("_", "").lower() == norm:
                        mapped_name = t.name
                        t_def = t
                        break
                        
            if not t_def:
                return {"status": "error", "message": f"Tool {name} not found"}
                
            if t_def.name not in allowed_tool_names:
                return {"status": "error", "message": f"Tool {t_def.name} not allowed for {agent_type.value}"}
            
            context = {
                "task_id": agent_run.task_id,
                "mission_id": input_data.get("mission_id"),
                "project_id": agent_run.project_id or input_data.get("project_id"),
                "sandbox_dir": input_data.get("sandbox_dir"),
                "sandbox_session_id": input_data.get("sandbox_session_id"),
                # Trusted identity — set by AgentExecutor, not by tool-call args
                "agent_type": agent_type.value,
                "organization_id": input_data.get("organization_id"),
                "agent_run_id": agent_run.id,
            }
            
            trace_logs.append({
                "type": "tool_call",
                "name": mapped_name,
                "args": args,
                "timestamp": datetime.now().isoformat()
            })
            
            if mapped_name.startswith("intelligence.") and db is not None:
                try:
                    org_id = agent_run.organization_id
                    proj_id = agent_run.project_id
                    miss_id = input_data.get("mission_id")
                    t_id = agent_run.task_id
                    ag_id = agent_run.agent_id or agent_type.value
                    
                    if mapped_name == "intelligence.store_agent_memory":
                        await store_agent_memory(db, ag_id, org_id, proj_id, args["category"], args["key"], args["value"])
                        result = {"status": "success", "message": f"Stored agent memory {args['key']}"}
                    elif mapped_name == "intelligence.publish_project_knowledge":
                        await publish_project_knowledge(db, ag_id, org_id, proj_id, args["category"], args["key"], args["value"], args.get("confidence", 1.0), args.get("evidence"))
                        result = {"status": "success", "message": f"Published project knowledge {args['key']}"}
                    elif mapped_name == "intelligence.send_message":
                        await send_agent_message(db, org_id, miss_id, ag_id, args.get("receiver"), t_id, MessageType(args["message_type"]), args["content"])
                        result = {"status": "success", "message": "Message sent"}
                    elif mapped_name == "intelligence.publish_artifact":
                        await publish_artifact(db, org_id, miss_id, t_id, ag_id, args["consumers"], args["type"], args["content"], args["summary"])
                        result = {"status": "success", "message": "Artifact published"}
                    elif mapped_name == "intelligence.record_decision":
                        await record_decision(db, org_id, proj_id, miss_id, ag_id, args["decision"], args["reason"], args.get("evidence"), args.get("alternatives"))
                        result = {"status": "success", "message": "Decision recorded"}
                    elif mapped_name == "intelligence.record_lesson":
                        await record_lesson(db, org_id, proj_id, miss_id, ag_id, AgentLessonType(args["type"]), args["cause"], args["lesson"])
                        result = {"status": "success", "message": "Lesson recorded"}
                    else:
                        result = await ToolRegistry.execute_tool(mapped_name, args, context)
                except Exception as e:
                    result = {"status": "error", "message": str(e)}
            else:
                result = await ToolRegistry.execute_tool(mapped_name, args, context)

            # ── LIVE MID-TURN STEERING INJECTION (Kyros 1:1 Parity) ──────────
            miss_id = context.get("mission_id")
            if db is not None and miss_id:
                try:
                    from sqlalchemy import select
                    from backend.models.project import Task, TaskStatus
                    steer_rows = (
                        await db.execute(
                            select(Task).where(
                                Task.mission_id == miss_id,
                                Task.is_correction == True,
                                Task.status == TaskStatus.PENDING,
                            )
                        )
                    ).scalars().all()
                    injected_directives = []
                    for st in steer_rows:
                        desc = (st.description or "").strip()
                        try:
                            s_meta = json.loads(st.metadata_json or "{}")
                        except Exception:
                            s_meta = {}
                        is_steer = desc.startswith("HUMAN_STEERING_OVERRIDE:") or s_meta.get("source") in ("steer", "kobits_cli")
                        if is_steer and not s_meta.get("injected_mid_turn"):
                            clean_dir = desc.replace("HUMAN_STEERING_OVERRIDE:", "", 1).strip() or st.title
                            injected_directives.append(clean_dir)
                            s_meta["injected_mid_turn"] = True
                            s_meta["injected_into_run_id"] = agent_run.id
                            s_meta["injected_at"] = datetime.now().isoformat()
                            st.metadata_json = json.dumps(s_meta)
                            if agent_type in CODING_AGENT_TYPES or agent_type in (AgentType.SOLUTION_ARCHITECT, AgentType.TECHNICAL_LEAD):
                                st.status = TaskStatus.COMPLETED
                    if injected_directives:
                        await db.commit()
                        joined_dir = " | ".join(injected_directives)
                        if isinstance(result, dict):
                            result = dict(result)
                        else:
                            result = {"output": result}
                        result["_LIVE_HUMAN_STEERING_DIRECTIVE"] = (
                            f"URGENT MID-FLIGHT STEERING FROM OPERATOR: {joined_dir}. "
                            "You MUST immediately adapt your current implementation and subsequent tool calls to obey this directive."
                        )
                        trace_logs.append({
                            "type": "mid_turn_steering_injected",
                            "directives": injected_directives,
                            "timestamp": datetime.now().isoformat(),
                        })
                        try:
                            from backend.services.websocket_manager import manager
                            await manager.broadcast(miss_id, {
                                "type": "live_steering_injected",
                                "mission_id": miss_id,
                                "agent_run_id": agent_run.id,
                                "directives": injected_directives,
                            })
                        except Exception:
                            pass
                except Exception as steer_err:
                    if os.environ.get("KOBITS_VERBOSE") == "1":
                        print(f"[AgentExecutor] Mid-turn steering check error: {steer_err}")

            trace_logs.append({
                "type": "tool_result",
                "name": mapped_name,
                "result": result,
                "timestamp": datetime.now().isoformat()
            })
            return result
            
        # Proceed with execution
        MAX_CORRECTIVE_RETRIES = 1
        corrective_retries = 0
        result = None
        classification = None
        classification_reason = None
        
        # ── GAP 3: CRASH RECOVERY ──
        # Check if we already ran the Coder for this task and have a debate in progress!
        skip_coder = False
        if db and agent_type in CODING_AGENT_TYPES and agent_run.task_id:
            try:
                from backend.models.debate_log import DebateLog
                from sqlalchemy import select
                
                stmt = select(DebateLog).where(DebateLog.task_id == agent_run.task_id)
                existing_logs = (await db.execute(stmt)).scalars().all()
                if existing_logs:
                    print(f"CRASH RECOVERY: Found {len(existing_logs)} debate logs for task {agent_run.task_id}. Skipping Coder and resuming debate.")
                    skip_coder = True
                    # Reconstruct initial coder result from the very first debate log
                    result = json.loads(existing_logs[0].coder_output_json) if existing_logs[0].coder_output_json else {"status": "SUCCESS", "summary": "Recovered state"}
            except Exception as e:
                print(f"[AgentExecutor] Failed to check for existing debate logs: {e}")
                
        if not skip_coder:
            try:
                result = await self.provider.generate_structured_output(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=response_schema,
                    model=model_name,
                    tools=tools,
                    max_turns=25,
                    tool_executor=execute_tool
                )
            except Exception as e:
                if "AntiLoopException" in str(e):
                    result = {"status": "FAILED", "summary": f"Run aborted due to infinite loop: {str(e)}"}
                else:
                    result = {"status": "FAILED", "summary": f"Provider error: {repr(e)}" }

        # Basic failure parsing
        is_valid_dict = isinstance(result, dict)

        # ── MULTI-AGENT DEBATE & CONSENSUS ──────────────────────────────
        # If this is a coding agent and it claims success, run the debate pipeline.
        # Security Agent and Code Reviewer will independently review the output.
        # If either rejects, the coder will be forced to revise.
        run_inline_debate = (
            provider_name == "mock"
            or bool(input_data.get("human_override_comment"))
            or os.environ.get("KOBITS_INLINE_DEBATE") == "1"
        ) and (not scope_info.get("fast_track") or bool(input_data.get("human_override_comment")))
        if is_valid_dict and agent_type in CODING_AGENT_TYPES and run_inline_debate:
            status_val = (result.get("status") or "").upper()
            has_content = bool(result.get("summary") or result.get("changes") or result.get("artifacts"))
            
            if status_val != "FAILED" and has_content:
                # Get WebSocket broadcast function if available
                ws_broadcast = None
                try:
                    from backend.services.websocket_manager import manager
                    ws_broadcast = manager.broadcast
                except Exception:
                    pass
                
                consensus = ConsensusEngine(db=db, ws_broadcast=ws_broadcast)
                
                task_context = {
                    "task_description": input_data.get("task_description", input_data.get("description", "")),
                    "mission_id": input_data.get("mission_id", ""),
                    "project_id": input_data.get("project_id", ""),
                    "human_override_comment": input_data.get("human_override_comment")
                }
                
                trace_logs.append({
                    "type": "consensus_debate_start",
                    "agent_type": agent_type.value,
                    "timestamp": datetime.now().isoformat()
                })
                
                # ── EXTRACT ACTUAL CODE from the coder's tool calls ──
                # This pulls real file contents from repository.write calls
                # so reviewers examine REAL CODE, not JSON summaries.
                code_artifacts = ConsensusEngine.extract_code_artifacts(trace_logs)
                
                trace_logs.append({
                    "type": "code_artifacts_extracted",
                    "files_found": len(code_artifacts),
                    "file_paths": [a["path"] for a in code_artifacts],
                    "timestamp": datetime.now().isoformat()
                })
                
                try:
                    result = await consensus.run_debate_pipeline(
                        task_id=agent_run.task_id or str(uuid.uuid4()),
                        mission_id=input_data.get("mission_id", ""),
                        coder_agent_type=agent_type,
                        coder_result=result,
                        task_context=task_context,
                        model_name=model_name,
                        code_artifacts=code_artifacts,
                    )
                    
                    debate_summary = result.get("_debate", {})
                    trace_logs.append({
                        "type": "consensus_debate_complete",
                        "verdict": debate_summary.get("final_verdict", "UNKNOWN"),
                        "rounds": debate_summary.get("total_rounds", 0),
                        "timestamp": datetime.now().isoformat()
                    })
                except Exception as e:
                    trace_logs.append({
                        "type": "consensus_debate_error",
                        "error": str(e),
                        "timestamp": datetime.now().isoformat()
                    })
                    # Don't let debate failure break the pipeline
                    
                is_valid_dict = isinstance(result, dict)

        # --- Canonical classification ---
        from backend.models.agent import classify_model_result, ResultClassification
        
        classification, run_status, classification_reason = classify_model_result(result)
        
        # Ensure result is a dict for downstream processing
        if not is_valid_dict:
            result = {"status": "FAILED", "summary": "Invalid JSON response"}
            
        # --- Corrective retry for INDETERMINATE (missing status with evidence) ---
        if classification == ResultClassification.INDETERMINATE and corrective_retries < MAX_CORRECTIVE_RETRIES:
            corrective_retries += 1
            trace_logs.append({
                "type": "corrective_retry",
                "reason": classification_reason,
                "attempt": corrective_retries,
                "timestamp": datetime.now().isoformat()
            })
            try:
                correction_prompt = user_prompt + (
                    "\n\n--- FEEDBACK ON PREVIOUS ATTEMPT ---\n"
                    "Your previous response was structurally incomplete: it is missing the required "
                    "\"status\" field. Return the same result with the required status field set to "
                    "\"SUCCESS\" or \"FAILED\"."
                )
                retry_result = await self.provider.generate_structured_output(
                    system_prompt=system_prompt,
                    user_prompt=correction_prompt,
                    schema=response_schema,
                    model=model_name,
                    tools=[],
                    tool_executor=None
                )
                if isinstance(retry_result, dict) and retry_result.get("status"):
                    result = retry_result
                    classification, run_status, classification_reason = classify_model_result(result)
                    classification_reason += " (after corrective retry)"
            except Exception:
                pass  # Keep the original INDETERMINATE classification

        if agent_type == AgentType.TECHNICAL_LEAD and classification in (ResultClassification.COMPLETED, ResultClassification.COMPLETED_WITH_INFERRED_STATUS):
            from backend.services.intelligence.task_parser import parse_and_validate_tasks
            try:
                if os.environ.get("KOBITS_VERBOSE") == "1":
                    print(f'\n[LLM RAW] {json.dumps(result, indent=2)}\n')
                parse_and_validate_tasks(result)
            except ValueError as e:
                if corrective_retries < MAX_CORRECTIVE_RETRIES:
                    corrective_retries += 1
                    trace_logs.append({
                        "type": "schema_retry",
                        "reason": str(e),
                        "attempt": corrective_retries,
                        "timestamp": datetime.now().isoformat()
                    })
                    try:
                        correction_prompt = user_prompt + (
                            "\n\n--- FEEDBACK ON PREVIOUS ATTEMPT ---\n"
                            f"The previous task decomposition was incomplete or malformed: {str(e)}. "
                            "Return the required task schema with non-null task descriptions and all required fields."
                        )
                        retry_result = await self.provider.generate_structured_output(
                            system_prompt=system_prompt,
                            user_prompt=correction_prompt,
                            schema=response_schema,
                            model=model_name,
                            tools=[],
                            tool_executor=None
                        )
                        if isinstance(retry_result, dict):
                            result = retry_result
                            classification, run_status, classification_reason = classify_model_result(result)
                    except Exception:
                        pass
                
                # Re-check after retry
                try:
                    if os.environ.get("KOBITS_VERBOSE") == "1":
                        print(f'\n[LLM RAW RETRY] {json.dumps(result, indent=2)}\n')
                    parse_and_validate_tasks(result)
                except ValueError as e:
                    # If it STILL fails, mark as failed so it doesn't enter the queue silently
                    classification = ResultClassification.FAILED
                    run_status = AgentRunStatus.FAILED
                    classification_reason = f"Schema validation failed after retry: {str(e)}"
                    result["status"] = "FAILED"
                    result["summary"] = classification_reason

        def scrub_secrets(text: str) -> str:
            if not text:
                return text
            secrets = [
                os.environ.get("ANTHROPIC_API_KEY"),
                os.environ.get("DEEPSEEK_API_KEY"),
                settings.DEEPSEEK_API_KEY,
                settings.ANTHROPIC_API_KEY,
                os.environ.get("GITHUB_TOKEN")
            ]
            for sec in secrets:
                if sec and len(sec) > 4:
                    text = text.replace(sec, "[REDACTED]")
            return text

        
        if "failure_category" in result:
            agent_run.failure_category = result["failure_category"]
            if result["failure_category"] in ["RATE_LIMIT_429", "TIMEOUT", "DNS_ERROR", "SERVER_ERROR_5XX"]:
                agent_run.recovery_action = "RETRY_LATER"
            else:
                agent_run.recovery_action = "ABORT"
                
        # Set run status from classification

        agent_run.status = run_status
            
        if "_usage" in result:
            usage = result["_usage"]
            agent_run.tokens_input = usage.get("input_tokens", 0)
            agent_run.tokens_output = usage.get("output_tokens", 0)
            agent_run.estimated_cost = usage.get("estimated_cost", 0.0)
            if usage.get("model_used") or usage.get("model"):
                agent_run.model = usage.get("model_used") or usage.get("model")

        # Attach classification telemetry to result
        result["_classification"] = {
            "model_result_status": classification.value,
            "runtime_status": run_status.value,
            "reason": classification_reason,
            "status_inferred": classification == ResultClassification.COMPLETED_WITH_INFERRED_STATUS,
            "corrective_retries": corrective_retries,
        }
            
        agent_run.output_text = scrub_secrets(json.dumps(result, indent=2))
        agent_run.tool_calls_json = json.dumps(trace_logs)
        
        return result

agent_executor = AgentExecutor()

async def execute_agent_run(agent_run: AgentRun, agent_type: AgentType, input_data: dict, db=None) -> dict:
    return await agent_executor.execute_run(agent_run, agent_type, input_data, db=db)
