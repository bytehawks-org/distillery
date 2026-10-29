"""Implementation of the CLI commands (autobuilder.py only parses arguments and dispatches)."""

import json
import sys
import tempfile
from pathlib import Path

from . import bump as bumper
from . import bundle, fetch, store as stores_mod
from .build import DEFAULT_STAGES, STAGES, ensure_unit, restore_unit
from .model import Catalog, ModelError, publish_tag
from .store import MultiStore, StoreError, Unsupported, entry_for, open_store
from .upstream import check_unit


def eprint(message):
    print(message, file=sys.stderr)


def emit(args, payload, text_lines):
    """JSON when --format=json, otherwise the text lines."""
    if getattr(args, "format", "text") == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for line in text_lines:
            print(line)


# ---------------------------------------------------------------------------- stores

def open_stores(catalog, args, extra=(), implicit=True):
    """Ordered stores from --store (repeatable); commands that need a store fall back to
    config.artifacts.default_stores (`implicit`), `build` only uses what it is told. `extra` goes first."""
    specs = list(getattr(args, "store", None) or [])
    if not specs and implicit:
        specs = (catalog.config["config"].get("artifacts") or {}).get("default_stores", [])
    return MultiStore([*extra, *(open_store(spec, catalog.config) for spec in specs)])


def _aliases(unit):
    names = unit.variant_data["name"]
    return names if isinstance(names, list) else [names]


def _selected_units(catalog, args):
    packages = getattr(args, "package", None)
    packages = [p for p in packages.replace(",", " ").split()] if isinstance(packages, str) else packages
    units = catalog.publishable_units(packages)
    variant = getattr(args, "variant", None)
    if variant:
        units = [u for u in units if variant in (u.variant, *_aliases(u))]
    kind = getattr(args, "kind", "all")
    return [u for u in units if kind == "all" or u.tag == kind or (kind == "application" and u.tag != "library")]


# ---------------------------------------------------------------------------- basics

def cmd_list(catalog, args):
    for name, pkg in sorted(catalog.packages.items()):
        variants = ", ".join(
            f"{'/'.join(v['name']) if isinstance(v['name'], list) else v['name']}={v['full_version']}"
            for v in pkg.get("variant", []))
        deps = ",".join(d if isinstance(d, str) else d["name"] for d in pkg.get("depends_on", [])) or "-"
        print(f"{name:12} {','.join(pkg.get('tags', [])):22} deps: {deps:24} {variants}")


def cmd_plan(catalog, args):
    unit = catalog.unit(args.package, args.variant, args.tag)
    for index, item in enumerate(catalog.plan(unit), 1):
        kind = "commit" if item.context["git"].get("ref_template") else "sha256"
        pinned = f"{kind} ok" if item.variant_data.get(kind) else f"{kind} MISSING"
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

    stores = open_stores(catalog, args, implicit=False)
    if not args.with_deps and unit.package.get("depends_on"):
        missing = [d for d in catalog.dependencies(unit) if not bundle.is_up_to_date(d)]
        if missing and not args.dry_run:
            eprint("warning: dependencies not built: " + ", ".join(d.name for d in missing) + " (use --with-deps)")

    for item in plan:
        ensure_unit(item, args.assets, stores, force=args.force, dry_run=args.dry_run,
                    allow_unverified=args.allow_unverified, stages=stages, artifacts_dir=args.artifacts_dir)


def cmd_bundle(catalog, args):
    unit = catalog.unit(args.package, args.variant, args.tag)
    catalog.plan(unit)
    out = bundle.emit(unit, args.assets, args.out)
    bundle.seal(out, catalog.config["config"], log=eprint)
    print(f"bundle written to {out}")


def cmd_checksum(catalog, args):
    unit = catalog.unit(args.package, args.variant, args.tag)
    git = unit.context["git"]
    if git.get("ref_template"):                                    # git source: the pin is the commit of the ref
        print(f"{fetch.git_commit_of(git['repo'], git['ref_template'])}  {git['ref_template']}")
        return
    url = git["release_download_url_template"]
    with tempfile.TemporaryDirectory() as tmp:
        digest = fetch.download(url, Path(tmp) / "source", allow_unverified=True, log=eprint)
    print(f"{digest}  {Path(url).name}")


