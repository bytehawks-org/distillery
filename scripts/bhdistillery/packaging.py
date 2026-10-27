"""Native packages (deb, rpm, apk) from an installed unit.

The files a build installed are already known exactly (`files.sha256` of the bundle), so a package is that file list
plus metadata, written by nfpm (https://nfpm.goreleaser.com, MIT): the same tree and bundle, installable with the
host's package manager. Names are decided by templates in `config.yaml`:

    packaging:
      name_template: "bytehawks-{{ name }}{{ variant_suffix }}"     # package name inside deb/rpm/apk
      release: 1
      file_name_template:                                          # file name per format
        deb: "{{ pkg_name }}_{{ pkg_version }}-{{ release }}_{{ pkg_arch }}.deb"

Variables: name, variant, variant_suffix ("" for the default variant, "-<variant>" otherwise), tag, arch (ours: amd64,
arm64), version, major_version, recipe12, release; for file names also pkg_name, pkg_version, pkg_arch (the format's
own architecture name) and format.
"""

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import __version__, bundle
from .model import default_variant
from .templates import TemplateError, resolve_template

FORMATS = ("deb", "rpm", "apk")
NATIVE_ARCH = {
    "deb": {"amd64": "amd64", "arm64": "arm64"},
    "rpm": {"amd64": "x86_64", "arm64": "aarch64"},
    "apk": {"amd64": "x86_64", "arm64": "aarch64"},
}
# The C library each format's target systems use: Debian/Ubuntu and RPM distributions are glibc, Alpine is musl.
# A package built against the other one installs but does not run, so mixing them is refused.
FORMAT_LIBC = {"deb": "glibc", "rpm": "glibc", "apk": "musl"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+._-]*$")
VERSION_RE = re.compile(r"^[0-9][A-Za-z0-9.+~]*$")

DEFAULTS = {
    "formats": list(FORMATS),
    "release": 1,
    "name_template": "bytehawks-{{ name }}{{ variant_suffix }}",
    "file_name_template": {
        "deb": "{{ pkg_name }}_{{ pkg_version }}-{{ release }}_{{ pkg_arch }}.deb",
        "rpm": "{{ pkg_name }}-{{ pkg_version }}-{{ release }}.{{ pkg_arch }}.rpm",
        "apk": "{{ pkg_name }}-{{ pkg_version }}-r{{ release }}.apk",          # apk keeps the architecture in the repository path
    },
    "description_template": "{{ name }} {{ version }} built by Distillery",
    "maintainer": "",
    "vendor": "",
    "homepage": "",
    "license": "",
    "section": "misc",
    "allow_cross_libc": False,       # true = allow e.g. an apk of glibc binaries (will not run on Alpine)
    "nfpm": "",                      # path of the nfpm binary; default: BH_NFPM, PATH
    "shaker": {"post_install": True, "shaker_path": "/opt/bytehawks/core/shaker/bin/shaker"},
    "signing": {},                   # {deb|rpm|apk: {key_file, key_id | key_name}}; passphrase: NFPM_PASSPHRASE (nfpm)
}


class PackagingError(RuntimeError):
    pass


def settings(config):
    """Defaults merged with `packaging:` of config.yaml (`config` is the whole file or its `config` section)."""
    section = (config or {}).get("config", config or {})
    given = copy.deepcopy(section.get("packaging") or {})
    merged = copy.deepcopy(DEFAULTS)
    for key, value in given.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value
    unknown = [f for f in merged["formats"] if f not in FORMATS]
    if unknown:
        raise PackagingError(f"packaging.formats: unknown format(s) {', '.join(unknown)} (use {', '.join(FORMATS)})")
    return merged


def default_formats(conf, libc):
    """The configured formats whose target systems use `libc`."""
    return [f for f in conf["formats"] if FORMAT_LIBC[f] == libc or conf["allow_cross_libc"]]


def check_libc(unit, fmt, conf):
    libc = unit.context["libc"]
    if FORMAT_LIBC[fmt] != libc and not conf["allow_cross_libc"]:
        raise PackagingError(f"{unit.name} is built for {libc} but {fmt} packages are installed on {FORMAT_LIBC[fmt]} "
                             f"systems: build it with --libc {FORMAT_LIBC[fmt]} (or set packaging.allow_cross_libc)")


def native_arch(fmt, arch):
    try:
        return NATIVE_ARCH[fmt][arch]
    except KeyError:
        raise PackagingError(f"no {fmt} architecture name for '{arch}'") from None


