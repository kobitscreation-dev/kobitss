import asyncio
import json
from datetime import datetime, timezone
import networkx as nx

from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.project import Project, Task, TaskStatus, Activity, ActivityType
from backend.models.agent import AgentRun, AgentType, AgentRunStatus
from backend.services.agent_executor import execute_agent_run, agent_executor
from backend.services.agent_registry import AGENT_REGISTRY


async def generate_dynamic_dag(prompt: str, project_name: str) -> list:
    """Uses the Orchestrator LLM to break a complex prompt into a DAG of agent tasks."""
    if not agent_executor.client:
        # Fallback to hardcoded plan if no API key
        return [
            {"id": "t1", "agent_type": "PRODUCT_MANAGER", "description": "Write PRD", "dependencies": []},
            {"id": "t2", "agent_type": "SOFTWARE_ARCHITECT", "description": "Design Architecture", "dependencies": ["t1"]},
            {"id": "t3", "agent_type": "BACKEND_ENGINEER", "description": "Implement Backend", "dependencies": ["t2"]},
            {"id": "t4", "agent_type": "FRONTEND_ENGINEER", "description": "Implement Frontend", "dependencies": ["t2"]},
            {"id": "t5", "agent_type": "QA_ENGINEER", "description": "Write Tests", "dependencies": ["t3", "t4"]}
        ]
        
    system_prompt = AGENT_REGISTRY[AgentType.ORCHESTRATOR].system_prompt
    schema = {
        "type": "OBJECT",
        "properties": {
            "tasks": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "STRING"},
                        "agent_type": {"type": "STRING"},
                        "description": {"type": "STRING"},
                        "dependencies": {"type": "ARRAY", "items": {"type": "STRING"}}
                    },
                    "required": ["id", "agent_type", "description", "dependencies"]
                }
            }
        },
        "required": ["tasks"]
    }
    
    from google.genai import types
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.1,
        response_mime_type="application/json",
        response_schema=schema
    )
    
    try:
        response = await agent_executor.client.aio.chats.create(model="gemini-2.5-pro", config=config).send_message(
            f"Project: {project_name}\nPrompt: {prompt}\nCreate a DAG of tasks using the 21 agents."
        )
        plan = json.loads(response.text)
        return plan.get("tasks", [])
    except Exception as e:
        print(f"Error generating dynamic DAG: {e}")
        return []

import uuid

PENDING_PLANS = {}

async def orchestrate_request(db: AsyncSession, project: Project, prompt: str, user_id: str) -> dict:
    """Entry point for handling a user request."""
    
    project_id = project.id
    project_name = project.name
    org_id = project.organization_id
    
    # 1. ORCHESTRATOR PHASE: Analyze prompt and build DAG
    activity = Activity(
        organization_id=org_id,
        project_id=project_id,
        user_id=user_id,
        type=ActivityType.AGENT_STARTED,
        title="Orchestrator Planning",
        description=f"Nexus (Orchestrator) is analyzing the request: '{prompt[:50]}...'",
        metadata_json=json.dumps({"agent": "ORCHESTRATOR"})
    )
    db.add(activity)
    await db.commit()
    
    # Generate the plan
    tasks_def = await generate_dynamic_dag(prompt, project_name)
    if not tasks_def:
        raise ValueError("Orchestrator failed to generate a plan.")
    
    # 2. Persist Tasks to DB
    db_tasks = {}
    for tdef in tasks_def:
        try:
            agent_enum = AgentType(tdef["agent_type"])
        except ValueError:
            agent_enum = AgentType.BACKEND_ENGINEER # fallback
            
        task = Task(
            project_id=project_id,
            title=tdef["description"],
            description=f"Assigned to {agent_enum.value}",
            status=TaskStatus.PENDING,
            created_by=user_id
        )
        db.add(task)
        db_tasks[tdef["id"]] = {"model": task, "deps": tdef["dependencies"], "def": tdef, "agent": agent_enum}
        
    await db.commit()
    
    # Cache the plan instead of running it immediately (Approval Gate)
    plan_id = str(uuid.uuid4())
    PENDING_PLANS[plan_id] = {
        "db_tasks": db_tasks,
        "project_id": project_id,
        "project_name": project_name,
        "org_id": org_id,
        "user_id": user_id
    }
    
    return {
        "summary": "Orchestrator generated DAG and is waiting for approval.",
        "goal": prompt[:100],
        "plan_id": plan_id,
        "tasks": [
            {
                "title": t["description"], 
                "description": f"Assigned to {t['agent_type']}",
                "agent": t["agent_type"],
                "priority": "MEDIUM"
            } for t in tasks_def
        ],
        "risks": ["Potential for circular dependencies if complex", "Agent execution might take time"],
        "requires_approval": True
    }

async def approve_plan(plan_id: str):
    """Executes a previously generated plan that was waiting for human approval."""
    plan = PENDING_PLANS.pop(plan_id, None)
    if not plan:
        raise ValueError("Plan not found or already executed.")
        
    # Execute Pipeline asynchronously in background
    asyncio.create_task(_execute_dag_pipeline(
        plan["db_tasks"], 
        plan["project_id"], 
        plan["project_name"], 
        plan["org_id"], 
        plan["user_id"]
    ))
    return {"status": "Execution started"}