# ------------------------------------------------------------------------ upstream

def _findings(catalog, args):
    names = [args.package] if getattr(args, "package", None) else sorted(catalog.packages)
    findings = []
    for name in names:
        package = catalog.package(name)
        if "upstream" not in package:
            continue
        for variant in package["variant"]:
            variant_name = variant["name"][0] if isinstance(variant["name"], list) else variant["name"]
            if getattr(args, "variant", None) and args.variant not in (
                    variant["name"] if isinstance(variant["name"], list) else [variant["name"]]):
                continue
            unit = catalog.unit(name, variant_name, publish_tag(package))
            findings.append(check_unit(unit))
    return findings


def cmd_outdated(catalog, args):
    findings = _findings(catalog, args)
    lines = []
    for f in findings:
        mark = "OUTDATED" if f.outdated else "ok"
        extra = f"  (newer series: {', '.join(f.newer_series)})" if f.newer_series else ""
        lines.append(f"{f.package:10} {f.variant:12} {f.current:10} follows {f.track:6} latest {f.latest or '-':10} {mark}{extra}")
    emit(args, [f.as_dict() for f in findings], lines)
    return 3 if args.exit_code and any(f.outdated for f in findings) else 0


def _raw_variant(catalog, finding):
    from .model import select_variant
    return select_variant(finding.package, catalog.package(finding.package), finding.variant)


def cmd_bump(catalog, args):
    results = []
    for finding in _findings(catalog, args):
        if not finding.outdated and not args.to:
            continue
        version = args.to or finding.latest
        unit = catalog.unit(finding.package, finding.variant, publish_tag(catalog.package(finding.package)))
        url, digest = bumper.fetch_checksum(unit, version, log=eprint)
        if isinstance(_raw_variant(catalog, finding).get("sha256"), dict):          # pinned per architecture
            digest = {}
            for key in _raw_variant(catalog, finding)["sha256"]:             # "amd64" or "amd64-musl"
                arch, _, libc = key.partition("-")
                per_platform = Catalog(catalog.config, {"package": catalog.packages}, catalog.jobs, arch,
                                       catalog.assets_root, libc or "glibc")
                digest[key] = bumper.fetch_checksum(
                    per_platform.unit(finding.package, finding.variant, unit.tag), version, log=eprint)[1]
        result = {"package": finding.package, "variant": finding.variant, "from": finding.current,
                  "to": version, "sha256": digest, "source": url, "newer_series": finding.newer_series}
        if not args.dry_run:
            bumper.apply_bump(args.packages, finding.package, finding.variant, version, digest,
                              key="commit" if unit.context["git"].get("ref_template") else "sha256")
        results.append(result)
        eprint(f"{'would bump' if args.dry_run else 'bumped'} {finding.package}/{finding.variant} "
               f"{finding.current} -> {version}")

    if args.summary_file:
        Path(args.summary_file).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    emit(args, results, [f"{r['package']}/{r['variant']}: {r['from']} -> {r['to']}  sha256 {r['sha256']}" for r in results])
    return 0


# ------------------------------------------------------------------------ artifacts

def _status(unit, inventory):
    wanted = entry_for(unit, unit.context["platform"])
    if any(e.name == wanted.name for e in inventory):
        return "present", wanted
    superseded = [e for e in inventory if e.key() == wanted.key()]
    return ("superseded" if superseded else "missing"), wanted


def cmd_inventory(catalog, args):
    entries = open_stores(catalog, args).inventory()
    entries.sort(key=lambda e: (e.package, e.variant, e.created_at), reverse=False)
    emit(args, [e.as_dict() for e in entries],
         [f"{e.package:10} {e.variant:12} {e.tag:11} {e.arch:6} {e.version:10} {e.recipe12}  {e.size:>10}  "
          f"{e.created_at[:19]}  exp {e.expires_at[:10] or '-':10} {e.store}" for e in entries]
         or ["(no artifacts)"])


