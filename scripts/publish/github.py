"""
GitHub Releases as artifact store: one release per package version, one asset per file.
"""

import logging
from pathlib import Path
from typing import Dict, Iterable, Optional

import httpx

from scripts.build.artifact import ArtifactRef
from scripts.config.models import GitHubRepositoryEndpoint
from scripts.core.exceptions import PublishError
from scripts.publish.base import ArtifactRepository, existing, request

logger = logging.getLogger(__name__)


class GitHubReleaseRepository(ArtifactRepository):
    def __init__(self, name: str, ep: GitHubRepositoryEndpoint,
                 transport: Optional[httpx.BaseTransport] = None):
        self.name, self.ep = name, ep
        self.can_write = bool(ep.token)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "bytehawks-distillery"}
        if ep.token:
            headers["Authorization"] = f"Bearer {ep.token}"
        self.client = httpx.Client(headers=headers, timeout=ep.timeout, transport=transport,
                                   follow_redirects=True)

    # -- helpers -------------------------------------------------------

    def _tag(self, ref: ArtifactRef) -> str:
        return self.ep.tag_template.format(package=ref.package, full_version=ref.version,
                                           version=ref.version, major_minor=ref.major_minor)

    def _release(self, ref: ArtifactRef, create: bool) -> Optional[Dict]:
        base = f"{self.ep.api_url}/repos/{self.ep.repo}/releases"
        tag = self._tag(ref)
        resp = request(self.client, "GET", f"{base}/tags/{tag}", retries=self.ep.retry, ok=(200, 404))
        if resp.status_code == 200:
            return resp.json()
        if not create:
            return None
        resp = request(self.client, "POST", base, retries=self.ep.retry, ok=(201, 422), json={
            "tag_name": tag, "name": f"{ref.package} {ref.version}",
            "body": f"Static {ref.libc} build of **{ref.package} {ref.version}** "
                    f"published by bytehawks-distillery.",
            "draft": False, "prerelease": False,
        })
        if resp.status_code == 422:  # lost a race with a parallel job
            return self._release(ref, create=False)
        return resp.json()

    # -- interface -----------------------------------------------------

    def exists(self, ref: ArtifactRef) -> bool:
        rel = self._release(ref, create=False)
        return bool(rel) and any(a["name"] == ref.filename for a in rel.get("assets", []))

    def upload(self, ref: ArtifactRef, files: Iterable[Path]) -> bool:
        if not self.can_write:
            raise PublishError(f"[{self.name}] no token (ARTIFACTS_GITHUB_TOKEN)")
        rel = self._release(ref, create=True)
        have = {a["name"] for a in rel.get("assets", [])}
        if ref.filename in have:
            logger.info("[%s] %s already present, skipping", self.name, ref.filename)
            return False
        ordered = sorted(existing(files), key=lambda p: p.name == ref.filename)
        for f in ordered:
            if f.name in have:
                continue
            url = f"{self.ep.upload_url}/repos/{self.ep.repo}/releases/{rel['id']}/assets"
            request(self.client, "POST", url, retries=self.ep.retry, ok=(201,),
                    params={"name": f.name}, content=f.read_bytes(),
                    headers={"Content-Type": "application/octet-stream"})
            logger.info("[%s] uploaded %s", self.name, f.name)
        return True

    def download(self, ref: ArtifactRef, dest_dir: Path) -> bool:
        rel = self._release(ref, create=False)
        if not rel:
            return False
        assets = {a["name"]: a for a in rel.get("assets", [])}
        if ref.filename not in assets:
            return False
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name in ref.all_files:
            asset = assets.get(name)
            if not asset:
                continue
            resp = request(self.client, "GET", asset["url"], retries=self.ep.retry, ok=(200,),
                           headers={"Accept": "application/octet-stream"})
            (dest_dir / name).write_bytes(resp.content)
        return True
