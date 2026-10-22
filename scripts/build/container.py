"""
Run the CLI inside the builder image (local development with Docker/Podman).
In CI the job already runs inside the image, so executor=native is used instead.
"""

import logging
import subprocess
from pathlib import Path
from typing import List

from scripts.config.models import DistilleryConfig, PullPolicy
from scripts.core.exceptions import BuildError

logger = logging.getLogger(__name__)

CONTAINER_BASE = "/opt/bytehawks"


def image_for(config: DistilleryConfig, variant: str) -> str:
    v = config.get_variant(variant)
    if not v or not v.get_resolved_image():
        raise BuildError(f"No builder image for variant '{variant}'")
    return v.get_resolved_image()


def ensure_image(config: DistilleryConfig, image: str) -> None:
    ct = config.build_type.container
    runtime = ct.runtime.value
    if ct.pull_policy == PullPolicy.NEVER:
        return
    present = subprocess.run([runtime, "image", "inspect", image],
                             capture_output=True).returncode == 0
    if ct.pull_policy == PullPolicy.ALWAYS or not present:
        logger.info("Pulling %s", image)
        if subprocess.run([runtime, "pull", image]).returncode != 0:
            raise BuildError(f"Could not pull builder image {image}")


def run_build_in_container(config: DistilleryConfig, *, variant: str, repo_root: Path,
                           host_base: Path, host_output: Path, cli_args: List[str]) -> None:
    """``cli_args`` are the arguments after ``python -m scripts.cli`` (inside the container)."""
    ct = config.build_type.container
    rt = config.runtime
    user = config.build_user
    image = image_for(config, variant)
    ensure_image(config, image)

    host_base.mkdir(parents=True, exist_ok=True)
    host_output.mkdir(parents=True, exist_ok=True)

    cmd = [ct.runtime.value, "run", "--rm", "-w", "/src",
           "-v", f"{repo_root.resolve()}:/src:ro",
           "-v", f"{host_base.resolve()}:{CONTAINER_BASE}",
           "-v", f"{host_output.resolve()}:/dist"]
    if user:
        cmd += ["-u", f"{user.uid}:{user.gid}", "-e", f"HOME={user.homedir}"]
    if rt:
        if rt.tmpfs.enabled:
            cmd += ["--tmpfs", f"{rt.tmpfs.mount_point}:rw,exec,size={rt.tmpfs.size}"]
        if rt.security.no_new_privileges:
            cmd += ["--security-opt", "no-new-privileges"]
        for cap in rt.security.drop_capabilities:
            cmd += ["--cap-drop", cap]
        for cap in rt.security.add_capabilities:
            cmd += ["--cap-add", cap]
    cmd += ["-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONPATH=/src",
            image, "python3", "-m", "scripts.cli"] + cli_args

    logger.info("Running build in %s", image)
    if subprocess.run(cmd).returncode != 0:
        raise BuildError(f"Containerized build failed ({image})")
