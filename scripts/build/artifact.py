"""
Artifact naming and remote layout shared by builder and publishers.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class ArtifactRef:
    package: str
    version: str
    variant: str
    arch: str = "amd64"
    libc: str = "musl"

    @property
    def major_minor(self) -> str:
        return ".".join(self.version.split(".")[:2])

    @property
    def stem(self) -> str:
        return f"{self.package}-{self.version}-{self.variant}-linux-{self.arch}"

    @property
    def filename(self) -> str:
        return f"{self.stem}.tar.gz"

    @property
    def sidecars(self) -> List[str]:
        return [f"{self.filename}.sha256", f"{self.stem}.manifest.json", f"{self.stem}.sbom.cdx.json"]

    @property
    def all_files(self) -> List[str]:
        return [self.filename] + self.sidecars

    def local_dir(self, root: Path) -> Path:
        return Path(root) / self.package / self.version

    def remote_dir(self, path_template: str) -> str:
        return path_template.format(
            package=self.package,
            major_minor=self.major_minor,
            full_version=self.version,
            version=self.version,
            variant=self.variant,
        ).strip("/")

    def remote_path(self, path_template: str, filename: str) -> str:
        return f"{self.remote_dir(path_template)}/{filename}"
