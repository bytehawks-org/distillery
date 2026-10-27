"""Build execution: fetch, verify, extract, run the declared stages, write the bundle."""

import os
import shlex
import subprocess
import time
from pathlib import Path

from . import bundle, fetch, package as artifact, signature
from .templates import resolve_template

STAGES = ("pre", "configure", "make", "check", "install", "post")
DEFAULT_STAGES = ("pre", "configure", "make", "install", "post")


class BuildError(RuntimeError):
    pass


def stage_script(unit, stages):
    """The shell script of a unit: one `bash -euo pipefail` session so that `cd` and
    exported variables survive between stages."""
    commands = unit.package.get("commands", {})
    lines = ["set -euo pipefail", f"cd {shlex.quote(unit.src_dir)}"]
    for stage in stages:
        if stage in commands and commands[stage]:
            lines.append(f'echo "==> stage: {stage}"')
            lines.append(resolve_template(commands[stage], unit.context).rstrip())
    return "\n".join(lines) + "\n"


def build_env(unit, dependencies_prefix):
    """Environment that lets the build find the libraries Distillery already produced."""
    deps = dependencies_prefix
    env = dict(os.environ)
    env.update({
        "PREFIX": unit.prefix,
        "JOBS": str(unit.context["jobs"]),
        "CPPFLAGS": f"-I{deps}/include {env.get('CPPFLAGS', '')}".strip(),
        "LDFLAGS": f"-L{deps}/lib -L{deps}/lib64 {env.get('LDFLAGS', '')}".strip(),
        "PKG_CONFIG_PATH": f"{deps}/lib/pkgconfig:{deps}/lib64/pkgconfig:{env.get('PKG_CONFIG_PATH', '')}".rstrip(":"),
        "PATH": f"{deps}/bin:{env.get('PATH', '')}",
    })
    for key, value in unit.package.get("env", {}).items():
        env[key] = resolve_template(str(value), unit.context)
    return env


def run_script(script, cwd, env, log_file, echo=print):
    """Run the script, stream its output to the console and to a log file."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w") as log, subprocess.Popen(
            ["bash", "-c", script], cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
        for line in proc.stdout:
            log.write(line)
            echo(line.rstrip("\n"))
        code = proc.wait()
    if code != 0:
        raise BuildError(f"build failed (exit {code}), full log: {log_file}")


def build_unit(unit, assets_root, *, dry_run=False, force=False, allow_unverified=False,
               stages=DEFAULT_STAGES, artifacts_dir=None, log=print):
    """Build one unit. Returns 'built', 'skipped' or 'dry-run'.

    The files the build changes are recorded in the bundle (`files.sha256`); with artifacts_dir the unit
    is also packaged there as <name>.tar.gz + <name>.json, ready for a store."""
    cfg = unit.context["config"]
    title = f"{unit.name} {unit.variant_data['full_version']} ({unit.variant}, {unit.tag})"

    if not force and not dry_run and bundle.is_up_to_date(unit):
        log(f"[skip] {title}: bundle up to date ({unit.recipe_sha256[:12]})")
        return "skipped"

    url = unit.context["git"].get("release_download_url_template")
    if not url:
        raise BuildError(f"{unit.name}: git.release_download_url_template is required")
    tarball = Path(cfg["default_download_path"]) / Path(url.split("?")[0]).name
    script = stage_script(unit, stages)

    if dry_run:
        log(f"[plan] {title}")
        log(f"  source   {url}")
        log(f"  sha256   {unit.variant_data.get('sha256') or '(NOT PINNED)'}")
        if unit.package.get("signature"):
            log(f"  gpg      signers {', '.join(unit.package['signature'].get('fingerprints', []))}")
        log(f"  prefix   {unit.prefix}")
        log(f"  bundle   {unit.bundle_dir}")
        log("  ----- script -----")
        log("\n".join("  " + line for line in script.splitlines()))
        return "dry-run"

    log(f"[build] {title}")
    fetch.download(url, tarball, unit.variant_data.get("sha256", ""), allow_unverified, log)
    signature.verify_source(unit.package, unit.context, tarball, url, log)
    fetch.extract(tarball, unit.src_dir, strip_components=unit.package.get("strip_components", 1))

    Path(unit.prefix).mkdir(parents=True, exist_ok=True)
    before = artifact.snapshot(unit.prefix)
    log_file = Path(cfg["log_path"]) / f"{unit.name}-{unit.variant_data['full_version']}.log"
    started = time.time()
    run_script(script, unit.src_dir, build_env(unit, unit.context["deps_prefix"]), log_file, echo=log)

    out = bundle.emit(unit, assets_root, built=True)
    changed = artifact.changed_paths(before, artifact.snapshot(unit.prefix))
    files_list = out / "files.sha256"
    artifact.write_file_list(unit, changed, files_list)       # describes everything except itself
    changed.append(str(files_list.relative_to(unit.prefix)))
    log(f"[done] {title} in {time.time() - started:.0f}s, bundle: {out}, {len(changed)} paths recorded")

    if artifacts_dir:
        entry, tarball, meta = artifact.build_tarball(unit, changed, artifacts_dir, unit.context["arch"])
        log(f"[pack] {tarball} ({meta['size']} bytes, sha256 {meta['sha256'][:12]})")
    return "built"


def restore_unit(unit, stores, work_dir, log=print):
    """Restore a unit from the first store that has its exact recipe. Returns the entry, or None."""
    from .store import entry_for

    wanted = entry_for(unit, unit.context["arch"])
    for store in stores.stores:
        entry = store.find(wanted)
        if entry is None:
            continue
        log(f"[restore] {unit.name} {wanted.version} from {entry.store} ({entry.name})")
        tarball, meta = store.fetch(entry, work_dir)
        artifact.restore(tarball, unit.context["config"]["root"], expected_recipe=unit.recipe_sha256, log=log)
        return entry
    return None


def ensure_unit(unit, assets_root, stores=None, work_dir=None, *, force=False, dry_run=False, **build_options):
    """The idempotent unit of work: up to date → skip; in a store → restore; else build.
    Returns 'skipped', 'restored', 'built' or 'dry-run'."""
    log = build_options.get("log", print)

    if not force and not dry_run and bundle.is_up_to_date(unit):
        log(f"[skip] {unit.name} {unit.variant_data['full_version']}: bundle up to date ({unit.recipe_sha256[:12]})")
        return "skipped"

    if not force and not dry_run and stores is not None and stores.stores:
        if restore_unit(unit, stores, work_dir or Path(unit.context["config"]["default_download_path"]) / "artifacts", log):
            return "restored"

    return build_unit(unit, assets_root, force=force, dry_run=dry_run, **build_options)
