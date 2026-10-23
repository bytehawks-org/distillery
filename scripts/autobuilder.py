#!/usr/bin/env python3
"""ByteHawks Distillery build orchestrator.

  autobuilder.py list
  autobuilder.py plan     --package nginx [--variant stable] [--tag application]
  autobuilder.py build    --package nginx [--with-deps] [--dry-run] [--force] [--root DIR]
  autobuilder.py bundle   --package nginx          (bundle only, no build)
  autobuilder.py checksum --package openssl --variant stable   (download and print sha256)
"""

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bhdistillery import __version__, bundle, fetch                      # noqa: E402
from bhdistillery.build import DEFAULT_STAGES, STAGES, BuildError, build_unit   # noqa: E402
from bhdistillery.model import (DISTILLERY_HOME, Catalog, ModelError,    # noqa: E402
                                 apply_root, load_yaml)
from bhdistillery.templates import TemplateError                         # noqa: E402


def make_parser():
    parser = argparse.ArgumentParser(description="ByteHawks Distillery build orchestrator")
    parser.add_argument("--version", action="version", version=f"distillery {__version__}")
    sub = parser.add_subparsers(dest="action")

    def common(p, package=True):
        p.add_argument("--config", default=str(DISTILLERY_HOME / "config.yaml"), help="config.yaml path")
        p.add_argument("--packages", default=str(DISTILLERY_HOME / "packages.yaml"), help="packages.yaml path")
        p.add_argument("--assets", default=str(DISTILLERY_HOME / "assets"), help="assets directory")
        p.add_argument("--root", help="rebase /opt/bytehawks onto another directory (non-root builds, staging, tests)")
        if package:
            p.add_argument("--package", required=True)
            p.add_argument("--variant", help="default: stable, or the first declared variant")
            p.add_argument("--tag", choices=["library", "application"], help="role; needed when the package has several")

    common(sub.add_parser("list", help="list packages and variants"), package=False)
    common(sub.add_parser("plan", help="show the dependency-ordered build plan"))

    p_build = sub.add_parser("build", help="build a package (and optionally its dependencies)")
    common(p_build)
    p_build.add_argument("--with-deps", action="store_true", help="build missing/outdated dependencies first")
    p_build.add_argument("--dry-run", action="store_true", help="print what would be done, change nothing")
    p_build.add_argument("--force", action="store_true", help="rebuild even if the bundle is up to date")
    p_build.add_argument("--allow-unverified", action="store_true", help="accept sources without pinned sha256 (not for releases)")
    p_build.add_argument("--jobs", type=int, help="parallel jobs (default: CPU count)")
    p_build.add_argument("--stages", default=",".join(DEFAULT_STAGES),
                         help=f"comma list among {','.join(STAGES)} (add 'check' to run the test suite)")

    p_bundle = sub.add_parser("bundle", help="write the Shaker bundle without building")
    common(p_bundle)
    p_bundle.add_argument("--out", help="output directory (default: <prefix>/.bytehawks[/<package>])")

    common(sub.add_parser("checksum", help="download the source and print its sha256"))
    return parser


def load_catalog(args):
    config = load_yaml(args.config)
    if args.root:
        config = apply_root(config, args.root)
    return Catalog(config, load_yaml(args.packages), getattr(args, "jobs", None))


def cmd_list(catalog):
    for name, pkg in sorted(catalog.packages.items()):
        variants = ", ".join(
            f"{'/'.join(v['name']) if isinstance(v['name'], list) else v['name']}={v['full_version']}"
            for v in pkg.get("variant", []))
        deps = ",".join(d if isinstance(d, str) else d["name"] for d in pkg.get("depends_on", [])) or "-"
        print(f"{name:12} {','.join(pkg.get('tags', [])):22} deps: {deps:24} {variants}")


def cmd_plan(catalog, args):
    unit = catalog.unit(args.package, args.variant, args.tag)
    for index, item in enumerate(catalog.plan(unit), 1):
        pinned = "sha256 ok" if item.variant_data.get("sha256") else "sha256 MISSING"
        state = "up-to-date" if bundle.is_up_to_date(item) else "to build"
        print(f"{index}. {item.name} {item.variant_data['full_version']} [{item.variant}/{item.tag}] "
              f"{pinned}, {state}, recipe {item.recipe_sha256[:12]}")


def cmd_build(catalog, args):
    unit = catalog.unit(args.package, args.variant, args.tag)
    plan = catalog.plan(unit, with_deps=args.with_deps)
    stages = tuple(s for s in args.stages.split(",") if s)
    unknown = set(stages) - set(STAGES)
    if unknown:
        raise ModelError(f"unknown stage(s): {', '.join(sorted(unknown))}")

    if not args.with_deps and unit.package.get("depends_on"):
        missing = [d for d in catalog.dependencies(unit) if not bundle.is_up_to_date(d)]
        if missing and not args.dry_run:
            print("warning: dependencies not built: " + ", ".join(d.name for d in missing)
                  + " (use --with-deps)", file=sys.stderr)

    for item in plan:
        build_unit(item, args.assets, dry_run=args.dry_run, force=args.force,
                   allow_unverified=args.allow_unverified, stages=stages)


def cmd_bundle(catalog, args):
    unit = catalog.unit(args.package, args.variant, args.tag)
    catalog.plan(unit)
    out = bundle.emit(unit, args.assets, args.out)
    print(f"bundle written to {out}")


def cmd_checksum(catalog, args):
    unit = catalog.unit(args.package, args.variant, args.tag)
    url = unit.context["git"]["release_download_url_template"]
    with tempfile.TemporaryDirectory() as tmp:
        digest = fetch.download(url, Path(tmp) / "source", allow_unverified=True, log=lambda m: print(m, file=sys.stderr))
    print(f"{digest}  {Path(url).name}")


def main(argv=None):
    parser = make_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].startswith("--") and argv[0] not in ("-h", "--help", "--version"):
        argv = ["build", "--dry-run"] + argv          # legacy invocation was a dry run
    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return 2

    try:
        catalog = load_catalog(args)
        if args.action == "list":
            cmd_list(catalog)
        elif args.action == "plan":
            cmd_plan(catalog, args)
        elif args.action == "build":
            cmd_build(catalog, args)
        elif args.action == "bundle":
            cmd_bundle(catalog, args)
        elif args.action == "checksum":
            cmd_checksum(catalog, args)
    except (ModelError, TemplateError, bundle.BundleError, fetch.FetchError, BuildError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
