import io
import json
import unittest
import zipfile

from helpers import *  # noqa: F401,F403
from bhdistillery import store
from bhdistillery.store import DirStore, Entry, GithubStore, MultiStore, NexusStore, StoreError, Unsupported


def entry(version="1.0", recipe="abcdef012345", **kw):
    return Entry(kw.get("package", "demo"), "stable", "library", "amd64", version, recipe)


def artifact(tmp, e, payload=b"payload"):
    tar = tmp / f"{e.name}.tar.gz"
    tar.write_bytes(payload)
    meta = {"sha256": store.sha256_file(tar), "created_at": "2026-01-01T00:00:00Z"}
    return tar, meta


class NamingTests(unittest.TestCase):
    def test_roundtrip_and_foreign_names(self):
        e = entry("3.5.4", "0123456789ab")
        self.assertEqual(e.name, "bh--demo--stable--library--amd64--3.5.4--0123456789ab")
        parsed = store.parse_name(e.name)
        self.assertEqual((parsed.package, parsed.variant, parsed.tag, parsed.arch, parsed.version, parsed.recipe12),
                         ("demo", "stable", "library", "amd64", "3.5.4", "0123456789ab"))
        for foreign in ("coverage-report", "bh--too--few", "x--demo--stable--library--amd64--1--a"):
            self.assertIsNone(store.parse_name(foreign))


class DirStoreTests(unittest.TestCase):
    def test_publish_inventory_fetch_and_tamper_detection(self):
        with TempDir() as t:
            s = DirStore(t / "dist")
            e = entry()
            tar, meta = artifact(t, e)
            s.publish(e, tar, meta)
            inv = s.inventory()
            self.assertEqual([x.name for x in inv], [e.name])
            self.assertIsNotNone(s.find(e))
            self.assertIsNone(s.find(entry(recipe="ffffffffffff")))
            path, got = s.fetch(inv[0], t / "work")
            self.assertEqual(got["sha256"], meta["sha256"])
            (t / "dist" / f"{e.name}.tar.gz").write_bytes(b"tampered")
            with self.assertRaises(StoreError):
                s.fetch(s.inventory()[0], t / "work")

    def test_missing_directory_is_empty(self):
        self.assertEqual(DirStore("/nonexistent/path").inventory(), [])


class GithubStoreTests(unittest.TestCase):
    def make_zip(self, e, payload=b"payload"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"{e.name}.tar.gz", payload)
            zf.writestr(f"{e.name}.json", json.dumps({"sha256": __import__("hashlib").sha256(payload).hexdigest()}))
        return buf.getvalue()

    def test_inventory_filters_expired_foreign_and_duplicates(self):
        names = [
            {"id": 3, "name": entry("2.0", "bbbbbbbbbbbb").name, "size_in_bytes": 10, "expired": False, "created_at": "c", "expires_at": "e"},
            {"id": 2, "name": entry("1.0", "aaaaaaaaaaaa").name, "expired": True},
            {"id": 1, "name": "playwright-report", "expired": False},
            {"id": 0, "name": entry("2.0", "bbbbbbbbbbbb").name, "expired": False},      # older duplicate
        ]
        with FakeServer({("GET", "/repos/o/r/actions/artifacts"): lambda h, b: json_response({"artifacts": names})}) as srv:
            s = GithubStore("o/r", token="tok", api_url=srv.url)
            inv = s.inventory()
            self.assertEqual([(e.version, e.ref) for e in inv], [("2.0", "3")])
            self.assertEqual(srv.requests[0][2]["Authorization"], "Bearer tok")

    def test_download_never_sends_the_token_to_the_blob_host(self):
        e = entry()
        blob = self.make_zip(e)
        seen = {}

        def api(handler, body):
            return 302, {"Location": f"{handler.server_url}/blob/zip"}, b""

        def blob_route(handler, body):
            seen["auth"] = handler.headers.get("Authorization")
            return 200, {"Content-Type": "application/zip"}, blob

        with FakeServer({("GET", "/repos/o/r/actions/artifacts/7/zip"): api, ("GET", "/blob/"): blob_route}) as srv:
            srv.httpd.RequestHandlerClass.server_url = srv.url
            s = GithubStore("o/r", token="secret-token", api_url=srv.url)
            found = Entry(**{**e.__dict__, "ref": "7", "store": s.label})
            with TempDir() as t:
                tar, meta = s.fetch(found, t)
                self.assertEqual(tar.read_bytes(), b"payload")
            self.assertIsNone(seen["auth"])
            self.assertEqual(srv.requests[0][2]["Authorization"], "Bearer secret-token")

    def test_publish_is_not_available_and_repo_is_required(self):
        s = GithubStore("o/r", token="t", api_url="http://x")
        with self.assertRaises(Unsupported):
            s.publish(entry(), "x", {})
        import os
        os.environ.pop("GITHUB_REPOSITORY", None)
        with self.assertRaises(StoreError):
            GithubStore(None, token="t")


