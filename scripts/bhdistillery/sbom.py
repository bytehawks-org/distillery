"""CycloneDX 1.5 SBOM of a unit: the unit itself, every unit it was built with, the toolchain, with hashes and licenses.

Written to `<bundle>/sbom.cdx.json` at build time (so it is covered by `bundle.sha256`, the signature and the artifact) and
printed by `autobuilder.py sbom` from the catalog without building. Deterministic: the serial number derives from the recipe hash
and the timestamp is the build time, so the same recipe gives the same document apart from `built_at`."""

import json
import re
import uuid

from . import __version__

# SPDX identifiers CycloneDX's schema enumerates (a subset: the licenses of what this catalog builds). Anything else is written as
# an `expression`, which the schema accepts for any SPDX expression, so the document stays valid.
KNOWN_SPDX = {
    "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Zlib", "EPL-2.0", "MPL-2.0", "PostgreSQL", "OpenSSL",
    "GPL-2.0-only", "GPL-3.0-only", "LGPL-2.1-only", "LGPL-3.0-only", "AGPL-3.0-only", "GPL-2.0-with-classpath-exception",
}
SPDX_TOKEN = re.compile(r"^[A-Za-z0-9.\-+]+$")


def _is_expression(value):
    """An SPDX license expression: ids joined by AND / OR / WITH (no two ids in a row), optional parentheses."""
    tokens = value.replace("(", " ( ").replace(")", " ) ").split()
    previous_id = False
    for token in tokens:
        if token in ("(", ")"):
            continue
        if token in ("AND", "OR", "WITH"):
            if not previous_id:
                return False
            previous_id = False
        elif SPDX_TOKEN.match(token):
            if previous_id:
                return False
            previous_id = True
        else:
            return False
    return previous_id


def purl(unit):
    return f"pkg:generic/{unit.name}@{unit.variant_data['full_version']}"


def bom_ref(unit):
    return f"{purl(unit)}?recipe={unit.recipe_sha256[:12]}"


def _licenses(unit):
    value = unit.package.get("license", "")
    if not value:
        return []
    if value in KNOWN_SPDX:
        return [{"license": {"id": value}}]
    if _is_expression(value):
        return [{"expression": value}]
    return [{"license": {"name": value}}]


def _source_references(unit):
    git = unit.context["git"]
    refs = []
    url = git.get("release_download_url_template")
    if url:
        ref = {"type": "distribution", "url": url}
        if unit.variant_data.get("sha256"):
            ref["hashes"] = [{"alg": "SHA-256", "content": unit.variant_data["sha256"]}]
        refs.append(ref)
    elif git.get("repo"):
        refs.append({"type": "vcs", "url": git["repo"]})
    if git.get("repo") and url:
        refs.append({"type": "vcs", "url": git["repo"]})
    if unit.context.get("website"):
        refs.append({"type": "website", "url": unit.context["website"]})
    return refs


def component(unit):
    item = {
        "type": "library" if unit.tag == "library" else "application",
        "bom-ref": bom_ref(unit),
        "name": unit.name,
        "version": str(unit.variant_data["full_version"]),
        "purl": purl(unit),
        "properties": [
            {"name": "bytehawks:recipe_sha256", "value": unit.recipe_sha256},
            {"name": "bytehawks:variant", "value": unit.variant},
            {"name": "bytehawks:platform", "value": unit.context["platform"]},
            {"name": "bytehawks:linkage", "value": unit.context["linkage"]},
        ],
    }
    if unit.variant_data.get("commit"):
        item["properties"].append({"name": "bytehawks:source_commit", "value": unit.variant_data["commit"]})
    if unit.variant_data.get("sha256"):
        item["hashes"] = [{"alg": "SHA-256", "content": unit.variant_data["sha256"]}]
    licenses = _licenses(unit)
    if licenses:
        item["licenses"] = licenses
    refs = _source_references(unit)
    if refs:
        item["externalReferences"] = refs
    return item


def closure(unit):
    """The unit's dependencies, transitively, each once, in a stable order."""
    seen, order = {}, []

    def visit(current):
        for dep in current.depends:
            if dep.key not in seen:
                seen[dep.key] = dep
                visit(dep)
                order.append(dep)

    visit(unit)
    return order


def build(unit, toolchain=None, built_at=None):
    """The SBOM of `unit` (plan resolved: `unit.depends` and recipe hashes set) as a dict."""
    deps = closure(unit)
    serial = uuid.uuid5(uuid.NAMESPACE_URL, f"bytehawks:{unit.recipe_sha256}")
    metadata = {
        "tools": {"components": [{"type": "application", "name": "distillery", "version": __version__}]},
        "component": component(unit),
    }
    if built_at:
        metadata["timestamp"] = built_at
    for tool, version in sorted((toolchain or {}).items()):
        metadata["tools"]["components"].append({"type": "application", "name": f"toolchain:{tool}", "version": version})
    graph = [{"ref": bom_ref(u), "dependsOn": sorted(bom_ref(d) for d in u.depends)} for u in [unit] + deps]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": metadata,
        "components": [component(d) for d in deps],
        "dependencies": graph,
    }


def render(unit, toolchain=None, built_at=None):
    return json.dumps(build(unit, toolchain, built_at), indent=2, sort_keys=True) + "\n"