async def _execute_dag_pipeline(db_tasks: dict, project_id: str, project_name: str, org_id: str, user_id: str):
    """Executes the task DAG concurrently using asyncio."""
    
    from backend.core.database import AsyncSessionLocal
    
    # Build Directed Acyclic Graph
    G = nx.DiGraph()
    for t_id, t_info in db_tasks.items():
        G.add_node(t_id)
        for dep in t_info["deps"]:
            if dep in db_tasks:
                G.add_edge(dep, t_id)
                
    if not nx.is_directed_acyclic_graph(G):
        print("Cycle detected in DAG, aborting execution!")
        return

    # Track execution state
    completed = set()
    failed = set()
    task_events = {node: asyncio.Event() for node in G.nodes()}
    task_outputs = {}  # Store output results per node
    
    # Nodes with no dependencies are ready immediately
    for node, in_degree in G.in_degree():
        if in_degree == 0:
            task_events[node].set()

    async def worker(node: str):
        # Wait for all predecessors to complete successfully
        predecessors = list(G.predecessors(node))
        for p in predecessors:
            await task_events[p].wait()
            if p in failed:
                # Dependency failed, mark self as failed
                failed.add(node)
                task_events[node].set()
                return

        # Aggregate artifacts and findings from predecessors
        upstream_results = {}
        for p in predecessors:
            task_desc = db_tasks[p]["def"]["description"]
            upstream_results[task_desc] = task_outputs.get(p, {})

        # Execute node
        t_info = db_tasks[node]
        task_model = t_info["model"]
        agent_type = t_info["agent"]
        
        async with AsyncSessionLocal() as db:
            task_model = await db.merge(task_model)
            task_model.status = TaskStatus.IN_PROGRESS
            
            activity = Activity(
                organization_id=org_id,
                project_id=project_id,
                user_id=user_id,
                type=ActivityType.TASK_CREATED,
                title=f"Task Started by {agent_type.value}",
                description=task_model.title
            )
            db.add(activity)
            await db.commit()
            
            # Look up the actual Agent row from the DB for FK
            from sqlalchemy import select
            from backend.models.agent import Agent
            result = await db.execute(select(Agent).where(Agent.type == agent_type))
            agent_row = result.scalars().first()
            agent_db_id = agent_row.id if agent_row else f"agent_{agent_type.value.lower()}_{node}"

            # Create AgentRun
            agent_run = AgentRun(
                agent_id=agent_db_id,
                project_id=project_id,
                task_id=task_model.id,
                organization_id=org_id,
                status=AgentRunStatus.RUNNING,
                started_at=datetime.now(timezone.utc)
            )
            db.add(agent_run)
            await db.commit()
            
            try:
                # Call Actual LLM Agent Engine
                input_data = {
                    "project_context": project_name,
                    "objective": task_model.title,
                    "upstream_findings_and_artifacts": upstream_results
                }
                
                result = await execute_agent_run(agent_run, agent_type, input_data)
                task_outputs[node] = result
                
                # Independent Validation & Quality Gate check
                returned_status = str(result.get("status", "")).upper()
                if returned_status in ["REJECTED", "FAILED", "BLOCKED", "CHANGES_REQUESTED"]:
                    # The agent explicitly failed the validation
                    task_model.status = TaskStatus.FAILED
                    agent_run.status = AgentRunStatus.FAILED
                    failed.add(node)
                    
                    # Log failure activity
                    activity_failed = Activity(
                        organization_id=org_id,
                        project_id=project_id,
                        user_id=user_id,
                        type=ActivityType.AGENT_COMPLETED,
                        title=f"Quality Gate Failed: {agent_type.value}",
                        description=result.get("summary", "Security or QA validation failed. Halting downstream tasks.")
                    )
                    db.add(activity_failed)
                else:
                    # Successful completion
                    task_model.status = TaskStatus.COMPLETED
                    agent_run.status = AgentRunStatus.COMPLETED
                    completed.add(node)
                    
                    # Log completion
                    activity_done = Activity(
                        organization_id=org_id,
                        project_id=project_id,
                        user_id=user_id,
                        type=ActivityType.AGENT_COMPLETED,
                        title=f"Task Completed by {agent_type.value}",
                        description=result.get("summary", "Task completed.")
                    )
                    db.add(activity_done)
                
            except Exception as e:
                task_model.status = TaskStatus.FAILED
                agent_run.status = AgentRunStatus.FAILED
                agent_run.error = str(e)
                failed.add(node)
                
            agent_run.completed_at = datetime.now(timezone.utc)
            await db.commit()
            
        # Signal dependents that this node finished
        task_events[node].set()

    # Launch all workers concurrently
    workers = [asyncio.create_task(worker(node)) for node in G.nodes()]
    await asyncio.gather(*workers)
    
    print(f"DAG Execution Complete. Completed: {len(completed)}, Failed: {len(failed)}")
    
    # Log overall completion
    async with AsyncSessionLocal() as db:
        final_activity = Activity(
            organization_id=org_id,
            project_id=project_id,
            user_id=user_id,
            type=ActivityType.AGENT_COMPLETED,
            title="Pipeline Execution Complete",
            description=f"DAG execution finished. {len(completed)} succeeded, {len(failed)} failed."
        )
        db.add(final_activity)
        await db.commit()
