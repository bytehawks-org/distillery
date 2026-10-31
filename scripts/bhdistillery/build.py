"""Build execution: fetch, verify, extract, run the declared stages, write the bundle."""

import os
import shlex
import subprocess
import time
from pathlib import Path

from . import bundle, elfcheck, fetch, package as artifact, sandbox as sandbox_mod, signature, toolchain
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


def source_epoch(src_dir):
    """The newest mtime in an extracted source tree: a stable SOURCE_DATE_EPOCH for the same source."""
    newest = 0
    for directory, dirs, files in os.walk(src_dir):
        for name in dirs + files:
            try:
                newest = max(newest, int(os.lstat(os.path.join(directory, name)).st_mtime))
            except OSError:
                pass
    return newest or 1


def run_script(script, cwd, env, log_file, echo=print, wrapper=None):
    """Run the script, stream its output to the console and to a log file. `wrapper(argv) -> argv` puts the
    `bash -c` command in a sandbox."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w") as log, subprocess.Popen(
            (wrapper or list)(["bash", "-c", script]), cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
        for line in proc.stdout:
            log.write(line)
            echo(line.rstrip("\n"))
        code = proc.wait()
    if code != 0:
        raise BuildError(f"build failed (exit {code}), full log: {log_file}")


def build_unit(unit, assets_root, *, dry_run=False, force=False, allow_unverified=False,
               stages=DEFAULT_STAGES, artifacts_dir=None, log=print, sandbox=None, reproducible=False):
    """Build one unit. Returns 'built', 'skipped' or 'dry-run'.

    The files the build changes are recorded in the bundle (`files.sha256`); with artifacts_dir the unit
    is also packaged there as <name>.tar.gz + <name>.json, ready for a store."""
    cfg = unit.context["config"]
    title = f"{unit.name} {unit.variant_data['full_version']} ({unit.variant}, {unit.tag})"

    if not force and not dry_run and bundle.is_up_to_date(unit):
        log(f"[skip] {title}: bundle up to date ({unit.recipe_sha256[:12]})")
        return "skipped"

    git = unit.context["git"]
    git_ref = git.get("ref_template")                       # a git source: pinned by commit instead of by tarball hash
    if git_ref:
        url = f"{git.get('repo', '')} {git_ref}"
        tarball = Path(cfg["default_download_path"]) / f"{unit.name}-{unit.variant_data['full_version']}-git.tar"
    else:
        url = git.get("release_download_url_template")
        if not url:
            raise BuildError(f"{unit.name}: git.release_download_url_template (or git.ref_template) is required")
        tarball = Path(cfg["default_download_path"]) / Path(url.split("?")[0]).name
    script = stage_script(unit, stages)

    if dry_run:
        log(f"[plan] {title}")
        log(f"  source   {url}")
        if git_ref:
            log(f"  commit   {unit.variant_data.get('commit') or '(NOT PINNED)'}")
        else:
            log(f"  sha256   {unit.variant_data.get('sha256') or '(NOT PINNED)'}")
        if unit.package.get("signature"):
            log(f"  gpg      signers {', '.join(unit.package['signature'].get('fingerprints', []))}")
        log(f"  prefix   {unit.prefix}")
        log(f"  bundle   {unit.bundle_dir}")
        log("  ----- script -----")
        log("\n".join("  " + line for line in script.splitlines()))
        return "dry-run"

    log(f"[build] {title}")
    if git_ref:
        if unit.package.get("signature"):
            raise BuildError(f"{unit.name}: signature: is not supported for git sources (the commit is the pin)")
        fetch.git_archive(git["repo"], git_ref, unit.variant_data.get("commit", ""), tarball, allow_unverified, log)
        fetch.extract(tarball, unit.src_dir, strip_components=unit.package.get("strip_components", 0))
    else:
        fetch.download(url, tarball, unit.variant_data.get("sha256", ""), allow_unverified, log)
        signature.verify_source(unit.package, unit.context, tarball, url, log)
        fetch.extract(tarball, unit.src_dir, strip_components=unit.package.get("strip_components", 1))

    Path(unit.prefix).mkdir(parents=True, exist_ok=True)
    if unit.tag == "application" and any(Path(unit.prefix).iterdir()):
        log(f"warning: {unit.prefix} is not empty: files this build leaves unchanged are not recorded in the artifact, "
            "the file list and the packages - build into a clean root")
    before = artifact.snapshot(unit.prefix)
    log_file = Path(cfg["log_path"]) / f"{unit.name}-{unit.variant_data['full_version']}.log"
    started = time.time()
    env = build_env(unit, unit.context["deps_prefix"])
    reproducible = reproducible or bool(cfg.get("build", {}).get("reproducible"))
    epoch = None
    if reproducible:                       # same source, same environment: the inputs that leak into binaries are fixed
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH") or source_epoch(unit.src_dir))
        env.update({"SOURCE_DATE_EPOCH": str(epoch), "TZ": "UTC", "LC_ALL": "C", "LANG": "C"})
    backend = sandbox_mod.resolve(sandbox or cfg.get("build", {}).get("sandbox"))
    wanted_net = bool(unit.package.get("build", {}).get("network"))
    writable = [unit.prefix, unit.src_dir, str(log_file.parent)]
    if backend != "none":
        log(f"[sandbox] {backend}: system read-only, writable {', '.join(writable)}, network {'on' if wanted_net else 'off'}")
    tools = toolchain.collect(unit, env)
    run_script(script, unit.src_dir, env, log_file, echo=log,
               wrapper=lambda argv: sandbox_mod.wrap(backend, argv, unit.src_dir, writable, network=wanted_net))

    if unit.context["linkage"] == "static":
        problems = elfcheck.check_static(unit.prefix, unit.package.get("static", {}).get("binaries", []))
        if problems:
            raise BuildError(f"{unit.name}: built as static but " + "; ".join(problems))
        log(f"[static] {', '.join(unit.package['static']['binaries']) or 'no binaries declared'}: no dynamic loader, no needed libraries")

    out = bundle.emit(unit, assets_root, built=True, toolchain=tools, built_epoch=epoch)
    changed = artifact.changed_paths(before, artifact.snapshot(unit.prefix))
    files_list = out / "files.sha256"
    artifact.write_file_list(unit, changed, files_list)       # describes everything except itself
    changed.append(str(files_list.relative_to(unit.prefix)))
    for sealed in bundle.seal(out, cfg, log):                  # digest (and signature) of the whole bundle
        changed.append(str(sealed.relative_to(unit.prefix)))
    log(f"[done] {title} in {time.time() - started:.0f}s, bundle: {out}, {len(changed)} paths recorded")

    if artifacts_dir:
        entry, tarball, meta = artifact.build_tarball(unit, changed, artifacts_dir, unit.context["platform"],
                                                      built_epoch=epoch, toolchain=tools)
        log(f"[pack] {tarball} ({meta['size']} bytes, sha256 {meta['sha256'][:12]})")
        signature.sign_artifact(tarball.with_suffix("").with_suffix(".json"), cfg, log)
    return "built"


def restore_unit(unit, stores, work_dir, log=print):
    """Restore a unit from the first store that has its exact recipe. Returns the entry, or None."""
    from .store import entry_for

    wanted = entry_for(unit, unit.context["platform"])
    for store in stores.stores:
        entry = store.find(wanted)
        if entry is None:
            continue
        log(f"[restore] {unit.name} {wanted.version} from {entry.store} ({entry.name})")
        tarball, meta = store.fetch(entry, work_dir)
        if entry.signer:
            log(f"[verify] {entry.name} signed by {entry.signer}")
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
