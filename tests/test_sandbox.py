import json
import os
import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery import repro, sandbox, toolchain
from bhdistillery.build import BuildError, build_unit, source_epoch

QUIET = lambda message: None


def usable():
    backend = sandbox.resolve("auto")
    if backend == "none":
        raise unittest.SkipTest("no sandbox backend (needs bwrap, or unshare with root/user namespaces)")
    return backend


class WrapTests(unittest.TestCase):
    def test_none_is_the_command_itself(self):
        self.assertEqual(sandbox.wrap("none", ["bash", "-c", "x"], "/w", ["/w"]), ["bash", "-c", "x"])

    def test_bwrap_arguments(self):
        cmd = sandbox.wrap("bwrap", ["bash", "-c", "x"], "/opt/w/src", ["/opt/w/p", "/opt/w/src"])
        self.assertEqual(cmd[:4], ["bwrap", "--ro-bind", "/", "/"])
        self.assertIn("--unshare-net", cmd)
        self.assertIn("--tmpfs", cmd)
        for path in ("/opt/w/p", "/opt/w/src"):
            self.assertIn(path, cmd[cmd.index("--bind"):])
        self.assertEqual(cmd[-3:], ["bash", "-c", "x"])
        self.assertNotIn("--unshare-net", sandbox.wrap("bwrap", ["true"], "/", ["/opt"], network=True))

    def test_tmp_stays_shared_when_the_build_lives_under_it(self):
        under = sandbox.wrap("bwrap", ["true"], "/tmp/r/src", ["/tmp/r"])
        self.assertNotIn("--tmpfs", under)
        self.assertIn("/tmp", under)

    def test_unshare_script_makes_the_system_read_only(self):
        cmd = sandbox.wrap("unshare", ["true"], "/opt/w", ["/opt/w"])
        self.assertEqual(cmd[0], "unshare")
        self.assertIn("--net", cmd)
        self.assertIn("remount,bind,ro /", cmd[cmd.index("bash") + 2])

    def test_modes(self):
        self.assertEqual(sandbox.resolve(None), "none")
        self.assertEqual(sandbox.resolve("none"), "none")
        with self.assertRaises(sandbox.SandboxError):
            sandbox.resolve("docker")
        self.assertIn(sandbox.resolve("auto"), ("none", "bwrap", "unshare"))


class IsolationTests(unittest.TestCase):
    def test_a_sandboxed_command_cannot_write_outside_or_reach_the_network(self):
        import subprocess
        backend = usable()
        with TempDir() as t:
            (t / "rw").mkdir()
            script = ('touch /etc/bh-sandbox-test 2>/dev/null && echo etc-writable; touch "$RW/ok" && echo rw-ok; '
                      '(exec 3<>/dev/tcp/1.1.1.1/80) 2>/dev/null && echo net-open || echo net-blocked')
            cmd = sandbox.wrap(backend, ["bash", "-c", script], str(t / "rw"), [str(t / "rw")])
            out = subprocess.run(cmd, env={**os.environ, "RW": str(t / "rw")}, capture_output=True, text=True, timeout=60).stdout
            self.assertIn("rw-ok", out)
            self.assertIn("net-blocked", out)
            self.assertNotIn("etc-writable", out)
            self.assertTrue((t / "rw" / "ok").exists())
            self.assertFalse(os.path.exists("/etc/bh-sandbox-test"))


