"""
Small shared helpers: env expansion, hashing, downloads.
"""

import hashlib
import os
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Optional

from scripts.core.exceptions import BuildError

_ENV_RE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def expand_env(value: Any) -> Any:
    """
    Expand ``${VAR}`` and ``${VAR:-default}`` references.

    A string that is *entirely* one reference resolves to the env value, the
    default, or ``None`` when neither exists (so unset secrets stay ``None``).
    References embedded in a longer string resolve to ``""`` when unset.
    Like bash ``:-``, an empty variable counts as unset.
    """
    if not isinstance(value, str):
        return value

    whole = _ENV_RE.fullmatch(value)
    if whole:
        return os.environ.get(whole.group(1)) or whole.group(2)

    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1)) or m.group(2) or "", value)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, *, expected_sha256: Optional[str] = None,
             timeout: int = 120, retries: int = 3) -> str:
    """
    Download ``url`` to ``dest`` (atomically) and return its sha256.

    Supports http(s):// and file://. When ``expected_sha256`` is given a
    mismatch raises BuildError and nothing is left at ``dest``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Optional[Exception] = None

    for _ in range(max(1, retries)):
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".dl-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                req = urllib.request.Request(url, headers={"User-Agent": "bytehawks-distillery"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    shutil.copyfileobj(resp, out)
            digest = sha256_file(tmp)
            if expected_sha256 and digest != expected_sha256.lower():
                tmp.unlink(missing_ok=True)
                raise BuildError(
                    f"sha256 mismatch for {url}\n  expected: {expected_sha256}\n  actual:   {digest}"
                )
            tmp.replace(dest)
            return digest
        except BuildError:
            raise
        except Exception as e:  # network errors: retry
            last_error = e
            tmp.unlink(missing_ok=True)

    raise BuildError(f"Download failed for {url}: {last_error}")