def _context(unit, conf):
    package = unit.package
    variant_suffix = "" if unit.variant == default_variant(package) else f"-{unit.variant}"
    return {
        "name": unit.name, "variant": unit.variant, "variant_suffix": variant_suffix, "tag": unit.tag,
        "arch": unit.context["arch"], "version": str(unit.variant_data["full_version"]),
        "major_version": unit.variant_data["major_version"], "recipe12": unit.recipe_sha256[:12],
        "release": conf["release"], "libc": unit.context["libc"],
    }


def _render(template, context, where):
    try:
        return resolve_template(template, context)
    except TemplateError as exc:
        raise PackagingError(f"packaging.{where}: {exc}") from exc


def package_name(unit, conf):
    name = _render(conf["name_template"], _context(unit, conf), "name_template")
    if not NAME_RE.match(name):
        raise PackagingError(f"packaging.name_template gives '{name}': use lowercase letters, digits and + . _ - "
                             "(valid for deb, rpm and apk)")
    return name


def package_version(unit):
    version = str(unit.variant_data["full_version"])
    if not VERSION_RE.match(version):
        raise PackagingError(f"{unit.name}: version '{version}' is not valid in a package (digits, letters, . + ~)")
    return version


def file_name(unit, conf, fmt):
    context = {**_context(unit, conf), "pkg_name": package_name(unit, conf), "pkg_version": package_version(unit),
               "pkg_arch": native_arch(fmt, unit.context["arch"]), "format": fmt}
    template = conf["file_name_template"].get(fmt)
    if not template:
        raise PackagingError(f"packaging.file_name_template has no entry for {fmt}")
    name = _render(template, context, f"file_name_template.{fmt}")
    if "/" in name or name.startswith("."):
        raise PackagingError(f"packaging.file_name_template.{fmt} gives '{name}': not a plain file name")
    return name


def dependency(fmt, name, version, release):
    """Exact-version dependency in the syntax of each format (a unit is built against precisely these dependencies)."""
    if fmt == "deb":
        return f"{name} (= {version}-{release})"
    if fmt == "rpm":
        return f"{name} = {version}-{release}"
    return f"{name}={version}-r{release}"


def installed_paths(unit):
    """(files, links, dirs) a build installed, absolute, from the bundle's files.sha256."""
    listing = unit.bundle_dir / "files.sha256"
    if not listing.exists():
        raise PackagingError(f"{unit.name} {unit.variant_data['full_version']} is not built (no {listing}): "
                             "run build or restore first")
    root = Path(unit.context["config"]["root"])
    files, links, dirs = [], [], []
    for line in listing.read_text().splitlines():
        digest, _, relative = line.partition("  ")
        if not relative:
            continue
        full = root / relative.rstrip("/")
        if digest == "-" * 64:
            dirs.append(full)
        elif full.is_symlink():
            links.append(full)
        else:
            files.append(full)
    files.append(listing)                                      # written last, not listed inside itself
    return files, links, dirs


def contents(unit):
    """nfpm `contents`: files and links of the unit, plus the directories it owns (all of them for an application;
    for a library only its bundle directory, since the library prefix is shared)."""
    files, links, dirs = installed_paths(unit)
    owned = Path(unit.bundle_dir)
    root_owner = {"owner": "root", "group": "root"}
    entries = [{"dst": str(d), "type": "dir", "file_info": dict(root_owner)}
               for d in dirs if unit.tag != "library" or d == owned or owned in d.parents]
    for path in files:
        if not path.exists():
            raise PackagingError(f"{path} is listed by the bundle but missing: rebuild the unit")
        entries.append({"src": str(path), "dst": str(path), "file_info": dict(root_owner)})
    for path in links:
        entries.append({"src": os.readlink(path), "dst": str(path), "type": "symlink", "file_info": dict(root_owner)})
    return entries


def post_install_script(unit, conf, root):
    """Shell script run after installation: initialise the service with Shaker when it is present."""
    if "runtime" not in unit.package or not conf["shaker"].get("post_install", True):
        return None
    shaker = conf["shaker"]["shaker_path"]
    return (f"#!/bin/sh\n# generated by distillery {__version__}\nset -e\n"
            f'SHAKER="$(command -v shaker || true)"\n[ -n "$SHAKER" ] || SHAKER={shaker}\n'
            f'if [ -x "$SHAKER" ]; then\n  BH_ROOT={root} "$SHAKER" install {unit.name}\n'
            f'else\n  echo "shaker not found: run \'shaker install {unit.name}\' to create the service account and directories"\nfi\n')


