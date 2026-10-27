import json
import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery import bundle


def unit_with(runtime, name="demo"):
    cat = catalog({name: package("file:///x.tgz", "a" * 64, runtime=runtime)}, "/r")
    unit = cat.unit(name)
    cat.plan(unit)
    return unit


class BundleTests(unittest.TestCase):
    def test_rosetta_columns(self):
        unit = unit_with({"env": [{
            "name": "BH_DEMO_PORT", "type": "port", "default": 8080, "required": False,
            "aliases": ["DEMO_PORT", "PORT"], "file_key": "server.port", "cli": "--port",
            "native_env": "PORT", "description": "Listen port"}]})
        line = bundle.rosetta_lines(unit)[-1]
        self.assertEqual(line, "BH_DEMO_PORT|port|8080||DEMO_PORT,PORT|server.port|--port|PORT|Listen port")

    def test_required_bool_and_prefix_templates(self):
        unit = unit_with({"env": [
            {"name": "BH_DEMO_TLS", "type": "bool", "default": False},
            {"name": "BH_DEMO_CONF", "type": "path", "default": "{{ prefix }}/etc", "required": True}]})
        lines = bundle.rosetta_lines(unit)
        self.assertIn("BH_DEMO_TLS|bool|false|||||", lines[2] + "|")
        self.assertEqual(lines[3].split("|")[2:4], ["/r/demo/etc", "required"])

    def test_invalid_declarations_fail(self):
        for env in ({"name": "PORT"}, {"name": "BH_DEMO_X", "type": "nope"}, {"name": "BH_DEMO_X", "description": "a|b"}):
            with self.assertRaises(bundle.BundleError, msg=str(env)):
                bundle.rosetta_lines(unit_with({"env": [env]}))
        with self.assertRaises(bundle.BundleError):
            bundle.rosetta_lines(unit_with({"env": [{"name": "BH_DEMO_X"}, {"name": "BH_DEMO_X"}]}))

    def test_spec_records(self):
        unit = unit_with({
            "user": {"name": "demo", "uid": 1001, "gid": 0, "home": "{{ prefix }}"},
            "dirs": [{"path": "{{ prefix }}/run", "mode": "g+rwX", "owner": "1001:0"}],
            "persistence": [{"path": "{{ prefix }}/data", "volume": "/bytehawks/data"}],
            "templates": [{"src": "a.tmpl", "dest": "{{ prefix }}/a.conf", "mode": "0640"}],
            "hooks": {"pre_init": "{{ prefix }}/hooks"},
            "run": {"command": ["{{ prefix }}/bin/demo", "--port=${BH_DEMO_PORT}"], "stop_signal": "QUIT"},
            "healthcheck": {"tcp": {"port": "${BH_DEMO_PORT}"}},
            "requires": ["openssl"]})
        spec = "\n".join(bundle.spec_lines(unit))
        for expected in ("user|demo|1001|0|/r/demo", "dir|/r/demo/run|g+rwX|1001:0",
                         "persist|/r/demo/data|/bytehawks/data", "template|a.tmpl|/r/demo/a.conf|0640",
                         "hook|pre_init|/r/demo/hooks", "run|/r/demo/bin/demo|--port=${BH_DEMO_PORT}",
                         "stopsignal|QUIT", "health|tcp|127.0.0.1|${BH_DEMO_PORT}", "requires|openssl"):
            self.assertIn(expected, spec)

    def test_hooks_are_installed_from_assets_with_execute_bit(self):
        with TempDir() as t:
            (t / "assets" / "demo" / "hooks" / "pre_init").mkdir(parents=True)
            (t / "assets" / "demo" / "hooks" / "pre_init" / "10-a.sh").write_text("#!/bin/sh\n")
            cat = catalog({"demo": package("file:///x.tgz", "a" * 64, runtime={
                "hooks": {"pre_init": "{{ prefix }}/hooks/pre-init.d", "first_init": "{{ prefix }}/hooks/init.d"}})}, t / "r")
            unit = cat.unit("demo")
            cat.plan(unit)
            bundle.emit(unit, t / "assets")
            hook = t / "r" / "demo" / "hooks" / "pre-init.d" / "10-a.sh"
            self.assertTrue(hook.is_file())
            self.assertTrue(hook.stat().st_mode & 0o111)
            self.assertFalse((t / "r" / "demo" / "hooks" / "init.d").exists())      # no assets for that stage

    def test_variant_keys_are_available_to_templates(self):
        pkg = package("file:///x-{{ variant.build }}.tgz", "a" * 64)
        pkg["variant"][0]["build"] = 10
        unit = catalog({"demo": pkg}, "/r").unit("demo")
        self.assertEqual(unit.context["variant"]["build"], 10)

    def test_spec_rejects_bad_hook_stage_and_shell_string_run(self):
        with self.assertRaises(bundle.BundleError):
            bundle.spec_lines(unit_with({"hooks": {"whenever": "/x"}}))
        with self.assertRaises(bundle.BundleError):
            bundle.spec_lines(unit_with({"run": {"command": "demo --serve"}}))

    def test_emit_writes_the_contract(self):
        with TempDir() as t:
            (t / "assets" / "demo").mkdir(parents=True)
            (t / "assets" / "demo" / "x.tmpl").write_text("hi")
            unit = unit_with({"env": [{"name": "BH_DEMO_A"}]})
            out = bundle.emit(unit, t / "assets", t / "out", built=True)
            self.assertEqual({p.name for p in out.iterdir()},
                             {"manifest.json", "manifest.env", "rosetta.map", "runtime.spec", "assets"})
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["package"], "demo")
            self.assertEqual(manifest["recipe_sha256"], unit.recipe_sha256)
            self.assertIn("built_at", manifest)
            self.assertIn("BH_PKG_PREFIX=/r/demo", (out / "manifest.env").read_text())
            self.assertEqual((out / "assets" / "x.tmpl").read_text(), "hi")


if __name__ == "__main__":
    unittest.main()
