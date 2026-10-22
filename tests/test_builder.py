from pathlib import Path

import pytest

from scripts.build.builder import Builder
from scripts.core.exceptions import BuildError
from scripts.publish.scan import scan_dist
from scripts.recipes import RecipeSet
from tests.conftest import REPO, needs_gcc, write_recipe

VARIANTS = ["old-legacy", "legacy", "stable", "stream"]


def make_builder(config, pkgs, tmp_path, **kw):
    return Builder(config, RecipeSet.load(VARIANTS, pkgs), tmp_path / "dist", REPO, **kw)


@needs_gcc
def test_build_app_with_library_dependency(config, fake_packages, tmp_path):
    config.defaults.build.env = {"LDFLAGS": "-static"}
    b = make_builder(config, fake_packages, tmp_path)
    result = b.build(b.recipes.target("bar", "stable"))

    names = {r.target.recipe.name for r in b.results}
    assert names == {"foo", "bar"}
    assert result.artifact.tarball.exists()
    assert (result.artifact.sha256_file.read_text().split()[0]) == result.artifact.sha256
    assert result.artifact.sbom is not None

    found = {ref.package for ref, _files in scan_dist(tmp_path / "dist")}
    assert found == {"foo", "bar"}


@needs_gcc
def test_dynamic_executable_fails_static_gate(config, fake_packages, tmp_path):
    # no -static in LDFLAGS => dynamic binary => link: static must be rejected
    b = make_builder(config, fake_packages, tmp_path)
    with pytest.raises(BuildError, match="dynamic loader"):
        b.build(b.recipes.target("bar", "stable"))


@needs_gcc
def test_artifacts_are_reproducible(config, fake_packages, tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    config.defaults.build.env = {"LDFLAGS": "-static"}
    shas = []
    for run in ("a", "b"):
        cfg = config.model_copy(deep=True)
        b = Builder(cfg, RecipeSet.load(VARIANTS, fake_packages), tmp_path / f"dist-{run}", REPO)
        shas.append(b.build(b.recipes.target("foo", "stable")).artifact.sha256)
    assert shas[0] == shas[1]


@needs_gcc
def test_dependency_is_fetched_instead_of_built(config, fake_packages, tmp_path):
    config.defaults.build.env = {"LDFLAGS": "-static"}
    seed = make_builder(config, fake_packages, tmp_path / "seed")
    foo_ref = seed.build(seed.recipes.target("foo", "stable")).artifact.ref

    class Fetcher:
        calls = 0

        def fetch(self, ref, dest):
            Fetcher.calls += 1
            import shutil
            src = foo_ref.local_dir(tmp_path / "seed" / "dist")
            dest.mkdir(parents=True, exist_ok=True)
            for n in ref.all_files:
                if (src / n).exists():
                    shutil.copy(src / n, dest / n)
            return True

    cfg = config.model_copy(deep=True)
    cfg.path.generated.libraries = str(tmp_path / "other-deps")
    b = make_builder(cfg, fake_packages, tmp_path / "second", fetcher=Fetcher())
    b.build(b.recipes.target("bar", "stable"))
    assert Fetcher.calls == 1
    assert {r.target.recipe.name for r in b.results} == {"bar"}   # foo was not rebuilt


def test_checksum_mismatch_rejected(config, fake_packages, tmp_path):
    p = fake_packages / "foo.yaml"
    text = p.read_text()
    import re
    p.write_text(re.sub(r"sha256: \"[0-9a-f]{64}\"", 'sha256: "' + "0" * 64 + '"', text))
    b = make_builder(config, fake_packages, tmp_path)
    with pytest.raises(BuildError, match="sha256 mismatch"):
        b.build(b.recipes.target("foo", "stable"))
