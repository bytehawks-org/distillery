"""Configuration loading, package/variant selection, context building and dependency planning."""

import copy
import hashlib
import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import BUNDLE_SCHEMA
from .templates import resolve_dict_templates, resolve_template

DISTILLERY_HOME = Path(__file__).resolve().parent.parent.parent
TAGS = ("library", "application")


class ModelError(ValueError):
    pass


def load_yaml(path):
    with open(path, "r") as handle:
        return yaml.safe_load(handle) or {}


def apply_root(config, root):
    """Rebase every path of config.yaml that lives under config.root onto another root.

    Used for non-root builds, tests and staging: --root /tmp/stage turns /opt/bytehawks/deps
    into /tmp/stage/deps. The recorded `root` itself is rewritten too.
    """
    cfg = copy.deepcopy(config)
    old = cfg["config"]["root"].rstrip("/")
    new = str(root).rstrip("/")

    def rebase(value):
        if isinstance(value, str):
            if value == old or value.startswith(old + "/"):
                return new + value[len(old):]
            return value
        if isinstance(value, dict):
            return {k: rebase(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rebase(v) for v in value]
        return value

    cfg["config"] = rebase(cfg["config"])
    return cfg


def select_variant(package_name, package, variant_name):
    for variant in package.get("variant", []):
        names = variant["name"] if isinstance(variant["name"], list) else [variant["name"]]
        if variant_name in names:
            return variant
    available = variant_names(package)
    raise ModelError(f"variant '{variant_name}' of '{package_name}' not found (available: {', '.join(available)})")


def variant_names(package):
    return [n for v in package.get("variant", []) for n in (v["name"] if isinstance(v["name"], list) else [v["name"]])]


def default_variant(package):
    """'stable' when the package has it, otherwise its first declared variant."""
    names = variant_names(package)
    return "stable" if "stable" in names else names[0]


def canonical_variant(package, name):
    """The first alias of the variant `name` belongs to (stable artifact names)."""
    for variant in package.get("variant", []):
        names = variant["name"] if isinstance(variant["name"], list) else [variant["name"]]
        if name in names:
            return names[0]
    return name


def detect_arch():
    """Go/OCI naming, the same Shaker exports as BH_SHAKER_ARCH."""
    machine = platform.machine().lower()
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        machine, "armv7" if machine.startswith("armv7") else machine)


LIBCS = ("glibc", "musl")
LINKAGES = ("dynamic", "static")


def detect_libc():
    """The C library of this host: BH_LIBC, else musl when a musl dynamic loader is installed, else glibc."""
    forced = os.environ.get("BH_LIBC")
    if forced:
        return forced
    import glob
    return "musl" if glob.glob("/lib/ld-musl-*") or glob.glob("/usr/lib/ld-musl-*") else "glibc"


def publish_tag(package):
    """Role under which a package is published: library when it can be one, else its first tag."""
    tags = package.get("tags", [])
    return package.get("publish_tag") or ("library" if "library" in tags else tags[0])


def default_tag(package_name, package):
    tags = package.get("tags", [])
    if len(tags) == 1:
        return tags[0]
    raise ModelError(f"package '{package_name}' has tags {tags}: pass --tag")


@dataclass
class Unit:
    """One buildable thing: a package, a variant and the role (tag) it is built for."""

    name: str
    variant: str
    tag: str
    package: dict
    variant_data: dict
    context: dict = field(default_factory=dict)
    depends: list = field(default_factory=list)       # resolved Units, set by Plan
    recipe_sha256: str = ""
    assets_sha256: str = ""                          # digest of assets/<package>/ (templates, hooks), "" without any

    @property
    def key(self):
        return (self.name, self.variant, self.tag)

    @property
    def prefix(self):
        return self.context["prefix"]

    @property
    def bundle_dir(self):
        """Applications own their prefix; libraries share one, so they get a sub directory."""
        base = Path(self.prefix) / ".bytehawks"
        return base / self.name if self.tag == "library" else base

    @property
    def src_dir(self):
        return self.context["src_dir"]


