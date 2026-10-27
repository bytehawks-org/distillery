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
