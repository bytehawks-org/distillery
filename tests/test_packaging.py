import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import *  # noqa: F401,F403
from bhdistillery import build, packaging
from bhdistillery.model import Catalog, apply_root
from bhdistillery.packaging import PackagingError


def built(t, packages, name, config_extra=None):
    """Build `packages` (a dict of definitions) under t/root and return (catalog, unit of `name`)."""
    config = apply_root(CONFIG, t / "root")
    config["config"].update(config_extra or {})
    cat = Catalog(config, {"package": packages}, jobs=1, arch="amd64", libc="glibc")
    for item in cat.plan(cat.unit(name)):
        build.build_unit(item, t / "assets", log=lambda m: None)
    return cat, cat.unit(name)


def demo_packages(t, **app_extra):
    lib_sha = make_tarball(t / "lib-1.0.tgz", {"include/lib.h": "h"}, top="lib-1.0")
    app_sha = make_tarball(t / "app-1.0.tgz", {"bin/app.sh": "#!/bin/sh\n"}, top="app-1.0")
    lib = package((t / "lib-1.0.tgz").as_uri(), lib_sha, tags=["library"],
                  commands={"install": "mkdir -p {{ prefix }}/include {{ prefix }}/lib && cp include/lib.h {{ prefix }}/include/ "
                                       "&& touch {{ prefix }}/lib/libx.so.1 && ln -s libx.so.1 {{ prefix }}/lib/libx.so"})
    app = package((t / "app-1.0.tgz").as_uri(), app_sha, depends_on=["lib"],
                  commands={"install": "mkdir -p {{ prefix }}/bin && cp bin/app.sh {{ prefix }}/bin/ && chmod +x {{ prefix }}/bin/app.sh"},
                  runtime={"run": {"command": ["{{ prefix }}/bin/app.sh"]}}, **app_extra)
    return {"lib": lib, "app": app}


class FakeNfpm:
    """Records the invocation and writes a placeholder package where --target points."""

    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        config = json.loads(Path(command[command.index("--config") + 1]).read_text())
        scripts = {k: Path(v).read_text() for k, v in config.get("scripts", {}).items()}
        self.calls.append({"packager": command[command.index("--packager") + 1], "config": config, "scripts": scripts})
        Path(command[command.index("--target") + 1]).write_bytes(b"pkg")
        return subprocess.CompletedProcess(command, 0, "", "")


class NamingTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        conf = packaging.settings({})
        self.assertEqual(conf["formats"], ["deb", "rpm", "apk"])
        conf = packaging.settings({"config": {"packaging": {"release": 3, "file_name_template": {"deb": "x_{{ pkg_arch }}.deb"}}}})
        self.assertEqual(conf["file_name_template"]["rpm"], packaging.DEFAULTS["file_name_template"]["rpm"])    # merged
        self.assertEqual((conf["release"], conf["file_name_template"]["deb"]), (3, "x_{{ pkg_arch }}.deb"))
        with self.assertRaises(PackagingError):
            packaging.settings({"config": {"packaging": {"formats": ["deb", "msi"]}}})

    def test_native_architecture_names(self):
        self.assertEqual([packaging.native_arch(f, "amd64") for f in packaging.FORMATS], ["amd64", "x86_64", "x86_64"])
        self.assertEqual([packaging.native_arch(f, "arm64") for f in packaging.FORMATS], ["arm64", "aarch64", "aarch64"])
        with self.assertRaises(PackagingError):
            packaging.native_arch("deb", "riscv64")

    def test_dependency_syntax_per_format(self):
        self.assertEqual(packaging.dependency("deb", "bytehawks-zlib", "1.3.1", 1), "bytehawks-zlib (= 1.3.1-1)")
        self.assertEqual(packaging.dependency("rpm", "bytehawks-zlib", "1.3.1", 1), "bytehawks-zlib = 1.3.1-1")
        self.assertEqual(packaging.dependency("apk", "bytehawks-zlib", "1.3.1", 1), "bytehawks-zlib=1.3.1-r1")

    def test_names_come_from_the_templates(self):
        with TempDir() as t:
            pkgs = demo_packages(t)
            pkgs["app"]["variant"].append({"name": "edge", "major_version": 2, "patch_version": 0, "full_version": "2.0",
                                           "sha256": "b" * 64})
            cat = Catalog(apply_root(CONFIG, t / "root"), {"package": pkgs}, jobs=1, arch="arm64")
            stable, edge = cat.unit("app"), cat.unit("app", "edge")
            conf = packaging.settings({})
            self.assertEqual(packaging.package_name(stable, conf), "bytehawks-app")
            self.assertEqual(packaging.package_name(edge, conf), "bytehawks-app-edge")
            self.assertEqual([packaging.file_name(stable, conf, f) for f in packaging.FORMATS],
                             ["bytehawks-app_1.0-1_arm64.deb", "bytehawks-app-1.0-1.aarch64.rpm", "bytehawks-app-1.0-r1.apk"])
            custom = packaging.settings({"config": {"packaging": {
                "name_template": "acme-{{ name }}-{{ major_version }}", "release": 7,
                "file_name_template": {"deb": "{{ pkg_name }}.{{ release }}.{{ pkg_arch }}.{{ format }}"}}}})
            self.assertEqual(packaging.file_name(stable, custom, "deb"), "acme-app-1.7.arm64.deb")

    def test_invalid_names_and_templates_are_rejected(self):
        with TempDir() as t:
            cat = Catalog(apply_root(CONFIG, t / "root"), {"package": demo_packages(t)}, jobs=1)
            unit = cat.unit("app")
            for conf, message in (({"name_template": "Bytehawks-{{ name }}"}, "lowercase"),
                                  ({"name_template": "{{ nope }}"}, "cannot resolve"),
                                  ({"file_name_template": {"deb": "../{{ pkg_name }}.deb"}}, "plain file name")):
                with self.assertRaises(PackagingError, msg=str(conf)) as ctx:
                    packaging.file_name(unit, packaging.settings({"config": {"packaging": conf}}), "deb")
                self.assertIn(message, str(ctx.exception))


