import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery import bump

YAML = """\
# header comment
package:
  demo:
    variant:
      - name: [old, legacy]            # aliases
        major_version: 1.2
        patch_version: 3
        full_version: 1.2.3
        sha256: aaaa
      - {name: other, major_version: 10, patch_version: 43, full_version: 10.43, sha256: bbbb}
    tags: [library]     # trailing comment
"""


class BumpTests(unittest.TestCase):
    def test_apply_bump_touches_only_the_variant_and_keeps_comments(self):
        with TempDir() as t:
            f = t / "packages.yaml"
            f.write_text(YAML)
            old, new = bump.apply_bump(f, "demo", "legacy", "1.2.9", "c" * 64)
            self.assertEqual((old, new), ("1.2.3", "1.2.9"))
            text = f.read_text()
            self.assertIn("# header comment", text)
            self.assertIn("# aliases", text)
            self.assertIn("# trailing comment", text)
            self.assertIn("full_version: 1.2.9", text)
            self.assertIn("patch_version: 9", text)
            self.assertIn("sha256: " + "c" * 64, text)
            self.assertIn("{name: other, major_version: 10, patch_version: 43, full_version: 10.43, sha256: bbbb}", text)

    def test_flow_style_variant_and_two_part_versions(self):
        with TempDir() as t:
            f = t / "packages.yaml"
            f.write_text(YAML)
            bump.apply_bump(f, "demo", "other", "10.44", "d" * 64)
            text = f.read_text()
            self.assertIn("patch_version: 44", text)
            self.assertIn("10.44", text)
            self.assertIn("full_version: 1.2.3", text)               # the other variant is untouched

    def test_series_change_is_refused(self):
        with TempDir() as t:
            f = t / "packages.yaml"
            f.write_text(YAML)
            with self.assertRaises(bump.BumpError):
                bump.apply_bump(f, "demo", "legacy", "1.3.0", "c" * 64)
            with self.assertRaises(bump.BumpError):
                bump.apply_bump(f, "demo", "nope", "1.2.9", "c" * 64)

    def test_checksum_is_computed_and_cross_checked_with_upstream(self):
        with TempDir() as t:
            digest = make_tarball(t / "demo-1.0.tgz", {"a": "x"})
            (t / "demo-1.0.tgz.sha256").write_text(digest + "  demo-1.0.tgz\n")
            pkg = package(f"file://{t}/demo-" + "{{ full_version }}.tgz", "a" * 64,
                          upstream={"checksum_url_template": "{{ git.release_download_url_template }}.sha256"})
            unit = catalog({"demo": pkg}, t / "r").unit("demo")
            url, got = bump.fetch_checksum(unit, "1.0", log=lambda m: None)
            self.assertEqual(got, digest)
            self.assertTrue(url.endswith("demo-1.0.tgz"))

            (t / "demo-1.0.tgz.sha256").write_text("0" * 64 + "  demo-1.0.tgz\n")
            with self.assertRaises(bump.BumpError) as ctx:
                bump.fetch_checksum(unit, "1.0", log=lambda m: None)
            self.assertIn("differs", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
