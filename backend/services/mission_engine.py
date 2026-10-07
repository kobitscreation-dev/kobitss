import asyncio
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.core.database import AsyncSessionLocal
from backend.models.mission import Mission, MissionStatus, MissionPlan, Milestone, MilestoneStatus
from backend.models.project import Task, TaskStatus, Activity, ActivityType, Project
from backend.models.github import Repository
from google import genai
from google.genai import types
import os

async def _get_project_context(db: AsyncSession, project_id: str) -> str:
    # Attempt to load project memory
    from backend.models.project import ProjectMemory
    stmt = select(ProjectMemory).where(ProjectMemory.project_id == project_id)
    res = await db.execute(stmt)
    mem = res.scalars().first()
    if mem:
        return f"Project Objective: {mem.objective}\nArchitecture: {mem.architecture}\nStack: {mem.tech_stack}"
    return "No memory available."

async def analyze_and_plan_mission(mission_id: str, organization_id: str, user_id: str):
    async with AsyncSessionLocal() as db:
        mission = await db.get(Mission, mission_id)
        if not mission:
            return
            
        try:
            mission.status = MissionStatus.ACTIVE
            # removed current_stage
            act_analyze = Activity(
                organization_id=organization_id, project_id=mission.project_id, user_id=user_id,
                type=ActivityType.MISSION_ANALYZED, title="Analyzing Mission",
                description="The Orchestrator is analyzing the engineering request."
            )
            db.add(act_analyze)
            await db.commit()
            
            project_context = await _get_project_context(db, mission.project_id)
            
            # Step 1: Structured Request Analysis
            api_key = os.getenv("GEMINI_API_KEY")
            client = genai.Client(api_key=api_key)
            
            prompt = f"""
            Analyze the following engineering request for an AI development platform.
            Project Context:
            {project_context}
            
            Mission Title: {mission.title}
            Mission Objective: {mission.objective}
            
            Determine the complexity, risk level, affected areas, and recommended agents.
            Available agents: PM, ARCHITECT, BACKEND, FRONTEND, SECURITY, QA, DEVOPS.
            """
            
            analysis_schema = types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "request_type": types.Schema(type=types.Type.STRING),
                    "summary": types.Schema(type=types.Type.STRING),
                    "complexity": types.Schema(type=types.Type.STRING, enum=["LOW", "MEDIUM", "HIGH"]),
                    "risk_level": types.Schema(type=types.Type.STRING, enum=["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
                    "affected_areas": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
                    "recommended_agents": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
                    "requires_approval": types.Schema(type=types.Type.BOOLEAN)
                },
                required=["request_type", "summary", "complexity", "risk_level", "affected_areas", "recommended_agents", "requires_approval"]
            )
            
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=analysis_schema,
                    temperature=0.1
                )
            )
            
            analysis = json.loads(resp.text)
            
            mission.status = MissionStatus.PLANNING
            
            mission.risk_level = analysis["risk_level"]
            mission.requires_approval = analysis["requires_approval"]
            await db.commit()
            
            # Step 2: Implementation Plan
            plan_prompt = f"""
            Create an implementation plan for: {mission.objective}
            Complexity: {analysis['complexity']}, Risk: {analysis['risk_level']}
            Affected Areas: {', '.join(analysis['affected_areas'])}
            Agents: {', '.join(analysis['recommended_agents'])}
            
            Return a JSON object containing milestones and their associated tasks.
            """
            
            plan_schema = types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "goal": types.Schema(type=types.Type.STRING),
                    "approach": types.Schema(type=types.Type.STRING),
                    "estimated_duration_minutes": types.Schema(type=types.Type.INTEGER),
                    "estimated_cost_usd": types.Schema(type=types.Type.NUMBER),
                    "milestones": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "title": types.Schema(type=types.Type.STRING),
                                "description": types.Schema(type=types.Type.STRING),
                                "tasks": types.Schema(
                                    type=types.Type.ARRAY,
                                    items=types.Schema(
                                        type=types.Type.OBJECT,
                                        properties={
                                            "title": types.Schema(type=types.Type.STRING),
                                            "description": types.Schema(type=types.Type.STRING),
                                            "agent_role": types.Schema(type=types.Type.STRING),
                                            "dependencies": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING))
                                        },
                                        required=["title", "description", "agent_role", "dependencies"]
                                    )
                                )
                            },
                            required=["title", "description", "tasks"]
                        )
                    )
                },
                required=["goal", "approach", "estimated_duration_minutes", "estimated_cost_usd", "milestones"]
            )
            
            plan_resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=plan_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=plan_schema,
                    temperature=0.2
                )
            )
            
            plan = json.loads(plan_resp.text)
            
            # Save Plan Revision
            mission_plan = MissionPlan(
                mission_id=mission.id,
                version=1,
                plan_data=json.dumps(plan),
                created_by=user_id,
                reason="Initial Orchestrator Plan"
            )
            db.add(mission_plan)
            
            # Create Milestones and Tasks
            mission.estimated_duration = plan.get("estimated_duration_minutes", 60)
            mission.estimated_cost = plan.get("estimated_cost_usd", 0.0)
            
            for i, m_data in enumerate(plan.get("milestones", [])):
                milestone = Milestone(
                    mission_id=mission.id,
                    title=m_data["title"],
                    description=m_data["description"],
                    order=i
                )
                db.add(milestone)
                await db.flush() # get milestone.id
                
                for t_data in m_data.get("tasks", []):
                    task = Task(
                        project_id=mission.project_id,
                        mission_id=mission.id,
                        milestone_id=milestone.id,
                        repository_id=mission.repository_id,
                        created_by=user_id,
                        title=t_data["title"],
                        description=t_data["description"],
                        dependencies_json=json.dumps(t_data.get("dependencies", [])),
                        metadata_json=json.dumps({"agent_role": t_data["agent_role"]})
                    )
                    db.add(task)
            
            # Update Mission Status
            mission.status = MissionStatus.AWAITING_APPROVAL if mission.requires_approval else MissionStatus.READY
            
            
            act_planned = Activity(
                organization_id=organization_id, project_id=mission.project_id, user_id=user_id,
                type=ActivityType.MISSION_PLANNED, title="Mission Planned",
                description=f"Generated {len(plan.get('milestones', []))} milestones. Waiting for approval." if mission.requires_approval else "Plan generated and ready."
            )
            db.add(act_planned)
            await db.commit()
            
            if mission.status == MissionStatus.READY:
                # auto-start if no approval needed
                asyncio.create_task(execute_mission(mission.id, organization_id, user_id))
                
        except Exception as e:
            mission.status = MissionStatus.FAILED
            # removed current_stage error
            await db.commit()
            raise e

from backend.services.mission_runtime import MissionRuntime

async def execute_mission(mission_id: str, organization_id: str, user_id: str):
    runtime = MissionRuntime(mission_id, organization_id, user_id)
    await runtime.execute()
