import io
import tarfile
import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery import fetch


class FetchTests(unittest.TestCase):
    def test_download_verifies_and_caches(self):
        with TempDir() as t:
            digest = make_tarball(t / "src.tgz", {"a.txt": "x"})
            url = (t / "src.tgz").as_uri()
            got = fetch.download(url, t / "dl" / "src.tgz", digest, log=lambda m: None)
            self.assertEqual(got, digest)
            messages = []
            fetch.download("file:///does/not/exist", t / "dl" / "src.tgz", digest, log=messages.append)
            self.assertIn("cache hit", messages[0])      # no network needed the second time

    def test_checksum_mismatch_is_rejected_and_leaves_nothing(self):
        with TempDir() as t:
            make_tarball(t / "src.tgz", {"a.txt": "x"})
            with self.assertRaises(fetch.FetchError) as ctx:
                fetch.download((t / "src.tgz").as_uri(), t / "dl" / "x.tgz", "0" * 64, log=lambda m: None)
            self.assertIn("mismatch", str(ctx.exception))
            self.assertFalse((t / "dl" / "x.tgz").exists())
            self.assertEqual(list((t / "dl").glob("*.part")), [])

    def test_unpinned_source_refused_unless_allowed(self):
        with TempDir() as t:
            make_tarball(t / "src.tgz", {"a.txt": "x"})
            with self.assertRaises(fetch.FetchError):
                fetch.download((t / "src.tgz").as_uri(), t / "dl" / "x.tgz", "", log=lambda m: None)
            fetch.download((t / "src.tgz").as_uri(), t / "dl" / "x.tgz", "", allow_unverified=True, log=lambda m: None)
            self.assertTrue((t / "dl" / "x.tgz").exists())

    def test_extract_strips_top_directory(self):
        with TempDir() as t:
            make_tarball(t / "src.tgz", {"sub/a.txt": "hello"})
            fetch.extract(t / "src.tgz", t / "out")
            self.assertEqual((t / "out" / "sub" / "a.txt").read_text(), "hello")

    def test_extract_rejects_traversal_and_escaping_links(self):
        with TempDir() as t:
            with tarfile.open(t / "evil.tgz", "w:gz") as archive:
                info = tarfile.TarInfo("top/../../evil.txt")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(fetch.FetchError):
                fetch.extract(t / "evil.tgz", t / "out")

            with tarfile.open(t / "link.tgz", "w:gz") as archive:
                info = tarfile.TarInfo("top/l")
                info.type = tarfile.SYMTYPE
                info.linkname = "../../../etc/passwd"
                archive.addfile(info)
            with self.assertRaises(fetch.FetchError):
                fetch.extract(t / "link.tgz", t / "out2")


if __name__ == "__main__":
    unittest.main()