class Catalog:
    """config.yaml + packages.yaml with everything needed to resolve units."""

    def __init__(self, config, packages, jobs=None, arch=None, assets_root=None, libc=None, linkage=None):
        self.config = config
        self.assets_root = Path(assets_root) if assets_root else None
        self.packages = packages.get("package", {})
        self.jobs = jobs or os.cpu_count() or 1
        self.arch = arch or detect_arch()
        self.libc = libc or detect_libc()
        if self.libc not in LIBCS:
            raise ModelError(f"unknown libc '{self.libc}' (use {', '.join(LIBCS)})")
        self.linkage = linkage or os.environ.get("BH_LINKAGE") or "dynamic"
        if self.linkage not in LINKAGES:
            raise ModelError(f"unknown linkage '{self.linkage}' (use {', '.join(LINKAGES)})")
        # identity of the platform in artifact names: glibc keeps the plain arch, musl is "<arch>-musl"
        self.platform = self.arch if self.libc == "glibc" else f"{self.arch}-{self.libc}"

    def package(self, name):
        if name not in self.packages:
            raise ModelError(f"package '{name}' not found in packages.yaml")
        return self.packages[name]

    def unit(self, name, variant=None, tag=None):
        package = self.package(name)
        variant = variant or default_variant(package)
        tag = tag or default_tag(name, package)
        if tag not in TAGS:
            raise ModelError(f"invalid tag '{tag}'")
        if tag not in package.get("tags", []):
            raise ModelError(f"package '{name}' is not tagged '{tag}' (tags: {package.get('tags', [])})")
        package, linkage = self._for_linkage(package)
        unit = Unit(name, canonical_variant(package, variant), tag, package,
                    self._variant_for_arch(select_variant(name, package, variant)))
        unit.context = self._context(unit, linkage)
        unit.assets_sha256 = assets_digest(self.assets_root, name)
        return unit

    def _for_linkage(self, package):
        """(package, linkage) as this unit is built. A package opts in to a static build with a `static:` block (commands and
        env replacing the default ones, `binaries` that must come out without a dynamic loader); one without it has nothing
        to link differently (libraries that only ship archives) and keeps its dynamic identity, so it is built and stored once."""
        override = package.get("static")
        if self.linkage != "static" or not override:
            return {k: v for k, v in package.items() if k != "static"}, "dynamic"
        merged = {k: v for k, v in package.items() if k != "static"}
        for key, value in override.items():
            merged[key] = {**merged.get(key, {}), **value} if isinstance(value, dict) else value
        merged["static"] = {"binaries": list(override.get("binaries", []))}
        return merged, "static"

    def _variant_for_arch(self, variant):
        """`sha256` may be a map when the source differs per platform. Keys: "<arch>-<libc>" (amd64-musl) or just
        "<arch>" (amd64); the most specific one wins. The unit sees the scalar ('' = not pinned for it: the build refuses)."""
        pins = variant.get("sha256")
        if isinstance(pins, dict):
            variant = {**variant, "sha256": pins.get(f"{self.arch}-{self.libc}", pins.get(self.arch, ""))}
        return variant

    def _context(self, unit, linkage="dynamic"):
        cfg = self.config["config"]
        v = unit.variant_data
        base = {
            "config": cfg,
            "name": unit.name,
            "package_name": unit.name,
            "tag": unit.tag,
            "jobs": self.jobs,
            "arch": self.arch,
            "libc": self.libc,
            "linkage": linkage,
            "platform": self.platform if linkage == "dynamic" else f"{self.platform}-static",
            "libc_name": unit.package.get("libc_map", {}).get(self.libc, self.libc),     # e.g. musl -> alpine-linux
            "full_version": v["full_version"],
            "major_version": v["major_version"],
            "patch_version": v["patch_version"],
            "variant": v,                                  # extra variant keys, e.g. {{ variant.build }}
            # the upstream's name for this architecture (package.arch_map), e.g. amd64 -> x64
            "arch_name": unit.package.get("arch_map", {}).get(self.arch, self.arch),
            "git": unit.package.get("git", {}),
            "website": unit.package.get("website", ""),
            "deps_prefix": cfg["libraries"]["default_prefix"],
        }
        prefix_key = "libraries" if unit.tag == "library" else "applications"
        base["default_prefix"] = resolve_template(cfg[prefix_key]["default_prefix"], base)
        base["prefix"] = base["default_prefix"]
        base["src_dir"] = resolve_template(
            "{{ config.default_src_path }}/{{ name }}-{{ full_version }}", base)
        base["git"] = resolve_dict_templates(base["git"], base)
        return base

    def dependencies(self, unit):
        """Direct dependencies of a unit, as Units. Entries: "zlib" or {name, variant, tag}."""
        result = []
        for entry in unit.package.get("depends_on", []):
            if isinstance(entry, str):
                entry = {"name": entry}
            dep_pkg = self.package(entry["name"])
            tag = entry.get("tag") or ("library" if "library" in dep_pkg.get("tags", []) else default_tag(entry["name"], dep_pkg))
            result.append(self.unit(entry["name"], entry.get("variant"), tag))
        return result

    def plan(self, root_unit, with_deps=True):
        """Topologically ordered build list (dependencies first), cycle-safe, deduplicated."""
        ordered, state = [], {}

        def visit(unit, trail):
            if state.get(unit.key) == "done":
                return next(u for u in ordered if u.key == unit.key)
            if state.get(unit.key) == "visiting":
                raise ModelError("dependency cycle: " + " -> ".join(trail + [unit.name]))
            state[unit.key] = "visiting"
            deps = [visit(d, trail + [unit.name]) for d in self.dependencies(unit)] if with_deps else []
            unit.depends = deps
            unit.recipe_sha256 = recipe_hash(unit)
            state[unit.key] = "done"
            ordered.append(unit)
            return unit

        visit(root_unit, [])
        return ordered


    def publishable_units(self, packages=None):
        """Every (package, variant) under its publish role, with hashes resolved, in catalog order."""
        units = []
        for name in sorted(packages or self.packages):
            package = self.package(name)
            for variant in package.get("variant", []):
                unit = self.unit(name, variant["name"][0] if isinstance(variant["name"], list) else variant["name"],
                                 publish_tag(package))
                self.plan(unit)
                units.append(unit)
        return sorted(units, key=lambda u: (_depth(u), u.name, u.variant))        # dependencies first

    def dependents(self, name):
        """Packages that depend on `name`, directly or transitively (sorted)."""
        found, frontier = set(), {name}
        while frontier:
            current = frontier.pop()
            for other, package in self.packages.items():
                deps = {d if isinstance(d, str) else d["name"] for d in package.get("depends_on", [])}
                if current in deps and other not in found:
                    found.add(other)
                    frontier.add(other)
        return sorted(found)


