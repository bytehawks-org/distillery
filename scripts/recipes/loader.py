"""
Recipe loading, cross-validation and dependency resolution.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

from scripts.config.template_engine import DistilleryTemplateEngine

from scripts.core.exceptions import PackageError
from scripts.recipes.models import PackageKind, Recipe, RecipeFile, Status, VersionSpec

logger = logging.getLogger(__name__)

DEFAULT_PACKAGES_DIR = Path(__file__).parent.parent.parent / "packages"


@dataclass(frozen=True)
class BuildTarget:
    """One concrete build: package x version x variant"""
    recipe: Recipe
    version: VersionSpec
    variant: str

    @property
    def key(self) -> str:
        return f"{self.recipe.name}-{self.version.version}@{self.variant}"


class RecipeSet:
    """All recipes of a packages/ directory, validated against each other."""

    def __init__(self, recipes: Dict[str, Recipe], variants: Iterable[str]):
        self.recipes = recipes
        self.variants = list(variants)
        self._validate()

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, variants: Iterable[str], packages_dir: Optional[Path] = None) -> "RecipeSet":
        packages_dir = Path(packages_dir or DEFAULT_PACKAGES_DIR)
        if not packages_dir.is_dir():
            raise PackageError(f"Packages directory not found: {packages_dir}")

        recipes: Dict[str, Recipe] = {}
        for path in sorted(packages_dir.glob("*.yaml")):
            recipe = cls._load_file(path)
            if recipe.name in recipes:
                raise PackageError(f"Duplicate package '{recipe.name}' ({path.name})")
            if path.stem != recipe.name:
                raise PackageError(f"{path.name}: file name must match package name '{recipe.name}'")
            recipes[recipe.name] = recipe

        if not recipes:
            raise PackageError(f"No recipes found in {packages_dir}")
        return cls(recipes, variants)

    @staticmethod
    def _load_file(path: Path) -> Recipe:
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            raise PackageError(f"{path.name}: invalid YAML: {e}") from e
        if not isinstance(data, dict):
            raise PackageError(f"{path.name}: must contain a YAML mapping")
        try:
            return RecipeFile(**data).package
        except Exception as e:
            raise PackageError(f"{path.name}: {e}") from e

    # -- validation ------------------------------------------------------

    def _validate(self) -> None:
        for recipe in self.recipes.values():
            for dep in recipe.depends_on:
                if dep not in self.recipes:
                    raise PackageError(f"{recipe.name}: unknown dependency '{dep}'")
                dep_recipe = self.recipes[dep]
                if dep_recipe.kind != PackageKind.LIBRARY:
                    raise PackageError(f"{recipe.name}: dependency '{dep}' is not a library")
                if dep_recipe.status == Status.EXPERIMENTAL and recipe.status == Status.STABLE:
                    raise PackageError(
                        f"{recipe.name}: stable package cannot depend on experimental '{dep}'")

            # every (variant) is covered by at most one version
            covered: Dict[str, str] = {}
            for v in recipe.versions:
                for variant in self.expand_variants(v):
                    if variant not in self.variants:
                        raise PackageError(f"{recipe.name} {v.version}: unknown variant '{variant}'")
                    if variant in covered:
                        raise PackageError(
                            f"{recipe.name}: variant '{variant}' maps to both "
                            f"{covered[variant]} and {v.version}")
                    covered[variant] = v.version

            if recipe.status == Status.STABLE:
                for v in recipe.versions:
                    if not v.sha256:
                        raise PackageError(
                            f"{recipe.name} {v.version}: stable packages require sha256 "
                            f"(run `distillery lock {recipe.name}`)")

        self.build_order()  # raises on cycles

    def expand_variants(self, version: VersionSpec) -> List[str]:
        if version.variants == ["all"]:
            return list(self.variants)
        return list(version.variants)

    # -- queries ---------------------------------------------------------

    def get(self, name: str) -> Recipe:
        try:
            return self.recipes[name]
        except KeyError:
            raise PackageError(f"Unknown package '{name}'. Known: {', '.join(sorted(self.recipes))}")

    def version_for(self, name: str, variant: str) -> Optional[VersionSpec]:
        recipe = self.get(name)
        for v in recipe.versions:
            if variant in self.expand_variants(v):
                return v
        return None

    def target(self, name: str, variant: str) -> BuildTarget:
        version = self.version_for(name, variant)
        if version is None:
            raise PackageError(f"{name} is not defined for variant '{variant}'")
        return BuildTarget(self.get(name), version, variant)

    def targets(self, names: Optional[Iterable[str]] = None,
                variants: Optional[Iterable[str]] = None,
                include_experimental: bool = False) -> List[BuildTarget]:
        """All concrete targets, in dependency order."""
        wanted = set(names) if names else None
        variant_list = list(variants) if variants else self.variants
        out: List[BuildTarget] = []
        for name in self.build_order():
            recipe = self.recipes[name]
            if wanted is not None and name not in wanted:
                continue
            if recipe.status == Status.EXPERIMENTAL and not include_experimental and wanted is None:
                continue
            for variant in variant_list:
                if self.version_for(name, variant):
                    out.append(self.target(name, variant))
        return out

    def dependencies(self, name: str, *, transitive: bool = True) -> List[str]:
        """Dependencies of ``name`` in build order (deps first, excluding itself)."""
        if not transitive:
            return list(self.get(name).depends_on)
        needed, stack = set(), list(self.get(name).depends_on)
        while stack:
            dep = stack.pop()
            if dep not in needed:
                needed.add(dep)
                stack.extend(self.get(dep).depends_on)
        return [n for n in self.build_order() if n in needed]

    def dependents(self, name: str) -> List[str]:
        return [n for n in self.build_order() if name in self.dependencies(n)]

    def build_order(self) -> List[str]:
        """Topological order (Kahn), deterministic; raises on cycles."""
        indegree = {n: len(r.depends_on) for n, r in self.recipes.items()}
        users: Dict[str, List[str]] = {n: [] for n in self.recipes}
        for n, r in self.recipes.items():
            for d in r.depends_on:
                users[d].append(n)

        ready = sorted(n for n, d in indegree.items() if d == 0)
        order: List[str] = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for u in sorted(users[n]):
                indegree[u] -= 1
                if indegree[u] == 0:
                    ready.append(u)
                    ready.sort()

        if len(order) != len(self.recipes):
            cyclic = sorted(set(self.recipes) - set(order))
            raise PackageError(f"Dependency cycle among: {', '.join(cyclic)}")
        return order


_engine = DistilleryTemplateEngine(max_passes=3, strict=True)


def source_url(recipe: Recipe, version: VersionSpec, **extra) -> str:
    """Render the source URL of ``version`` (Jinja: name, version, major_minor, vars, ...)."""
    ctx = {"name": recipe.name, "version": version.version, "major_minor": version.major_minor,
           "vars": dict(version.vars)}
    ctx.update(extra)
    return _engine.render(version.url or recipe.source.url, ctx)
