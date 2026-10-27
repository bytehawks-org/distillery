import hashlib
import json
import os
import shutil
import subprocess
import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery import build, signature, store as stores
from bhdistillery.model import Catalog, apply_root
from bhdistillery.store import DirStore, NexusStore, StoreError, meta_bytes

GPG = shutil.which("gpg")


class Keyring:
    """A private GNUPGHOME with one throw-away signing key; becomes the process keyring while active."""

    def __init__(self, t, name="signer"):
        self.home = t / f"gnupg-{name}"
        self.home.mkdir(mode=0o700)
        self._saved = None
        self.run("--passphrase", "", "--quick-generate-key", f"{name} <{name}@example.com>", "ed25519", "sign", "never")
        listing = self.run("--with-colons", "--list-keys").stdout
        self.fingerprint = [l for l in listing.splitlines() if l.startswith("fpr")][0].split(":")[9]

    def run(self, *args):
        env = {**os.environ, "GNUPGHOME": str(self.home)}
        return subprocess.run(["gpg", "--batch", "--no-tty", *args], env=env, check=True, capture_output=True, text=True)

    def __enter__(self):
        self._saved = os.environ.get("GNUPGHOME")
        os.environ["GNUPGHOME"] = str(self.home)
        return self

    def __exit__(self, *exc):
        if self._saved is None:
            os.environ.pop("GNUPGHOME", None)
        else:
            os.environ["GNUPGHOME"] = self._saved


def artifact_files(directory, name="bh--demo--stable--library--amd64--1.0--abcdef012345"):
    """A tarball + sidecar the way build_tarball writes them."""
    directory.mkdir(parents=True, exist_ok=True)
    tarball = directory / f"{name}.tar.gz"
    tarball.write_bytes(b"payload")
    meta = {"name": name, "sha256": hashlib.sha256(b"payload").hexdigest(), "recipe_sha256": "ab" * 32}
    (directory / f"{name}.json").write_bytes(meta_bytes(meta))
    return stores.parse_name(name), tarball, meta


def policy_config(fingerprints, require=True, keys=""):
    return {"config": {"artifacts": {"signing": {"fingerprints": fingerprints, "require": require, "keys": keys}}}}


