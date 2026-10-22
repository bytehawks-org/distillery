"""
Applies the configured repository strategy across endpoints.
"""

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import httpx

from scripts.build.artifact import ArtifactRef
from scripts.config.models import (GitHubRepositoryEndpoint, NexusRepositoryEndpoint,
                                   RepositoryConfig, RepositoryStrategy)
from scripts.core.exceptions import PublishError
from scripts.publish.base import ArtifactRepository
from scripts.publish.github import GitHubReleaseRepository
from scripts.publish.nexus import NexusRepository

logger = logging.getLogger(__name__)


class RepositoryManager:
    def __init__(self, config: RepositoryConfig,
                 transports: Optional[Dict[str, httpx.BaseTransport]] = None):
        self.config = config
        transports = transports or {}
        self.repos: List[ArtifactRepository] = []
        for name, ep in config.endpoints():
            if isinstance(ep, NexusRepositoryEndpoint):
                self.repos.append(NexusRepository(name, ep, transports.get(name)))
            elif isinstance(ep, GitHubRepositoryEndpoint):
                self.repos.append(GitHubReleaseRepository(name, ep, transports.get(name)))
            else:
                logger.warning("Repository '%s' (type %s) is not supported yet, ignoring", name, ep.type)

        n_primary = len(config.primary)
        self._primary = self.repos[:n_primary]

    # -- queries -------------------------------------------------------

    def exists(self, ref: ArtifactRef) -> bool:
        """True if the artifact is available from at least one reachable endpoint."""
        for repo in self.repos:
            try:
                if repo.exists(ref):
                    return True
            except PublishError as e:
                logger.warning("[%s] existence check failed: %s", repo.name, e)
        return False

    def fetch(self, ref: ArtifactRef, dest_dir: Path) -> bool:
        for repo in self.repos:
            try:
                if repo.download(ref, dest_dir):
                    return True
            except PublishError as e:
                logger.warning("[%s] download failed: %s", repo.name, e)
        return False

    # -- publishing ----------------------------------------------------

    def publish(self, ref: ArtifactRef, files: Iterable[Path]) -> Dict[str, str]:
        """
        Publish according to strategy. Returns {endpoint: 'uploaded'|'exists'|'error: ...'}.
        Raises PublishError if the strategy's requirements are not met.
        """
        files = list(files)
        strategy = self.config.strategy
        status: Dict[str, str] = {}

        if strategy == RepositoryStrategy.PRIMARY_ONLY:
            targets, need = self._primary, "all"
        elif strategy == RepositoryStrategy.PRIMARY_WITH_FALLBACK:
            targets, need = self.repos, "any"
        else:
            targets, need = self.repos, "all"

        for repo in targets:
            try:
                status[repo.name] = "uploaded" if repo.upload(ref, files) else "exists"
                if strategy == RepositoryStrategy.PRIMARY_WITH_FALLBACK:
                    break
            except PublishError as e:
                logger.error("[%s] publish failed: %s", repo.name, e)
                status[repo.name] = f"error: {e}"

        ok = [s for s in status.values() if not s.startswith("error")]
        failed = [n for n, s in status.items() if s.startswith("error")]
        if (need == "all" and failed) or (need == "any" and not ok):
            raise PublishError(f"Publish of {ref.filename} failed on: {', '.join(failed) or 'all endpoints'}")
        return status