class BuildTests(unittest.TestCase):
    def unit(self, t, install, **extra):
        digest = make_tarball(t / "d.tgz", {"README": "x"}, top="demo-1.0")
        cat = catalog({"demo": package((t / "d.tgz").as_uri(), digest, commands={"install": install}, **extra)}, t / "root")
        unit = cat.unit("demo")
        cat.plan(unit)
        return unit

    def test_build_in_a_sandbox_installs_but_cannot_touch_the_system(self):
        usable()
        with TempDir() as t:
            unit = self.unit(t, "mkdir -p {{ prefix }}/bin && echo ok > {{ prefix }}/bin/demo; touch /etc/bh-sandbox-build 2>/dev/null && exit 3 || true")
            logs = []
            self.assertEqual(build_unit(unit, t / "assets", sandbox="auto", log=logs.append), "built")
            self.assertTrue((t / "root" / "demo" / "bin" / "demo").exists())
            self.assertTrue(any(line.startswith("[sandbox]") for line in logs))
            self.assertFalse(os.path.exists("/etc/bh-sandbox-build"))

    def test_a_build_that_writes_outside_its_directories_fails(self):
        usable()
        with TempDir() as t:
            unit = self.unit(t, "touch /usr/bh-outside")
            with self.assertRaises(BuildError):
                build_unit(unit, t / "assets", sandbox="auto", log=QUIET)

    def test_network_is_off_unless_the_package_asks(self):
        usable()
        probe = "(exec 3<>/dev/tcp/1.1.1.1/80) 2>/dev/null && echo open > {{ prefix }}/net || echo blocked > {{ prefix }}/net"
        for extra, expected in (({}, "blocked"), ({"build": {"network": True}}, None)):
            with TempDir() as t:
                unit = self.unit(t, f"mkdir -p {{{{ prefix }}}} && {probe}", **extra)
                build_unit(unit, t / "assets", sandbox="auto", log=QUIET)
                if expected:
                    self.assertEqual((t / "root" / "demo" / "net").read_text().strip(), expected)

    def test_toolchain_is_recorded_in_manifest_and_sidecar(self):
        with TempDir() as t:
            unit = self.unit(t, "mkdir -p {{ prefix }}/bin && echo ok > {{ prefix }}/bin/demo", toolchain=["perl"])
            build_unit(unit, t / "assets", artifacts_dir=t / "dist", log=QUIET)
            manifest = json.loads((unit.bundle_dir / "manifest.json").read_text())
            self.assertIn("libc", manifest["toolchain"])                  # always known; compilers only when the host has them
            self.assertIn("machine", manifest["toolchain"])
            side = json.loads(next((t / "dist").glob("*.json")).read_text())
            self.assertEqual(side["toolchain"], manifest["toolchain"])

    def test_toolchain_key_is_not_part_of_the_recipe(self):
        with TempDir() as t:
            plain, declared = self.unit(t, "true"), self.unit(t, "true", toolchain=["perl"])
            self.assertEqual(plain.recipe_sha256, declared.recipe_sha256)


class ReproTests(unittest.TestCase):
    def check(self, t, install, **extra):
        digest = make_tarball(t / "d.tgz", {"README": "x"}, top="demo-1.0")
        cat = catalog({"demo": package((t / "d.tgz").as_uri(), digest, commands={"install": install}, **extra)}, t / "root")
        unit = cat.unit("demo")
        cat.plan(unit)
        return repro.check(unit, t / "assets", stages=("pre", "configure", "make", "install", "post"), log=QUIET)

    def test_deterministic_build_is_reproducible(self):
        with TempDir() as t:
            identical, diff, count = self.check(t, "mkdir -p {{ prefix }}/bin && echo fixed > {{ prefix }}/bin/demo")
            self.assertTrue(identical, diff)
            self.assertGreater(count, 0)

    def test_embedded_clock_is_caught(self):
        with TempDir() as t:
            identical, diff, _ = self.check(t, "mkdir -p {{ prefix }}/bin && echo $RANDOM$RANDOM$RANDOM > {{ prefix }}/bin/demo")
            self.assertFalse(identical)
            self.assertTrue(any(p.endswith("bin/demo") for p in diff["different"]))

    def test_source_date_epoch_makes_a_timestamp_reproducible(self):
        with TempDir() as t:
            identical, diff, _ = self.check(t, "mkdir -p {{ prefix }}/bin && echo $SOURCE_DATE_EPOCH $TZ $LC_ALL > {{ prefix }}/bin/demo")
            self.assertTrue(identical, diff)

    def test_source_epoch_is_the_newest_source_mtime(self):
        with TempDir() as t:
            (t / "a").mkdir()
            for name, when in (("a/old", 1000), ("a/new", 5000)):
                (t / name).write_text("x")
                os.utime(t / name, (when, when))
            os.utime(t / "a", (1, 1))
            self.assertEqual(source_epoch(t / "a"), 5000)


class ToolchainTests(unittest.TestCase):
    def test_collect_reports_libc_and_os(self):
        with TempDir() as t:
            digest = make_tarball(t / "d.tgz", {"README": "x"}, top="demo-1.0")
            cat = catalog({"demo": package((t / "d.tgz").as_uri(), digest)}, t / "root")
            data = toolchain.collect(cat.unit("demo"))
            self.assertIn("libc", data)
            self.assertIn("machine", data)


if __name__ == "__main__":
    unittest.main()
