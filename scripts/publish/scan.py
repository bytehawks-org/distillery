"""
Discover built artifacts in a dist directory.
"""

import json
from pathlib import Path
from typing import List, Tuple

from scripts.build.artifact import ArtifactRef


def scan_dist(dist: Path) -> List[Tuple[ArtifactRef, List[Path]]]:
    """(ref, files) for every ``*.manifest.json`` under ``dist`` with its tarball present."""
    out = []
    for manifest in sorted(Path(dist).rglob("*.manifest.json")):
        data = json.loads(manifest.read_text())
        ref = ArtifactRef(data["name"], data["version"], data["variant"],
                          data.get("arch", "amd64"), data.get("libc", "musl"))
        folder = manifest.parent
        if not (folder / ref.filename).exists():
            continue
        files = [folder / n for n in ref.all_files if (folder / n).exists()]
        out.append((ref, files))
    return out
