import json
import os
import shutil
import subprocess
import tarfile

import httpx
import pytest

from scripts.build.artifact import ArtifactRef
from scripts.build.packager import create_tarball, extract_artifact
from scripts.build.verify import find_dynamic_executables, read_elf
from scripts.core.util import expand_env
from scripts.planner import plan
from scripts.recipes import RecipeSet
from scripts.upstream import check_updates
from tests.conftest import REPO, needs_gcc, write_recipe

VARIANTS = ["old-legacy", "legacy", "stable", "stream"]


def test_expand_env(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    monkeypatch.delenv("NOPE", raising=False)
    assert expand_env("${FOO}") == "bar"
    assert expand_env("${NOPE}") is None
    assert expand_env("${NOPE:-dflt}") == "dflt"
    assert expand_env("http://${FOO}:80/${NOPE:-x}") == "http://bar:80/x"
    assert expand_env(5) == 5


def test_artifact_ref_layout():
    ref = ArtifactRef("nginx", "1.30.6", "stable")
    assert ref.filename == "nginx-1.30.6-stable-linux-amd64.tar.gz"
    assert ref.remote_path("{package}/{major_minor}/{full_version}", ref.filename) == \
        "nginx/1.30/1.30.6/nginx-1.30.6-stable-linux-amd64.tar.gz"


@needs_gcc
def test_static_detection(tmp_path):
    src = tmp_path / "h.c"
    src.write_text("int main(void){return 0;}")
    stat, dyn = tmp_path / "bin" / "s", tmp_path / "bin" / "d"
    stat.parent.mkdir()
    subprocess.run(["gcc", "-static", str(src), "-o", str(stat)], check=True)
    subprocess.run(["gcc", str(src), "-o", str(dyn)], check=True)
    assert read_elf(stat).is_static is True
    assert read_elf(dyn).is_static is False
    assert find_dynamic_executables(tmp_path) == ["bin/d"]


def test_tarball_is_deterministic_and_extracts_without_toplevel(tmp_path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "x").write_text("x")
    (root / "bin" / "x").chmod(0o755)
    (root / "lnk").symlink_to("bin/x")
    a, b = tmp_path / "a.tgz", tmp_path / "b.tgz"
    for d in (a, b):
        create_tarball(root, "pkg-1", d, 1700000000, {"name": "pkg"})
    assert a.read_bytes() == b.read_bytes()

    out = tmp_path / "out"
    extract_artifact(a, out)
    assert (out / "bin" / "x").read_text() == "x"
    assert (out / "lnk").is_symlink()
    assert not (out / ".distillery").exists()
    with tarfile.open(a) as t:
        assert {m.uid for m in t.getmembers()} == {0}


class _Repos:
    def __init__(self, present):
        self.present = present

    def exists(self, ref):
        return ref.package in self.present


def test_plan_splits_buckets_and_skips_published(config):
    rs = RecipeSet.load(config.list_variants(), REPO / "packages")
    full = plan(config, rs, variants=["stable"])
    stable_libs = {e["package"] for e in full["libraries"]}
    assert {"pcre2", "zlib", "openssl", "curl"} <= stable_libs
    assert "libxml2" not in stable_libs          # experimental: only on request
    assert any(e["package"] == "nginx" for e in full["applications"])
    assert full["applications"][0]["image"].endswith(":stable")

    part = plan(config, rs, variants=["stable"], repositories=_Repos({"zlib", "nginx"}))
    assert {e["package"] for e in part["libraries"]} >= {"pcre2", "openssl"}
    assert "zlib" not in {e["package"] for e in part["libraries"]}
    assert not any(e["package"] == "nginx" for e in part["applications"])

    forced = plan(config, rs, variants=["stable"], repositories=_Repos({"zlib"}), force=True)
    assert any(e["package"] == "zlib" for e in forced["libraries"])


def test_check_updates_respects_series(tmp_path):
    write_recipe(tmp_path, "nginx", f"""
        version: 1.0.0
        schema: distillery-package
        package:
          name: nginx
          kind: application
          summary: x
          license: MIT
          website: https://example.com
          source: {{url: "https://example.com/n.tar.gz"}}
          upstream: {{type: github-releases, repo: nginx/nginx, tag_regex: '^release-(\\d+\\.\\d+\\.\\d+)$'}}
          versions:
            - {{version: "1.28.1", sha256: {'a' * 64}, variants: [legacy], series: "1.28"}}
            - {{version: "1.30.2", sha256: {'b' * 64}, variants: [stable], series: "1.30"}}
          steps: [{{name: b, run: "true"}}]
        """)
    rs = RecipeSet.load(VARIANTS, tmp_path)
    releases = [{"tag_name": t, "prerelease": False} for t in
                ("release-1.31.0", "release-1.30.2", "release-1.28.3", "release-1.29.9-rc1", "junk")]
    t = httpx.MockTransport(lambda req: httpx.Response(200, json=releases))
    ups = check_updates(rs, transport=t)
    assert [(u["current"], u["latest"]) for u in ups] == [("1.28.1", "1.28.3")]


@needs_gcc
def test_bundle_makes_dynamic_binary_relocatable(tmp_path):
    from scripts.build.bundle import bundle_runtime
    from scripts.build.verify import find_dynamic_executables

    src = tmp_path / "m.c"
    src.write_text('#include <math.h>\n#include <stdio.h>\nint main(int c,char**v){printf("%.1f %s\\n",sqrt(16.0),v[1]);return 0;}')
    tree = tmp_path / "tree"
    (tree / "bin").mkdir(parents=True)
    subprocess.run(["gcc", str(src), "-o", str(tree / "bin" / "m"), "-lm"], check=True)

    bundle_runtime(tree)

    assert (tree / "bin" / "m.bin").exists()
    assert find_dynamic_executables(tree, ignore_suffix=".bin") == []
    moved = tmp_path / "elsewhere" / "deep"
    moved.parent.mkdir()
    shutil.copytree(tree, moved)
    out = subprocess.run(["env", "-i", str(moved / "bin" / "m"), "ok"], capture_output=True, text=True)
    assert out.stdout.strip() == "4.0 ok", out.stderr
