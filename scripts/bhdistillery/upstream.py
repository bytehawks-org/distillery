"""Upstream release discovery.

A package declares where new versions are announced:

    upstream:
      type: git-tags                       # default; the only type today
      repo: https://github.com/openssl/openssl      # default: git.repo
      tag_regex: '^openssl-(\\d+\\.\\d+\\.\\d+)$'     # group 1 = version; excludes pre-releases by construction
      checksum_url_template: "{{ git.release_download_url_template }}.sha256"   # optional cross-check at bump time

and each variant says which series it follows (`track`, default: its `major_version`):
`stable` 3.5.4 follows "3.5" - a 3.5.5 is an update, a 3.6.0 is only reported as a newer series.

`git ls-remote` is used on purpose: no API token, no rate limit, works for any git host.
"""

import re
import subprocess
from dataclasses import dataclass, field

from .templates import resolve_template


class UpstreamError(RuntimeError):
    pass


def parse_version(text):
    """'3.5.10' -> (3, 5, 10). Non numeric components make the version unparsable (None)."""
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return None


def in_series(version, track):
    """`version` belongs to the series `track` ('3.5' matches 3.5, 3.5.4; '10' matches 10.44)."""
    track = str(track)
    return version == track or version.startswith(track + ".")


def list_tags(repo_url, runner=subprocess.run, timeout=120):
    """Tag names of a git repository, via `git ls-remote --tags --refs` (annotated tags de-duplicated)."""
    try:
        result = runner(["git", "ls-remote", "--tags", "--refs", repo_url],
                        capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpstreamError(f"git ls-remote {repo_url}: {exc}") from exc
    if result.returncode != 0:
        raise UpstreamError(f"git ls-remote {repo_url} failed: {result.stderr.strip()}")
    return [line.split("refs/tags/", 1)[1] for line in result.stdout.splitlines() if "refs/tags/" in line]


def versions_from_tags(tags, tag_regex):
    """[(version, tag)] for the tags matching the regex, newest first."""
    pattern = re.compile(tag_regex)
    found = []
    for tag in tags:
        match = pattern.match(tag)
        if match and parse_version(match.group(1)):
            found.append((match.group(1), tag))
    return sorted(found, key=lambda item: parse_version(item[0]), reverse=True)


@dataclass
class Finding:
    package: str
    variant: str
    current: str
    track: str
    latest: str = ""                  # newest in the followed series (== current when up to date)
    tag: str = ""
    newer_series: list = field(default_factory=list)      # versions outside the series, informational

    @property
    def outdated(self):
        return bool(self.latest) and parse_version(self.latest) > parse_version(self.current)

    def as_dict(self):
        return {"package": self.package, "variant": self.variant, "current": self.current,
                "track": self.track, "latest": self.latest, "tag": self.tag,
                "outdated": self.outdated, "newer_series": self.newer_series}


def upstream_config(unit_or_package, context):
    package = getattr(unit_or_package, "package", unit_or_package)
    config = package.get("upstream")
    if config is None:
        return None
    config = dict(config)
    config.setdefault("type", "git-tags")
    config.setdefault("repo", "{{ git.repo }}")
    return {key: resolve_template(value, context) if isinstance(value, str) else value
            for key, value in config.items()}


def variant_track(variant):
    return str(variant.get("track", variant["major_version"]))


def check_unit(unit, tags_for=list_tags):
    """Compare a unit's pinned version with upstream. Returns a Finding, or None without `upstream:`."""
    config = upstream_config(unit, unit.context)
    if config is None:
        return None
    if config["type"] != "git-tags":
        raise UpstreamError(f"{unit.name}: unsupported upstream type '{config['type']}'")
    if "tag_regex" not in config:
        raise UpstreamError(f"{unit.name}: upstream.tag_regex is required")

    current = str(unit.variant_data["full_version"])
    track = variant_track(unit.variant_data)
    finding = Finding(unit.name, unit.variant, current, track)
    releases = versions_from_tags(tags_for(config["repo"]), config["tag_regex"])

    same = [(v, t) for v, t in releases if in_series(v, track)]
    if same:
        finding.latest, finding.tag = same[0]
    newest_per_series = {}
    for version, _ in releases:                                  # newest first
        if not in_series(version, track) and parse_version(version) > parse_version(current):
            newest_per_series.setdefault(".".join(version.split(".")[:2]), version)
    finding.newer_series = list(newest_per_series.values())[:4]
    return finding
