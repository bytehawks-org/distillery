import json
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import *  # noqa: F401,F403
from bhdistillery import elfcheck
from bhdistillery.build import BuildError, build_unit
from bhdistillery.model import Catalog, ModelError, apply_root
from bhdistillery.store import entry_for

STATIC = {"binaries": ["bin/demo"], "commands": {"make": "echo static > built.txt"}, "env": {"MODE": "static"}}


def cat_for(t, linkage, pkgs):
    return Catalog(apply_root(CONFIG, t / "root"), {"package": pkgs}, jobs=1, linkage=linkage)


def compile_c(path, static):
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        raise unittest.SkipTest("no C compiler")
    src = Path(str(path) + ".c")
    src.write_text("int main(void){return 0;}\n")
    result = subprocess.run([cc, "-o", str(path), str(src)] + (["-static"] if static else []), capture_output=True)
    if result.returncode:
        raise unittest.SkipTest("cannot link " + ("statically" if static else "dynamically"))


class LinkageModelTests(unittest.TestCase):
    def units(self, t, linkage, **extra):
        tarball = (t / "d.tgz").as_uri()
        pkgs = {"lib": package(tarball, "a" * 64, tags=["library"]),
                "demo": package(tarball, "a" * 64, static=STATIC, depends_on=["lib"], env={"MODE": "dyn"}, **extra)}
        cat = cat_for(t, linkage, pkgs)
        unit = cat.unit("demo")
        cat.plan(unit)
        return unit

    def test_dynamic_is_the_default_and_hashes_do_not_change(self):
        with TempDir() as t:
            plain = cat_for(t, None, {"demo": package("file:///x", "a" * 64)}).unit("demo")
            with_static = cat_for(t, None, {"demo": package("file:///x", "a" * 64, static=STATIC)}).unit("demo")
            for unit in (plain, with_static):
                unit.recipe_sha256 = "x"
            from bhdistillery.model import recipe_hash
            self.assertEqual(recipe_hash(plain), recipe_hash(with_static))     # an unused `static:` block changes nothing
            self.assertEqual(with_static.context["linkage"], "dynamic")
            self.assertEqual(with_static.context["platform"], plain.context["platform"])        # whatever this host's platform is

    def test_static_overrides_commands_and_env_and_changes_identity(self):
        with TempDir() as t:
            dyn, stat = self.units(t, "dynamic"), self.units(t, "static")
            self.assertEqual(dyn.package["commands"]["make"], "echo built > built.txt")
            self.assertEqual(stat.package["commands"]["make"], "echo static > built.txt")
            self.assertIn("install", stat.package["commands"])              # stages the override does not name are kept
            self.assertEqual((dyn.package["env"], stat.package["env"]), ({"MODE": "dyn"}, {"MODE": "static"}))
            self.assertNotEqual(dyn.recipe_sha256, stat.recipe_sha256)
            self.assertEqual(stat.context["linkage"], "static")
            self.assertTrue(stat.context["platform"].endswith("-static"))
            self.assertEqual(entry_for(stat, stat.context["platform"]).name.split("_")[4], stat.context["platform"])
            self.assertFalse(dyn.context["platform"].endswith("-static"))

    def test_static_identity_composes_with_libc(self):
        with TempDir() as t:
            cat = Catalog(apply_root(CONFIG, t / "root"), {"package": {"demo": package("file:///x", "a" * 64, static=STATIC)}},
                          jobs=1, arch="amd64", libc="musl", linkage="static")
            self.assertEqual(cat.unit("demo").context["platform"], "amd64-musl-static")

    def test_package_without_static_recipe_is_shared_between_linkages(self):
        with TempDir() as t:
            dyn, stat = self.units(t, "dynamic"), self.units(t, "static")
            lib_dyn, lib_stat = dyn.depends[0], stat.depends[0]
            self.assertEqual(lib_dyn.recipe_sha256, lib_stat.recipe_sha256)
            self.assertEqual(lib_stat.context["platform"], lib_dyn.context["platform"])

    def test_unknown_linkage(self):
        with TempDir() as t:
            with self.assertRaises(ModelError):
                cat_for(t, "shared", {})

    def test_manifest_records_linkage(self):
        from bhdistillery import bundle
        with TempDir() as t:
            data = bundle.manifest(self.units(t, "static"))
            self.assertEqual(data["linkage"], "static")
            self.assertIn("BH_PKG_LINKAGE=static\n", bundle.manifest_env(data))


class ElfCheckTests(unittest.TestCase):
    def test_detects_static_and_dynamic(self):
        with TempDir() as t:
            compile_c(t / "dyn", False)
            self.assertTrue(elfcheck.dynamic_dependencies(t / "dyn")[0])
            self.assertTrue(elfcheck.check_static(t, ["dyn"]))
            try:
                compile_c(t / "stat", True)
            except unittest.SkipTest:
                self.skipTest("no static libc")
            self.assertEqual(elfcheck.dynamic_dependencies(t / "stat"), ("", []))
            self.assertEqual(elfcheck.check_static(t, ["stat"]), [])

    def test_missing_and_non_elf_files_are_reported(self):
        with TempDir() as t:
            (t / "script").write_text("#!/bin/sh\n")
            problems = elfcheck.check_static(t, ["nope", "script"])
            self.assertIn("nope: not found", problems[0])
            self.assertIn("not an ELF", problems[1])


class StaticBuildTests(unittest.TestCase):
    def build(self, t, install):
        digest = make_tarball(t / "d.tgz", {"README": "x"}, top="demo-1.0")
        pkg = package((t / "d.tgz").as_uri(), digest, static={"binaries": ["bin/demo"], "commands": {"install": install}})
        cat = cat_for(t, "static", {"demo": pkg})
        unit = cat.unit("demo")
        cat.plan(unit)
        return unit, lambda: build_unit(unit, t / "assets", log=lambda m: None)

    def test_dynamic_binary_declared_static_fails_the_build(self):
        with TempDir() as t:
            compile_c(t / "probe", False)
            unit, run = self.build(t, f"mkdir -p {{{{ prefix }}}}/bin && cp {t}/probe {{{{ prefix }}}}/bin/demo")
            with self.assertRaises(BuildError) as ctx:
                run()
            self.assertIn("dynamically linked", str(ctx.exception))

    def test_static_binary_builds_and_manifest_says_so(self):
        with TempDir() as t:
            try:
                compile_c(t / "probe", True)
            except unittest.SkipTest:
                self.skipTest("no static libc")
            unit, run = self.build(t, f"mkdir -p {{{{ prefix }}}}/bin && cp {t}/probe {{{{ prefix }}}}/bin/demo")
            self.assertEqual(run(), "built")
            manifest = json.loads((unit.bundle_dir / "manifest.json").read_text())
            self.assertEqual(manifest["linkage"], "static")


if __name__ == "__main__":
    unittest.main()