class PackagingTests(unittest.TestCase):
    def test_application_owns_its_directories_library_only_its_bundle(self):
        with TempDir() as t:
            cat, app = built(t, demo_packages(t), "app")
            lib = cat.unit("lib")
            lib_entries, app_entries = packaging.contents(lib), packaging.contents(app)
            root = t / "root"
            lib_dirs = {e["dst"] for e in lib_entries if e.get("type") == "dir"}
            self.assertTrue(all(str(root / "deps" / ".bytehawks") in d for d in lib_dirs))      # nothing in the shared prefix
            self.assertIn(str(root / "deps" / "include" / "lib.h"), {e["dst"] for e in lib_entries})
            link = [e for e in lib_entries if e.get("type") == "symlink"][0]
            self.assertEqual((link["src"], Path(link["dst"]).name), ("libx.so.1", "libx.so"))
            app_dirs = {e["dst"] for e in app_entries if e.get("type") == "dir"}
            self.assertIn(str(root / "app" / "bin"), app_dirs)
            self.assertIn(str(root / "app" / ".bytehawks" / "files.sha256"), {e["dst"] for e in app_entries})
            self.assertTrue(all(e["file_info"] == {"owner": "root", "group": "root"} for e in app_entries))

    def test_formats_follow_the_libc_of_the_build(self):
        conf = packaging.settings({})
        self.assertEqual(packaging.default_formats(conf, "glibc"), ["deb", "rpm"])
        self.assertEqual(packaging.default_formats(conf, "musl"), ["apk"])
        with TempDir() as t:
            cat, app = built(t, demo_packages(t), "app")                  # built for the host libc (glibc in CI images)
            musl_cat = Catalog(apply_root(CONFIG, t / "root"), {"package": demo_packages(t)}, jobs=1, arch="amd64", libc="musl")
            musl_app = musl_cat.unit("app")
            musl_cat.plan(musl_app)
            with self.assertRaises(PackagingError) as ctx:
                packaging.check_libc(musl_app, "deb", conf)
            self.assertIn("--libc glibc", str(ctx.exception))
            packaging.check_libc(musl_app, "apk", conf)
            packaging.check_libc(musl_app, "deb", packaging.settings({"config": {"packaging": {"allow_cross_libc": True}}}))

    def test_seal_and_mutable_paths_in_packages_and_verify(self):
        from bhdistillery import package as artifact
        with TempDir() as t:
            pkgs = demo_packages(t)
            pkgs["app"]["runtime"]["mutable"] = ["{{ prefix }}/bin"]
            cat, app = built(t, pkgs, "app")
            self.assertEqual(artifact.verify_files(app), [])
            (t / "root" / "app" / "bin" / "app.sh").write_text("tampered")
            self.assertEqual(artifact.verify_files(app), [])                    # bin/ is declared mutable
            (t / "root" / "app" / ".bytehawks" / "runtime.spec").write_text("tampered")
            self.assertTrue(any("runtime.spec" in p for p in artifact.verify_files(app)))
            dst = {e["dst"] for e in packaging.contents(app)}
            self.assertIn(str(t / "root" / "app" / ".bytehawks" / "bundle.sha256"), dst)

    def test_unbuilt_unit_is_refused(self):
        with TempDir() as t:
            cat = Catalog(apply_root(CONFIG, t / "root"), {"package": demo_packages(t)}, jobs=1)
            with self.assertRaises(PackagingError) as ctx:
                packaging.build_packages(cat, cat.unit("app"), ["deb"], t / "out", packaging.settings({}), nfpm="x",
                                         runner=FakeNfpm(), log=lambda m: None)
            self.assertIn("not built", str(ctx.exception))

    def test_nfpm_receives_metadata_dependencies_scripts_and_signing(self):
        with TempDir() as t:
            pkgs = demo_packages(t, license="MIT", website="https://example.com/app", description="The app")
            cat, app = built(t, pkgs, "app")
            conf = packaging.settings({"config": {"packaging": {
                "maintainer": "Me <me@example.com>", "allow_cross_libc": True, "signing": {"deb": {"key_file": "/k.asc", "key_id": "ABCD"}}}}})
            fake = FakeNfpm()
            written = packaging.build_packages(cat, app, ["deb", "rpm", "apk"], t / "out", conf, with_deps=True,
                                               nfpm="/bin/true", runner=fake, log=lambda m: None)
            self.assertEqual(len(written), 6)                                   # lib + app, three formats each
            by = {(c["config"]["name"], c["packager"]): c for c in fake.calls}
            deb = by[("bytehawks-app", "deb")]["config"]
            self.assertEqual((deb["version"], deb["release"], deb["arch"], deb["license"], deb["maintainer"]),
                             ("1.0", "1", "amd64", "MIT", "Me <me@example.com>"))
            self.assertEqual(deb["depends"], ["bytehawks-lib (= 1.0-1)"])
            self.assertEqual(by[("bytehawks-app", "apk")]["config"]["depends"], ["bytehawks-lib=1.0-r1"])
            self.assertEqual(deb["homepage"], "https://example.com/app")
            self.assertEqual(deb["description"], "The app")
            self.assertEqual(deb["deb"], {"signature": {"key_file": "/k.asc", "key_id": "ABCD"}})
            self.assertNotIn("deb", by[("bytehawks-app", "rpm")]["config"])      # signing only where configured
            script = by[("bytehawks-app", "deb")]["scripts"]["postinstall"]
            self.assertIn(f"BH_ROOT={t / 'root'}", script)
            self.assertIn("install app", script)
            self.assertNotIn("scripts", by[("bytehawks-lib", "deb")]["config"])  # no runtime, nothing to initialise
            sums = (t / "out" / "SHA256SUMS").read_text()
            self.assertEqual(len(sums.splitlines()), 6)
            self.assertIn("bytehawks-app_1.0-1_amd64.deb", sums)

    def test_services_can_depend_on_a_shaker_package(self):
        with TempDir() as t:
            cat, app = built(t, demo_packages(t), "app")
            conf = packaging.settings({"config": {"packaging": {"shaker": {"package": "bytehawks-shaker"}}}})
            fake = FakeNfpm()
            packaging.build_packages(cat, app, ["deb"], t / "out", conf, with_deps=True, nfpm="/bin/true", runner=fake, log=lambda m: None)
            depends = {c["config"]["name"]: c["config"].get("depends", []) for c in fake.calls}
            self.assertEqual(depends["bytehawks-app"], ["bytehawks-lib (= 1.0-1)", "bytehawks-shaker"])
            self.assertEqual(depends["bytehawks-lib"], [])                       # no runtime, no Shaker

    def test_post_install_can_be_turned_off(self):
        with TempDir() as t:
            cat, app = built(t, demo_packages(t), "app")
            conf = packaging.settings({"config": {"packaging": {"shaker": {"post_install": False}}}})
            fake = FakeNfpm()
            packaging.build_packages(cat, app, ["deb"], t / "out", conf, nfpm="/bin/true", runner=fake, log=lambda m: None)
            self.assertNotIn("scripts", fake.calls[0]["config"])

    def test_other_variants_are_declared_as_conflicts(self):
        with TempDir() as t:
            pkgs = demo_packages(t)
            pkgs["app"]["variant"].append({"name": "edge", "major_version": 2, "patch_version": 0, "full_version": "2.0",
                                           "sha256": make_tarball(t / "app-2.0.tgz", {"bin/app.sh": "x"}, top="app-2.0")})
            pkgs["app"]["git"] = {"repo": "file://x", "release_download_url_template": f"file://{t}/app-" + "{{ full_version }}.tgz"}
            cat, stable = built(t, pkgs, "app")
            conf = packaging.settings({})
            self.assertEqual(packaging.conflicts_for(cat, stable, conf), ["bytehawks-app-edge"])
            self.assertEqual(packaging.conflicts_for(cat, cat.unit("app", "edge"), conf), ["bytehawks-app"])

    def test_dry_run_writes_nothing(self):
        with TempDir() as t:
            cat, app = built(t, demo_packages(t), "app")
            fake = FakeNfpm()
            lines = []
            packaging.build_packages(cat, app, ["deb", "rpm"], t / "out", packaging.settings({}), dry_run=True,
                                     runner=fake, log=lines.append)
            self.assertEqual((fake.calls, (t / "out").exists()), ([], False))
            self.assertTrue(lines[0].startswith("[plan] bytehawks-app_1.0-1_amd64.deb"))

    def test_nfpm_failure_is_reported(self):
        with TempDir() as t:
            cat, app = built(t, demo_packages(t), "app")
            failing = lambda command, **kw: subprocess.CompletedProcess(command, 1, "", "boom: bad content")
            with self.assertRaises(PackagingError) as ctx:
                packaging.build_packages(cat, app, ["deb"], t / "out", packaging.settings({}), nfpm="/bin/true",
                                         runner=failing, log=lambda m: None)
            self.assertIn("boom", str(ctx.exception))

    def test_find_nfpm(self):
        with TempDir() as t:
            fake = t / "nfpm"
            fake.write_text("#!/bin/sh\n")
            fake.chmod(0o755)
            self.assertEqual(packaging.find_nfpm({"nfpm": str(fake)}), str(fake))
            os.environ["BH_NFPM"] = str(fake)
            try:
                self.assertEqual(packaging.find_nfpm({}), str(fake))
            finally:
                del os.environ["BH_NFPM"]
            if shutil.which("nfpm") is None:
                with self.assertRaises(PackagingError):
                    packaging.find_nfpm({"nfpm": str(t / "missing")})


@unittest.skipUnless(shutil.which("nfpm") and shutil.which("dpkg-deb"), "needs nfpm and dpkg-deb")
class RealNfpmTests(unittest.TestCase):
    def test_deb_has_the_metadata_and_files(self):
        with TempDir() as t:
            cat, app = built(t, demo_packages(t, license="MIT"), "app")
            packaging.build_packages(cat, app, ["deb"], t / "out", packaging.settings({}), log=lambda m: None)
            deb = t / "out" / "bytehawks-app_1.0-1_amd64.deb"
            info = subprocess.run(["dpkg-deb", "-f", str(deb)], capture_output=True, text=True, check=True).stdout
            self.assertIn("Package: bytehawks-app", info)
            self.assertIn("Version: 1.0-1", info)
            listing = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True, check=True).stdout
            self.assertIn(f"{t / 'root' / 'app' / 'bin' / 'app.sh'}".lstrip("/"), listing)


if __name__ == "__main__":
    unittest.main()
