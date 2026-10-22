import json
import shutil

from scripts import cli
from tests.conftest import REPO, make_tarball, write_recipe


def test_validate_command_on_shipped_recipes(capsys):
    assert cli.main(["validate"]) == 0
    out = capsys.readouterr().out
    assert "nginx" in out and "recipes OK" in out


def test_plan_github_output(tmp_path, monkeypatch, capsys):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert cli.main(["plan", "--format", "github", "--variant", "stable"]) == 0
    lines = dict(l.split("=", 1) for l in out.read_text().splitlines())
    libs = json.loads(lines["libraries"])["include"]
    apps = json.loads(lines["applications"])["include"]
    assert lines["has_libraries"] == "true"
    assert {e["package"] for e in libs} >= {"zlib", "openssl", "pcre2"}
    assert [e["package"] for e in apps] == ["nginx"]       # experimental packages are not planned
    assert all(e["image"].endswith(":stable") for e in libs + apps)


def test_plan_explicit_package_includes_experimental(capsys):
    cli.main(["plan", "--package", "php", "--variant", "stable"])
    plan = json.loads(capsys.readouterr().out)
    assert [e["package"] for e in plan["applications"]] == ["php"]


def test_lock_pins_sha256_in_place(tmp_path, capsys):
    tar = make_tarball(tmp_path / "dummy-1.0.tar.gz", "dummy-1.0", {"a.txt": "x"})
    pkgs = tmp_path / "packages"
    write_recipe(pkgs, "dummy", f"""
        version: 1.0.0
        schema: distillery-package
        package:
          name: dummy
          kind: library
          summary: x
          license: MIT
          website: https://example.com
          status: experimental
          source:
            url: "file://{tmp_path}/dummy-{{{{ version }}}}.tar.gz"
          versions:
            # keep this comment
            - version: "1.0"
              variants: [all]
          steps: [{{name: b, run: "true"}}]
        """)
    argv = ["--packages-dir", str(pkgs), "--base-path", str(tmp_path / "base")]
    assert cli.main(argv + ["lock", "dummy"]) == 0
    text = (pkgs / "dummy.yaml").read_text()
    assert "# keep this comment" in text
    from scripts.core.util import sha256_file
    assert f'sha256: "{sha256_file(tar)}"' in text
    # idempotent
    assert cli.main(argv + ["lock", "dummy"]) == 0
    assert text == (pkgs / "dummy.yaml").read_text()
