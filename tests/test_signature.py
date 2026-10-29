import os
import shutil
import subprocess
import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery import signature, build, bump, fetch

GPG = shutil.which("gpg")


def make_signed(t, payload=b"release bytes"):
    """A throw-away key, a file and its detached signature. Returns (file, sig, keys, fingerprint)."""
    home = t / "gnupg"
    home.mkdir(mode=0o700)
    env = {**os.environ, "GNUPGHOME": str(home)}
    run = lambda *a: subprocess.run(["gpg", "--batch", "--no-tty", *a], env=env, check=True, capture_output=True, text=True)
    run("--passphrase", "", "--quick-generate-key", "Release Manager <rm@example.com>", "ed25519", "sign", "never")
    fingerprint = [l for l in run("--with-colons", "--list-keys").stdout.splitlines() if l.startswith("fpr")][0].split(":")[9]
    (t / "src.tar").write_bytes(payload)
    run("--detach-sign", "--armor", "-o", str(t / "src.tar.asc"), str(t / "src.tar"))
    run("--armor", "--export", "-o", str(t / "KEYS"), fingerprint)
    return t / "src.tar", t / "src.tar.asc", t / "KEYS", fingerprint


@unittest.skipUnless(GPG, "gpg not installed")
class SignatureTests(unittest.TestCase):
    def test_good_signature_by_pinned_key(self):
        with TempDir() as t:
            archive, sig, keys, fpr = make_signed(t)
            self.assertEqual(signature.verify_gpg(archive, sig, keys, [fpr]), fpr)

    def test_tampered_file_is_rejected(self):
        with TempDir() as t:
            archive, sig, keys, fpr = make_signed(t)
            archive.write_bytes(b"other bytes")
            with self.assertRaises(signature.SignatureError):
                signature.verify_gpg(archive, sig, keys, [fpr])

    def test_valid_signature_by_unpinned_key_is_rejected(self):
        with TempDir() as t:
            archive, sig, keys, fpr = make_signed(t)
            with self.assertRaises(signature.SignatureError) as ctx:
                signature.verify_gpg(archive, sig, keys, ["A" * 40])
            self.assertIn("not a pinned signer", str(ctx.exception))

    def test_config_validation(self):
        pkg = {"signature": {"keys_url": "file:///k", "fingerprints": ["abc"]}}
        with self.assertRaises(signature.SignatureError):
            signature.signature_config(pkg, {"git": {}}, "file:///x.tgz")
        self.assertIsNone(signature.signature_config({}, {"git": {}}, "file:///x.tgz"))
        pkg = {"signature": {"keys_url": "file:///k", "fingerprints": ["0123 4567 89ab cdef 0123 4567 89ab cdef 0123 4567"]}}
        cfg = signature.signature_config(pkg, {"git": {}}, "file:///x.tgz")
        self.assertEqual(cfg["url"], "file:///x.tgz.asc")                    # default: <source>.asc

    def _unit(self, t, fingerprints):
        digest = make_tarball(t / "demo-1.0.tgz", {"a": "x"})
        subprocess.run(["gpg", "--homedir", str(t / "gnupg"), "--batch", "--no-tty", "--yes", "--detach-sign", "--armor",
                        "-o", str(t / "demo-1.0.tgz.asc"), str(t / "demo-1.0.tgz")], check=True, capture_output=True)
        pkg = package(f"file://{t}/demo-" + "{{ full_version }}.tgz", digest,
                      signature={"keys_url": f"file://{t}/KEYS", "fingerprints": fingerprints})
        cat = catalog({"demo": pkg}, t / "root")
        unit = cat.unit("demo")
        cat.plan(unit)
        return unit

    def test_build_refuses_unsigned_or_wrongly_signed_source_before_running_anything(self):
        with TempDir() as t:
            _, _, _, fpr = make_signed(t)
            unit = self._unit(t, ["B" * 40])
            with self.assertRaises(fetch.FetchError):
                build.build_unit(unit, t / "assets", log=lambda m: None)
            self.assertFalse((t / "root" / "demo").exists())                # nothing was built

            unit = self._unit(t, [fpr])
            self.assertEqual(build.build_unit(unit, t / "assets", log=lambda m: None), "built")

    def test_bump_checks_the_signature_of_the_new_release(self):
        with TempDir() as t:
            _, _, _, fpr = make_signed(t)
            unit = self._unit(t, [fpr])
            url, digest = bump.fetch_checksum(unit, "1.0", log=lambda m: None)
            self.assertEqual(digest, unit.variant_data["sha256"])
            (t / "demo-1.0.tgz.asc").write_text("garbage")
            with self.assertRaises(fetch.FetchError):
                bump.fetch_checksum(unit, "1.0", log=lambda m: None)


if __name__ == "__main__":
    unittest.main()