def cmd_stale(catalog, args):
    inventory = open_stores(catalog, args).inventory()
    rows = []
    for unit in _selected_units(catalog, args):
        status, wanted = _status(unit, inventory)
        if args.force and status == "present":
            status = "rebuild"
        rows.append({"package": unit.name, "variant": unit.variant, "tag": unit.tag, "version": wanted.version,
                     "recipe12": wanted.recipe12, "name": wanted.name, "status": status,
                     "depends": [d.name for d in unit.depends]})
    stale = [r for r in rows if r["status"] != "present"]

    if args.github_output:
        libs = [r for r in stale if r["tag"] == "library"]
        apps = [r for r in stale if r["tag"] != "library"]
        with open(args.github_output, "a") as handle:
            handle.write(f"libs={json.dumps({'include': libs})}\napps={json.dumps({'include': apps})}\n")
            handle.write(f"has_libs={'true' if libs else 'false'}\nhas_apps={'true' if apps else 'false'}\n")
            handle.write(f"count={len(stale)}\n")

    shown = rows if args.all else stale
    emit(args, shown, [f"{r['status']:10} {r['package']:10} {r['variant']:12} {r['tag']:11} {r['version']:10} {r['recipe12']}"
                       for r in shown] or ["everything is up to date in the store"])
    return 3 if args.exit_code and stale else 0


def cmd_verify(catalog, args):
    from .package import verify_files

    unit = catalog.unit(args.package, args.variant, args.tag)
    bad = 0
    for item in catalog.plan(unit, with_deps=args.with_deps):
        problems = verify_files(item)
        print(f"{'FAIL' if problems else 'ok  '} {item.name} {item.variant_data['full_version']} "
              f"({len(problems)} problem(s))")
        for problem in problems[:20]:
            print(f"     {problem}")
        bad += bool(problems)
    return 1 if bad else 0


def cmd_affected(catalog, args):
    names = [args.package, *catalog.dependents(args.package)]
    emit(args, names, names)


def cmd_chain(catalog, args):
    """Build what is stale, in dependency order, restoring everything else from the stores."""
    work = Path(tempfile.mkdtemp(prefix="bh-chain-"))
    local = [stores_mod.DirStore(args.artifacts_dir)] if args.artifacts_dir else []
    stores = open_stores(catalog, args, extra=local)
    inventory = stores.inventory()
    targets = [u for u in _selected_units(catalog, args) if args.force or _status(u, inventory)[0] != "present"]
    forced = {u.key for u in targets} if args.force else set()

    if not targets:
        print("chain: nothing to build, the stores already hold every selected unit")
        return 0

    print("chain: " + ", ".join(f"{u.name}/{u.variant}" for u in targets))
    stages = tuple(DEFAULT_STAGES)
    done = set()
    for target in targets:
        for item in catalog.plan(target):
            if item.key in done:
                continue
            done.add(item.key)
            ensure_unit(item, args.assets, stores, work, force=item.key in forced, dry_run=args.dry_run,
                        allow_unverified=args.allow_unverified, stages=stages, artifacts_dir=args.artifacts_dir)
    return 0


def cmd_restore(catalog, args):
    unit = catalog.unit(args.package, args.variant, args.tag)
    stores = open_stores(catalog, args)
    work = Path(tempfile.mkdtemp(prefix="bh-restore-"))
    for item in catalog.plan(unit, with_deps=args.with_deps):
        if not args.force and bundle.is_up_to_date(item):
            print(f"[skip] {item.name}: already installed")
            continue
        if restore_unit(item, stores, work, log=print) is None:
            raise StoreError(f"{item.name} {item.variant_data['full_version']} ({item.recipe_sha256[:12]}) "
                             "is not in any store")


