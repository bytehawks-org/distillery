import json
import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery import bundle
from bhdistillery.build import BuildError, build_unit, stage_script


class BuildTests(unittest.TestCase):
    def setup_catalog(self, t, files=None, **extra):
        digest = make_tarball(t / "demo.tgz", files or {"README": "demo"}, top="demo-1.0")
        cat = catalog({"demo": package((t / "demo.tgz").as_uri(), digest, **extra)}, t / "root")
        return cat, cat.unit("demo"), digest

    def test_stage_script_keeps_one_shell_session(self):
        with TempDir() as t:
            _, unit, _ = self.setup_catalog(t)
            script = stage_script(unit, ("configure", "make", "install"))
            self.assertTrue(script.startswith("set -euo pipefail"))
            self.assertLess(script.index("configure"), script.index("make"))
            self.assertIn(f"cd {unit.src_dir}", script)

    def test_full_build_installs_and_writes_bundle(self):
        with TempDir() as t:
            cat, unit, digest = self.setup_catalog(t)
            cat.plan(unit)
            logs = []
            self.assertEqual(build_unit(unit, t / "assets", log=logs.append), "built")
            self.assertEqual((t / "root" / "demo" / "bin" / "demo.txt").read_text().strip(), "built")
            manifest = json.loads((t / "root" / "demo" / ".bytehawks" / "manifest.json").read_text())
            self.assertEqual(manifest["source_sha256"], digest)
            self.assertTrue((t / "root" / "var" / "log" / "distillery" / "demo-1.0.log").exists())

    def test_second_build_is_skipped_until_forced_or_recipe_changes(self):
        with TempDir() as t:
            cat, unit, _ = self.setup_catalog(t)
            cat.plan(unit)
            self.assertEqual(build_unit(unit, t / "assets", log=lambda m: None), "built")
            self.assertEqual(build_unit(unit, t / "assets", log=lambda m: None), "skipped")
            self.assertEqual(build_unit(unit, t / "assets", force=True, log=lambda m: None), "built")

            changed = catalog({"demo": package((t / "demo.tgz").as_uri(), unit.variant_data["sha256"],
                                               commands={"make": "echo other > built.txt",
                                                         "install": "mkdir -p {{ prefix }}/bin && cp built.txt {{ prefix }}/bin/demo.txt"})}, t / "root")
            unit2 = changed.unit("demo")
            changed.plan(unit2)
            self.assertFalse(bundle.is_up_to_date(unit2))

    def test_dry_run_changes_nothing(self):
        with TempDir() as t:
            cat, unit, _ = self.setup_catalog(t)
            cat.plan(unit)
            logs = []
            self.assertEqual(build_unit(unit, t / "assets", dry_run=True, log=logs.append), "dry-run")
            self.assertFalse((t / "root").exists())
            self.assertTrue(any("sha256" in line for line in logs))

    def test_failing_command_aborts_with_log_path(self):
        with TempDir() as t:
            cat, unit, _ = self.setup_catalog(t, commands={"make": "false", "install": "touch should-not-run"})
            cat.plan(unit)
            with self.assertRaises(BuildError) as ctx:
                build_unit(unit, t / "assets", log=lambda m: None)
            self.assertIn("log", str(ctx.exception))
            self.assertFalse((t / "root" / "demo" / ".bytehawks").exists())

    def test_wrong_checksum_never_reaches_the_shell(self):
        with TempDir() as t:
            digest = make_tarball(t / "demo.tgz", {"README": "demo"})
            pkg = package((t / "demo.tgz").as_uri(), "0" * 64,
                          commands={"make": f"touch {t}/pwned"})
            cat = catalog({"demo": pkg}, t / "root")
            unit = cat.unit("demo")
            cat.plan(unit)
            from bhdistillery.fetch import FetchError
            with self.assertRaises(FetchError):
                build_unit(unit, t / "assets", log=lambda m: None)
            self.assertFalse((t / "pwned").exists())

    def test_dependencies_are_visible_to_dependents(self):
        with TempDir() as t:
            digest = make_tarball(t / "demo.tgz", {"README": "demo"})
            url = (t / "demo.tgz").as_uri()
            lib = package(url, digest, tags=["library"], commands={
                "make": "echo lib > lib.txt", "install": "mkdir -p {{ prefix }}/include && cp lib.txt {{ prefix }}/include/lib.h"})
            app = package(url, digest, depends_on=["lib"], commands={
                "make": 'test -f "{{ deps_prefix }}/include/lib.h" && echo "$CPPFLAGS" > flags.txt',
                "install": "mkdir -p {{ prefix }} && cp flags.txt {{ prefix }}/flags.txt"})
            cat = catalog({"lib": lib, "app": app}, t / "root")
            for unit in cat.plan(cat.unit("app")):
                build_unit(unit, t / "assets", log=lambda m: None)
            self.assertIn(f"-I{t}/root/deps/include", (t / "root" / "app" / "flags.txt").read_text())


if __name__ == "__main__":
    unittest.main()
