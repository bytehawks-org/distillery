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

    def test_hash_depends_on_prefix(self):
        h = lambda root: (lambda c: c.plan(c.unit("a", tag="library"))[-1].recipe_sha256)(catalog(pkgs(a=[]), root))
        self.assertNotEqual(h("/r1"), h("/r2"))


if __name__ == "__main__":
    unittest.main()
