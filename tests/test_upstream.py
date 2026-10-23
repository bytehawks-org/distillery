import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery import upstream


class UpstreamTests(unittest.TestCase):
    def test_version_helpers(self):
        self.assertEqual(upstream.parse_version("3.5.10"), (3, 5, 10))
        self.assertIsNone(upstream.parse_version("3.5rc1"))
        self.assertTrue(upstream.in_series("3.5.4", "3.5"))
        self.assertTrue(upstream.in_series("3.5", "3.5"))
        self.assertFalse(upstream.in_series("3.50.1", "3.5"))        # not a prefix match on characters
        self.assertTrue(upstream.in_series("10.44", "10"))

    def test_versions_from_tags_filters_and_sorts(self):
        tags = ["v1.2.9", "v1.2.10", "v1.3.0", "v1.3.0-rc1", "foo", "v1.2.10-beta"]
        found = upstream.versions_from_tags(tags, r"^v(\d+\.\d+\.\d+)$")
        self.assertEqual([v for v, _ in found], ["1.3.0", "1.2.10", "1.2.9"])

    def test_check_unit_against_a_real_git_repository(self):
        with TempDir() as t:
            repo = git_repo_with_tags(t / "up", ["rel-1.2.3", "rel-1.2.4", "rel-1.2.5", "rel-1.3.0", "rel-2.0.0", "rel-1.2.6-rc1"])
            pkg = package("file:///x.tgz", "a" * 64, upstream={"repo": repo, "tag_regex": r"^rel-(\d+\.\d+\.\d+)$"})
            pkg["variant"][0].update(major_version=1.2, full_version="1.2.3", patch_version=3)
            cat = catalog({"demo": pkg}, t / "root")
            finding = upstream.check_unit(cat.unit("demo"))
            self.assertEqual((finding.current, finding.track, finding.latest), ("1.2.3", "1.2", "1.2.5"))
            self.assertTrue(finding.outdated)
            self.assertEqual(finding.newer_series, ["2.0.0", "1.3.0"])
            self.assertEqual(finding.tag, "rel-1.2.5")

    def test_up_to_date_and_series_override(self):
        with TempDir() as t:
            repo = git_repo_with_tags(t / "up", ["v1.0.0", "v1.0.1"])
            pkg = package("file:///x.tgz", "a" * 64, upstream={"repo": repo, "tag_regex": r"^v(\d+\.\d+\.\d+)$"})
            pkg["variant"][0].update(major_version=1.0, full_version="1.0.1", patch_version=1)
            finding = upstream.check_unit(catalog({"demo": pkg}, t / "r").unit("demo"))
            self.assertFalse(finding.outdated)
            pkg["variant"][0]["track"] = "2"
            finding = upstream.check_unit(catalog({"demo": pkg}, t / "r").unit("demo"))
            self.assertEqual(finding.latest, "")                     # nothing in series 2
            self.assertFalse(finding.outdated)

    def test_no_upstream_block_and_errors(self):
        with TempDir() as t:
            pkg = package("file:///x.tgz", "a" * 64)
            self.assertIsNone(upstream.check_unit(catalog({"demo": pkg}, t).unit("demo")))
            bad = package("file:///x.tgz", "a" * 64, upstream={"repo": str(t / "missing"), "tag_regex": "(x)"})
            with self.assertRaises(upstream.UpstreamError):
                upstream.check_unit(catalog({"demo": bad}, t).unit("demo"))
            noregex = package("file:///x.tgz", "a" * 64, upstream={"repo": str(t)})
            with self.assertRaises(upstream.UpstreamError):
                upstream.check_unit(catalog({"demo": noregex}, t).unit("demo"))


if __name__ == "__main__":
    unittest.main()
