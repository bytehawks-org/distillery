import io
import os
import stat
import tarfile
import unittest
import zipfile

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

    def test_extract_zip_restores_modes_and_strips_top(self):
        with TempDir() as t:
            with zipfile.ZipFile(t / "app.zip", "w") as archive:
                run = zipfile.ZipInfo("app-1.0/bin/run.sh")
                run.external_attr = (stat.S_IFREG | 0o4755) << 16
                archive.writestr(run, "#!/bin/sh\n")
                archive.writestr("app-1.0/conf/app.properties", "k=v\n")
                link = zipfile.ZipInfo("app-1.0/bin/latest")
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(link, "run.sh")
            fetch.extract(t / "app.zip", t / "out")
            self.assertEqual(os.stat(t / "out" / "bin" / "run.sh").st_mode & 0o7777, 0o755)   # setuid dropped
            self.assertEqual((t / "out" / "conf" / "app.properties").read_text(), "k=v\n")
            self.assertEqual(os.readlink(t / "out" / "bin" / "latest"), "run.sh")

    def test_extract_zip_rejects_traversal_and_escaping_links(self):
        with TempDir() as t:
            with zipfile.ZipFile(t / "evil.zip", "w") as archive:
                archive.writestr("top/../../evil.txt", "x")
            with self.assertRaises(fetch.FetchError):
                fetch.extract(t / "evil.zip", t / "out")

            with zipfile.ZipFile(t / "link.zip", "w") as archive:
                link = zipfile.ZipInfo("top/l")
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(link, "../../../etc/passwd")
            with self.assertRaises(fetch.FetchError):
                fetch.extract(t / "link.zip", t / "out2")

    def test_hash_file_algorithms(self):
        with TempDir() as t:
            (t / "f").write_bytes(b"abc")
            self.assertEqual(fetch.hash_file(t / "f", "sha256"), fetch.sha256_file(t / "f"))
            self.assertEqual(len(fetch.hash_file(t / "f", "sha512")), 128)


if __name__ == "__main__":
    unittest.main()
