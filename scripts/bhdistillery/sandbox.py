"""Run build stages in a sandbox: read-only system, writable only where the build must write, no network.

Sources are fetched and verified by Distillery *before* the stages run, so a build has no reason to reach the
network (a package that must, e.g. to fetch language modules, says `build: {network: true}`). Backends:

  bwrap     bubblewrap: unprivileged, preferred when installed
  unshare   util-linux namespaces: needs root (or user namespaces); mounts are private to the build
  none      no isolation (the historical behaviour)
  auto      bwrap, else unshare when it works here, else none (reported)
"""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

MODES = ("auto", "none", "bwrap", "unshare")


class SandboxError(RuntimeError):
    pass


def _unshare_works():
    if not shutil.which("unshare"):
        return False
    try:
        return subprocess.run(["unshare", "--mount", "--pid", "--fork", "--net", "true"],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def resolve(mode):
    """The backend to use for `mode`: 'none', 'bwrap' or 'unshare'. An explicit backend that is unusable is an error."""
    mode = mode or "none"
    if mode not in MODES:
        raise SandboxError(f"unknown sandbox '{mode}' (use {', '.join(MODES)})")
    if mode == "none":
        return "none"
    if mode == "bwrap":
        if not shutil.which("bwrap"):
            raise SandboxError("sandbox 'bwrap' requested but bwrap is not installed")
        return "bwrap"
    if mode == "unshare":
        if not _unshare_works():
            raise SandboxError("sandbox 'unshare' requested but namespaces are not available here (need root or user namespaces)")
        return "unshare"
    if shutil.which("bwrap"):
        return "bwrap"
    return "unshare" if _unshare_works() else "none"


def _under(path, parent):
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def wrap(backend, argv, cwd, writable, network=False):
    """argv that runs `argv` in the sandbox. `writable`: directories the build may write (everything else is read-only).
    /tmp is private unless a writable directory lives under it (then it stays shared, so those paths remain reachable)."""
    if backend == "none":
        return list(argv)
    writable = sorted({str(Path(p).resolve()) for p in writable})
    private_tmp = not any(_under(p, "/tmp") or _under(cwd, "/tmp") for p in writable)

    if backend == "bwrap":
        cmd = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
               "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--die-with-parent", "--new-session"]
        if not network:
            cmd.append("--unshare-net")
        if private_tmp:
            cmd += ["--tmpfs", "/tmp"]
        else:
            cmd += ["--bind", "/tmp", "/tmp"]
        for path in writable:
            cmd += ["--bind", path, path]
        return cmd + ["--chdir", str(cwd), "--"] + list(argv)

    if backend == "unshare":
        steps = ["set -e", "mount --make-rprivate /", "mount --rbind / /", "mount -o remount,bind,ro /"]
        if private_tmp:
            steps.append("mount -t tmpfs tmpfs /tmp")
        else:
            steps += ["mount --bind /tmp /tmp", "mount -o remount,bind,rw /tmp"]
        for path in writable:
            q = shlex.quote(path)
            steps += [f"mount --bind {q} {q}", f"mount -o remount,bind,rw {q}"]
        steps.append(f"cd {shlex.quote(str(cwd))}")
        steps.append('exec "$@"')
        cmd = ["unshare", "--mount", "--pid", "--fork", "--mount-proc", "--ipc", "--uts"]
        if not network:
            cmd.append("--net")
        return cmd + ["--", "bash", "-c", "\n".join(steps), "sandbox"] + list(argv)

    raise SandboxError(f"unknown backend '{backend}'")
