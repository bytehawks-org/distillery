import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery.model import ModelError, apply_root


def pkgs(**deps):
    out = {}
    for name, dependencies in deps.items():
        out[name] = package("file:///x/{{ name }}.tgz", "a" * 64, depends_on=dependencies, tags=["library", "application"])
    return out


class ModelTests(unittest.TestCase):
    def test_apply_root_rebases_every_path(self):
        cfg = apply_root(CONFIG, "/tmp/stage")["config"]
        self.assertEqual(cfg["root"], "/tmp/stage")
        self.assertEqual(cfg["libraries"]["default_prefix"], "/tmp/stage/deps")
        self.assertEqual(cfg["applications"]["default_prefix"], "/tmp/stage/{{ name }}")
        self.assertEqual(cfg["link"], "/opt/bh")           # the symlink is not under root

    def test_prefix_and_bundle_dir_depend_on_tag(self):
        cat = catalog(pkgs(a=[]), "/r")
        lib, app = cat.unit("a", tag="library"), cat.unit("a", tag="application")
        self.assertEqual(str(lib.bundle_dir), "/r/deps/.bytehawks/a")
        self.assertEqual(str(app.bundle_dir), "/r/a/.bytehawks")

    def test_ambiguous_tag_needs_flag(self):
        with self.assertRaises(ModelError):
            catalog(pkgs(a=[]), "/r").unit("a")

    def test_topological_order_and_dedup(self):
        packages = pkgs(app=["lib1", {"name": "lib2"}], lib1=["lib2"], lib2=[])
        cat = catalog(packages, "/r")
        order = [u.name for u in cat.plan(cat.unit("app", tag="application"))]
        self.assertEqual(order, ["lib2", "lib1", "app"])

    def test_cycle_detected(self):
        cat = catalog(pkgs(a=["b"], b=["a"]), "/r")
        with self.assertRaises(ModelError) as ctx:
            cat.plan(cat.unit("a", tag="library"))
        self.assertIn("cycle", str(ctx.exception))

    def test_unknown_dependency_and_variant(self):
        cat = catalog(pkgs(a=["ghost"]), "/r")
        with self.assertRaises(ModelError):
            cat.plan(cat.unit("a", tag="library"))
        with self.assertRaises(ModelError):
            cat.unit("a", variant="nope", tag="library")

    def test_recipe_hash_cascades_to_dependents(self):
        before = catalog(pkgs(app=["lib"], lib=[]), "/r")
        packages = pkgs(app=["lib"], lib=[])
        packages["lib"]["commands"]["make"] = "make -j9"
        after = catalog(packages, "/r")
        h = lambda c: c.plan(c.unit("app", tag="application"))[-1].recipe_sha256
        self.assertNotEqual(h(before), h(after))
        self.assertEqual(h(before), h(catalog(pkgs(app=["lib"], lib=[]), "/r")))

    def test_assets_are_part_of_the_recipe_hash(self):
        from bhdistillery.model import Catalog, apply_root
        with TempDir() as t:
            hook = t / "assets" / "demo" / "hooks" / "post_init" / "10.sh"
            hook.parent.mkdir(parents=True)
            hook.write_text("echo one\n")
            packages = {"package": {"demo": package("file:///x.tgz", "a" * 64), "plain": package("file:///y.tgz", "a" * 64)}}

            def hashes(assets):
                cat = Catalog(apply_root(CONFIG, "/r"), packages, jobs=1, assets_root=assets)
                units = {n: cat.unit(n) for n in ("demo", "plain")}
                for unit in units.values():
                    cat.plan(unit)
                return {n: u.recipe_sha256 for n, u in units.items()}

            before, without = hashes(t / "assets"), hashes(None)
            hook.write_text("echo two\n")
            after = hashes(t / "assets")
            self.assertNotEqual(before["demo"], after["demo"])               # a hook change makes the unit stale
            self.assertNotEqual(before["demo"], without["demo"])
            self.assertEqual(before["plain"], after["plain"])                # no assets: hash unchanged
            self.assertEqual(without["plain"], after["plain"])
            hook.chmod(0o755)
            self.assertNotEqual(after["demo"], hashes(t / "assets")["demo"])     # so does the execute bit

    def test_per_arch_sha256_and_arch_name(self):
        from bhdistillery.model import Catalog, apply_root
        pkg = package("file:///x-{{ arch_name }}.tgz", "ignored", arch_map={"amd64": "x64", "arm64": "aarch64"})
        pkg["variant"][0]["sha256"] = {"amd64": "a" * 64, "arm64": "b" * 64}
        packages = {"package": {"demo": pkg}}

        def unit(arch):
            cat = Catalog(apply_root(CONFIG, "/r"), packages, jobs=1, arch=arch)
            u = cat.unit("demo")
            cat.plan(u)
            return u

        amd, arm, riscv = unit("amd64"), unit("arm64"), unit("riscv64")
        self.assertEqual((amd.variant_data["sha256"], arm.variant_data["sha256"]), ("a" * 64, "b" * 64))
        self.assertEqual((amd.context["arch_name"], arm.context["arch_name"]), ("x64", "aarch64"))
        self.assertEqual(riscv.variant_data["sha256"], "")                  # not pinned for it: the build refuses
        self.assertEqual(riscv.context["arch_name"], "riscv64")
        self.assertNotEqual(amd.recipe_sha256, arm.recipe_sha256)

    def test_libc_is_part_of_the_platform_identity_and_the_hash(self):
        from bhdistillery.model import Catalog, ModelError, apply_root
        pkg = package("file:///x-{{ libc_name }}.tgz", "ignored", libc_map={"musl": "alpine-linux"})
        pkg["variant"][0]["sha256"] = {"amd64": "a" * 64, "amd64-musl": "c" * 64, "arm64": "b" * 64}
        packages = {"package": {"demo": pkg}}

        def unit(arch, libc):
            cat = Catalog(apply_root(CONFIG, "/r"), packages, jobs=1, arch=arch, libc=libc)
            u = cat.unit("demo")
            cat.plan(u)
            return u

        glibc, musl, arm_musl = unit("amd64", "glibc"), unit("amd64", "musl"), unit("arm64", "musl")
        self.assertEqual((glibc.context["platform"], musl.context["platform"]), ("amd64", "amd64-musl"))
        self.assertEqual((glibc.context["libc_name"], musl.context["libc_name"]), ("glibc", "alpine-linux"))
        self.assertEqual((glibc.variant_data["sha256"], musl.variant_data["sha256"]), ("a" * 64, "c" * 64))
        self.assertEqual(arm_musl.variant_data["sha256"], "b" * 64)         # no arm64-musl pin: falls back to the arch pin
        self.assertNotEqual(glibc.recipe_sha256, musl.recipe_sha256)
        from bhdistillery.store import entry_for
        self.assertEqual(entry_for(glibc, glibc.context["platform"]).name.split("_")[4], "amd64")      # names of glibc unchanged
        self.assertEqual(entry_for(musl, musl.context["platform"]).name.split("_")[4], "amd64-musl")
        with self.assertRaises(ModelError):
            Catalog(apply_root(CONFIG, "/r"), packages, libc="uclibc")

    def test_libc_detection_can_be_forced(self):
        import os
        from bhdistillery.model import detect_libc
        os.environ["BH_LIBC"] = "musl"
        try:
            self.assertEqual(detect_libc(), "musl")
        finally:
            del os.environ["BH_LIBC"]
        self.assertIn(detect_libc(), ("glibc", "musl"))

    def test_hash_depends_on_prefix(self):
        h = lambda root: (lambda c: c.plan(c.unit("a", tag="library"))[-1].recipe_sha256)(catalog(pkgs(a=[]), root))
        self.assertNotEqual(h("/r1"), h("/r2"))


if __name__ == "__main__":
    unittest.main()


class JobsTests(unittest.TestCase):
    def test_recipe_hash_does_not_depend_on_the_number_of_jobs(self):
        with TempDir() as t:
            digest = make_tarball(t / "d.tgz", {"README": "x"}, top="demo-1.0")
            pkg = package((t / "d.tgz").as_uri(), digest, commands={"make": "make -j{{ jobs }}"}, env={"J": "{{ jobs }}"})
            hashes = set()
            for jobs in (1, 4, 64):
                cat = Catalog(apply_root(CONFIG, t / "root"), {"package": {"demo": pkg}}, jobs=jobs)
                unit = cat.unit("demo")
                cat.plan(unit)
                hashes.add(unit.recipe_sha256)
            self.assertEqual(len(hashes), 1)
            self.assertIn("make -j4", stage_script_text(cat, unit, 4))


def stage_script_text(cat, unit, jobs):
    from bhdistillery.build import stage_script
    other = Catalog(apply_root(CONFIG, "/r"), {"package": {"demo": unit.package}}, jobs=jobs)
    return stage_script(other.unit("demo"), ("make",))
