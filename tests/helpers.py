import io
import hashlib
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bhdistillery.model import Catalog  # noqa: E402


def make_tarball(path, files, top="demo-1.0"):
    """files: {relative name: text}; every entry lives below `top`/ like a release tarball."""
    with tarfile.open(path, "w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(data))
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


CONFIG = {"config": {
    "root": "/opt/bytehawks", "link": "/opt/bh", "core_path": "/opt/bytehawks/core",
    "default_download_path": "/opt/bytehawks/var/cache/distillery",
    "default_src_path": "/opt/bytehawks/var/build",
    "log_path": "/opt/bytehawks/var/log/distillery",
    "libraries": {"default_prefix": "/opt/bytehawks/deps"},
    "applications": {"default_prefix": "/opt/bytehawks/{{ name }}"},
}}


def package(url, sha256, **extra):
    base = {
        "variant": [{"name": "stable", "major_version": 1, "patch_version": 0, "full_version": "1.0", "sha256": sha256}],
        "git": {"repo": "file://x", "release_download_url_template": url},
        "commands": {
            "configure": "echo configured > configured.txt",
            "make": "echo built > built.txt",
            "install": 'mkdir -p {{ prefix }}/bin && cp built.txt {{ prefix }}/bin/demo.txt',
        },
        "tags": ["application"],
    }
    base.update(extra)
    return base


def catalog(packages, root):
    from bhdistillery.model import apply_root
    return Catalog(apply_root(CONFIG, root), {"package": packages}, jobs=1)


class TempDir:
    def __enter__(self):
        self._t = tempfile.TemporaryDirectory()
        return Path(self._t.name)

    def __exit__(self, *exc):
        self._t.cleanup()
