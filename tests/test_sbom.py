import json
import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery import sbom
from bhdistillery.build import build_unit


def two_units(t, **lib_extra):
    digest = make_tarball(t / "d.tgz", {"README": "x"}, top="demo-1.0")
    url = (t / "d.tgz").as_uri()
    pkgs = {
        "lib": package(url, digest, tags=["library"], license="Zlib", **lib_extra),
        "app": package(url, digest, depends_on=["lib"], license="GPL-2.0-with-classpath-exception", website="https://example.com/{{ name }}",
                       commands={"install": "mkdir -p {{ prefix }}/bin && echo x > {{ prefix }}/bin/app"}),
    }
    cat = catalog(pkgs, t / "root")
    unit = cat.unit("app")
    cat.plan(unit)
    return unit, digest


class SbomTests(unittest.TestCase):
    def test_document_describes_the_unit_and_what_it_was_built_with(self):
        with TempDir() as t:
            unit, digest = two_units(t)
            doc = sbom.build(unit, toolchain={"cc": "gcc 13", "libc": "glibc 2.39"}, built_at="2026-10-31T00:00:00Z")
            self.assertEqual((doc["bomFormat"], doc["specVersion"]), ("CycloneDX", "1.5"))
            main = doc["metadata"]["component"]
            self.assertEqual((main["name"], main["version"], main["type"]), ("app", "1.0", "application"))
            self.assertEqual(main["purl"], "pkg:generic/app@1.0")
            self.assertEqual(main["hashes"], [{"alg": "SHA-256", "content": digest}])
            self.assertEqual([c["name"] for c in doc["components"]], ["lib"])
            self.assertEqual(doc["components"][0]["type"], "library")
            props = {p["name"]: p["value"] for p in main["properties"]}
            self.assertEqual(props["bytehawks:recipe_sha256"], unit.recipe_sha256)
            self.assertEqual(props["bytehawks:linkage"], "dynamic")
            graph = {d["ref"]: d["dependsOn"] for d in doc["dependencies"]}
            self.assertEqual(graph[main["bom-ref"]], [doc["components"][0]["bom-ref"]])
            tools = {c["name"]: c["version"] for c in doc["metadata"]["tools"]["components"]}
            self.assertEqual(tools["toolchain:cc"], "gcc 13")
            self.assertEqual(doc["metadata"]["timestamp"], "2026-10-31T00:00:00Z")

    def test_templates_in_references_are_resolved_and_licenses_stay_valid(self):
        with TempDir() as t:
            unit, _ = two_units(t)
            doc = sbom.build(unit)
            urls = [r["url"] for r in doc["metadata"]["component"]["externalReferences"]]
            self.assertIn("https://example.com/app", urls)
            self.assertFalse(any("{{" in u for u in urls))
            self.assertEqual(doc["metadata"]["component"]["licenses"], [{"license": {"id": "GPL-2.0-with-classpath-exception"}}])
            self.assertEqual(doc["components"][0]["licenses"], [{"license": {"id": "Zlib"}}])

    def test_unknown_or_compound_licenses_use_an_expression(self):
        with TempDir() as t:
            unit, _ = two_units(t)
            unit.package["license"] = "Apache-2.0 OR MIT"
            self.assertEqual(sbom.component(unit)["licenses"], [{"expression": "Apache-2.0 OR MIT"}])
            unit.package["license"] = "Custom Vendor Licence v2"
            self.assertEqual(sbom.component(unit)["licenses"], [{"license": {"name": "Custom Vendor Licence v2"}}])

    def test_same_recipe_same_serial_number(self):
        with TempDir() as t:
            unit, _ = two_units(t)
            self.assertEqual(sbom.build(unit)["serialNumber"], sbom.build(unit)["serialNumber"])
            self.assertTrue(sbom.build(unit)["serialNumber"].startswith("urn:uuid:"))

    def test_closure_is_transitive_and_without_duplicates(self):
        with TempDir() as t:
            digest = make_tarball(t / "d.tgz", {"README": "x"}, top="demo-1.0")
            url = (t / "d.tgz").as_uri()
            pkgs = {"a": package(url, digest, tags=["library"]),
                    "b": package(url, digest, tags=["library"], depends_on=["a"]),
                    "c": package(url, digest, tags=["library"], depends_on=["a", "b"]),
                    "app": package(url, digest, depends_on=["b", "c"])}
            cat = catalog(pkgs, t / "root")
            unit = cat.unit("app")
            cat.plan(unit)
            self.assertEqual(sorted(u.name for u in sbom.closure(unit)), ["a", "b", "c"])

    def test_build_writes_the_sbom_into_the_sealed_bundle(self):
        with TempDir() as t:
            unit, _ = two_units(t)
            build_unit(unit, t / "assets", log=lambda m: None)
            path = unit.bundle_dir / "sbom.cdx.json"
            doc = json.loads(path.read_text())
            self.assertEqual(doc["metadata"]["component"]["name"], "app")
            self.assertIn("toolchain:libc", {c["name"] for c in doc["metadata"]["tools"]["components"]})
            sealed = (unit.bundle_dir / "bundle.sha256").read_text()
            self.assertIn("  sbom.cdx.json\n", sealed)
            files = (unit.bundle_dir / "files.sha256").read_text()
            self.assertIn("/.bytehawks/sbom.cdx.json", files)

    def test_schema_validation_when_the_cyclonedx_library_is_present(self):
        try:
            from cyclonedx.schema import SchemaVersion
            from cyclonedx.validation.json import JsonStrictValidator
        except ImportError:
            self.skipTest("cyclonedx-python-lib not installed")
        with TempDir() as t:
            unit, _ = two_units(t)
            self.assertIsNone(JsonStrictValidator(SchemaVersion.V1_5).validate_str(sbom.render(unit, {"cc": "gcc"}, "2026-10-31T00:00:00Z")))


if __name__ == "__main__":
    unittest.main()