@unittest.skipUnless(GPG, "gpg not installed")
class SigningTests(unittest.TestCase):
    def test_settings_come_from_config_and_environment(self):
        os.environ["BH_SIGNING_KEY"] = "ENVKEY"
        try:
            got = signature.signing_settings({"config": {"artifacts": {"signing": {"key": "CFG", "require": True,
                                                                                      "fingerprints": ["ab " * 13 + "ab"]}}}})
        finally:
            del os.environ["BH_SIGNING_KEY"]
        self.assertEqual((got["key"], got["require"]), ("ENVKEY", True))
        self.assertEqual(signature.signing_settings({})["key"], "")

    def test_signed_artifact_round_trips_through_a_dir_store(self):
        with TempDir() as t, Keyring(t) as ring:
            entry, tarball, meta = artifact_files(t / "dist")
            signature.sign_artifact(t / "dist" / f"{entry.name}.json", {"config": {"artifacts": {"signing": {"key": ring.fingerprint}}}},
                                    log=lambda m: None)
            self.assertTrue((t / "dist" / f"{entry.name}.json.asc").exists())

            target = DirStore(t / "shared")
            target.publish(entry, tarball, meta)                              # carries the signature along
            self.assertTrue((t / "shared" / f"{entry.name}.json.asc").exists())

            target.policy = signature.ArtifactPolicy.from_config(policy_config([ring.fingerprint]))
            found = target.inventory()[0]
            target.fetch(found, t / "work")
            self.assertEqual(found.signer, ring.fingerprint)

    def test_required_policy_refuses_unsigned_tampered_and_foreign_artifacts(self):
        with TempDir() as t, Keyring(t, "mine") as mine:
            entry, tarball, meta = artifact_files(t / "dist")
            sidecar = t / "dist" / f"{entry.name}.json"

            store = DirStore(t / "dist")
            store.policy = signature.ArtifactPolicy.from_config(policy_config([mine.fingerprint]))
            with self.assertRaises(signature.SignatureError) as ctx:          # unsigned
                store.fetch(store.inventory()[0], t / "work")
            self.assertIn("unsigned", str(ctx.exception))

            store.policy = signature.ArtifactPolicy.from_config(policy_config([mine.fingerprint], require=False))
            store.fetch(store.inventory()[0], t / "work")                     # not required: unsigned is tolerated

            signature.sign_file(sidecar, mine.fingerprint)
            store.policy = signature.ArtifactPolicy.from_config(policy_config([mine.fingerprint]))
            store.fetch(store.inventory()[0], t / "work")                     # good

            sidecar.write_bytes(sidecar.read_bytes() + b" ")                  # sidecar changed after signing
            with self.assertRaises(signature.SignatureError):
                store.fetch(store.inventory()[0], t / "work")
            with self.assertRaises(signature.SignatureError):                 # a bad signature is refused even if optional
                store.policy = signature.ArtifactPolicy.from_config(policy_config([mine.fingerprint], require=False))
                store.fetch(store.inventory()[0], t / "work")

    def test_signature_of_another_key_is_refused(self):
        with TempDir() as t, Keyring(t, "mine") as mine:
            other = Keyring(t, "other")
            entry, tarball, meta = artifact_files(t / "dist")
            env = {**os.environ, "GNUPGHOME": str(other.home)}
            subprocess.run(["gpg", "--batch", "--no-tty", "--yes", "--armor", "--detach-sign", "--local-user", other.fingerprint,
                            "-o", str(t / "dist" / f"{entry.name}.json.asc"), str(t / "dist" / f"{entry.name}.json")],
                           env=env, check=True, capture_output=True)
            keys = t / "keys.asc"
            keys.write_text(other.run("--armor", "--export", other.fingerprint).stdout)
            store = DirStore(t / "dist")
            store.policy = signature.ArtifactPolicy.from_config(policy_config([mine.fingerprint], keys=str(keys)))
            with self.assertRaises(signature.SignatureError):
                store.fetch(store.inventory()[0], t / "work")

    def test_keys_file_replaces_the_local_keyring(self):
        with TempDir() as t:
            signer = Keyring(t, "ci")
            entry, tarball, meta = artifact_files(t / "dist")
            with signer:
                signature.sign_file(t / "dist" / f"{entry.name}.json", signer.fingerprint)
            keys = t / "ci-public.asc"
            keys.write_text(signer.run("--armor", "--export", signer.fingerprint).stdout)
            consumer = Keyring(t, "consumer")                                 # a machine that does not know the signer
            with consumer:
                store = DirStore(t / "dist")
                store.policy = signature.ArtifactPolicy.from_config(policy_config([signer.fingerprint]))
                with self.assertRaises(signature.SignatureError):             # key neither in keyring nor configured
                    store.fetch(store.inventory()[0], t / "work")
                store.policy = signature.ArtifactPolicy.from_config(policy_config([signer.fingerprint], keys=str(keys)))
                store.fetch(store.inventory()[0], t / "work")

    def test_sign_directory_signs_only_what_is_unsigned(self):
        with TempDir() as t, Keyring(t) as ring:
            artifact_files(t / "dist")
            artifact_files(t / "dist", "bh--demo2--stable--library--amd64--1.0--abcdef012345")
            config = {"config": {"artifacts": {"signing": {"key": ring.fingerprint}}}}
            first = signature.sign_directory(t / "dist", config, log=lambda m: None)
            self.assertEqual(len(first), 2)
            self.assertEqual(signature.sign_directory(t / "dist", config, log=lambda m: None), [])
            self.assertEqual(len(signature.sign_directory(t / "dist", config, force=True, log=lambda m: None)), 2)
            with self.assertRaises(signature.SignatureError):
                signature.sign_directory(t / "dist", {})                      # no key configured

    def test_build_signs_the_artifact_when_a_key_is_configured(self):
        with TempDir() as t, Keyring(t) as ring:
            digest = make_tarball(t / "demo-1.0.tgz", {"a": "x"})
            config = apply_root(CONFIG, t / "root")
            config["config"]["artifacts"] = {"signing": {"key": ring.fingerprint}}
            cat = Catalog(config, {"package": {"demo": package((t / "demo-1.0.tgz").as_uri(), digest)}}, jobs=1)
            unit = cat.unit("demo")
            cat.plan(unit)
            build.build_unit(unit, t / "assets", artifacts_dir=t / "dist", log=lambda m: None)
            [sig] = list((t / "dist").glob("*.json.asc"))
            checked = DirStore(t / "dist")
            checked.policy = signature.ArtifactPolicy.from_config(policy_config([ring.fingerprint]))
            checked.fetch(checked.inventory()[0], t / "work")
            self.assertEqual(checked.inventory()[0].name + ".json.asc", sig.name)

    def test_nexus_carries_the_signature(self):
        assets = {}

        def put(handler, body):
            assets[handler.path.split("/repository/repo/", 1)[1]] = body
            return 201, {}, b""

        def get(handler, body):
            key = handler.path.split("/repository/repo/", 1)[1]
            return (200, {}, assets[key]) if key in assets else (404, {}, b"")

        def listing(handler, body):
            return json_response({"items": [{"path": p} for p in assets], "continuationToken": None})

        routes = {("PUT", "/repository/repo/"): put, ("GET", "/repository/repo/"): get,
                  ("GET", "/service/rest/v1/assets"): listing}
        with FakeServer(routes) as srv, TempDir() as t, Keyring(t) as ring:
            entry, tarball, meta = artifact_files(t / "dist")
            signature.sign_file(t / "dist" / f"{entry.name}.json", ring.fingerprint)
            nexus = NexusStore(srv.url, "repo")
            nexus.publish(entry, tarball, meta)
            self.assertTrue(any(p.endswith(".json.asc") for p in assets))
            nexus.policy = signature.ArtifactPolicy.from_config(policy_config([ring.fingerprint]))
            found = nexus.inventory()[0]
            nexus.fetch(found, t / "work")
            self.assertEqual(found.signer, ring.fingerprint)


if __name__ == "__main__":
    unittest.main()
