import asyncio
import json
import logging
import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.models.github import (
    Repository, PullRequest, PullRequestStatus, GitHubConnection
)
from backend.models.mission import Mission, MissionStatus, WorkflowPhase, ApprovalStatus
from backend.models.project import (
    Project, Task, TaskStatus, TaskPriority, Activity, ActivityType
)
from backend.services.mission_runtime import MissionRuntime
from backend.services.github_service import GitHubService

logger = logging.getLogger("kobits.security.webhooks")

router = APIRouter()


@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Unified Webhook listener for GitHub.
    Handles:
    1. issues.opened: Creates new missions if @kobits is tagged or assigned.
    2. check_run / workflow_run: Extracts CI failures, traces, and annotations; feeds into
       Negative-to-Positive TDD Reproducer Engine and Best-of-N Candidate Search;
       automatically commits and pushes fixes directly to the branch with zero human intervention.
    3. pull_request_review_comment: Extracts human code review feedback on specific diff lines,
       runs TDD reproducer verification, and pushes corrections back to the PR branch.
    4. issue_comment: Handles general PR review conversation comments.
    5. pull_request_review: Handles Human Engineer Delivery Review / approvals.
    6. pull_request: Tracks PR lifecycle (synchronize, opened, closed).
    Multi-tenant isolation: Requires exact repository mapping, otherwise returns 404.
    """
    raw_event = request.headers.get("x-github-event") or request.headers.get("X-GitHub-Event") or ""
    event = raw_event.strip().lower()

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # ─────────────────────────────────────────────────────────────────
    # 1. ISSUES: OPENED
    # ─────────────────────────────────────────────────────────────────
    if event == "issues" and payload.get("action") == "opened":
        issue = payload.get("issue", {})
        repo = payload.get("repository", {})

        repo_url = repo.get("html_url")
        clone_url = repo.get("clone_url")
        full_name = repo.get("full_name")
        title = issue.get("title", "GitHub Issue")
        body = issue.get("body", "")

        body_lower = (body or "").lower()
        assignees = [a.get("login", "").lower() for a in issue.get("assignees", [])]

        if "@kobits" in body_lower or "kobits" in assignees:
            project = None
            if repo_url:
                stmt = select(Project).where(Project.repository_url == repo_url)
                result = await db.execute(stmt)
                project = result.scalars().first()

            if not project and (full_name or clone_url or repo_url):
                r_stmt = select(Repository).where(
                    (Repository.full_name == full_name) |
                    (Repository.clone_url == clone_url) |
                    (Repository.web_url == repo_url)
                )
                r_res = await db.execute(r_stmt)
                repo_rec = r_res.scalars().first()
                if repo_rec and repo_rec.project_id:
                    project = await db.get(Project, repo_rec.project_id)

            if not project:
                logger.warning(
                    f"Security Audit: GitHub webhook rejected. Unmapped repository: '{repo_url or full_name}'"
                )
                raise HTTPException(status_code=404, detail="Project not found for repository")

            m = Mission(
                id=str(uuid.uuid4()),
                organization_id=project.organization_id,
                project_id=project.id,
                repository_id=project.repository_id,
                created_by=project.created_by,
                title=f"[GitHub] {title}",
                objective=body or title,
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.INTAKE,
            )
            db.add(m)
            await db.commit()

            runtime = MissionRuntime(m.id)
            background_tasks.add_task(runtime.execute)

            return {"status": "success", "mission_id": m.id, "message": "Mission started from GitHub issue"}

    # ─────────────────────────────────────────────────────────────────
    # 2. CI FAILURES: CHECK_RUN / WORKFLOW_RUN
    # ─────────────────────────────────────────────────────────────────
    elif event in ("check_run", "workflow_run"):
        action = payload.get("action")
        check_run = payload.get("check_run", {})
        workflow_run = payload.get("workflow_run", {})
        is_workflow = (event == "workflow_run")

        head_sha = workflow_run.get("head_sha") if is_workflow else check_run.get("head_sha")
        check_name = (
            workflow_run.get("name", "GitHub Actions Workflow")
            if is_workflow
            else check_run.get("name", "CI Check")
        )
        conclusion = workflow_run.get("conclusion") if is_workflow else check_run.get("conclusion")
        status = workflow_run.get("status") if is_workflow else check_run.get("status")
        head_branch = (
            workflow_run.get("head_branch")
            if is_workflow
            else (check_run.get("check_suite", {}).get("head_branch") or "")
        )

        output_data = check_run.get("output", {})
        output_title = output_data.get("title", "")
        output_summary = output_data.get("summary", "")
        output_text = output_data.get("text", "")
        annotations = output_data.get("annotations", [])

        repo = payload.get("repository", {})
        repo_url = repo.get("html_url")
        clone_url = repo.get("clone_url")
        full_name = repo.get("full_name")

        project = None
        repo_rec = None
        if full_name or clone_url or repo_url:
            r_stmt = select(Repository).where(
                (Repository.full_name == full_name) |
                (Repository.clone_url == clone_url) |
                (Repository.web_url == repo_url)
            )
            repo_rec = (await db.execute(r_stmt)).scalars().first()
            if repo_rec and repo_rec.project_id:
                project = await db.get(Project, repo_rec.project_id)

        if not project and repo_url:
            p_stmt = select(Project).where(Project.repository_url == repo_url)
            project = (await db.execute(p_stmt)).scalars().first()

        if not project and full_name:
            p_stmt = select(Project).where(Project.name == full_name)
            project = (await db.execute(p_stmt)).scalars().first()

        if not project:
            logger.warning(f"Security Audit: GitHub CI webhook rejected. Unmapped repository: '{full_name or repo_url}'")
            raise HTTPException(status_code=404, detail="Project not found for repository")

        # Resolve PullRequest
        pr = None
        if head_sha:
            stmt = select(PullRequest).where(PullRequest.head_sha == head_sha)
            pr = (await db.execute(stmt)).scalars().first()
        if not pr and head_branch:
            stmt = select(PullRequest).where(PullRequest.source_branch == head_branch)
            pr = (await db.execute(stmt)).scalars().first()
        if not pr and check_run.get("pull_requests"):
            pr_num_str = str(check_run["pull_requests"][0].get("number"))
            stmt = select(PullRequest).where(
                (PullRequest.github_pr_id == pr_num_str) | (PullRequest.number == int(pr_num_str))
            )
            pr = (await db.execute(stmt)).scalars().first()
        if not pr and is_workflow and workflow_run.get("pull_requests"):
            pr_num_str = str(workflow_run["pull_requests"][0].get("number"))
            stmt = select(PullRequest).where(
                (PullRequest.github_pr_id == pr_num_str) | (PullRequest.number == int(pr_num_str))
            )
            pr = (await db.execute(stmt)).scalars().first()

        # Resolve Mission
        mission = None
        if pr and pr.mission_id:
            mission = await db.get(Mission, pr.mission_id)
        if not mission and head_branch:
            stmt = select(Mission).where(Mission.active_branch == head_branch)
            mission = (await db.execute(stmt)).scalars().first()
        if not mission and head_branch and "kobits" in head_branch.lower():
            stmt = select(Mission).where(Mission.active_branch.isnot(None))
            for m in (await db.execute(stmt)).scalars().all():
                if m.active_branch and (m.active_branch in head_branch or head_branch in m.active_branch):
                    mission = m
                    break

        if status == "completed":
            if conclusion == "success":
                if pr:
                    pr.checks_status = "success"
                    if pr.review_status == "approved":
                        pr.status = PullRequestStatus.MERGEABLE
                    await db.commit()
                return {"status": "processed", "conclusion": "success"}

            elif conclusion in ("failure", "timed_out", "action_required"):
                if pr:
                    pr.checks_status = "failure"
                    pr.status = PullRequestStatus.CHECKS_FAILED

                failure_lines = []
                if output_title:
                    failure_lines.append(f"**Check Title**: {output_title}")
                if output_summary:
                    failure_lines.append(f"**Summary**: {output_summary}")
                if output_text:
                    failure_lines.append(f"**Details**:\n```\n{output_text}\n```")
                if annotations:
                    failure_lines.append("**Failed Steps & Annotations:**")
                    for ann in annotations:
                        p = ann.get("path", "")
                        l = ann.get("start_line") or ann.get("line") or 1
                        msg = ann.get("message", "")
                        lvl = ann.get("annotation_level", "failure").upper()
                        failure_lines.append(f"- `[{lvl}]` `{p}:{l}`: {msg}")
                if is_workflow and workflow_run.get("html_url"):
                    failure_lines.append(f"**Workflow Run**: {workflow_run.get('html_url')}")

                target_branch = head_branch or (mission.active_branch if mission else "kobits/ci-healing")
                failure_details = "\n\n".join(failure_lines) if failure_lines else (
                    f"GitHub Actions CI '{check_name}' failed on branch `{target_branch}`."
                )

                if not mission:
                    mission = Mission(
                        id=str(uuid.uuid4()),
                        organization_id=project.organization_id,
                        project_id=project.id,
                        repository_id=project.repository_id or (repo_rec.id if repo_rec else None),
                        created_by=project.created_by,
                        title=f"Remediate CI Failure: {check_name}",
                        objective=failure_details,
                        status=MissionStatus.ACTIVE,
                        phase=WorkflowPhase.IMPLEMENTATION,
                        active_branch=target_branch,
                    )
                    db.add(mission)
                    await db.flush()
                else:
                    mission.status = MissionStatus.ACTIVE
                    mission.phase = WorkflowPhase.IMPLEMENTATION
                    target_branch = head_branch or mission.active_branch or "main"

                task_id = str(uuid.uuid4())
                remediation_task = Task(
                    id=task_id,
                    project_id=mission.project_id,
                    mission_id=mission.id,
                    title=f"Remediate CI Failure: {check_name}",
                    description=(
                        f"GitHub Actions CI check `{check_name}` failed on branch `{target_branch}`.\n\n"
                        f"{failure_details}\n\n"
                        f"Remediation Directive: Synthesize reproducer using Negative-to-Positive TDD Reproducer Engine, "
                        f"sample Best-of-N Candidate Patches for parsimonious diff, verify syntax & regression gates, "
                        f"and push verified fix directly to `{target_branch}`."
                    ),
                    status=TaskStatus.PENDING,
                    is_correction=True,
                    priority=TaskPriority.HIGH,
                    phase=WorkflowPhase.IMPLEMENTATION,
                    created_by=mission.created_by or "system_automator",
                    metadata_json=json.dumps({
                        "source": "ci_failure",
                        "agent_role": "BACKEND_ENGINEER",
                        "check_name": check_name,
                        "head_sha": head_sha,
                        "head_branch": target_branch,
                        "tdd_mode": True,
                        "best_of_n": True,
                        "annotations": annotations,
                    }),
                )
                db.add(remediation_task)

                activity = Activity(
                    organization_id=mission.organization_id,
                    project_id=mission.project_id,
                    type=ActivityType.TASK_CREATED,
                    title=f"CI Remediation Dispatched: {check_name}",
                    description=f"Auto-generated correction task {task_id[:8]} for CI failure on branch {target_branch}.",
                )
                db.add(activity)
                await db.commit()
                await db.refresh(remediation_task)

                try:
                    from backend.services.durable_queue import DurableQueue
                    await DurableQueue.enqueue(
                        task_type="EXECUTE_MISSION",
                        payload={
                            "mission_id": mission.id,
                            "remediation": True,
                            "task_id": remediation_task.id,
                            "check_name": check_name,
                        },
                        queue_name="missions",
                        priority=10,
                        job_id=f"ci_remediation_{mission.id}_{uuid.uuid4().hex[:6]}",
                    )
                except Exception:
                    pass

                background_tasks.add_task(MissionRuntime.run_mission_background, mission.id)

                gh_token = ""
                pr_num = pr.github_pr_id if (pr and pr.github_pr_id) else None
                if not pr_num and check_run.get("pull_requests"):
                    pr_num = check_run["pull_requests"][0].get("number")
                if not pr_num and is_workflow and workflow_run.get("pull_requests"):
                    pr_num = workflow_run["pull_requests"][0].get("number")

                if repo_rec and repo_rec.github_connection_id:
                    conn = await db.get(GitHubConnection, repo_rec.github_connection_id)
                    if conn and conn.metadata_json:
                        try:
                            gh_token = json.loads(conn.metadata_json).get("token", "")
                        except Exception:
                            pass

                if gh_token and pr_num and (full_name or (repo_rec and repo_rec.full_name)):
                    r_name = full_name or repo_rec.full_name
                    comment_body = (
                        f"🤖 **Kobits CI Remediation Dispatched** (Task `{remediation_task.id[:8]}` on Mission `{mission.id[:8]}`)\n\n"
                        f"- **Failed Check**: `{check_name}`\n"
                        f"- **Branch**: `{target_branch}`\n"
                        f"- **Logs & Annotations**:\n{failure_details[:400]}...\n\n"
                        f"Kobits is reproducing the failure in the isolated sandbox, generating the fix via Best-of-N Candidate Search, "
                        f"running verification gates, and pushing directly to `{target_branch}`."
                    )
                    background_tasks.add_task(
                        GitHubService.create_issue_comment,
                        r_name,
                        int(pr_num),
                        comment_body,
                        gh_token,
                    )

                return {
                    "status": "ci_remediation_dispatched",
                    "mission_id": mission.id,
                    "task_id": remediation_task.id,
                    "branch": target_branch,
                    "check_name": check_name,
                }

    # ─────────────────────────────────────────────────────────────────
    # 3. CODE REVIEW COMMENTS: PULL_REQUEST_REVIEW_COMMENT
    # ─────────────────────────────────────────────────────────────────
    elif event == "pull_request_review_comment":
        action = payload.get("action")
        if action not in ("created", "edited"):
            return {"status": "ignored", "reason": f"action '{action}' not actionable"}

        comment_data = payload.get("comment", {})
        pr_data = payload.get("pull_request", {})
        repo_data = payload.get("repository", {})
        sender_data = payload.get("sender", {})
        sender_login = (sender_data.get("login") or "").lower()

        if sender_login.endswith("[bot]") or "kobits" in sender_login:
            return {"status": "ignored", "reason": "ignoring bot sender to prevent loop"}

        comment_body = comment_data.get("body", "")
        file_path = comment_data.get("path")
        line_num = comment_data.get("line") or comment_data.get("original_line")
        diff_hunk = comment_data.get("diff_hunk", "")
        comment_id = comment_data.get("id")
        pr_number = pr_data.get("number")
        repo_full_name = repo_data.get("full_name")
        pr_head_branch = pr_data.get("head", {}).get("ref")

        if not repo_full_name or not pr_head_branch:
            return {"status": "ignored", "reason": "missing repository or PR branch context"}

        project = None
        repo_rec = None
        r_stmt = select(Repository).where(Repository.full_name == repo_full_name)
        repo_rec = (await db.execute(r_stmt)).scalars().first()
        if repo_rec and repo_rec.project_id:
            project = await db.get(Project, repo_rec.project_id)

        if not project:
            p_stmt = select(Project).where(Project.repository_url.contains(repo_full_name))
            project = (await db.execute(p_stmt)).scalars().first()

        if not project:
            logger.warning(f"Security Audit: GitHub webhook rejected. Unmapped repo: '{repo_full_name}'")
            raise HTTPException(status_code=404, detail="Project not found for repository")

        has_mention = any(m in comment_body.lower() for m in ("@kobits", "@kobits-ai", "@kobits[bot]"))
        is_kobits_pr = pr_head_branch.startswith("kobits/") or "kobits" in pr_head_branch.lower()

        pr_stmt = select(PullRequest).where(
            (PullRequest.github_pr_id == str(pr_number)) |
            (PullRequest.number == pr_number) |
            (PullRequest.source_branch == pr_head_branch)
        )
        tracked_pr = (await db.execute(pr_stmt)).scalars().first()
        if tracked_pr:
            is_kobits_pr = True

        if not (has_mention or is_kobits_pr):
            return {"status": "ignored", "reason": "neither @kobits mention nor Kobits PR branch"}

        gh_token = ""
        if repo_rec and repo_rec.github_connection_id:
            conn = await db.get(GitHubConnection, repo_rec.github_connection_id)
            if conn and conn.metadata_json:
                try:
                    gh_token = json.loads(conn.metadata_json).get("token", "")
                except Exception:
                    pass

        cleaned_prompt = re.sub(r"@kobits(-ai)?(\[bot\])?", "", comment_body, flags=re.IGNORECASE).strip()
        if not cleaned_prompt:
            cleaned_prompt = comment_body

        objective = (
            f"Address PR #{pr_number} review comment from @{sender_login} on `{file_path}`"
            f"{f' (line {line_num})' if line_num else ''}:\n\n"
            f"Instruction: {cleaned_prompt}\n\n"
            f"Diff Context:\n```\n{diff_hunk}\n```"
        )

        existing_mission = None
        if tracked_pr and tracked_pr.mission_id:
            existing_mission = await db.get(Mission, tracked_pr.mission_id)
        if not existing_mission:
            stmt = select(Mission).where(Mission.active_branch == pr_head_branch)
            existing_mission = (await db.execute(stmt)).scalars().first()

        if not existing_mission:
            existing_mission = Mission(
                id=str(uuid.uuid4()),
                organization_id=project.organization_id,
                project_id=project.id,
                repository_id=project.repository_id or (repo_rec.id if repo_rec else None),
                created_by=project.created_by,
                title=f"[PR #{pr_number} Review] Fix {os.path.basename(file_path or 'code')}",
                objective=objective,
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.IMPLEMENTATION,
                active_branch=pr_head_branch,
            )
            db.add(existing_mission)
            await db.flush()
        else:
            existing_mission.status = MissionStatus.ACTIVE
            existing_mission.phase = WorkflowPhase.IMPLEMENTATION

        task_id = str(uuid.uuid4())
        correction_task = Task(
            id=task_id,
            project_id=existing_mission.project_id,
            mission_id=existing_mission.id,
            title=f"[PR #{pr_number} Review] Fix {os.path.basename(file_path or 'file')}:{line_num or 1}",
            description=objective,
            status=TaskStatus.PENDING,
            is_correction=True,
            priority=TaskPriority.HIGH,
            phase=WorkflowPhase.IMPLEMENTATION,
            created_by=existing_mission.created_by or "system_automator",
            metadata_json=json.dumps({
                "source": "pr_review_comment",
                "agent_role": "BACKEND_ENGINEER",
                "comment_id": comment_id,
                "pr_number": pr_number,
                "file_path": file_path,
                "line_num": line_num,
                "tdd_mode": True,
                "best_of_n": True,
            }),
        )
        db.add(correction_task)

        activity = Activity(
            organization_id=existing_mission.organization_id,
            project_id=existing_mission.project_id,
            type=ActivityType.TASK_CREATED,
            title=f"PR Review Feedback Received: PR #{pr_number}",
            description=f"Queued correction task {task_id[:8]} for review feedback on `{file_path}`.",
        )
        db.add(activity)
        await db.commit()
        await db.refresh(correction_task)

        try:
            from backend.services.durable_queue import DurableQueue
            await DurableQueue.enqueue(
                task_type="EXECUTE_MISSION",
                payload={
                    "mission_id": existing_mission.id,
                    "remediation": True,
                    "task_id": correction_task.id,
                    "comment_id": comment_id,
                },
                queue_name="missions",
                priority=15,
                job_id=f"pr_review_{existing_mission.id}_{uuid.uuid4().hex[:6]}",
            )
        except Exception:
            pass

        background_tasks.add_task(MissionRuntime.run_mission_background, existing_mission.id)

        if gh_token and comment_id and repo_full_name:
            reply_body = (
                f"🤖 **Kobits Review Remediation In Progress**\n\n"
                f"I've queued this feedback into the TDD reproducer engine (Task `{correction_task.id[:8]}` on Mission `{existing_mission.id[:8]}`). "
                f"Synthesizing reproducer test, evaluating Best-of-N candidate patches, and will push the verified fix directly to `{pr_head_branch}`."
            )
            background_tasks.add_task(
                GitHubService.reply_to_pr_review_comment,
                repo_full_name,
                int(pr_number),
                int(comment_id),
                reply_body,
                gh_token,
            )

        return {
            "status": "pr_review_correction_dispatched",
            "mission_id": existing_mission.id,
            "task_id": correction_task.id,
            "file_path": file_path,
            "pr_number": pr_number,
        }

    # ─────────────────────────────────────────────────────────────────
    # 4. PR CONVERSATION COMMENTS: ISSUE_COMMENT
    # ─────────────────────────────────────────────────────────────────
    elif event == "issue_comment":
        action = payload.get("action")
        if action != "created":
            return {"status": "ignored"}

        issue_data = payload.get("issue", {})
        comment_data = payload.get("comment", {})
        repo_data = payload.get("repository", {})
        sender_data = payload.get("sender", {})
        sender_login = (sender_data.get("login") or "").lower()

        if sender_login.endswith("[bot]") or "kobits" in sender_login:
            return {"status": "ignored", "reason": "ignoring bot sender to prevent loop"}

        is_pr_comment = bool(issue_data.get("pull_request"))
        if not is_pr_comment:
            return {"status": "ignored", "reason": "not a pull request comment"}

        comment_body = comment_data.get("body", "")
        pr_number = issue_data.get("number")
        repo_full_name = repo_data.get("full_name")

        project = None
        repo_rec = None
        if repo_full_name:
            r_stmt = select(Repository).where(Repository.full_name == repo_full_name)
            repo_rec = (await db.execute(r_stmt)).scalars().first()
            if repo_rec and repo_rec.project_id:
                project = await db.get(Project, repo_rec.project_id)

        if not project:
            raise HTTPException(status_code=404, detail="Project not found for repository")

        tracked_pr = None
        stmt = select(PullRequest).where(
            (PullRequest.github_pr_id == str(pr_number)) | (PullRequest.number == pr_number)
        )
        tracked_pr = (await db.execute(stmt)).scalars().first()

        existing_mission = None
        if tracked_pr and tracked_pr.mission_id:
            existing_mission = await db.get(Mission, tracked_pr.mission_id)

        if not existing_mission:
            return {"status": "ignored", "reason": "PR not associated with active Kobits mission"}

        existing_mission.status = MissionStatus.ACTIVE
        existing_mission.phase = WorkflowPhase.IMPLEMENTATION

        task_id = str(uuid.uuid4())
        correction_task = Task(
            id=task_id,
            project_id=existing_mission.project_id,
            mission_id=existing_mission.id,
            title=f"[PR #{pr_number} Conversation] Address Reviewer Feedback",
            description=f"PR #{pr_number} Comment from @{sender_login}:\n\n{comment_body}",
            status=TaskStatus.PENDING,
            is_correction=True,
            priority=TaskPriority.HIGH,
            phase=WorkflowPhase.IMPLEMENTATION,
            created_by=existing_mission.created_by or "system_automator",
            metadata_json=json.dumps({
                "source": "pr_issue_comment",
                "agent_role": "BACKEND_ENGINEER",
                "pr_number": pr_number,
                "tdd_mode": True,
                "best_of_n": True,
            }),
        )
        db.add(correction_task)
        await db.commit()
        await db.refresh(correction_task)

        background_tasks.add_task(MissionRuntime.run_mission_background, existing_mission.id)

        return {
            "status": "pr_comment_correction_dispatched",
            "mission_id": existing_mission.id,
            "task_id": correction_task.id,
        }

    # ─────────────────────────────────────────────────────────────────
    # 5. HUMAN ENGINEER DELIVERY REVIEW: PULL_REQUEST_REVIEW
    # ─────────────────────────────────────────────────────────────────
    elif event == "pull_request_review":
        action = payload.get("action")
        review_data = payload.get("review", {})
        pr_data = payload.get("pull_request", {})
        pr_number = pr_data.get("number")

        stmt = select(PullRequest).where(
            (PullRequest.github_pr_id == str(pr_number)) | (PullRequest.number == pr_number)
        )
        pr = (await db.execute(stmt)).scalars().first()

        mission = None
        if pr and pr.mission_id:
            mission = await db.get(Mission, pr.mission_id)

        if action == "submitted":
            state = review_data.get("state", "").lower()
            if state == "approved":
                if pr:
                    pr.review_status = "approved"
                    if pr.checks_status == "success":
                        pr.status = PullRequestStatus.MERGEABLE
                    else:
                        pr.status = PullRequestStatus.APPROVED

                # Human Engineer Delivery Review approval
                if mission and (mission.phase == WorkflowPhase.DELIVERY_REVIEW or mission.status == MissionStatus.AWAITING_APPROVAL):
                    mission.approval_status = ApprovalStatus.APPROVED
                    mission.status = MissionStatus.ACTIVE
                    mission.phase = WorkflowPhase.DEPLOYMENT
                    mission.current_stage = "Deployment"

                await db.commit()
                return {"status": "processed", "review_status": "approved"}

            elif state == "changes_requested":
                if pr:
                    pr.review_status = "changes_requested"
                    pr.status = PullRequestStatus.CHANGES_REQUESTED

                if mission:
                    mission.status = MissionStatus.ACTIVE
                    mission.phase = WorkflowPhase.IMPLEMENTATION

                    body_text = review_data.get("body", "Changes requested by reviewer")
                    task_id = str(uuid.uuid4())
                    correction_task = Task(
                        id=task_id,
                        project_id=mission.project_id,
                        mission_id=mission.id,
                        title=f"[PR #{pr_number} Review] Changes Requested",
                        description=f"Human Reviewer Feedback:\n\n{body_text}",
                        status=TaskStatus.PENDING,
                        is_correction=True,
                        priority=TaskPriority.HIGH,
                        phase=WorkflowPhase.IMPLEMENTATION,
                        created_by=mission.created_by or "system_automator",
                        metadata_json=json.dumps({
                            "source": "pr_changes_requested",
                            "agent_role": "BACKEND_ENGINEER",
                            "tdd_mode": True,
                            "best_of_n": True,
                        }),
                    )
                    db.add(correction_task)
                    await db.commit()
                    background_tasks.add_task(MissionRuntime.run_mission_background, mission.id)

                return {"status": "changes_requested_dispatched"}

    # ─────────────────────────────────────────────────────────────────
    # 6. PULL_REQUEST LIFECYCLE
    # ─────────────────────────────────────────────────────────────────
    elif event == "pull_request":
        action = payload.get("action")
        pr_data = payload.get("pull_request", {})
        pr_number = pr_data.get("number")
        stmt = select(PullRequest).where(
            (PullRequest.github_pr_id == str(pr_number)) | (PullRequest.number == pr_number)
        )
        pr = (await db.execute(stmt)).scalars().first()

        if pr:
            if action == "closed":
                pr.status = PullRequestStatus.MERGED if pr_data.get("merged") else PullRequestStatus.CLOSED
            elif action in ("opened", "reopened", "synchronize"):
                pr.head_sha = pr_data.get("head", {}).get("sha", pr.head_sha)
                pr.status = PullRequestStatus.OPEN
            await db.commit()
            return {"status": "processed", "action": action}

    return {"status": "ignored"}


@router.post("/jira")
async def jira_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook listener for Jira.
    Multi-tenant isolation: Requires project mapping by key or id, otherwise returns 404.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    issue = payload.get("issue", {})
    fields = issue.get("fields", {})
    
    title = fields.get("summary", "Jira Issue")
    body = fields.get("description", "")
    
    jira_proj = fields.get("project", {})
    jira_proj_key = jira_proj.get("key") or jira_proj.get("id") or payload.get("project_key")
    
    project = None
    if jira_proj_key:
        stmt = select(Project).where(
            (Project.name == jira_proj_key) | (Project.id == jira_proj_key)
        )
        result = await db.execute(stmt)
        project = result.scalars().first()
    
    if not project:
        logger.warning(
            f"Security Audit: Jira webhook rejected. Unmapped project key: '{jira_proj_key}'"
        )
        raise HTTPException(status_code=404, detail="Project not found for Jira issue")
    
    m = Mission(
        id=str(uuid.uuid4()),
        organization_id=project.organization_id,
        project_id=project.id,
        repository_id=project.repository_id,
        created_by=project.created_by,
        title=f"[Jira] {title}",
        objective=body or title,
        status=MissionStatus.ACTIVE,
        phase=WorkflowPhase.INTAKE
    )
    db.add(m)
    await db.commit()
    
    runtime = MissionRuntime(m.id)
    asyncio.create_task(runtime.execute())
    
    return {"status": "success", "mission_id": m.id}


@router.post("/linear")
async def linear_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook listener for Linear.
    Multi-tenant isolation: Requires project mapping by team/project id, otherwise returns 404.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    action = payload.get("action")
    if action == "create" and payload.get("type") == "Issue":
        data = payload.get("data", {})
        title = data.get("title", "Linear Issue")
        body = data.get("description", "")
        
        linear_team = data.get("teamId") or data.get("projectId") or (
            data.get("team", {}).get("key") if isinstance(data.get("team"), dict) else None
        )
        
        project = None
        if linear_team:
            stmt = select(Project).where(
                (Project.name == linear_team) | (Project.id == linear_team)
            )
            result = await db.execute(stmt)
            project = result.scalars().first()
        
        if not project:
            logger.warning(
                f"Security Audit: Linear webhook rejected. Unmapped team/project: '{linear_team}'"
            )
            raise HTTPException(status_code=404, detail="Project not found for Linear issue")
        
        m = Mission(
            id=str(uuid.uuid4()),
            organization_id=project.organization_id,
            project_id=project.id,
            repository_id=project.repository_id,
            created_by=project.created_by,
            title=f"[Linear] {title}",
            objective=body or title,
            status=MissionStatus.ACTIVE,
            phase=WorkflowPhase.INTAKE
        )
        db.add(m)
        await db.commit()
        
        runtime = MissionRuntime(m.id)
        asyncio.create_task(runtime.execute())
        
        return {"status": "success", "mission_id": m.id}

    return {"status": "ignored"}


@router.post("/slack")
async def slack_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook listener for Slack Events API.
    Handles URL verification and app_mention events.
    Multi-tenant isolation: Requires channel mapping to Project, otherwise returns 404.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    # 1. Slack URL Verification Challenge
    if payload.get("type") == "url_verification":
        return Response(content=payload.get("challenge"), media_type="text/plain")
        
    # 2. Handle actual Slack events
    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        
        if event.get("type") == "app_mention":
            text = event.get("text", "")
            channel = event.get("channel")
            
            cleaned_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
            if not cleaned_text:
                return {"status": "ignored", "reason": "empty prompt"}
            
            project = None
            if channel:
                stmt = select(Project).where(
                    (Project.id == channel) | (Project.name == channel)
                )
                result = await db.execute(stmt)
                project = result.scalars().first()
            
            if not project:
                logger.warning(
                    f"Security Audit: Slack webhook rejected. Unmapped channel: '{channel}'"
                )
                raise HTTPException(status_code=404, detail=f"Project not mapped for Slack channel {channel}")
            
            m = Mission(
                id=str(uuid.uuid4()),
                organization_id=project.organization_id,
                project_id=project.id,
                repository_id=project.repository_id,
                created_by=project.created_by,
                title=f"[Slack Request] {cleaned_text[:30]}...",
                objective=cleaned_text,
                status=MissionStatus.ACTIVE,
                phase=WorkflowPhase.INTAKE
            )
            db.add(m)
            await db.commit()
            
            runtime = MissionRuntime(m.id)
            asyncio.create_task(runtime.execute())
            
            return {"status": "success", "mission_id": m.id}

    return {"status": "ignored"}
