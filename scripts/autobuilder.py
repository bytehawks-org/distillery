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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bhdistillery import __version__, bundle, commands, fetch                  # noqa: E402
from bhdistillery.build import DEFAULT_STAGES, STAGES, BuildError              # noqa: E402
from bhdistillery.bump import BumpError                                        # noqa: E402
from bhdistillery.model import (DISTILLERY_HOME, Catalog, ModelError,          # noqa: E402
                                 apply_root, load_yaml)
from bhdistillery.package import PackageError                                  # noqa: E402
from bhdistillery.store import StoreError                                      # noqa: E402
from bhdistillery.templates import TemplateError                               # noqa: E402
from bhdistillery.upstream import UpstreamError                                # noqa: E402


def make_parser():
    parser = argparse.ArgumentParser(description="ByteHawks Distillery build orchestrator")
    parser.add_argument("--version", action="version", version=f"distillery {__version__}")
    sub = parser.add_subparsers(dest="action")

    def common(p, package=True, required=True):
        p.add_argument("--config", default=str(DISTILLERY_HOME / "config.yaml"), help="config.yaml path")
        p.add_argument("--packages", default=str(DISTILLERY_HOME / "packages.yaml"), help="packages.yaml path")
        p.add_argument("--assets", default=str(DISTILLERY_HOME / "assets"), help="assets directory")
        p.add_argument("--root", help="rebase /opt/bytehawks onto another directory (non-root builds, staging, tests)")
        p.add_argument("--arch", help="architecture label (default: detected, amd64/arm64)")
        if package:
            p.add_argument("--package", required=required)
            p.add_argument("--variant", help="default: stable, or the first declared variant")
            p.add_argument("--tag", choices=["library", "application"], help="role; needed when the package has several")

    def stores(p):
        p.add_argument("--store", action="append", metavar="SPEC",
                       help="artifact store: dir:PATH | github[:owner/repo] | nexus | name from config; repeatable, first match wins")

    def fmt(p):
        p.add_argument("--format", choices=["text", "json"], default="text")

    common(sub.add_parser("list", help="list packages and variants"), package=False)
    common(sub.add_parser("plan", help="show the dependency-ordered build plan"))

    p_build = sub.add_parser("build", help="build a package (and optionally its dependencies)")
    common(p_build)
    stores(p_build)
    p_build.add_argument("--with-deps", action="store_true", help="build missing/outdated dependencies first")
    p_build.add_argument("--dry-run", action="store_true", help="print what would be done, change nothing")
    p_build.add_argument("--force", action="store_true", help="rebuild even if the bundle is up to date")
    p_build.add_argument("--allow-unverified", action="store_true", help="accept sources without pinned sha256 (not for releases)")
    p_build.add_argument("--jobs", type=int, help="parallel jobs (default: CPU count)")
    p_build.add_argument("--stages", default=",".join(DEFAULT_STAGES),
                         help=f"comma list among {','.join(STAGES)} (add 'check' to run the test suite)")
    p_build.add_argument("--artifacts-dir", help="also package every built unit here (<name>.tar.gz + .json) for a store")

    p_bundle = sub.add_parser("bundle", help="write the Shaker bundle without building")
    common(p_bundle)
    p_bundle.add_argument("--out", help="output directory (default: <prefix>/.bytehawks[/<package>])")

    common(sub.add_parser("checksum", help="download the source and print its sha256"))

    p_out = sub.add_parser("outdated", help="compare pinned versions with upstream releases")
    common(p_out, required=False)
    fmt(p_out)
    p_out.add_argument("--exit-code", action="store_true", help="exit 3 when something is outdated")

    p_bump = sub.add_parser("bump", help="update packages.yaml to the latest release of the followed series")
    common(p_bump, required=False)
    fmt(p_bump)
    p_bump.add_argument("--to", help="explicit version (inside the followed series)")
    p_bump.add_argument("--dry-run", action="store_true", help="compute and verify, do not write packages.yaml")
    p_bump.add_argument("--summary-file", help="write the JSON list of bumps here (PR body, notifications)")

    p_inv = sub.add_parser("inventory", help="list the artifacts held by the stores")
    common(p_inv, package=False)
    stores(p_inv)
    fmt(p_inv)

    p_stale = sub.add_parser("stale", help="catalog units whose exact recipe is not in the stores")
    common(p_stale, required=False)
    stores(p_stale)
    fmt(p_stale)
    p_stale.add_argument("--force", action="store_true", help="list the selected units as 'rebuild' even when stored")
    p_stale.add_argument("--kind", choices=["all", "library", "application"], default="all")
    p_stale.add_argument("--all", action="store_true", help="show present units too")
    p_stale.add_argument("--exit-code", action="store_true", help="exit 3 when something is stale")
    p_stale.add_argument("--github-output", help="append libs/apps/has_libs/has_apps/count to this file ($GITHUB_OUTPUT)")

    p_aff = sub.add_parser("affected", help="a package and everything that depends on it, directly or not")
    common(p_aff)
    fmt(p_aff)

    p_chain = sub.add_parser("chain", help="build every stale unit in dependency order, restoring the rest from the stores")
    common(p_chain, required=False)
    stores(p_chain)
    p_chain.add_argument("--kind", choices=["all", "library", "application"], default="all")
    p_chain.add_argument("--artifacts-dir", help="package built units here (also read as the first store)")
    p_chain.add_argument("--dry-run", action="store_true")
    p_chain.add_argument("--force", action="store_true", help="rebuild the selected units even if stored")
    p_chain.add_argument("--allow-unverified", action="store_true")

    p_rest = sub.add_parser("restore", help="install a unit (and dependencies) from the stores, without building")
    common(p_rest)
    stores(p_rest)
    p_rest.add_argument("--with-deps", action="store_true")
    p_rest.add_argument("--force", action="store_true")

    p_ver = sub.add_parser("verify", help="check installed files against the bundle's files.sha256")
    common(p_ver)
    p_ver.add_argument("--with-deps", action="store_true")

    p_pub = sub.add_parser("publish", help="push artifacts from a directory to a store")
    common(p_pub, package=False)
    stores(p_pub)
    p_pub.add_argument("--from", dest="source", required=True, metavar="DIR", help="directory written by --artifacts-dir")
    p_pub.add_argument("--ignore-unsupported", action="store_true", help="do not fail on stores that cannot be published to from a script (github)")
    return parser


def load_catalog(args):
    config = load_yaml(args.config)
    if args.root:
        config = apply_root(config, args.root)
    return Catalog(config, load_yaml(args.packages), getattr(args, "jobs", None), getattr(args, "arch", None), getattr(args, "assets", None))


COMMANDS = {
    "list": commands.cmd_list, "plan": commands.cmd_plan, "build": commands.cmd_build,
    "bundle": commands.cmd_bundle, "checksum": commands.cmd_checksum, "outdated": commands.cmd_outdated,
    "bump": commands.cmd_bump, "inventory": commands.cmd_inventory, "stale": commands.cmd_stale,
    "affected": commands.cmd_affected, "chain": commands.cmd_chain, "restore": commands.cmd_restore,
    "publish": commands.cmd_publish, "verify": commands.cmd_verify,
}


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
        return COMMANDS[args.action](load_catalog(args), args) or 0
    except (ModelError, TemplateError, bundle.BundleError, fetch.FetchError, BuildError, StoreError,
            PackageError, UpstreamError, BumpError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
