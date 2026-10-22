"""
Package recipe models (one YAML file per package under packages/).
"""

import re
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9._+-]*$")


class PackageKind(str, Enum):
    LIBRARY = "library"          # installed into the shared deps prefix, consumed by other recipes
    APPLICATION = "application"  # self-contained tree under apps/<name>


class LinkMode(str, Enum):
    STATIC = "static"            # every ELF executable is verified to have no dynamic loader
    RELOCATABLE = "relocatable"  # cannot be fully static (e.g. JVM): ships its own runtime libs


class Status(str, Enum):
    STABLE = "stable"            # built by default in CI
    EXPERIMENTAL = "experimental"  # built only on explicit request


class SourceSpec(BaseModel):
    type: Literal["tarball", "repack"] = "tarball"
    url: str = Field(..., description="Download URL template ({{ version }}, {{ name }}, ...)")
    archive_dir: Optional[str] = Field(
        None, description="Top-level directory of the archive (default: <name>-<version>)")
    strip_components: int = Field(default=1, ge=0)


class VersionSpec(BaseModel):
    version: str
    sha256: Optional[str] = Field(None, description="sha256 of the source archive")
    variants: List[str] = Field(..., min_length=1, description="Variant names, or ['all']")
    url: Optional[str] = Field(None, description="Overrides source.url for this version")
    vars: Dict[str, str] = Field(
        default_factory=dict, description="Extra per-version template variables ({{ vars.key }})")
    series: Optional[str] = Field(
        None, description="Version prefix tracked by check-updates (e.g. '1.28'); "
                          "default: any newer upstream release")

    @field_validator("sha256", mode="before")
    @classmethod
    def _stringify(cls, v):
        return None if v is None else str(v)

    @field_validator("sha256")
    @classmethod
    def _check_sha(cls, v):
        if v is not None and not _SHA256.match(v.lower()):
            raise ValueError("sha256 must be 64 hex characters")
        return v.lower() if v else v

    @property
    def major_minor(self) -> str:
        return ".".join(self.version.split(".")[:2])


class PatchWhen(BaseModel):
    variant: Optional[str] = None
    version: Optional[str] = None


class PatchSpec(BaseModel):
    file: Optional[str] = Field(None, description="Path relative to packages/patches/<name>/")
    url: Optional[str] = None
    sha256: Optional[str] = None
    strip: int = Field(default=1, ge=0)
    comment: Optional[str] = None
    when: PatchWhen = Field(default_factory=PatchWhen)

    @model_validator(mode="after")
    def _one_source(self):
        if bool(self.file) == bool(self.url):
            raise ValueError("patch needs exactly one of 'file' or 'url'")
        if self.url and not self.sha256:
            raise ValueError("remote patches require sha256")
        return self


class StepSpec(BaseModel):
    name: str
    run: str
    # Wrap the step in fakeroot (when enabled in config and installed)
    fakeroot: bool = False


class UpstreamSpec(BaseModel):
    """Where to look for new releases (used by `check-updates`)"""
    type: Literal["github-releases", "github-tags", "none"] = "none"
    repo: Optional[str] = None
    # Regex with ONE group extracting the version from the tag name
    tag_regex: str = r"^v?(\d+(?:\.\d+)*)$"


class SmokeTest(BaseModel):
    run: str
    description: Optional[str] = None


class Recipe(BaseModel):
    name: str
    kind: PackageKind
    summary: str
    license: str
    website: str
    status: Status = Status.STABLE
    link: LinkMode = LinkMode.STATIC
    bundle: bool = Field(
        default=False,
        description="relocatable only: ship the musl loader + shared libs and wrap dynamic executables")
    prefix: Optional[str] = Field(
        None, description="Install prefix override (default from config path.generated)")
    depends_on: List[str] = Field(default_factory=list)
    source: SourceSpec
    versions: List[VersionSpec] = Field(..., min_length=1)
    patches: List[PatchSpec] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    steps: List[StepSpec] = Field(..., min_length=1)
    smoke_tests: List[SmokeTest] = Field(default_factory=list)
    upstream: UpstreamSpec = Field(default_factory=UpstreamSpec)
    tags: List[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v):
        if not _NAME.match(v):
            raise ValueError("invalid package name")
        return v

    @model_validator(mode="after")
    def _check(self):
        seen = set()
        for step in self.steps:
            if step.name in seen:
                raise ValueError(f"duplicate step name: {step.name}")
            seen.add(step.name)
        if self.name in self.depends_on:
            raise ValueError("package cannot depend on itself")
        if self.kind == PackageKind.LIBRARY and self.link != LinkMode.STATIC:
            raise ValueError("libraries must be link: static")
        if self.bundle and self.link != LinkMode.RELOCATABLE:
            raise ValueError("bundle: true requires link: relocatable")
        return self


class RecipeFile(BaseModel):
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    schema_name: str = Field(default="distillery-package", alias="schema")
    package: Recipe

    model_config = {"populate_by_name": True}
