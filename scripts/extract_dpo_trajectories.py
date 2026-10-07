import asyncio
import json
import os
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from backend.core.database import AsyncSessionLocal
from backend.models.project import Task, TaskStatus
from backend.models.mission import Mission, AgentRun

async def extract_dpo_trajectories():
    """
    Extracts Direct Preference Optimization (DPO) training data from the Kobits database.
    Finds tasks where an agent failed (rejected) but later succeeded (chosen).
    """
    db_path = r"C:\Users\Arvind Kumar\.gemini\antigravity\scratch\kobits\kobits.db"
    if not os.path.exists(db_path):
        print("Database not found. Run some missions first!")
        return

    out_file = r"C:\Users\Arvind Kumar\.gemini\antigravity\scratch\kobits\dpo_dataset.jsonl"
    sft_file = r"C:\Users\Arvind Kumar\.gemini\antigravity\scratch\kobits\sft_dataset.jsonl"
    
    dpo_count = 0
    sft_count = 0

    async with AsyncSessionLocal() as db:
        # Get all completed tasks
        stmt = select(Task).where(Task.status == TaskStatus.COMPLETED)
        result = await db.execute(stmt)
        completed_tasks = result.scalars().all()

        with open(out_file, 'w', encoding='utf-8') as dpo_f, \
             open(sft_file, 'w', encoding='utf-8') as sft_f:
             
            for task in completed_tasks:
                # Fetch all agent runs for this task, ordered by creation
                run_stmt = select(AgentRun).where(AgentRun.task_id == task.id).order_by(AgentRun.created_at.asc())
                run_result = await db.execute(run_stmt)
                runs = run_result.scalars().all()
                
                if not runs:
                    continue
                    
                successful_run = runs[-1] # The last run resulted in the COMPLETED status
                prompt = task.description

                if len(runs) > 1:
                    # We have a failure followed by a success! Perfect DPO pair.
                    failed_run = runs[-2]
                    
                    dpo_record = {
                        "prompt": prompt,
                        "chosen": successful_run.response_payload,
                        "rejected": failed_run.response_payload,
                        "metadata": {
                            "task_id": task.id,
                            "agent": task.agent_name
                        }
                    }
                    dpo_f.write(json.dumps(dpo_record) + "\n")
                    dpo_count += 1
                else:
                    # Just a single successful run. Good for standard SFT.
                    sft_record = {
                        "prompt": prompt,
                        "completion": successful_run.response_payload,
                        "metadata": {
                            "task_id": task.id,
                            "agent": task.agent_name
                        }
                    }
                    sft_f.write(json.dumps(sft_record) + "\n")
                    sft_count += 1

    print(f"Extraction Complete!")
    print(f"Generated {dpo_count} DPO pairs (Failed -> Success trajectories)")
    print(f"Generated {sft_count} SFT examples (First-try success trajectories)")
    print(f"Files saved to:\n- {out_file}\n- {sft_file}")

if __name__ == "__main__":
    asyncio.run(extract_dpo_trajectories())
