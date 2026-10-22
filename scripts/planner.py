"""
CI planning: which package x version x variant combinations still need building,
emitted as GitHub Actions matrices (libraries first, then applications).
"""

import logging
from typing import Dict, Iterable, List, Optional

from scripts.build.artifact import ArtifactRef
from scripts.config.models import DistilleryConfig
from scripts.recipes import PackageKind, RecipeSet

logger = logging.getLogger(__name__)


def builder_image(config: DistilleryConfig, variant: str, registry: str = "primary") -> str:
    """Builder image reference from the primary or the fallback registry."""
    if registry == "fallback":
        fb = config.get_fallback_registry()
        if fb:
            _, ep = fb
            base = config.build_type.container.image_basename
            return f"{ep.url}/{ep.namespace}/{base}:{variant}"
    return config.get_variant(variant).get_resolved_image()


def plan(config: DistilleryConfig, recipes: RecipeSet, *,
         packages: Optional[Iterable[str]] = None,
         variants: Optional[Iterable[str]] = None,
         include_experimental: bool = False,
         repositories=None,
         force: bool = False,
         registry: str = "primary",
         arch: str = "amd64") -> Dict[str, List[Dict]]:
    """
    Returns {"libraries": [...], "applications": [...]}; each entry is a dict
    usable as a GitHub matrix row.
    """
    out: Dict[str, List[Dict]] = {"libraries": [], "applications": []}
    for t in recipes.targets(packages, variants, include_experimental):
        ref = ArtifactRef(t.recipe.name, t.version.version, t.variant, arch)
        if repositories is not None and not force and repositories.exists(ref):
            logger.info("skip %s (already published)", t.key)
            continue
        entry = {
            "package": t.recipe.name,
            "version": t.version.version,
            "variant": t.variant,
            "kind": t.recipe.kind.value,
            "image": builder_image(config, t.variant, registry),
            "experimental": t.recipe.status.value == "experimental",
            "key": t.key,
        }
        bucket = "libraries" if t.recipe.kind == PackageKind.LIBRARY else "applications"
        out[bucket].append(entry)
    return out
