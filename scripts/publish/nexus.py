"""
Nexus 3 raw (hosted) repository.
"""

import logging
from pathlib import Path
from typing import Iterable, Optional

import httpx

from scripts.build.artifact import ArtifactRef
from scripts.config.models import NexusRepositoryEndpoint
from scripts.core.exceptions import PublishError
from scripts.publish.base import ArtifactRepository, existing, request

logger = logging.getLogger(__name__)


class NexusRepository(ArtifactRepository):
    def __init__(self, name: str, ep: NexusRepositoryEndpoint,
                 transport: Optional[httpx.BaseTransport] = None):
        self.name, self.ep = name, ep
        auth = (ep.username, ep.password) if ep.username and ep.password else None
        self.can_write = auth is not None
        self.client = httpx.Client(auth=auth, verify=ep.verify_tls, timeout=ep.timeout,
                                   transport=transport, follow_redirects=True)

    def _url(self, ref: ArtifactRef, filename: str) -> str:
        return f"{self.ep.get_base_url()}/{ref.remote_path(self.ep.path_template, filename)}"

    def exists(self, ref: ArtifactRef) -> bool:
        resp = request(self.client, "HEAD", self._url(ref, ref.filename),
                       retries=self.ep.retry, ok=(200, 404))
        return resp.status_code == 200

    def upload(self, ref: ArtifactRef, files: Iterable[Path]) -> bool:
        if not self.can_write:
            raise PublishError(f"[{self.name}] no credentials (NEXUS_USER / NEXUS_PASSWORD)")
        if self.exists(ref):
            logger.info("[%s] %s already present, skipping", self.name, ref.filename)
            return False
        # tarball last: its presence marks the artifact as complete
        ordered = sorted(existing(files), key=lambda p: p.name == ref.filename)
        for f in ordered:
            with open(f, "rb") as fh:
                request(self.client, "PUT", self._url(ref, f.name), retries=self.ep.retry,
                        ok=(200, 201, 204), content=fh.read())
            logger.info("[%s] uploaded %s", self.name, f.name)
        return True

    def download(self, ref: ArtifactRef, dest_dir: Path) -> bool:
        if not self.exists(ref):
            return False
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name in ref.all_files:
            resp = request(self.client, "GET", self._url(ref, name), retries=self.ep.retry, ok=(200, 404))
            if resp.status_code == 200:
                (dest_dir / name).write_bytes(resp.content)
            elif name == ref.filename:
                return False
        return True
