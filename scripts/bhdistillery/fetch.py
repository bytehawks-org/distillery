"""Download with SHA-256 verification and safe tarball/zip extraction."""

import hashlib
import os
import shutil
import stat
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

CHUNK = 1024 * 256


class FetchError(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, dest, expected_sha256="", allow_unverified=False, log=print):
    """Download url to dest, verify the checksum, return the actual SHA-256.

    A cached file with the right checksum is reused. A missing checksum is an error unless
    allow_unverified is set (then the computed value is printed so it can be pinned).
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and expected_sha256 and sha256_file(dest) == expected_sha256:
        log(f"cache hit: {dest.name}")
        return expected_sha256

    if not expected_sha256 and not allow_unverified:
        raise FetchError(
            f"no sha256 pinned for {url}: refusing to build an unverified source "
            "(run `autobuilder.py checksum` and pin it, or pass --allow-unverified)")

    log(f"downloading {url}")
    partial = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(partial, "wb") as out:
            for chunk in iter(lambda: response.read(CHUNK), b""):
                digest.update(chunk)
                out.write(chunk)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise FetchError(f"download failed: {url}: {exc}") from exc

    actual = digest.hexdigest()
    if expected_sha256 and actual != expected_sha256:
        partial.unlink(missing_ok=True)
        raise FetchError(f"SHA-256 mismatch for {url}\n  expected {expected_sha256}\n  actual   {actual}")

    if not expected_sha256:
        log(f"WARNING unverified source, sha256 = {actual}")

    os.replace(partial, dest)
    return actual


def _safe_member_name(name):
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def hash_file(path, algorithm):
    """Hex digest of a file with any hashlib algorithm (upstreams publish sha256 or sha512)."""
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_link(name, linkname, symbolic):
    target = PurePosixPath(linkname)
    anchor = PurePosixPath(name).parent if symbolic else PurePosixPath(".")
    resolved = os.path.normpath(str(anchor / target))
    if target.is_absolute() or resolved == ".." or resolved.startswith("../"):
        raise FetchError(f"unsafe link in archive: {name} -> {linkname}")


def extract(archive, dest, strip_components=1):
    """Extract a tarball or a zip into a fresh directory, dropping leading path components.

    Rejects absolute paths, parent traversal and links pointing outside the tree.
    """
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    if zipfile.is_zipfile(archive):
        _extract_zip(archive, dest, strip_components)
    else:
        _extract_tar(archive, dest, strip_components)


def _extract_tar(tarball, dest, strip_components):
    try:
        with tarfile.open(tarball) as archive:
            for member in archive.getmembers():
                if not _safe_member_name(member.name):
                    raise FetchError(f"unsafe path in archive: {member.name}")
                parts = PurePosixPath(member.name).parts[strip_components:]
                if not parts:
                    continue
                member.name = "/".join(parts)
                if member.issym() or member.islnk():
                    _safe_link(member.name, member.linkname, member.issym())
                if hasattr(tarfile, "data_filter"):
                    archive.extract(member, dest, filter="data")
                else:
                    archive.extract(member, dest)
    except tarfile.TarError as exc:
        raise FetchError(f"cannot extract {tarball}: {exc}") from exc


def _extract_zip(path, dest, strip_components):
    """Zip has no execute bit of its own: the Unix mode stored in `external_attr` is restored
    (files only; setuid/setgid/sticky are dropped)."""
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not _safe_member_name(info.filename):
                    raise FetchError(f"unsafe path in archive: {info.filename}")
                parts = PurePosixPath(info.filename).parts[strip_components:]
                if not parts:
                    continue
                name = "/".join(parts)
                target = dest / name
                mode = info.external_attr >> 16
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if stat.S_ISLNK(mode):
                    linkname = archive.read(info).decode()
                    _safe_link(name, linkname, True)
                    os.symlink(linkname, target)
                    continue
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                    raise FetchError(f"unsupported entry type in archive: {info.filename}")
                with archive.open(info) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out, CHUNK)
                os.chmod(target, (mode & 0o777) or 0o644)
    except zipfile.BadZipFile as exc:
        raise FetchError(f"cannot extract {path}: {exc}") from exc
