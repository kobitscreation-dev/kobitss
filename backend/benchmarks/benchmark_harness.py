import json
import uuid
import asyncio
import os
from datetime import datetime
from backend.core.database import AsyncSessionLocal
from backend.models.agent import AgentRun, AgentType
from backend.models.mission import Mission, MissionStatus
from backend.services.memory_service import (
    store_agent_memory, publish_project_knowledge, 
    record_decision, record_lesson
)
from backend.models.communication import AgentLessonType
from backend.core.config import settings

class BenchmarkHarness:
    def __init__(self):
        provider = settings.LLM_PROVIDER.lower() if settings.LLM_PROVIDER else (os.environ.get("LLM_PROVIDER") or "mock").lower()
        if provider == "deepseek":
            model = os.environ.get("DEEPSEEK_MODEL") or settings.DEEPSEEK_MODEL or "deepseek-flash"
        elif provider == "anthropic":
            model = os.environ.get("ANTHROPIC_MODEL") or settings.ANTHROPIC_MODEL
        elif provider == "gemini":
            model = "gemini-2.5-pro"
        else:
            model = "mock-model"
            
        self.report = {
            'provider': provider,
            'model': model,
            'start_time': datetime.now().isoformat(),
            'tasks': [],
            'metrics': {
                'token_usage': 0,
                'cost': 0.0,
                'duration_seconds': 0,
                'files_changed': 0,
                'tests_added': 0,
                'security_issues': 0,
                'failures': 0
            },
            'flow': []
        }
        
    async def run_standard_profile_benchmark(self, org_id: str, proj_id: str):
        print('Starting Standard Profile Benchmark...')
        mission_id = str(uuid.uuid4())
        self.report['mission_id'] = mission_id
        
        start_time = datetime.now()
        is_real = settings.REAL_AI_TEST or os.environ.get("REAL_AI_TEST", "").lower() in ["true", "1"]
        
        if is_real:
            from backend.services.agent_executor import AgentExecutor
            executor = AgentExecutor()
            
            # Step 1: Nexus plans
            nexus_run = AgentRun(id=str(uuid.uuid4()), task_id="task_plan", project_id=proj_id, organization_id=org_id, agent_id="Nexus")
            plan_input = {"objective": "Add a secure user profile endpoint with authentication, validation, tests."}
            async with AsyncSessionLocal() as db:
                nexus_res = await executor.execute_run(nexus_run, AgentType.ORCHESTRATOR, plan_input, db=db)
                self.report['flow'].append(f"Nexus -> {nexus_res.get('status', 'EXECUTED')}")
                
            # Step 2: Axiom architecture
            axiom_run = AgentRun(id=str(uuid.uuid4()), task_id="task_arch", project_id=proj_id, organization_id=org_id, agent_id="Axiom")
            async with AsyncSessionLocal() as db:
                axiom_res = await executor.execute_run(axiom_run, AgentType.SOLUTION_ARCHITECT, {"nexus_plan": nexus_res}, db=db)
                self.report['flow'].append(f"Axiom -> {axiom_res.get('status', 'EXECUTED')}")
                
            # Step 3: Core backend implementation
            core_run = AgentRun(id=str(uuid.uuid4()), task_id="task_impl", project_id=proj_id, organization_id=org_id, agent_id="Core")
            async with AsyncSessionLocal() as db:
                core_res = await executor.execute_run(core_run, AgentType.BACKEND_ENGINEER, {"architecture": axiom_res}, db=db)
                self.report['flow'].append(f"Core -> {core_res.get('status', 'EXECUTED')}")
                
            # Step 4: Sentinel security & tests
            sentinel_run = AgentRun(id=str(uuid.uuid4()), task_id="task_test", project_id=proj_id, organization_id=org_id, agent_id="Sentinel")
            async with AsyncSessionLocal() as db:
                sentinel_res = await executor.execute_run(sentinel_run, AgentType.SECURITY_ENGINEER, {"backend_changes": core_res}, db=db)
                self.report['flow'].append(f"Sentinel -> {sentinel_res.get('status', 'EXECUTED')}")
                
            # Step 5: Review
            review_run = AgentRun(id=str(uuid.uuid4()), task_id="task_rev", project_id=proj_id, organization_id=org_id, agent_id="Review")
            async with AsyncSessionLocal() as db:
                review_res = await executor.execute_run(review_run, AgentType.CODE_REVIEWER, {"deliverables": [core_res, sentinel_res]}, db=db)
                self.report['flow'].append(f"Review -> {review_res.get('status', 'EXECUTED')}")
                
            self.report['status'] = "COMPLETED"
        else:
            self.report['flow'].append('Nexus -> plans architecture')
            self.report['flow'].append('Axiom -> defines components')
            self.report['flow'].append('Core -> implements code')
            self.report['flow'].append('Sentinel -> writes tests')
            self.report['flow'].append('Review -> conducts code review')
            self.report['status'] = 'SUCCESS' if settings.LLM_PROVIDER == 'mock' else 'REAL_EXECUTION_REQUIRED'
            
        end_time = datetime.now()
        self.report['metrics']['duration_seconds'] = (end_time - start_time).total_seconds()
        
        return self.report
        
    async def run_memory_benchmark(self, org_id: str, proj_id: str):
        print('Starting Memory Benchmark...')
        async with AsyncSessionLocal() as db:
            await store_agent_memory(db, 'Core', org_id, proj_id, 'preference', 'style', 'Core likes fast loops.')
            await publish_project_knowledge(db, 'Core', org_id, proj_id, 'architecture', 'structure', 'The repository uses the existing service/repository separation pattern.')
            await db.commit()
            
        self.report['flow'].append('Memory benchmark seeded successfully.')
        return self.report
        
    async def generate_final_report(self):
        with open('benchmark_report.json', 'w') as f:
            json.dump(self.report, f, indent=2)
        print('Report written to benchmark_report.json')