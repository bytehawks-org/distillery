import contextlib
import io
import json
import os
import tarfile
import unittest

import yaml

from helpers import *  # noqa: F401,F403
import autobuilder
from bhdistillery import package as artifact


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = autobuilder.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Env:
    """A catalog on disk (config.yaml + packages.yaml) with a tiny library and an application on top of it."""

    def __init__(self, t, lib_make="echo lib-v1 > lib.txt"):
        self.t = t
        self.digest = make_tarball(t / "src.tgz", {"README": "x"}, top="demo-1.0")
        url = (t / "src.tgz").as_uri()
        self.url = url
        self.write(lib_make)

    def write(self, lib_make):
        lib = package(self.url, self.digest, tags=["library"], commands={
            "make": lib_make, "install": "mkdir -p {{ prefix }}/include && cp lib.txt {{ prefix }}/include/lib.h"})
        app = package(self.url, self.digest, depends_on=["lib"], commands={
            "make": 'cat "{{ deps_prefix }}/include/lib.h" > app.txt',
            "install": "mkdir -p {{ prefix }}/bin && cp app.txt {{ prefix }}/bin/app.txt"})
        (self.t / "packages.yaml").write_text(yaml.safe_dump({"package": {"lib": lib, "app": app}}))
        (self.t / "config.yaml").write_text(yaml.safe_dump(CONFIG))

    def cli(self, *argv, root="root", store=None):
        args = [*argv, "--config", str(self.t / "config.yaml"), "--packages", str(self.t / "packages.yaml"),
                "--assets", str(self.t / "assets"), "--root", str(self.t / root), "--arch", "amd64"]
        if store is not None:
            args += ["--store", store]
        return run_cli(*args)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        os.environ["SOURCE_DATE_EPOCH"] = "1700000000"

    def tearDown(self):
        os.environ.pop("SOURCE_DATE_EPOCH", None)

    def test_snapshot_diff_only_reports_what_the_build_changed(self):
        with TempDir() as t:
            (t / "p").mkdir()
            (t / "p" / "old").write_text("1")
            before = artifact.snapshot(t / "p")
            (t / "p" / "new").write_text("2")
            (t / "p" / "d").mkdir()
            (t / "p" / "old").write_text("changed!")
            self.assertEqual(artifact.changed_paths(before, artifact.snapshot(t / "p")), ["d", "new", "old"])

    def test_build_writes_file_list_and_deterministic_artifact(self):
        with TempDir() as t:
            env = Env(t)
            code, out, err = env.cli("build", "--package", "lib", "--artifacts-dir", str(t / "dist"))
            self.assertEqual(code, 0, err)
            files = (t / "root" / "deps" / ".bytehawks" / "lib" / "files.sha256").read_text()
            self.assertIn("deps/include/lib.h", files)
            self.assertNotIn("deps/.bytehawks/lib/files.sha256", files)         # does not list itself
            tarballs = list((t / "dist").glob("*.tar.gz"))
            self.assertEqual(len(tarballs), 1)
            names = tarfile.open(tarballs[0]).getnames()
            self.assertIn("deps/include/lib.h", names)
            self.assertIn("deps/.bytehawks/lib/files.sha256", names)
            self.assertTrue(all(not n.startswith("/") for n in names))
            meta = json.loads(tarballs[0].with_suffix("").with_suffix(".json").read_text())
            self.assertEqual(meta["package"], "lib")
            first = meta["sha256"]

            import shutil
            shutil.rmtree(t / "root")                                            # a clean machine, same recipe
            env.cli("build", "--package", "lib", "--artifacts-dir", str(t / "dist2"))
            second = json.loads(next((t / "dist2").glob("*.json")).read_text())["sha256"]
            self.assertEqual(first, second)                                      # byte-identical tarball

    def test_restore_into_a_fresh_root_equals_a_build(self):
        with TempDir() as t:
            env = Env(t)
            env.cli("build", "--package", "app", "--with-deps", "--artifacts-dir", str(t / "dist"))
            code, out, err = env.cli("restore", "--package", "app", "--with-deps", root="root2", store=f"dir:{t / 'dist'}")
            self.assertEqual(code, 1)                                            # artifacts are for root "root", not "root2"
            code, out, err = env.cli("restore", "--package", "app", "--with-deps", root="root", store=f"dir:{t / 'dist'}", )
            self.assertEqual(code, 0, err)                                       # already installed here
            self.assertIn("already installed", out)

            import shutil
            shutil.rmtree(t / "root")
            code, out, err = env.cli("restore", "--package", "app", "--with-deps", store=f"dir:{t / 'dist'}")
            self.assertEqual(code, 0, err)
            self.assertEqual((t / "root" / "app" / "bin" / "app.txt").read_text().strip(), "lib-v1")
            code, out, _ = env.cli("plan", "--package", "app")
            self.assertEqual(out.count("up-to-date"), 2)

    def test_restore_rejects_unsafe_and_foreign_artifacts(self):
        with TempDir() as t:
            with tarfile.open(t / "evil.tar.gz", "w:gz") as tar:
                info = tarfile.TarInfo("../escape")
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(artifact.PackageError):
                artifact.restore(t / "evil.tar.gz", t / "root", log=lambda m: None)

            env = Env(t)
            env.cli("build", "--package", "lib", "--artifacts-dir", str(t / "dist"))
            tarball = next((t / "dist").glob("*.tar.gz"))
            with self.assertRaises(artifact.PackageError):
                artifact.restore(tarball, t / "elsewhere", expected_recipe="0" * 64, log=lambda m: None)

    def test_stale_chain_and_cascade(self):
        with TempDir() as t:
            env = Env(t)
            dist = f"dir:{t / 'dist'}"

            code, out, _ = env.cli("stale", "--format", "json", store=dist)
            rows = json.loads(out)
            self.assertEqual({r["package"]: r["status"] for r in rows}, {"app": "missing", "lib": "missing"})

            code, out, err = env.cli("chain", "--artifacts-dir", str(t / "dist"), store=dist)
            self.assertEqual(code, 0, err)
            self.assertIn("[pack]", out)
            self.assertEqual(len(list((t / "dist").glob("*.tar.gz"))), 2)

            code, out, _ = env.cli("stale", "--exit-code", store=dist)
            self.assertEqual((code, "up to date" in out), (0, True))
            code, out, _ = env.cli("chain", "--artifacts-dir", str(t / "dist"), store=dist)
            self.assertIn("nothing to build", out)

            # a change in the LIBRARY makes the library AND the application stale (Merkle cascade)
            env.write("echo lib-v2 > lib.txt")
            code, out, _ = env.cli("stale", "--format", "json", "--exit-code", store=dist)
            self.assertEqual(code, 3)
            rows = {r["package"]: r["status"] for r in json.loads(out)}
            self.assertEqual(rows, {"app": "superseded", "lib": "superseded"})

            code, out, err = env.cli("chain", "--artifacts-dir", str(t / "dist"), store=dist)
            self.assertEqual(code, 0, err)
            self.assertEqual((t / "root" / "app" / "bin" / "app.txt").read_text().strip(), "lib-v2")
            self.assertEqual(len(list((t / "dist").glob("*.tar.gz"))), 4)         # old versions kept until pruned

    def test_chain_restores_instead_of_building_on_a_clean_machine(self):
        with TempDir() as t:
            env = Env(t)
            dist = f"dir:{t / 'dist'}"
            env.cli("chain", "--artifacts-dir", str(t / "dist"), store=dist)

            import shutil
            shutil.rmtree(t / "root")
            env.write("echo lib-v1 > lib.txt")                                    # same recipe
            code, out, err = env.cli("build", "--package", "app", "--with-deps", store=dist)
            self.assertEqual(code, 0, err)
            self.assertEqual(out.count("[restore]"), 2)
            self.assertNotIn("[build]", out)
            self.assertEqual((t / "root" / "app" / "bin" / "app.txt").read_text().strip(), "lib-v1")

    def test_kind_filter_for_the_two_stage_pipeline(self):
        with TempDir() as t:
            env = Env(t)
            out_file = t / "gh_output"
            code, out, _ = env.cli("stale", "--github-output", str(out_file), store=f"dir:{t / 'dist'}")
            lines = dict(l.split("=", 1) for l in out_file.read_text().splitlines())
            libs, apps = json.loads(lines["libs"]), json.loads(lines["apps"])
            self.assertEqual([r["package"] for r in libs["include"]], ["lib"])
            self.assertEqual([r["package"] for r in apps["include"]], ["app"])
            self.assertEqual((lines["has_libs"], lines["has_apps"], lines["count"]), ("true", "true", "2"))

            code, out, err = env.cli("chain", "--kind", "library", "--artifacts-dir", str(t / "dist"), store=f"dir:{t / 'dist'}")
            self.assertEqual(len(list((t / "dist").glob("*.tar.gz"))), 1)

    def test_affected_and_publish(self):
        with TempDir() as t:
            env = Env(t)
            code, out, _ = env.cli("affected", "--package", "lib", "--format", "json")
            self.assertEqual(json.loads(out), ["lib", "app"])
            env.cli("chain", "--artifacts-dir", str(t / "dist"), store=f"dir:{t / 'dist'}")

            code, out, err = env.cli("publish", "--from", str(t / "dist"), store=f"dir:{t / 'mirror'}")
            self.assertEqual(code, 0, err)
            self.assertIn("published 2", out)
            code, out, _ = env.cli("publish", "--from", str(t / "dist"), store=f"dir:{t / 'mirror'}")
            self.assertIn("published 0", out)                                    # idempotent
            code, out, _ = env.cli("inventory", "--format", "json", store=f"dir:{t / 'mirror'}")
            self.assertEqual(len(json.loads(out)), 2)

            code, _, err = env.cli("publish", "--from", str(t / "dist"), store="github:o/r")
            self.assertEqual(code, 1)
            code, out, _ = env.cli("publish", "--from", str(t / "dist"), "--ignore-unsupported", store="github:o/r")


