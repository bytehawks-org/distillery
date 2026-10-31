"""Detached OpenPGP signature check of a downloaded source, before anything is extracted.

A package declares who may sign its releases:

    signature:
      type: gpg                                          # the only type today
      url_template: "{{ git.release_download_url_template }}.asc"       # default; .sig works too
      keys_url: https://downloads.apache.org/nifi/KEYS   # where the public keys come from (any key server)
      fingerprints: [3FE925C2BED744021E6169A2F92A93B30C07C6D5]      # PINNED: the trust anchor

The keys are fetched into a throw-away keyring and are only believed if the signature was made by a key whose
full fingerprint is pinned in the catalog: a compromised key host cannot add a signer. A release signed by a
new release manager fails until a human reviews the fingerprint and adds it.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import fetch
from .templates import resolve_template

FINGERPRINT = re.compile(r"^[0-9A-F]{40}$")
STATUS_BAD = ("BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "NO_PUBKEY")


class SignatureError(fetch.FetchError):
    pass


def normalize(fingerprint):
    return re.sub(r"\s+", "", str(fingerprint)).upper()


def signature_config(package, context, url):
    """The resolved `signature:` block of a package, or None. `url` is the source download URL."""
    config = package.get("signature")
    if config is None:
        return None
    config = dict(config)
    if config.get("type", "gpg") != "gpg":
        raise SignatureError(f"unsupported signature type '{config.get('type')}'")
    fingerprints = [normalize(f) for f in config.get("fingerprints", [])]
    if not fingerprints or not all(FINGERPRINT.match(f) for f in fingerprints):
        raise SignatureError("signature.fingerprints must list the full 40-hex fingerprints of the allowed signers")
    if not config.get("keys_url"):
        raise SignatureError("signature.keys_url is required")
    context = {**context, "git": {**context.get("git", {}), "release_download_url_template": url}}
    return {
        "url": resolve_template(config.get("url_template", "{{ git.release_download_url_template }}.asc"), context),
        "keys_url": resolve_template(config["keys_url"], context),
        "fingerprints": fingerprints,
    }


def verify_gpg(archive, signature, keys, fingerprints, gpg="gpg", runner=subprocess.run):
    """Verify `archive` against the detached `signature` using the public keys in `keys`.

    Returns the primary fingerprint of the signer. Raises SignatureError unless the signature is good,
    made by a valid (non expired, non revoked) key and that key is one of `fingerprints`."""
    if shutil.which(gpg) is None:
        raise SignatureError(f"{gpg} not found: install GnuPG to verify signed sources")
    home = tempfile.mkdtemp(prefix="bh-gpg-")
    os.chmod(home, 0o700)
    base = [gpg, "--homedir", home, "--batch", "--no-tty", "--no-autostart",
            "--no-auto-key-retrieve", "--no-auto-key-locate"]
    try:
        imported = runner(base + ["--import", str(keys)], capture_output=True, text=True)
        if imported.returncode != 0:
            raise SignatureError(f"cannot import public keys: {imported.stderr.strip()[-300:]}")
        result = runner(base + ["--status-fd", "1", "--verify", str(signature), str(archive)],
                        capture_output=True, text=True)
        status = [line.split()[1:] for line in result.stdout.splitlines() if line.startswith("[GNUPG:] ")]
        bad = [s[0] for s in status if s and s[0] in STATUS_BAD]
        if bad or result.returncode != 0:
            raise SignatureError(f"signature of {Path(archive).name} is not valid ({', '.join(bad) or 'gpg exit ' + str(result.returncode)})")
        valid = [s for s in status if s and s[0] == "VALIDSIG"]
        if not any(s[0] == "GOODSIG" for s in status) or not valid:
            raise SignatureError(f"no good signature found for {Path(archive).name}")
        fields = valid[0]
        signer = normalize(fields[10] if len(fields) > 10 else fields[1])        # primary key, not the subkey
        if signer not in fingerprints:
            raise SignatureError(f"{Path(archive).name} is signed by {signer}, which is not a pinned signer "
                                 "(review the key, then add it to signature.fingerprints)")
        return signer
    finally:
        try:
            runner(["gpgconf", "--homedir", home, "--kill", "all"], capture_output=True)      # no agent left behind
        except OSError:
            pass
        shutil.rmtree(home, ignore_errors=True)


def verify_source(package, context, archive, url, log=print):
    """Verify a downloaded source if the package declares a signature. Returns the signer or None."""
    config = signature_config(package, context, url)
    if config is None:
        return None
    with tempfile.TemporaryDirectory(prefix="bh-sig-") as tmp:
        quiet = lambda message: None
        sig, keys = Path(tmp) / "source.sig", Path(tmp) / "keys"
        fetch.download(config["url"], sig, allow_unverified=True, log=quiet)
        fetch.download(config["keys_url"], keys, allow_unverified=True, log=quiet)
        signer = verify_gpg(archive, sig, keys, config["fingerprints"])
    log(f"signature ok: {Path(archive).name} signed by {signer}")
    return signer


# ------------------------------------------------------------------ signing built artifacts
#
# The sidecar <artifact>.json carries the sha256 of the tarball, the recipe hash and the identity of the unit,
# so one detached signature over it (<artifact>.json.asc) authenticates all of them. The same code path serves
# a developer signing with the key in their own keyring and CI signing with a key imported from a secret into a
# throw-away GNUPGHOME: only the environment differs.
#
#   artifacts:
#     signing:
#       key: ""            # key id / fingerprint to sign with (or BH_SIGNING_KEY); empty = do not sign
#       fingerprints: []   # trusted signers (primary key fingerprints) checked by restore / chain
#       keys: ""           # file or URL with their public keys; empty = export them from the local gpg keyring
#       require: false     # true = refuse unsigned artifacts and artifacts of other signers
#   BH_SIGNING_PASSPHRASE_FILE: passphrase for non interactive signing (gpg loopback pinentry)


def signing_settings(config):
    """The effective settings from config.yaml (`config` is the whole file or its `config` section) and the environment."""
    section = (config or {}).get("config", config or {})
    settings = dict((section.get("artifacts") or {}).get("signing") or {})
    return {
        "key": os.environ.get("BH_SIGNING_KEY") or settings.get("key") or "",
        "passphrase_file": os.environ.get("BH_SIGNING_PASSPHRASE_FILE") or settings.get("passphrase_file") or "",
        "fingerprints": [normalize(f) for f in settings.get("fingerprints") or []],
        "keys": settings.get("keys") or "",
        "require": bool(settings.get("require", False)),
    }


def sign_file(path, key, passphrase_file="", gpg="gpg", runner=subprocess.run):
    """Detached, armored signature of `path` written to `path`.asc. Returns the signature path."""
    if shutil.which(gpg) is None:
        raise SignatureError(f"{gpg} not found: install GnuPG to sign artifacts")
    out = Path(str(path) + ".asc")
    command = [gpg, "--batch", "--no-tty", "--yes", "--armor", "--detach-sign", "--local-user", key, "-o", str(out)]
    if passphrase_file:
        command += ["--pinentry-mode", "loopback", "--passphrase-file", str(passphrase_file)]
    result = runner(command + [str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise SignatureError(f"cannot sign {Path(path).name} with {key}: {result.stderr.strip()[-300:]}")
    return out


def sign_artifact(meta_file, config, log=print):
    """Sign an artifact sidecar if a signing key is configured. Returns the signature path or None."""
    settings = signing_settings(config)
    if not settings["key"]:
        return None
    sig = sign_file(meta_file, settings["key"], settings["passphrase_file"])
    log(f"[sign] {sig.name} with {settings['key']}")
    return sig


def sign_directory(directory, config, force=False, log=print):
    """Sign every artifact sidecar of a directory that has no signature yet (e.g. built unsigned in CI, signed
    later on a trusted workstation). Returns the list of signatures written."""
    settings = signing_settings(config)
    if not settings["key"]:
        raise SignatureError("no signing key: set artifacts.signing.key or BH_SIGNING_KEY")
    written = []
    from .store import holder_of, order_of, parse_name

    holder, order = holder_of(config), order_of(config)
    for meta_file in sorted(Path(directory).glob("*.json")):
        if parse_name(meta_file.stem, holder=holder, order=order) is None:
            continue                                            # not one of this holder's artifacts
        if Path(str(meta_file) + ".asc").exists() and not force:
            continue
        written.append(sign_artifact(meta_file, config, log))
    return written


def _trusted_keys(spec, fingerprints, workdir, gpg="gpg", runner=subprocess.run):
    keys = Path(workdir) / "trusted-keys.asc"
    if spec:
        if "://" in spec:
            fetch.download(spec, keys, allow_unverified=True, log=lambda message: None)
        else:
            shutil.copyfile(spec, keys)
    else:
        result = runner([gpg, "--batch", "--no-tty", "--armor", "--export", *fingerprints], capture_output=True)
        if result.returncode != 0 or not result.stdout:
            raise SignatureError("none of the trusted signing keys is in the local gpg keyring "
                                 "(import them, or set artifacts.signing.keys to a file or URL)")
        keys.write_bytes(result.stdout)
    return keys


class ArtifactPolicy:
    """What a store accepts: signed by one of `fingerprints`, or (not `require`d) unsigned."""

    def __init__(self, fingerprints, require=False, keys=""):
        if require and not fingerprints:
            raise SignatureError("artifacts.signing.require needs artifacts.signing.fingerprints")
        if not all(FINGERPRINT.match(f) for f in fingerprints):
            raise SignatureError("artifacts.signing.fingerprints must be full 40-hex fingerprints")
        self.fingerprints, self.require, self.keys = list(fingerprints), require, keys

    @classmethod
    def from_config(cls, config):
        settings = signing_settings(config)
        if not settings["fingerprints"] and not settings["require"]:
            return None                                              # nothing to check, nothing configured
        return cls(settings["fingerprints"], settings["require"], settings["keys"])

    def check(self, name, meta_file, sig_file):
        """Returns the signer's fingerprint, or "" for an unsigned artifact that is allowed. Raises StoreError-like
        SignatureError otherwise (a bad signature is never tolerated, even when signatures are not required)."""
        if sig_file is None or meta_file is None:
            if self.require:
                raise SignatureError(f"{name}: unsigned artifact refused (artifacts.signing.require)")
            return ""
        with tempfile.TemporaryDirectory(prefix="bh-sig-") as tmp:
            keys = _trusted_keys(self.keys, self.fingerprints, tmp)
            try:
                return verify_gpg(meta_file, sig_file, keys, self.fingerprints)
            except SignatureError as exc:
                raise SignatureError(f"{name}: {exc}") from exc
