"""Apply a version bump to packages.yaml: new version, new sha256, comments and layout preserved."""

import tempfile
from pathlib import Path

from ruamel.yaml import YAML

from . import fetch, signature
from .templates import resolve_dict_templates, resolve_template
from .upstream import in_series, variant_track


class BumpError(RuntimeError):
    pass


def _yaml():
    yaml = YAML()                       # round-trip: keeps comments, quoting, flow/block style
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def new_variant_values(variant, new_version):
    """major_version stays (it names the followed series); patch_version is the component after it."""
    track = variant_track(variant)
    if not in_series(new_version, track):
        raise BumpError(f"{new_version} is outside the followed series '{track}': change `track` and "
                        "`major_version` explicitly to move to a new series")
    remainder = new_version[len(track):].lstrip(".").split(".")
    patch = int(remainder[0]) if remainder and remainder[0] else 0
    return {"patch_version": patch, "full_version": new_version}


def source_url(unit, new_version):
    """Download URL of `new_version`, rendered with the unit's own template."""
    context = dict(unit.context)
    context["full_version"] = new_version
    context["git"] = dict(unit.package.get("git", {}))
    return resolve_template(unit.package["git"]["release_download_url_template"], context)


def _quiet(log):
    """Computing a checksum is the point of a bump: drop the 'unverified source' warning of fetch.download."""
    return lambda message: None if message.startswith("WARNING unverified") else log(message)


def git_pin(unit, new_version):
    """For a git source: (`<repo> <ref>`, commit) of the new version, resolved on the remote without cloning."""
    context = {**unit.context, "full_version": new_version}
    git = resolve_dict_templates(unit.package.get("git", {}), context)
    return f"{git['repo']} {git['ref_template']}", fetch.git_commit_of(git["repo"], git["ref_template"])


def fetch_checksum(unit, new_version, log=print):
    """SHA-256 of the new tarball, cross-checked against the upstream-published checksum if declared.
    For a git source the pin is the commit of the new tag and nothing is downloaded."""
    if unit.package.get("git", {}).get("ref_template"):
        return git_pin(unit, new_version)
    url = source_url(unit, new_version)
    log = _quiet(log)
    with tempfile.TemporaryDirectory() as tmp:
        digest = fetch.download(url, Path(tmp) / "source", allow_unverified=True, log=log)
        signature.verify_source(unit.package, {**unit.context, "full_version": new_version}, Path(tmp) / "source", url, log)
        template = unit.package.get("upstream", {}).get("checksum_url_template")
        if template:
            published_url = resolve_template(template, {**unit.context, "full_version": new_version,
                                                         "git": {**unit.context["git"], "release_download_url_template": url}})
            published_file = Path(tmp) / "published"
            fetch.download(published_url, published_file, allow_unverified=True, log=lambda m: None)
            words = published_file.read_text().split()
            published = words[0].lower() if words else ""
            # upstreams publish sha256 (64 hex) or sha512 (128 hex, e.g. Apache): compare like with like
            algorithm = {64: "sha256", 128: "sha512"}.get(len(published))
            if algorithm is None:
                raise BumpError(f"checksum published at {published_url} is neither sha256 nor sha512")
            actual = digest if algorithm == "sha256" else fetch.hash_file(Path(tmp) / "source", algorithm)
            if published != actual:
                raise BumpError(f"{algorithm} published by upstream ({published}) differs from the downloaded "
                                f"source ({actual}) for {url}")
            log(f"checksum confirmed by upstream ({published_url.rsplit('/', 1)[-1]})")
    return url, digest


def apply_bump(packages_file, package, variant_name, new_version, sha256, key="sha256"):
    """Rewrite one variant in place. Returns the (old, new) full_version.
    `sha256` is a string, or a {arch: sha256} map for variants pinned per architecture; for a git source it is the
    commit and `key` is "commit"."""
    yaml = _yaml()
    path = Path(packages_file)
    data = yaml.load(path.read_text())
    variants = data["package"][package]["variant"]

    for variant in variants:
        names = variant["name"] if isinstance(variant["name"], list) else [variant["name"]]
        if variant_name in names:
            old = str(variant["full_version"])
            values = new_variant_values(variant, new_version)
            variant["patch_version"] = values["patch_version"]
            variant["full_version"] = new_version
            variant[key] = sha256                       # "sha256" for tarballs, "commit" for git sources
            with open(path, "w") as handle:
                yaml.dump(data, handle)
            return old, new_version
    raise BumpError(f"variant '{variant_name}' of '{package}' not found")
