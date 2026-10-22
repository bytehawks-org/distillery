import re

import pytest

from scripts.core.exceptions import PackageError
from scripts.recipes import RecipeSet
from tests.conftest import REPO, write_recipe

VARIANTS = ["old-legacy", "legacy", "stable", "stream"]


def test_shipped_recipes_are_valid():
    rs = RecipeSet.load(VARIANTS, REPO / "packages")
    order = rs.build_order()
    for name, recipe in rs.recipes.items():
        for dep in recipe.depends_on:
            assert order.index(dep) < order.index(name)


def recipe(name, deps="", extra_versions="", sha="a" * 64, variants="[all]", kind="library"):
    return f"""
        version: 1.0.0
        schema: distillery-package
        package:
          name: {name}
          kind: {kind}
          summary: x
          license: MIT
          website: https://example.com
          depends_on: [{deps}]
          source: {{url: "https://example.com/{name}.tar.gz"}}
          versions:
            - version: "1.0"
              sha256: {sha}
              variants: {variants}
            {extra_versions}
          steps: [{{name: b, run: "true"}}]
        """


def test_cycle_detected(tmp_path):
    write_recipe(tmp_path, "a", recipe("a", "b"))
    write_recipe(tmp_path, "b", recipe("b", "a"))
    with pytest.raises(PackageError, match="cycle"):
        RecipeSet.load(VARIANTS, tmp_path)


def test_unknown_dependency(tmp_path):
    write_recipe(tmp_path, "a", recipe("a", "ghost"))
    with pytest.raises(PackageError, match="unknown dependency"):
        RecipeSet.load(VARIANTS, tmp_path)


def test_stable_requires_sha(tmp_path):
    body = re.sub(r"sha256: a{64}\n\s*", "", recipe("a"))
    write_recipe(tmp_path, "a", body)
    with pytest.raises(PackageError, match="sha256"):
        RecipeSet.load(VARIANTS, tmp_path)


def test_variant_assigned_twice(tmp_path):
    extra = f'- version: "2.0"\n              sha256: {"b" * 64}\n              variants: [stable]'
    write_recipe(tmp_path, "a", recipe("a", extra_versions=extra))
    with pytest.raises(PackageError, match="maps to both"):
        RecipeSet.load(VARIANTS, tmp_path)


def test_unknown_variant(tmp_path):
    write_recipe(tmp_path, "a", recipe("a", variants="[nope]"))
    with pytest.raises(PackageError, match="unknown variant"):
        RecipeSet.load(VARIANTS, tmp_path)


def test_filename_must_match_name(tmp_path):
    write_recipe(tmp_path, "wrong", recipe("a"))
    with pytest.raises(PackageError, match="file name"):
        RecipeSet.load(VARIANTS, tmp_path)


def test_dependencies_are_transitive_and_ordered(tmp_path):
    write_recipe(tmp_path, "a", recipe("a"))
    write_recipe(tmp_path, "b", recipe("b", "a"))
    write_recipe(tmp_path, "c", recipe("c", "b", kind="application"))
    rs = RecipeSet.load(VARIANTS, tmp_path)
    assert rs.dependencies("c") == ["a", "b"]
    assert rs.dependents("a") == ["b", "c"]
