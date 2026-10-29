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
from bhdistillery.packaging import PackagingError
from bhdistillery.scaffold import ScaffoldError                              # noqa: E402
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
        p.add_argument("--libc", choices=["glibc", "musl"], help="C library the units are built for (default: detected, or BH_LIBC)")
        p.add_argument("--linkage", choices=["dynamic", "static"], help="static = link the binaries of packages that define a `static:` recipe without a dynamic loader (default: dynamic, or BH_LINKAGE)")
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
    p_pkg = sub.add_parser("package", help="write deb / rpm / apk packages of a built package (needs nfpm)")
    common(p_pkg)
    p_pkg.add_argument("--format", metavar="LIST", help="comma separated: deb,rpm,apk (default: packaging.formats)")
    p_pkg.add_argument("--out", default="dist/packages", metavar="DIR")
    p_pkg.add_argument("--with-deps", action="store_true", help="also package the dependencies (needed to install it)")
    p_pkg.add_argument("--dry-run", action="store_true", help="show names, contents and dependencies; write nothing")
    p_pkg.add_argument("--nfpm", metavar="PATH", help="nfpm binary (default: BH_NFPM, packaging.nfpm, PATH)")

    p_sc = sub.add_parser("scaffold", help="derive runtime.env entries and a template from the configuration file an application ships")
    common(p_sc, package=False)
    p_sc.add_argument("--package", required=True, help="package the parameters belong to (names become BH_<PACKAGE>_<KEY>)")
    p_sc.add_argument("--from", dest="source", required=True, metavar="FILE", help="the application's sample / default configuration file")
    p_sc.add_argument("--format", choices=["space", "equals", "ini", "yaml"], help="default: detected (key value | key = value | [sections] | yaml)")
    p_sc.add_argument("--commented", choices=["no", "strict", "loose"], default="no",
                      help="also take settings documented as comments: strict = '#key = value' (no space after #), loose = '# key value' (prose can match)")
    p_sc.add_argument("--include", metavar="REGEX", help="only settings whose key (or section.key) matches")
    p_sc.add_argument("--exclude", metavar="REGEX", help="leave out settings that match")
    p_sc.add_argument("--also", metavar="REGEX", help="besides the active settings, also these ones when the file only has them as commented defaults")
    p_sc.add_argument("--max", type=int, help="at most N settings")
    p_sc.add_argument("--hint", action="append", metavar="KEY=TYPE", help="override an inferred type, e.g. loglevel=enum:debug,notice,warning")
    p_sc.add_argument("--default", action="append", metavar="KEY=VALUE", help="set the default of a setting, e.g. dir='{{ prefix }}/data'")
    p_sc.add_argument("--list", action="store_true", help="show the proposed parameters and stop")
    p_sc.add_argument("--write", action="store_true", help="add the entries to packages.yaml, write assets/<package>/<name>.tmpl and register it")
    p_sc.add_argument("--template", metavar="NAME", help="template file name (default <file>.tmpl)")
    p_sc.add_argument("--dest", metavar="PATH", help="where Shaker renders it (default {{ prefix }}/conf/<file>)")
    p_sc.add_argument("--mode", default="0640")

    p_sign = sub.add_parser("sign", help="sign the artifacts of a directory that have no signature yet")
    common(p_sign, package=False)
    p_sign.add_argument("--from", dest="source", required=True, metavar="DIR", help="directory written by --artifacts-dir")
    p_sign.add_argument("--force", action="store_true", help="sign again artifacts that already have a signature")
    return parser


def load_catalog(args):
    config = load_yaml(args.config)
    if args.root:
        config = apply_root(config, args.root)
    return Catalog(config, load_yaml(args.packages), getattr(args, "jobs", None), getattr(args, "arch", None), getattr(args, "assets", None),
                   getattr(args, "libc", None), getattr(args, "linkage", None))


COMMANDS = {
    "list": commands.cmd_list, "plan": commands.cmd_plan, "build": commands.cmd_build,
    "bundle": commands.cmd_bundle, "checksum": commands.cmd_checksum, "outdated": commands.cmd_outdated,
    "bump": commands.cmd_bump, "sign": commands.cmd_sign, "scaffold": commands.cmd_scaffold, "package": commands.cmd_package, "inventory": commands.cmd_inventory, "stale": commands.cmd_stale,
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
            PackageError, UpstreamError, BumpError, PackagingError, ScaffoldError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
