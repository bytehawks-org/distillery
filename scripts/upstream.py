"""
Upstream release monitoring (GitHub releases / tags) for `check-updates`.
"""

import logging
import re
from typing import Dict, List, Optional

import httpx
from packaging.version import InvalidVersion, Version

from scripts.recipes import RecipeSet

logger = logging.getLogger(__name__)


def _parse(v: str) -> Optional[Version]:
    try:
        return Version(v)
    except InvalidVersion:
        return None


def upstream_versions(client: httpx.Client, api_url: str, spec) -> List[str]:
    """Stable (non pre-release) versions found upstream, newest first."""
    kind = "releases" if spec.type == "github-releases" else "tags"
    resp = client.get(f"{api_url}/repos/{spec.repo}/{kind}", params={"per_page": 100})
    resp.raise_for_status()
    rx = re.compile(spec.tag_regex)
    found = {}
    for item in resp.json():
        if item.get("prerelease") or item.get("draft"):
            continue
        m = rx.match(item.get("tag_name") or item.get("name") or "")
        if not m:
            continue
        ver = _parse(m.group(1))
        if ver and not ver.is_prerelease:
            found[ver] = m.group(1)
    return [found[v] for v in sorted(found, reverse=True)]


def check_updates(recipes: RecipeSet, *, api_url: str = "https://api.github.com",
                  token: Optional[str] = None,
                  transport: Optional[httpx.BaseTransport] = None) -> List[Dict]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "bytehawks-distillery"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    updates = []
    with httpx.Client(headers=headers, timeout=30, transport=transport) as client:
        for name in recipes.build_order():
            r = recipes.recipes[name]
            if r.upstream.type == "none" or not r.upstream.repo:
                continue
            try:
                available = upstream_versions(client, api_url, r.upstream)
            except httpx.HTTPError as e:
                logger.warning("%s: upstream check failed: %s", name, e)
                continue
            for spec in r.versions:
                cur = _parse(spec.version)
                pool = [a for a in available
                        if not spec.series or a == spec.series or a.startswith(spec.series + ".")]
                latest = pool[0] if pool else None
                lv = _parse(latest) if latest else None
                if cur and lv and lv > cur:
                    updates.append({"package": name, "current": spec.version, "latest": latest,
                                    "series": spec.series, "variants": spec.variants,
                                    "repo": r.upstream.repo})
    return updates
