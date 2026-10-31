"""Turning a built unit into an artifact and back.

A unit installs into a prefix that may be shared with other units (all libraries live in deps/), so the
artifact content is *what the build changed*: files and directories that are new or modified between a
snapshot taken before the stages ran and one taken after the bundle was written. The list, with SHA-256,
is stored in the bundle as `files.sha256` (also the basis of future tamper detection).

Artifacts are only valid for the root they were built for (paths are baked into binaries): the recipe
hash includes the prefix, so a store never serves a tree to a different layout.
"""

import gzip
import hashlib
import json
import os
import stat
import tarfile
import time
from pathlib import Path, PurePosixPath

from .templates import resolve_dict_templates
from . import __version__
from .fetch import sha256_file

META_SCHEMA = 1


class PackageError(RuntimeError):
    pass


def snapshot(prefix):
    """{relative path: (kind, size, mtime_ns)} of everything below prefix (kind: f, d, l)."""
    prefix = Path(prefix)
    result = {}
    if not prefix.exists():
        return result
    for directory, dirs, files in os.walk(prefix):
        base = Path(directory)
        for name in dirs + files:
            path = base / name
            info = os.lstat(path)
            kind = "l" if stat.S_ISLNK(info.st_mode) else "d" if stat.S_ISDIR(info.st_mode) else "f"
            result[str(path.relative_to(prefix))] = (kind, info.st_size, info.st_mtime_ns)
    return result


def changed_paths(before, after):
    """New or modified files/links, and new directories (modified directories are not ours)."""
    changed = []
    for path, state in after.items():
        previous = before.get(path)
        if state[0] == "d":
            if previous is None:
                changed.append(path)
        elif previous != state:
            changed.append(path)
    return sorted(changed)


def _digest(path):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        return hashlib.sha256(os.readlink(path).encode()).hexdigest()
    return sha256_file(path)


def write_file_list(unit, paths, destination):
    """files.sha256: '<sha256>  <path relative to the root>' per file/link; directories get 64 dashes."""
    root = Path(unit.context["config"]["root"])
    prefix = Path(unit.prefix)
    lines = []
    for rel in paths:
        full = prefix / rel
        shown = full.relative_to(root).as_posix()
        if full.is_dir() and not full.is_symlink():
            lines.append(f"{'-' * 64}  {shown}/")
        else:
            lines.append(f"{_digest(full)}  {shown}")
    Path(destination).write_text("\n".join(lines) + "\n")
    return lines


def build_tarball(unit, rel_paths, out_dir, platform, built_epoch=None, toolchain=None):
    """Deterministic tar.gz of `rel_paths` (relative to unit.prefix), stored relative to the root."""
    from .store import entry_for

    root = Path(unit.context["config"]["root"])
    prefix = Path(unit.prefix)
    entry = entry_for(unit, platform)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tarball = out_dir / f"{entry.name}.tar.gz"
    epoch = int(built_epoch if built_epoch is not None else os.environ.get("SOURCE_DATE_EPOCH", time.time()))

    members = sorted(prefix / rel for rel in rel_paths)
    with open(tarball, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for full in members:
                    info = archive.gettarinfo(str(full), arcname=full.relative_to(root).as_posix())
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = epoch
                    if info.isreg():
                        with open(full, "rb") as handle:
                            archive.addfile(info, handle)
                    else:
                        archive.addfile(info)

    meta = {
        "schema": META_SCHEMA,
        "generator": f"distillery/{__version__}",
        "name": entry.name,
        "package": unit.name, "variant": unit.variant, "tag": unit.tag, "arch": unit.context["arch"], "libc": unit.context["libc"], "linkage": unit.context["linkage"], "platform": platform,
        "version": entry.version, "recipe_sha256": unit.recipe_sha256,
        "root": str(root), "prefix": str(prefix),
        "files": len(members),
        "sha256": sha256_file(tarball),
        "size": tarball.stat().st_size,
        "depends": [{"package": d.name, "variant": d.variant, "recipe_sha256": d.recipe_sha256} for d in unit.depends],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch)),
    }
    if toolchain:
        meta["toolchain"] = toolchain
    from .store import meta_bytes

    (out_dir / f"{entry.name}.json").write_bytes(meta_bytes(meta))
    return entry, tarball, meta


def _safe(name):
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and name not in ("", ".")


def restore(tarball, root, expected_recipe=None, log=print):
    """Extract an artifact below `root`. Refuses unsafe members and a bundle for another recipe."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(tarball) as archive:
            members = archive.getmembers()
            for member in members:
                if not _safe(member.name):
                    raise PackageError(f"unsafe path in artifact: {member.name}")
                if member.issym() or member.islnk():
                    target = PurePosixPath(member.linkname)
                    anchor = PurePosixPath(member.name).parent if member.issym() else PurePosixPath(".")
                    resolved = os.path.normpath(str(anchor / target))
                    if target.is_absolute() or resolved.startswith(".."):
                        raise PackageError(f"unsafe link in artifact: {member.name} -> {member.linkname}")
                if member.isdev() or member.isfifo():
                    raise PackageError(f"special file in artifact: {member.name}")

            manifest = next((m for m in members if ".bytehawks/" in m.name and m.name.endswith("manifest.json")), None)
            if expected_recipe and manifest:
                recipe = json.load(archive.extractfile(manifest)).get("recipe_sha256")
                if recipe != expected_recipe:
                    raise PackageError(f"artifact holds recipe {str(recipe)[:12]}, expected {expected_recipe[:12]}")

            kwargs = {"filter": "tar"} if hasattr(tarfile, "tar_filter") else {}
            archive.extractall(root, members=members, **kwargs)
    except (tarfile.TarError, OSError) as exc:
        raise PackageError(f"cannot restore {tarball}: {exc}") from exc
    log(f"restored {len(members)} entries into {root}")
    return len(members)


def verify_files(unit):
    """Compare the installed files with the bundle's files.sha256. Returns a list of problems (empty = intact)."""
    listing = unit.bundle_dir / "files.sha256"
    if not listing.exists():
        return [f"{listing}: missing (unit not built by this Distillery version?)"]
    root = Path(unit.context["config"]["root"])
    mutable = [str(Path(p)) for p in resolve_dict_templates(unit.package.get("runtime", {}), unit.context).get("mutable", [])]
    problems = []
    for line in listing.read_text().splitlines():
        digest, _, path = line.partition("  ")
        full = root / path.rstrip("/")
        if any(str(full) == m or str(full).startswith(m + "/") for m in mutable):
            continue                                            # rewritten by the runtime on purpose
        if path.endswith("/"):
            if not full.is_dir():
                problems.append(f"missing directory {path}")
        elif not (full.exists() or full.is_symlink()):
            problems.append(f"missing {path}")
        elif _digest(full) != digest:
            problems.append(f"modified {path}")
    return problems