def nfpm_config(unit, conf, fmt, dependencies, conflicts, scripts_dir=None):
    """The nfpm configuration (a dict; nfpm reads JSON as YAML) of one unit in one format."""
    name, version = package_name(unit, conf), package_version(unit)
    context = {**_context(unit, conf)}
    description = _render(unit.package.get("description") or conf["description_template"], context, "description_template")
    config = {
        "name": name, "arch": unit.context["arch"], "platform": "linux", "version": version,
        "release": str(conf["release"]), "section": conf["section"], "priority": "optional",
        "description": description,
        "maintainer": conf["maintainer"] or "ByteHawks Distillery <noreply@example.com>",
        "vendor": conf["vendor"], "homepage": unit.package.get("website") or conf["homepage"],
        "license": unit.package.get("license") or conf["license"],
        "depends": [dependency(fmt, n, v, conf["release"]) for n, v in dependencies],
        "conflicts": list(conflicts),
        "contents": contents(unit),
    }
    config = {k: v for k, v in config.items() if v not in ("", [], None)}
    if scripts_dir:
        script = post_install_script(unit, conf, unit.context["config"]["root"])
        if script:
            path = Path(scripts_dir) / f"{name}.postinstall.sh"
            path.write_text(script)
            path.chmod(0o755)
            config["scripts"] = {"postinstall": str(path)}
    signature = (conf.get("signing") or {}).get(fmt)
    if signature:
        config[fmt] = {"signature": dict(signature)}
    return config


def find_nfpm(conf, root=None):
    candidates = [os.environ.get("BH_NFPM"), conf.get("nfpm"), shutil.which("nfpm"),
                  str(Path(root) / "nfpm" / "bin" / "nfpm") if root else None]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise PackagingError("nfpm not found: install it (https://nfpm.goreleaser.com), put it in PATH, or set "
                         "BH_NFPM / packaging.nfpm")


def conflicts_for(catalog, unit, conf):
    """Other variants of the same package install to the same prefix: they cannot be installed together."""
    names = set()
    for variant in catalog.package(unit.name).get("variant", []):
        alias = variant["name"][0] if isinstance(variant["name"], list) else variant["name"]
        other = catalog.unit(unit.name, alias, unit.tag)
        if other.variant != unit.variant:
            names.add(package_name(other, conf))
    names.discard(package_name(unit, conf))
    return sorted(names)


def build_packages(catalog, unit, formats, out_dir, conf, *, dry_run=False, with_deps=False, nfpm=None,
                   runner=subprocess.run, log=print):
    """Write the packages of `unit` (and of its dependencies with with_deps). Returns [(unit, format, path)]."""
    plan = catalog.plan(unit)
    units = plan if with_deps else [unit]
    out_dir = Path(out_dir)
    written = []
    for item in units:
        if not bundle.is_up_to_date(item):
            raise PackagingError(f"{item.name} {item.variant_data['full_version']} is not built in "
                                 f"{item.context['config']['root']}: run build or restore first")
        by_name = {d.name: d for d in item.depends}
        dependencies = [(package_name(d, conf), package_version(d)) for d in by_name.values()]
        conflicts = conflicts_for(catalog, item, conf)
        for fmt in formats:
            check_libc(item, fmt, conf)
            target = out_dir / file_name(item, conf, fmt)
            if dry_run:
                files = sum(1 for e in contents(item) if e.get("type") != "dir")
                log(f"[plan] {target.name}  ({files} files, depends: "
                    f"{', '.join(dependency(fmt, n, v, conf['release']) for n, v in dependencies) or '-'})")
                written.append((item, fmt, target))
                continue
            binary = nfpm or find_nfpm(conf, item.context["config"]["root"])
            out_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="bh-nfpm-") as tmp:
                config = nfpm_config(item, conf, fmt, dependencies, conflicts, scripts_dir=tmp)
                config_file = Path(tmp) / f"{fmt}.json"
                config_file.write_text(json.dumps(config, indent=1))
                result = runner([binary, "package", "--packager", fmt, "--config", str(config_file),
                                 "--target", str(target)], capture_output=True, text=True)
            if result.returncode != 0:
                raise PackagingError(f"nfpm failed for {target.name}: {(result.stderr or result.stdout).strip()[-400:]}")
            log(f"[package] {target} ({target.stat().st_size} bytes)")
            written.append((item, fmt, target))
    if written and not dry_run:
        from .fetch import sha256_file
        sums = out_dir / "SHA256SUMS"
        known = dict(line.split("  ", 1)[::-1] for line in sums.read_text().splitlines() if "  " in line) if sums.exists() else {}
        known.update({path.name: sha256_file(path) for _, _, path in written})
        sums.write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(known.items())))
    return written
