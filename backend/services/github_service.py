import httpx
import json
import logging

logger = logging.getLogger(__name__)

# To support deterministic testing, we can override this globally in tests
MOCK_GITHUB_API = False
MOCK_PR_STATE = {}

class GitHubService:
    BASE_URL = "https://api.github.com"
    
    @staticmethod
    def _get_headers(token: str):
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

    @classmethod
    async def create_pull_request(cls, repo_full_name: str, title: str, head: str, base: str, body: str, token: str) -> dict:
        """Create a PR on GitHub."""
        if MOCK_GITHUB_API:
            pr_number = len(MOCK_PR_STATE) + 1
            mock_pr = {
                "id": f"mock_pr_{pr_number}",
                "number": pr_number,
                "html_url": f"https://github.com/{repo_full_name}/pull/{pr_number}",
                "state": "open",
                "title": title,
                "body": body,
                "head": {"ref": head, "sha": "mock-head-sha"},
                "base": {"ref": base, "sha": "mock-base-sha"}
            }
            MOCK_PR_STATE[pr_number] = mock_pr
            return mock_pr

        url = f"{cls.BASE_URL}/repos/{repo_full_name}/pulls"
        payload = {
            "title": title,
            "head": head,
            "base": base,
            "body": body
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=cls._get_headers(token))
            if resp.status_code == 201:
                return resp.json()
            else:
                logger.error(f"GitHub PR creation failed: {resp.text}")
                return {"error": resp.text, "status_code": resp.status_code}

    @classmethod
    async def get_pull_request(cls, repo_full_name: str, pr_number: int, token: str) -> dict:
        """Fetch PR details from GitHub."""
        if MOCK_GITHUB_API:
            return MOCK_PR_STATE.get(pr_number, {"error": "Not Found"})

        url = f"{cls.BASE_URL}/repos/{repo_full_name}/pulls/{pr_number}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=cls._get_headers(token))
            if resp.status_code == 200:
                return resp.json()
            else:
                return {"error": resp.text, "status_code": resp.status_code}
                
    @classmethod
    async def get_pr_reviews(cls, repo_full_name: str, pr_number: int, token: str) -> list:
        if MOCK_GITHUB_API:
            return []
            
        url = f"{cls.BASE_URL}/repos/{repo_full_name}/pulls/{pr_number}/reviews"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=cls._get_headers(token))
            if resp.status_code == 200:
                return resp.json()
            return []

    @classmethod
    async def get_check_runs(cls, repo_full_name: str, ref: str, token: str) -> dict:
        if MOCK_GITHUB_API:
            return {"total_count": 0, "check_runs": []}
            
        url = f"{cls.BASE_URL}/repos/{repo_full_name}/commits/{ref}/check-runs"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=cls._get_headers(token))
            if resp.status_code == 200:
                return resp.json()
            return {"total_count": 0, "check_runs": []}

    @classmethod
    async def merge_pull_request(cls, repo_full_name: str, pr_number: int, commit_title: str, token: str) -> dict:
        if MOCK_GITHUB_API:
            if pr_number in MOCK_PR_STATE:
                MOCK_PR_STATE[pr_number]["state"] = "closed"
                MOCK_PR_STATE[pr_number]["merged"] = True
                return {"merged": True, "message": "Pull Request successfully merged"}
            return {"error": "Not Found"}

        url = f"{cls.BASE_URL}/repos/{repo_full_name}/pulls/{pr_number}/merge"
        payload = {"commit_title": commit_title}
        async with httpx.AsyncClient() as client:
            resp = await client.put(url, json=payload, headers=cls._get_headers(token))
            if resp.status_code in (200, 201):
                return resp.json()
            return {"error": resp.text, "status_code": resp.status_code}

    @classmethod
    async def create_issue_comment(cls, repo_full_name: str, issue_number: int, body: str, token: str) -> dict:
        """Post an informative comment on a GitHub issue or PR."""
        if MOCK_GITHUB_API:
            return {"id": 12345, "body": body}

        url = f"{cls.BASE_URL}/repos/{repo_full_name}/issues/{issue_number}/comments"
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={"body": body}, headers=cls._get_headers(token))
            if resp.status_code in (200, 201):
                return resp.json()
            logger.error(f"GitHub issue comment failed: {resp.text}")
            return {"error": resp.text, "status_code": resp.status_code}

    @classmethod
    async def reply_to_pr_review_comment(cls, repo_full_name: str, pr_number: int, comment_id: int, body: str, token: str) -> dict:
        """Reply directly to a line-level review comment in a Pull Request."""
        if MOCK_GITHUB_API:
            return {"id": 123456, "body": body}

        url = f"{cls.BASE_URL}/repos/{repo_full_name}/pulls/{pr_number}/comments/{comment_id}/replies"
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={"body": body}, headers=cls._get_headers(token))
            if resp.status_code in (200, 201):
                return resp.json()
            # Fallback to standard PR issue comment if thread reply fails
            fallback_url = f"{cls.BASE_URL}/repos/{repo_full_name}/issues/{pr_number}/comments"
            fb_resp = await client.post(fallback_url, json={"body": body}, headers=cls._get_headers(token))
            if fb_resp.status_code in (200, 201):
                return fb_resp.json()
            logger.error(f"GitHub review comment reply failed: {resp.text}")
            return {"error": resp.text, "status_code": resp.status_code}

    @classmethod
    async def create_commit_status(
        cls,
        repo_full_name: str,
        sha: str,
        state: str,
        description: str,
        context: str = "Kobits AI / Verification Gate",
        token: str = "",
        target_url: str = "https://kobits.space"
    ) -> dict:
        """
        Post a commit status check directly to GitHub (pending | success | failure | error).
        Gives real-time PR check run feedback in GitHub's conversation UI.
        """
        if MOCK_GITHUB_API:
            return {"state": state, "description": description, "context": context}

        if not token or not repo_full_name or not sha:
            return {"error": "Missing required fields for commit status"}

        url = f"{cls.BASE_URL}/repos/{repo_full_name}/statuses/{sha}"
        payload = {
            "state": state,
            "description": description[:140],
            "context": context,
            "target_url": target_url or "https://kobits.space"
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=cls._get_headers(token))
            if resp.status_code in (200, 201):
                return resp.json()
            logger.warning(f"GitHub commit status update ({state}) returned {resp.status_code}: {resp.text}")
            return {"error": resp.text, "status_code": resp.status_code}



