"""
Native step execution (inside the builder image / on the host).
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict

from scripts.core.exceptions import BuildError

logger = logging.getLogger(__name__)


class StepRunner:
    """Runs recipe steps with ``bash -euo pipefail``."""

    def __init__(self, use_fakeroot: bool = False):
        self.use_fakeroot = use_fakeroot and shutil.which("fakeroot") is not None
        self._gha = os.environ.get("GITHUB_ACTIONS") == "true"

    def run(self, label: str, script: str, env: Dict[str, str], cwd: Path,
            *, fakeroot: bool = False) -> None:
        cmd = ["bash", "-euo", "pipefail", "-c", script]
        if fakeroot and self.use_fakeroot:
            cmd = ["fakeroot", "--"] + cmd

        if self._gha:
            print(f"::group::{label}", flush=True)
        else:
            logger.info("[%s] running", label)

        try:
            proc = subprocess.run(
                cmd, cwd=cwd, env=env, stdout=sys.stdout if self._gha else None,
                stderr=subprocess.STDOUT if self._gha else None,
            )
        finally:
            if self._gha:
                print("::endgroup::", flush=True)

        if proc.returncode != 0:
            raise BuildError(f"Step '{label}' failed with exit code {proc.returncode}")