def assets_digest(assets_root, package_name):
    """Digest of every file under assets/<package>/ (relative path, mode bit, content), or "" when there are none.

    Templates and hooks end up in the bundle and in the installed tree, so they belong to the recipe."""
    if assets_root is None:
        return ""
    base = Path(assets_root) / package_name
    if not base.is_dir():
        return ""
    digest = hashlib.sha256()
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        digest.update(f"{path.relative_to(base).as_posix()}\0{int(os.access(path, os.X_OK))}\0".encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _depth(unit):
    return 1 + max((_depth(d) for d in unit.depends), default=-1)


def recipe_hash(unit):
    """Hash of everything that influences the produced artifact, including dependency hashes:
    a change in a library invalidates every application built on top of it."""
    recipe = resolve_dict_templates(
        {k: v for k, v in unit.package.items() if k not in ("variant",)}, unit.context)
    payload = {
        "schema": BUNDLE_SCHEMA,
        "recipe": recipe,
        "variant": unit.variant_data,
        "tag": unit.tag,
        "prefix": unit.prefix,
        "arch": unit.context["arch"],
        "libc": unit.context["libc"],
        "depends": [d.recipe_sha256 for d in unit.depends],
    }
    if unit.context["linkage"] != "dynamic":     # absent for dynamic units: their hashes do not change
        payload["linkage"] = unit.context["linkage"]
    if unit.assets_sha256:                       # absent for packages without assets: their hashes do not change
        payload["assets"] = unit.assets_sha256
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
