"""
Deterministic packaging: tar.gz + sha256 + manifest + CycloneDX SBOM.
"""

import gzip
import io
import json
import os
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts.build.artifact import ArtifactRef
from scripts.core.util import sha256_file


@dataclass
class PackagedArtifact:
    ref: ArtifactRef
    tarball: Path
    sha256: str
    sha256_file: Path
    manifest: Path
    sbom: Optional[Path] = None

    @property
    def files(self) -> List[Path]:
        return [p for p in (self.tarball, self.sha256_file, self.manifest, self.sbom) if p]


def _entries(root: Path):
    """Sorted (relative_path, absolute_path) so the archive order is stable."""
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(dirs + files):
            p = Path(dirpath) / name
            yield p.relative_to(root), p


def create_tarball(content_root: Path, top_dir: str, dest: Path, epoch: int,
                   meta: Dict[str, Any]) -> None:
    """Reproducible tar.gz: sorted, root:root, fixed mtimes, gzip mtime=0."""

    def norm(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mtime = epoch
        return info

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                tar.addfile(_dir_info(top_dir, epoch))

                meta_bytes = json.dumps(meta, indent=2, sort_keys=True).encode() + b"\n"
                dinfo = _dir_info(f"{top_dir}/.distillery", epoch)
                tar.addfile(dinfo)
                finfo = tarfile.TarInfo(f"{top_dir}/.distillery/package.json")
                finfo.size = len(meta_bytes)
                finfo.mode = 0o644
                tar.addfile(norm(finfo), io.BytesIO(meta_bytes))

                for rel, path in _entries(content_root):
                    info = tar.gettarinfo(str(path), arcname=f"{top_dir}/{rel.as_posix()}")
                    if info.isreg():
                        with open(path, "rb") as fh:
                            tar.addfile(norm(info), fh)
                    else:
                        tar.addfile(norm(info))


def _dir_info(name: str, epoch: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name.rstrip("/") + "/")
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = epoch
    return info


def file_inventory(root: Path) -> List[Dict[str, Any]]:
    out = []
    for rel, path in _entries(root):
        if path.is_symlink():
            out.append({"path": rel.as_posix(), "type": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            out.append({"path": rel.as_posix(), "type": "file",
                        "size": path.stat().st_size, "sha256": sha256_file(path)})
    return out


def build_sbom(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Minimal CycloneDX 1.5 document: the package, its source and build deps."""
    purl = f"pkg:generic/{manifest['name']}@{manifest['version']}"
    component: Dict[str, Any] = {
        "type": "library" if manifest["kind"] == "library" else "application",
        "bom-ref": purl,
        "name": manifest["name"],
        "version": manifest["version"],
        "purl": purl,
        "licenses": [{"license": {"name": manifest["license"]}}],
        "externalReferences": [{"type": "website", "url": manifest["website"]}],
    }
    src = manifest.get("source") or {}
    if src.get("url"):
        component["externalReferences"].append(
            {"type": "distribution", "url": src["url"],
             **({"hashes": [{"alg": "SHA-256", "content": src["sha256"]}]} if src.get("sha256") else {})})

    deps = manifest.get("depends_on", {})
    components = [{
        "type": "library", "bom-ref": f"pkg:generic/{n}@{v}", "name": n, "version": v,
        "purl": f"pkg:generic/{n}@{v}", "scope": "required",
    } for n, v in sorted(deps.items())]

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": manifest["built_at"],
            "component": component,
            "tools": {"components": [{"type": "application", "name": "bytehawks-distillery"}]},
        },
        "components": components,
        "dependencies": [{"ref": purl, "dependsOn": [c["bom-ref"] for c in components]}],
    }


def package_artifact(ref: ArtifactRef, content_root: Path, out_root: Path,
                     manifest: Dict[str, Any], *, epoch: int,
                     sbom: bool = True) -> PackagedArtifact:
    out_dir = ref.local_dir(out_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    tarball = out_dir / ref.filename

    manifest = dict(manifest)
    manifest["built_at"] = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["files"] = file_inventory(content_root)

    inner = {k: manifest[k] for k in ("name", "version", "variant", "arch", "libc", "kind", "link",
                                       "prefix", "depends_on", "built_at") if k in manifest}
    create_tarball(content_root, f"{ref.package}-{ref.version}", tarball, epoch, inner)

    digest = sha256_file(tarball)
    manifest["artifact"] = {"filename": ref.filename, "sha256": digest, "size": tarball.stat().st_size}

    sha_path = out_dir / f"{ref.filename}.sha256"
    sha_path.write_text(f"{digest}  {ref.filename}\n")

    manifest_path = out_dir / f"{ref.stem}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    sbom_path = None
    if sbom:
        sbom_path = out_dir / f"{ref.stem}.sbom.cdx.json"
        sbom_path.write_text(json.dumps(build_sbom(manifest), indent=2, sort_keys=True) + "\n")

    return PackagedArtifact(ref, tarball, digest, sha_path, manifest_path, sbom_path)


def extract_artifact(tarball: Path, dest: Path) -> None:
    """Extract ``<top>/...`` into ``dest`` dropping the top directory and metadata."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tar:
        members = []
        for m in tar.getmembers():
            parts = Path(m.name).parts
            if len(parts) < 2 or parts[1] == ".distillery":
                continue
            m.name = "/".join(parts[1:])
            members.append(m)
        tar.extractall(dest, members=members, filter="tar")