def cmd_package(catalog, args):
    from . import packaging

    conf = packaging.settings(catalog.config)
    unit = catalog.unit(args.package, args.variant, args.tag)
    formats = ([f for f in args.format.replace(" ", "").split(",") if f] if args.format
               else packaging.default_formats(conf, catalog.libc))
    bad = [f for f in formats if f not in packaging.FORMATS]
    if bad:
        raise packaging.PackagingError(f"unknown format(s): {', '.join(bad)} (use {', '.join(packaging.FORMATS)})")
    if not formats:
        raise packaging.PackagingError(f"no configured package format targets {catalog.libc} systems")
    if catalog.config["config"]["root"] != "/opt/bytehawks":
        eprint(f"warning: built under {catalog.config['config']['root']}: the packages install to exactly these paths "
               "(build with the default root for packages meant for /opt/bytehawks)")
    written = packaging.build_packages(catalog, unit, formats, args.out, conf, dry_run=args.dry_run,
                                       with_deps=args.with_deps, nfpm=args.nfpm, log=eprint)
    print(f"{'planned' if args.dry_run else 'wrote'} {len(written)} package(s) in {args.out}")
    return 0


def cmd_scaffold(catalog, args):
    import yaml
    from . import scaffold

    source = Path(args.source)
    text = source.read_text()
    fmt = args.format or scaffold.detect_format(text, source.name)
    hints = dict(h.split("=", 1) for h in (args.hint or []))
    commented = args.commented if args.commented != "no" or not args.also else "loose"
    settings, lines = scaffold.parse(text, fmt, commented)
    chosen = scaffold.select(settings, args.include or "", args.exclude or "", args.max or 0, args.also or "")
    entries = scaffold.env_entries(args.package, chosen, fmt, hints, lines, dict(d.split("=", 1) for d in (args.default or [])))
    skipped = sum(1 for s in settings if s.repeated)
    eprint(f"{source.name}: format {fmt}, {len(settings)} settings found, {len(entries)} selected"
           + (f", {skipped} repeated directives left out" if skipped else ""))

    if args.list:
        for e in entries:
            print(f"{e['name']:44} {e['type']:16} {str(e.get('default', '(none)')):24} {e.get('description', '')[:60]}")
        return 0

    template_name = args.template or f"{source.name}.tmpl"
    dest = args.dest or "{{ prefix }}/conf/" + source.name
    template = {"src": template_name, "dest": dest, "mode": args.mode}
    rendered = scaffold.render_template(lines, chosen, fmt)

    if not args.write:
        print(yaml.safe_dump(entries, sort_keys=False, width=200).rstrip())
        eprint(f"--write would add these to package '{args.package}' (existing entries untouched) and create "
               f"assets/{args.package}/{template_name} ({len(rendered.splitlines())} lines), rendered to {dest}")
        return 0

    target = Path(args.assets) / args.package / template_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered)
    added, template_added = scaffold.merge_into_packages(args.packages, args.package, entries, template)
    eprint(f"wrote {target}; {len(added)} new runtime.env entries in {args.packages}"
           + ("; template registered" if template_added else "; template already registered")
           + f"; {len(entries) - len(added)} already present, left as they are")
    return 0


def cmd_sign(catalog, args):
    from .signature import sign_directory

    written = sign_directory(args.source, catalog.config, force=args.force, log=eprint)
    print(f"signed {len(written)} artifact(s)")
    return 0


def cmd_publish(catalog, args):
    source = stores_mod.DirStore(args.source)
    target = open_stores(catalog, args)
    if not target.stores:
        raise StoreError("publish needs a destination (--store)")
    published = 0
    for entry in source.inventory():
        for store in target.stores:
            if store.find(entry) is not None:
                print(f"[have] {entry.name} in {store.label}")
                continue
            tarball, meta = source.fetch(entry, args.source)
            try:
                where = store.publish(entry, tarball, meta)
            except Unsupported as exc:
                if args.ignore_unsupported:
                    print(f"[skip] {store.label}: {exc}")
                    continue
                raise
            print(f"[push] {entry.name} -> {where}")
            published += 1
    print(f"published {published} artifact(s)")