if __name__ == "__main__":
    unittest.main()


class VerifyTests(unittest.TestCase):
    def test_verify_detects_tampering_of_installed_files(self):
        with TempDir() as t:
            env = Env(t)
            env.cli("build", "--package", "app", "--with-deps")
            code, out, _ = env.cli("verify", "--package", "app", "--with-deps")
            self.assertEqual(code, 0)
            self.assertEqual(out.count("ok  "), 2)
            (t / "root" / "deps" / "include" / "lib.h").write_text("backdoor")
            (t / "root" / "app" / "bin" / "app.txt").unlink()
            code, out, _ = env.cli("verify", "--package", "app", "--with-deps")
            self.assertEqual(code, 1)
            self.assertIn("modified deps/include/lib.h", out)
            self.assertIn("missing app/bin/app.txt", out)

    def test_forced_and_multi_package_selection(self):
        with TempDir() as t:
            env = Env(t)
            dist = f"dir:{t / 'dist'}"
            env.cli("chain", "--artifacts-dir", str(t / "dist"), store=dist)
            code, out, _ = env.cli("stale", "--package", "lib,app", "--force", "--format", "json", store=dist)
            self.assertEqual({r["status"] for r in json.loads(out)}, {"rebuild"})
            code, out, _ = env.cli("stale", "--format", "json", store=dist)
            self.assertEqual(json.loads(out), [])
            code, out, _ = env.cli("stale", "--all", "--format", "json", store=dist)
            order = [r["package"] for r in json.loads(out)]
            self.assertEqual(order, ["lib", "app"])                               # dependencies first
