"""What a unit was built with: compiler, linker, make, libc, OS. Recorded in the manifest and the artifact sidecar.

It is *recorded*, not hashed: the recipe hash names what was asked for, the toolchain says what answered. Two artifacts with the
same recipe built by different compilers are visible in the manifests (and `repro` tells whether it mattered). For strictness
build in a fixed image and set `BH_BUILDER_IMAGE` (CI does), which is recorded as well."""

import os
import platform
import shutil
import subprocess

DEFAULT_TOOLS = ("cc", "ld", "ar", "make")          # a package adds what else it uses with `toolchain: [perl, c++, go]`
LIBC_PROBES = (["getconf", "GNU_LIBC_VERSION"], ["ldd", "--version"])


def _first_line(argv, env=None):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=20, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = [l.strip() for l in (out.stdout or out.stderr or "").splitlines()
             if l.strip() and not l.startswith("Picked up")]          # the JVM announces its options on stderr
    return lines[0] if lines else ""


def _libc(libc, env):
    if libc == "musl":
        import glob
        for loader in glob.glob("/lib/ld-musl-*") + glob.glob("/usr/lib/ld-musl-*"):
            line = _first_line([loader], env)           # musl's loader prints its version to stderr and exits non-zero
            if line:
                return line
        return "musl"
    for probe in LIBC_PROBES:
        if shutil.which(probe[0]):
            line = _first_line(probe, env)
            if line:
                return line
    return "glibc"


def _os_release():
    try:
        with open("/etc/os-release") as handle:
            pairs = dict(l.rstrip("\n").split("=", 1) for l in handle if "=" in l)
    except OSError:
        return ""
    return " ".join(pairs.get(k, "").strip('"') for k in ("ID", "VERSION_ID")).strip()


def collect(unit, env=None):
    """{tool: first line of --version, 'libc', 'os', 'machine', 'builder_image'} for the tools found on the build PATH."""
    env = env or os.environ
    path = env.get("PATH")
    wanted = list(DEFAULT_TOOLS) + list(unit.package.get("toolchain", []))
    result = {}
    for tool in dict.fromkeys(wanted):
        found = shutil.which(tool, path=path)
        if not found:
            continue
        line = _first_line([found, "--version"], env)
        if tool == "java":
            line = _first_line([found, "-version"], env)
        if tool == "go":
            line = _first_line([found, "version"], env)
        if line:
            result[tool] = line
    result["libc"] = _libc(unit.context["libc"], env)
    result["os"] = _os_release()
    result["machine"] = platform.machine()
    if env.get("BH_BUILDER_IMAGE"):
        result["builder_image"] = env["BH_BUILDER_IMAGE"]
    return {k: v for k, v in result.items() if v}
