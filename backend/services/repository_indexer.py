"""
Repository Indexer — triggers re-indexing of a repository after a merge.
In production this would call the existing indexing pipeline.
"""
import logging

logger = logging.getLogger(__name__)


class RepositoryIndexer:
    @staticmethod
    async def trigger_reindex(repository_id: str) -> None:
        """Trigger background re-index of the repository. Best-effort, non-blocking."""
        logger.info(f"[RepositoryIndexer] Triggering re-index for repository {repository_id}")
        # In production: call the existing indexing job / queue task.
        # Here we record the event and return. The real indexer picks it up asynchronously.
