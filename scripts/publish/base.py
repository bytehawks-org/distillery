"""
Artifact repository interface and shared HTTP helper.
"""

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable, List, Optional

import httpx

from scripts.build.artifact import ArtifactRef
from scripts.core.exceptions import PublishError

logger = logging.getLogger(__name__)


class ArtifactRepository(ABC):
    name: str
    can_write: bool = True

    @abstractmethod
    def exists(self, ref: ArtifactRef) -> bool:
        """True if the main tarball of ``ref`` is already stored."""

    @abstractmethod
    def upload(self, ref: ArtifactRef, files: Iterable[Path]) -> bool:
        """Upload files; returns False when the artifact already existed (left untouched)."""

    @abstractmethod
    def download(self, ref: ArtifactRef, dest_dir: Path) -> bool:
        """Download tarball + sidecars into ``dest_dir``; False if not present."""


def request(client: httpx.Client, method: str, url: str, *, retries: int = 3,
            ok: Iterable[int] = (200,), **kwargs) -> httpx.Response:
    """HTTP call with exponential backoff on network errors and 5xx."""
    ok = set(ok)
    delay = 1.0
    last: Optional[Exception] = None
    for attempt in range(max(1, retries + 1)):
        try:
            resp = client.request(method, url, **kwargs)
            if resp.status_code in ok:
                return resp
            if resp.status_code < 500:
                raise PublishError(f"{method} {url} -> HTTP {resp.status_code}: {resp.text[:300]}")
            last = PublishError(f"{method} {url} -> HTTP {resp.status_code}")
        except httpx.HTTPError as e:
            last = e
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    raise PublishError(f"{method} {url} failed after {retries + 1} attempts: {last}")


def existing(files: Iterable[Path]) -> List[Path]:
    return [Path(f) for f in files if Path(f).exists()]
