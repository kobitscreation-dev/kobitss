import asyncio
import os
import subprocess
from pyngrok import ngrok
from backend.models.github import PullRequest

class EphemeralPreviewRunner:
    """
    Manages ephemeral dev servers (npm run dev / uvicorn) 
    and exposes them via ngrok tunnels.
    """
    active_tunnels = {}

    @classmethod
    async def start_preview(cls, mission_id: str, repo_path: str, port: int = 3000):
        """Starts a dev server and opens an ngrok tunnel."""
        # Note: In production, we'd detect package.json or requirements.txt
        # to run `npm run dev` or `uvicorn`.
        # Mocking the subprocess start for safety in this backend env
        print(f"Starting ephemeral preview for {mission_id} on port {port}")
        
        try:
            # Start ngrok tunnel
            public_url = ngrok.connect(port).public_url
            cls.active_tunnels[mission_id] = public_url
            
            print(f"Preview running at {public_url}")
            return public_url
        except Exception as e:
            print(f"Failed to start ngrok: {e}")
            return None

    @classmethod
    async def inject_pr_comment(cls, db, pr_id: str, preview_url: str):
        """Injects the preview URL into the GitHub PR."""
        from backend.models.github import PullRequest
        from sqlalchemy import select
        
        stmt = select(PullRequest).where(PullRequest.id == pr_id)
        pr = (await db.execute(stmt)).scalars().first()
        
        if pr and pr.github_pr_id:
            # In production, use httpx to POST to GitHub API:
            # /repos/{owner}/{repo}/issues/{pr_id}/comments
            print(f"Mock GitHub API Call: Added comment to PR #{pr.github_pr_id}: 'Live Preview URL: {preview_url}'")
            return True
        return False
