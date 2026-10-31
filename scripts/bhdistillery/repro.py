"""Reproducibility check: build a unit twice from the same source in the same place and compare what it installed."""

import shutil
from pathlib import Path

from .build import build_unit


def installed_files(unit):
    """{path relative to the root: sha256 or '-'} from files.sha256, without the bundle (it carries build metadata)."""
    listing = unit.bundle_dir / "files.sha256"
    result = {}
    for line in listing.read_text().splitlines():
        digest, _, path = line.partition("  ")
        if "/.bytehawks/" in f"/{path}" or path.endswith("/.bytehawks/"):
            continue
        result[path] = "-" if digest.startswith("-") else digest
    return result


def remove_installed(unit, paths):
    """Delete what a build installed (files, links, then the empty directories it created) so the next one starts clean."""
    root = Path(unit.context["config"]["root"])
    directories = []
    for rel in paths:
        target = root / rel
        if rel.endswith("/"):
            directories.append(target)
        elif target.is_symlink() or target.is_file():
            target.unlink()
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    shutil.rmtree(unit.bundle_dir, ignore_errors=True)


def compare(first, second):
    """(only_in_first, only_in_second, differing) of two {path: digest} maps."""
    return (sorted(set(first) - set(second)), sorted(set(second) - set(first)),
            sorted(p for p in set(first) & set(second) if first[p] != second[p]))


def check(unit, assets_root, *, stages, allow_unverified=False, log=print, sandbox=None):
    """Build `unit` twice with fixed time, locale and time zone. Returns (identical, differences, file count)."""
    options = dict(force=True, allow_unverified=allow_unverified, stages=stages, log=log, sandbox=sandbox, reproducible=True)
    log(f"[repro] build 1/2: {unit.name}")
    build_unit(unit, assets_root, **options)
    first = installed_files(unit)
    remove_installed(unit, list(first))
    log(f"[repro] build 2/2: {unit.name}")
    build_unit(unit, assets_root, **options)
    second = installed_files(unit)
    only_first, only_second, different = compare(first, second)
    return not (only_first or only_second or different), {"missing": only_first, "extra": only_second, "different": different}, len(first)
