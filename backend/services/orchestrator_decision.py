import json
import uuid
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.project import Task, TaskStatus
from backend.models.agent import AgentRun, AgentType
from backend.models.finding import Finding, FindingStatus, FindingSeverity, FindingType

class OrchestratorDecision:
    @staticmethod
    async def evaluate_task_result(db: AsyncSession, task: Task, agent_run: AgentRun) -> dict:
        """
        Evaluates the result of a task and determines if a correction is needed.
        Creates findings if applicable.
        """
        output_data = {}
        if agent_run.output_text:
            try:
                output_data = json.loads(agent_run.output_text)
            except:
                pass
                
        # 1. Did the task explicitly fail via exception/timeout or provider error?
        summary_str = str(output_data.get("summary") or "")
        if summary_str.startswith("Provider error:") or (agent_run.error and "Anthropic API Error" in str(agent_run.error)):
            return {
                "action": "ESCALATE",
                "reason": summary_str or str(agent_run.error),
                "finding_id": None,
            }

        if agent_run.status == "FAILED" or output_data.get("status") == "FAILED":
            # Determine if we should retry or escalate based on attempt count
            if task.attempt_count >= 3:
                return {
                    "action": "INTERRUPT",
                    "reason": "Max correction attempts reached.",
                    "finding_id": None
                }
                
            # If it's a provider failure without a finding payload, we could just retry the task itself
            # But the requirement asks us to create a CORRECTION task for validation failures.
            # Let's see if there's a finding payload.
            
        # 2. Did the debate pipeline interrupt?
        debate = output_data.get("_debate", {})
        if debate.get("final_verdict") == "BLOCKED":
            return {
                "action": "INTERRUPT",
                "reason": "Debate blocked by CRITICAL rejection. Human intervention required.",
                "finding_id": None
            }
        elif debate.get("final_verdict") == "MAX_ROUNDS_EXCEEDED":
            return {
                "action": "INTERRUPT",
                "reason": "Max debate rounds reached without consensus. Human intervention required.",
                "finding_id": None
            }
            
        # 2. Check for findings in artifacts
        artifacts = output_data.get("artifacts", {})
        finding_payload = None
        if isinstance(artifacts, dict):
            finding_payload = artifacts.get("finding")
        elif isinstance(artifacts, list):
            for a in artifacts:
                if isinstance(a, dict) and a.get("type") == "finding":
                    finding_payload = a
                    break
        
        
        # 3. Check for QA, Security, or Review rejection directly
        role = ""
        if task.metadata_json:
            try:
                meta = json.loads(task.metadata_json)
                role = meta.get("agent_role", "")
            except:
                pass

        if role == "QA_ENGINEER" and (output_data.get("status") == "FAILED" or finding_payload):
            finding_type = FindingType.QA
            severity = FindingSeverity.HIGH
            title = finding_payload.get("title", "QA Failure") if finding_payload else "QA Failure"
            desc = finding_payload.get("description", output_data.get("summary", "Test failed")) if finding_payload else output_data.get("summary", "Test failed")
            return await OrchestratorDecision._create_correction_loop(db, task, agent_run, finding_type, severity, title, desc, artifacts)
            
        if role in ("SECURITY_SPECIALIST", "SECURITY_ENGINEER") and (output_data.get("status") == "FAILED" or finding_payload):
            finding_type = FindingType.SECURITY
            severity = FindingSeverity.CRITICAL if finding_payload and finding_payload.get("severity") == "CRITICAL" else FindingSeverity.HIGH
            title = finding_payload.get("title", "Security Vulnerability") if finding_payload else "Security Vulnerability"
            desc = finding_payload.get("description", output_data.get("summary", "Security checks failed")) if finding_payload else output_data.get("summary", "Security checks failed")
            if severity == FindingSeverity.CRITICAL:
                # Block mission
                return {
                    "action": "BLOCK",
                    "reason": "CRITICAL security finding.",
                    "finding_id": await OrchestratorDecision._create_finding(db, task, agent_run, finding_type, severity, title, desc, artifacts)
                }
            return await OrchestratorDecision._create_correction_loop(db, task, agent_run, finding_type, severity, title, desc, artifacts)
            
        if role == "CODE_REVIEWER" and (output_data.get("status") == "FAILED" or finding_payload):
            finding_type = FindingType.CODE_REVIEW
            severity = FindingSeverity.MEDIUM
            title = finding_payload.get("title", "Changes Requested") if finding_payload else "Changes Requested"
            desc = finding_payload.get("description", output_data.get("summary", "Review rejected")) if finding_payload else output_data.get("summary", "Review rejected")
            return await OrchestratorDecision._create_correction_loop(db, task, agent_run, finding_type, severity, title, desc, artifacts)
            
        if role == "SOLUTION_ARCHITECT" and (output_data.get("status") == "FAILED" or finding_payload):
            finding_type = FindingType.ARCHITECTURE
            severity = FindingSeverity.HIGH
            title = finding_payload.get("title", "Architecture Failure") if finding_payload else "Architecture Failure"
            desc = finding_payload.get("description", output_data.get("summary", "Architecture rejected")) if finding_payload else output_data.get("summary", "Architecture rejected")
            return await OrchestratorDecision._create_correction_loop(db, task, agent_run, finding_type, severity, title, desc, artifacts)

        # 4. Success / Continue
        return {
            "action": "CONTINUE"
        }

    @staticmethod
    async def _create_finding(db: AsyncSession, task: Task, agent_run: AgentRun, type_: FindingType, severity: FindingSeverity, title: str, desc: str, artifacts: dict) -> str:
        finding_id = str(uuid.uuid4())
        f = Finding(
            id=finding_id,
            mission_id=task.mission_id,
            task_id=task.id,
            agent_id=agent_run.agent_id,
            type=type_,
            severity=severity,
            status=FindingStatus.OPEN,
            title=title,
            description=desc,
            evidence=json.dumps(artifacts),
            affected_files=artifacts.get("affected_files", "[]") if isinstance(artifacts, dict) else "[]",
            recommended_fix=artifacts.get("recommended_fix") if isinstance(artifacts, dict) else None
        )
        db.add(f)
        await db.commit()
        return finding_id

    @staticmethod
    async def _create_correction_loop(db: AsyncSession, task: Task, agent_run: AgentRun, type_: FindingType, severity: FindingSeverity, title: str, desc: str, artifacts: dict) -> dict:
        if task.attempt_count >= 3:
            return {
                "action": "ESCALATE",
                "reason": "Max correction attempts reached.",
                "finding_id": None
            }
            
        # 1. Create finding
        finding_id = await OrchestratorDecision._create_finding(db, task, agent_run, type_, severity, title, desc, artifacts)
        
        # 2. Determine target agent to fix it. Usually the dependencies of the validation task are the implementation tasks.
        # We look at the DAG to find the upstream task.
        deps = json.loads(task.dependencies_json or "[]")
        target_role = "BACKEND_ENGINEER" # Default fallback
        if deps:
            # Simple heuristic: find the first dependency and use its role
            from sqlalchemy import select
            upstream = (await db.execute(select(Task).where(Task.id == deps[0]))).scalars().first()
            if upstream and upstream.metadata_json:
                try:
                    meta = json.loads(upstream.metadata_json)
                    target_role = meta.get("agent_role", "BACKEND_ENGINEER")
                except:
                    pass
        
        # 3. Create Correction Task
        correction_id = str(uuid.uuid4())
        c_task = Task(
            id=correction_id,
            mission_id=task.mission_id,
            project_id=task.project_id,
            created_by=task.created_by,
            title=f"Fix {type_.value} Finding: {title}",
            description=f"Fix the following finding: {desc}\nEvidence: {json.dumps(artifacts)}",
            status=TaskStatus.PENDING,
            phase=task.phase,
            metadata_json=json.dumps({"agent_role": target_role}),
            is_correction=True,
            finding_id=finding_id,
            parent_correction_task_id=deps[0] if deps else None,
            attempt_count=task.attempt_count + 1
        )
        db.add(c_task)
        
        # 4. Create Revalidation Task (the same task that just failed, but targeting the new correction)
        reval_id = str(uuid.uuid4())
        reval_task = Task(
            id=reval_id,
            mission_id=task.mission_id,
            project_id=task.project_id,
            created_by=task.created_by,
            title=f"Revalidate: {task.title}",
            description=task.description,
            status=TaskStatus.PENDING,
            phase=task.phase,
            dependencies_json=json.dumps([correction_id]),
            metadata_json=task.metadata_json,
            is_correction=True,
            finding_id=finding_id,
            attempt_count=task.attempt_count + 1
        )
        db.add(reval_task)
        
        await db.commit()
        
        return {
            "action": "CREATE_CORRECTION_TASK",
            "reason": f"Created correction for {type_.value} finding",
            "target_agent": target_role,
            "finding_id": finding_id,
            "correction_task_id": correction_id,
            "revalidation_task_id": reval_id
        }

    @classmethod
    async def handle_pr_feedback(cls, db: AsyncSession, mission_id: str, project_id: str, title: str, description: str):
        from backend.models.mission import Mission, MissionStatus
        from sqlalchemy import select
        
        # 1. Re-open mission
        mission = await db.get(Mission, mission_id)
        if mission:
            mission.status = MissionStatus.READY
            mission.current_stage = "Applying PR Feedback"
            
        # 2. Create Finding
        finding_id = str(uuid.uuid4())
        f = Finding(
            id=finding_id,
            mission_id=mission_id,
            type=FindingType.CODE_REVIEW,
            severity=FindingSeverity.HIGH,
            title=title,
            description=description,
            status=FindingStatus.OPEN
        )
        db.add(f)
        
        # 3. Create Correction Task
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            mission_id=mission_id,
            project_id=project_id,
            title=f"Fix PR Feedback: {title}",
            description=f"Feedback:\n{description}",
            is_correction=True,
            finding_id=finding_id,
            created_by=mission.created_by if mission else "system",
            status=TaskStatus.PENDING,
            attempt_count=0
        )
        db.add(task)
        await db.commit()
        return task_id



    @staticmethod
    async def handle_merge_conflict(db: AsyncSession, mission_id: str, project_id: str, pr_id: str, base_ref: str, head_ref: str):
        from backend.models.mission import Mission, MissionStatus
        from backend.models.project import Task, TaskStatus
        from backend.models.finding import Finding, FindingType, FindingSeverity, FindingStatus
        import uuid
        from datetime import datetime, timezone

        # 1. Update Mission state to BLOCKED
        mission = await db.get(Mission, mission_id)
        if mission:
            mission.status = MissionStatus.BLOCKED
            mission.current_stage = "Merge Conflict Detected"
            
        # 2. Create a MERGE_CONFLICT finding
        finding_id = str(uuid.uuid4())
        finding = Finding(
            id=finding_id,
            mission_id=mission_id,
            type=FindingType.MERGE_CONFLICT,
            severity=FindingSeverity.CRITICAL,
            title="Merge Conflict",
            description=f"Branch {head_ref} has conflicts with {base_ref}.",
            status=FindingStatus.OPEN,
            created_at=datetime.now(timezone.utc)
        )
        db.add(finding)
        
        # 3. Create a RESOLUTION task
        task = Task(
            id=str(uuid.uuid4()),
            project_id=project_id,
            mission_id=mission_id,
            title="Resolve Merge Conflict",
            description=f"Resolve merge conflicts between {head_ref} and {base_ref}.",
            status=TaskStatus.PENDING,
            is_correction=True,
            finding_id=finding_id,
            created_by=mission.created_by if mission else "system",
            created_at=datetime.now(timezone.utc)
        )
        db.add(task)
        await db.commit()
