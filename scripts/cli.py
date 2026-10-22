#!/usr/bin/env python3
"""
ByteHawks Distillery CLI.

    python -m scripts.cli validate
    python -m scripts.cli plan --skip-existing --format github
    python -m scripts.cli build nginx --variant stable --publish
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from scripts.build.artifact import ArtifactRef
from scripts.build.builder import Builder
from scripts.build.container import run_build_in_container
from scripts.config.loader import load_config
from scripts.config.models import DistilleryConfig, LogFormat
from scripts.core.exceptions import DistilleryError
from scripts.core.util import download
from scripts.planner import builder_image, plan
from scripts.publish import RepositoryManager
from scripts.publish.scan import scan_dist
from scripts.recipes import RecipeSet, source_url
from scripts.upstream import check_updates

REPO_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("distillery")


# --------------------------------------------------------------------- logging

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                           "level": record.levelname, "logger": record.name,
                           "msg": record.getMessage()})


def setup_logging(config: DistilleryConfig, verbose: bool) -> None:
    handler = logging.StreamHandler(sys.stderr)
    if config.logging.format == LogFormat.JSON and not sys.stderr.isatty() \
            and os.environ.get("GITHUB_ACTIONS") != "true":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if verbose else config.logging.level.value)


# -------------------------------------------------------------------- commands

def cmd_validate(ctx, args) -> int:
    config, recipes = ctx.config, ctx.recipes
    print(f"config OK: {len(config.list_variants())} variants, "
          f"{len(config.repository.endpoints())} repository endpoints")
    print(f"recipes OK: {len(recipes.recipes)} packages")
    for name in recipes.build_order():
        r = recipes.recipes[name]
        print(f"  {name:<14} {r.kind.value:<12} {r.link.value:<12} {r.status.value:<13} "
              f"deps={','.join(r.depends_on) or '-'}")
    return 0


def cmd_list(ctx, args) -> int:
    for t in ctx.recipes.targets(args.package or None, args.variant or None, True):
        print(f"{t.recipe.name:<14} {t.version.version:<12} {t.variant:<11} "
              f"{t.recipe.kind.value:<12} {t.recipe.status.value}")
    return 0


def cmd_plan(ctx, args) -> int:
    repos = RepositoryManager(ctx.config.repository) if args.skip_existing else None
    result = plan(ctx.config, ctx.recipes, packages=args.package or None,
                  variants=args.variant or None, include_experimental=args.include_experimental,
                  repositories=repos, force=args.force, registry=args.registry)
    if args.format == "github":
        out = os.environ.get("GITHUB_OUTPUT")
        lines = []
        for bucket in ("libraries", "applications"):
            lines.append(f"{bucket}={json.dumps({'include': result[bucket]}, separators=(',', ':'))}")
            lines.append(f"has_{bucket}={'true' if result[bucket] else 'false'}")
        if out:
            with open(out, "a") as f:
                f.write("\n".join(lines) + "\n")
        else:
            print("\n".join(lines))
    else:
        print(json.dumps(result, indent=2))
    return 0


def _selected(ctx, args):
    names = None if args.all else args.package
    if not names and not args.all:
        raise DistilleryError("Specify at least one package or --all")
    variants = [args.variant] if args.variant else [ctx.config.defaults.build.variant]
    if args.all_variants:
        variants = None
    return ctx.recipes.targets(names, variants, args.include_experimental)


def cmd_build(ctx, args) -> int:
    config = ctx.config
    executor = args.executor or config.defaults.build.executor
    targets = _selected(ctx, args)
    if not targets:
        print("nothing to build")
        return 0

    output = Path(args.output).resolve()
    fetcher = None if args.no_fetch else RepositoryManager(config.repository)

    if executor == "container":
        base = Path(args.base_path or "work").resolve()
        for t in targets:
            inner = ["--base-path", "/opt/bytehawks", "--packages-dir", "/src/packages",
                     "--config", "/src/config/config.yaml", "build", t.recipe.name,
                     "--variant", t.variant, "--executor", "native", "--output", "/dist"]
            if args.no_fetch:
                inner.append("--no-fetch")
            if t.recipe.status.value == "experimental":
                inner.append("--include-experimental")
            run_build_in_container(config, variant=t.variant, repo_root=REPO_ROOT,
                                   host_base=base, host_output=output, cli_args=inner)
    else:
        builder = Builder(config, ctx.recipes, output, REPO_ROOT,
                          arch=config.defaults.build.arch, fetcher=fetcher)
        for t in targets:
            builder.build(t)

    if args.publish:
        return _publish_dir(config, output, force=False)
    return 0


def _publish_dir(config: DistilleryConfig, dist: Path, force: bool) -> int:
    repos = RepositoryManager(config.repository)
    artifacts = scan_dist(dist)
    if not artifacts:
        print(f"no artifacts found in {dist}")
        return 0
    failures = 0
    rows = []
    for ref, files in artifacts:
        try:
            status = repos.publish(ref, files)
            line = ", ".join(f"{k}={v}" for k, v in status.items())
        except DistilleryError as e:
            logger.error("%s", e)
            failures += 1
            line = f"FAILED: {e}"
        print(f"{ref.filename}: {line}")
        rows.append((ref.filename, line))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("| artifact | result |\n|---|---|\n")
            f.writelines(f"| `{n}` | {r} |\n" for n, r in rows)
    return 1 if failures else 0


def cmd_publish(ctx, args) -> int:
    return _publish_dir(ctx.config, Path(args.dist).resolve(), force=False)


def cmd_lock(ctx, args) -> int:
    """Fill in missing sha256 values of a recipe by downloading the sources."""
    recipe = ctx.recipes.get(args.package)
    path = Path(ctx.packages_dir) / f"{recipe.name}.yaml"
    text = path.read_text()
    changed = False
    for v in recipe.versions:
        if v.sha256 and not args.refresh:
            continue
        url = source_url(recipe, v, arch=ctx.config.defaults.build.arch)
        tmp = Path(ctx.config.path.downloads) / url.rsplit("/", 1)[-1]
        digest = download(url, tmp)
        print(f"{recipe.name} {v.version}: {digest}")
        pat = re.compile(rf'^(\s*)- version:\s*["\']?{re.escape(v.version)}["\']?\s*$(\n\1\s+sha256:.*$)?', re.M)
        m = pat.search(text)
        if not m:
            raise DistilleryError(f"Cannot find 'version: {v.version}' entry in {path.name}")
        indent = m.group(1)
        replacement = f'{indent}- version: "{v.version}"\n{indent}  sha256: "{digest}"'
        text = text[:m.start()] + replacement + text[m.end():]
        changed = True
    if changed:
        path.write_text(text)
    return 0


def cmd_check_updates(ctx, args) -> int:
    updates = check_updates(ctx.recipes, api_url=ctx.config.github.api_url if ctx.config.github else
                            "https://api.github.com",
                            token=(ctx.config.github.token if ctx.config.github else None))
    if args.format == "json":
        print(json.dumps(updates, indent=2))
    else:
        if not updates:
            print("All packages are up to date.")
        for u in updates:
            print(f"- **{u['package']}** ({','.join(u['variants'])}): {u['current']} → {u['latest']} "
                  f"(https://github.com/{u['repo']})")
    return 0


def cmd_builder_matrix(ctx, args) -> int:
    """Matrix for the builder-image workflow: one row per variant."""
    base = ctx.config.build_type.container.image_basename
    rows = []
    for name in ctx.config.list_variants():
        v = ctx.config.get_variant(name)
        rows.append({
            "variant": name,
            "alpine_version": v.metadata.alpine_version,
            "image_primary": builder_image(ctx.config, name, "primary"),
            "image_fallback": builder_image(ctx.config, name, "fallback"),
        })
    payload = json.dumps({"include": rows}, separators=(",", ":"))
    out = os.environ.get("GITHUB_OUTPUT")
    if out and args.format == "github":
        with open(out, "a") as f:
            f.write(f"matrix={payload}\n")
    else:
        print(payload)
    return 0


def cmd_image(ctx, args) -> int:
    print(builder_image(ctx.config, args.variant, args.registry))
    return 0


# ------------------------------------------------------------------------ main

class Context:
    def __init__(self, config, recipes, packages_dir):
        self.config, self.recipes, self.packages_dir = config, recipes, packages_dir


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="distillery", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=str(REPO_ROOT / "config" / "config.yaml"))
    p.add_argument("--packages-dir", default=str(REPO_ROOT / "packages"))
    p.add_argument("--base-path", default=os.environ.get("DISTILLERY_BASE_PATH"),
                   help="Override variables.artifact_base_path (work dir)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="validate config and recipes").set_defaults(fn=cmd_validate)

    s = sub.add_parser("list", help="list package/version/variant targets")
    s.add_argument("package", nargs="*")
    s.add_argument("--variant", action="append")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("plan", help="emit the CI build matrix")
    s.add_argument("--package", action="append")
    s.add_argument("--variant", action="append")
    s.add_argument("--include-experimental", action="store_true")
    s.add_argument("--skip-existing", action="store_true",
                   help="omit artifacts already present in the repositories")
    s.add_argument("--force", action="store_true", help="ignore --skip-existing")
    s.add_argument("--registry", choices=["primary", "fallback"], default="primary")
    s.add_argument("--format", choices=["json", "github"], default="json")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("build", help="build packages (dependencies first)")
    s.add_argument("package", nargs="*")
    s.add_argument("--all", action="store_true")
    s.add_argument("--variant")
    s.add_argument("--all-variants", action="store_true")
    s.add_argument("--include-experimental", action="store_true")
    s.add_argument("--executor", choices=["native", "container"])
    s.add_argument("--output", default="dist")
    s.add_argument("--no-fetch", action="store_true",
                   help="always build dependencies from source")
    s.add_argument("--publish", action="store_true")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("publish", help="upload artifacts from a dist directory")
    s.add_argument("--dist", default="dist")
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("lock", help="pin missing source sha256 in a recipe")
    s.add_argument("package")
    s.add_argument("--refresh", action="store_true", help="recompute even if present")
    s.set_defaults(fn=cmd_lock)

    s = sub.add_parser("check-updates", help="look for new upstream releases")
    s.add_argument("--format", choices=["json", "md"], default="md")
    s.set_defaults(fn=cmd_check_updates)

    s = sub.add_parser("builder-matrix", help="matrix of builder images (one per variant)")
    s.add_argument("--format", choices=["json", "github"], default="json")
    s.set_defaults(fn=cmd_builder_matrix)

    s = sub.add_parser("image", help="print the builder image for a variant")
    s.add_argument("variant")
    s.add_argument("--registry", choices=["primary", "fallback"], default="primary")
    s.set_defaults(fn=cmd_image)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        overrides = {"artifact_base_path": str(Path(args.base_path).absolute())} if args.base_path else None
        config = load_config(Path(args.config), overrides)
        setup_logging(config, args.verbose)
        recipes = RecipeSet.load(config.list_variants(), Path(args.packages_dir))
        return args.fn(Context(config, recipes, args.packages_dir), args)
    except DistilleryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