class NexusStoreTests(unittest.TestCase):
    def test_publish_list_fetch_roundtrip_with_basic_auth(self):
        assets = {}

        def put(handler, body):
            assets[handler.path.split("/repository/repo/", 1)[1]] = body
            return 201, {}, b""

        def get(handler, body):
            return 200, {}, assets[handler.path.split("/repository/repo/", 1)[1]]

        def listing(handler, body):
            items = [{"path": p, "checksum": {"sha256": __import__("hashlib").sha256(v).hexdigest()}}
                     for p, v in assets.items()] + [{"path": "other/thing.tar.gz"}]
            return json_response({"items": items, "continuationToken": None})

        routes = {("PUT", "/repository/repo/"): put, ("GET", "/repository/repo/"): get,
                  ("GET", "/service/rest/v1/assets"): listing}
        with FakeServer(routes) as srv, TempDir() as t:
            s = NexusStore(srv.url, "repo", prefix="bytehawks", user="ci", password="pw")
            e = entry("1.0", "abcdef012345")
            tar, meta = artifact(t, e)
            s.publish(e, tar, meta)
            self.assertIn(f"bytehawks/amd64/demo/stable/library/1.0-abcdef012345/{e.name}.tar.gz", assets)
            self.assertTrue(srv.requests[0][2]["Authorization"].startswith("Basic "))

            inv = s.inventory()
            self.assertEqual([x.name for x in inv], [e.name])
            self.assertEqual(inv[0].sha256, meta["sha256"])
            path, got = s.fetch(inv[0], t / "work")
            self.assertEqual(path.read_bytes(), b"payload")

    def test_listing_error_is_a_store_error(self):
        s = NexusStore("http://127.0.0.1:1", "repo")
        with self.assertRaises(StoreError):
            s.inventory()


class FactoryTests(unittest.TestCase):
    def test_specs(self):
        cfg = {"config": {"artifacts": {"stores": {"mynexus": {"type": "nexus", "url": "http://n", "repository": "r"}}}}}
        self.assertIsInstance(store.open_store("dir:/tmp/x", cfg), DirStore)
        self.assertIsInstance(store.open_store("mynexus", cfg), NexusStore)
        self.assertEqual(store.open_store("github:o/r", cfg).repo, "o/r")
        with self.assertRaises(StoreError):
            store.open_store("ftp", cfg)

    def test_multistore_first_match_and_dedup(self):
        with TempDir() as t:
            a, b = DirStore(t / "a"), DirStore(t / "b")
            e = entry()
            tar, meta = artifact(t, e)
            a.publish(e, tar, meta)
            b.publish(e, tar, meta)
            multi = MultiStore([a, b])
            self.assertEqual(len(multi.inventory()), 1)
            self.assertEqual(multi.inventory()[0].store, a.label)


if __name__ == "__main__":
    unittest.main()
