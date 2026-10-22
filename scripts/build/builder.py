"""
Build orchestration: dependencies -> source -> patches -> steps -> verify ->
package -> install (libraries) -> smoke tests.
"""

import logging
import os
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from scripts.build.artifact import ArtifactRef
from scripts.build.executor import StepRunner
from scripts.build.packager import PackagedArtifact, extract_artifact, package_artifact
from scripts.build.verify import find_dynamic_executables, find_shared_objects, iter_files, read_elf
from scripts.config.models import DistilleryConfig
from scripts.config.template_engine import DistilleryTemplateEngine
from scripts.core.exceptions import BuildError, PublishError
from scripts.core.util import download, sha256_file
from scripts.recipes import BuildTarget, LinkMode, PackageKind, RecipeSet, Status

logger = logging.getLogger(__name__)

FALLBACK_EPOCH = 315532800  # 1980-01-01


def source_date_epoch(repo_root: Path) -> int:
    """SOURCE_DATE_EPOCH: env, else HEAD commit time of the recipes repo, else 1980."""
    if os.environ.get("SOURCE_DATE_EPOCH"):
        return int(os.environ["SOURCE_DATE_EPOCH"])
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=repo_root,
                             capture_output=True, text=True, check=True).stdout.strip()
        return int(out)
    except Exception:
        return FALLBACK_EPOCH


@dataclass
class BuildResult:
    target: BuildTarget
    artifact: PackagedArtifact
    built: bool = True  # False when installed from a repository


@dataclass
class Builder:
    config: DistilleryConfig
    recipes: RecipeSet
    output_dir: Path
    repo_root: Path
    patches_dir: Optional[Path] = None
    arch: str = "amd64"
    fetcher: Optional[object] = None   # RepositoryManager-like: .fetch(ref, dest_dir) -> bool
    results: List[BuildResult] = field(default_factory=list)

    def __post_init__(self):
        self.engine = DistilleryTemplateEngine(max_passes=5, strict=True)
        self.output_dir = Path(self.output_dir)
        self.patches_dir = Path(self.patches_dir or self.repo_root / "packages" / "patches")
        self.epoch = source_date_epoch(self.repo_root)
        rt = self.config.runtime
        self.runner = StepRunner(use_fakeroot=bool(rt and rt.fakeroot.enabled))
        self._installed: Dict[str, str] = {}   # dep key -> version, per variant

    # ------------------------------------------------------------------ paths

    def deps_prefix(self, variant: str) -> Path:
        return Path(self.config.path.generated.libraries) / variant

    def prefix_for(self, target: BuildTarget) -> str:
        r = target.recipe
        if r.prefix:
            return self.engine.render(r.prefix, {"name": r.name, "variant": target.variant})
        if r.kind == PackageKind.LIBRARY:
            return str(self.deps_prefix(target.variant))
        tmpl = self.config.path.generated.applications
        return self.engine.render(tmpl, {"package": {"name": r.name}, "variant": target.variant})

    def work_dir(self, target: BuildTarget) -> Path:
        return Path(self.config.path.build) / f"{target.recipe.name}-{target.version.version}-{target.variant}"

    def jobs(self) -> int:
        j = self.config.defaults.build.jobs
        return (os.cpu_count() or 2) if j == "auto" else int(j)

    # ------------------------------------------------------------- public API

    def build(self, target: BuildTarget, *, with_deps: bool = True) -> BuildResult:
        """Build ``target`` (and, first, its dependencies)."""
        if with_deps:
            for dep in self.recipes.dependencies(target.recipe.name):
                self._ensure_dependency(self.recipes.target(dep, target.variant))
        return self._build_one(target)

    # ----------------------------------------------------------- dependencies

    def _ensure_dependency(self, dep: BuildTarget) -> None:
        key = f"{dep.key}"
        if key in self._installed:
            return
        marker = self.deps_prefix(dep.variant) / ".distillery" / f"{dep.recipe.name}-{dep.version.version}"
        if marker.exists():
            self._installed[key] = dep.version.version
            return

        ref = self._ref(dep)
        local = ref.local_dir(self.output_dir) / ref.filename
        if not local.exists() and self.fetcher is not None:
            try:
                if self.fetcher.fetch(ref, ref.local_dir(self.output_dir)):
                    logger.info("Fetched %s from repository", ref.filename)
            except PublishError as e:
                logger.warning("Fetch of %s failed (%s); building from source", ref.filename, e)

        if local.exists() and self._checksum_ok(local):
            self._install_library(dep, local)
        else:
            self._build_one(dep)

    @staticmethod
    def _checksum_ok(tarball: Path) -> bool:
        sidecar = tarball.with_name(tarball.name + ".sha256")
        if not sidecar.exists():
            return False
        return sidecar.read_text().split()[0] == sha256_file(tarball)

    def _install_library(self, dep: BuildTarget, tarball: Path) -> None:
        prefix = self.deps_prefix(dep.variant)
        extract_artifact(tarball, prefix)
        marker_dir = prefix / ".distillery"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / f"{dep.recipe.name}-{dep.version.version}").write_text(sha256_file(tarball) + "\n")
        self._installed[dep.key] = dep.version.version

    # ------------------------------------------------------------ single build

    def _ref(self, t: BuildTarget) -> ArtifactRef:
        return ArtifactRef(t.recipe.name, t.version.version, t.variant, self.arch)

    def _context(self, target: BuildTarget, work: Path, srcdir: Path, stage: Path, prefix: str) -> Dict:
        r, v = target.recipe, target.version
        deps = {d: self.recipes.target(d, target.variant).version.version
                for d in self.recipes.dependencies(r.name)}
        return {
            "name": r.name, "version": v.version, "major_minor": v.major_minor,
            "variant": target.variant, "arch": self.arch, "jobs": self.jobs(),
            "prefix": prefix, "destdir": str(stage), "deps_prefix": str(self.deps_prefix(target.variant)),
            "srcdir": str(srcdir), "dep": deps,
        }

    def _env(self, target: BuildTarget, ctx: Dict) -> Dict[str, str]:
        deps = ctx["deps_prefix"]
        base = dict(os.environ)
        env = dict(base)
        for flag in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
            env.setdefault(flag, "")   # recipes run with `set -u`
        env.update(self.config.defaults.build.env)

        cppflags = f"-I{deps}/include"
        ldflags = f"-L{deps}/lib {env.get('LDFLAGS', '')}".strip()
        pc = f"{deps}/lib/pkgconfig:{deps}/lib64/pkgconfig:{deps}/share/pkgconfig"
        env.update({
            "DESTDIR": ctx["destdir"], "PREFIX": ctx["prefix"], "DEPS": deps,
            "SRCDIR": ctx["srcdir"], "JOBS": str(ctx["jobs"]),
            "PKG_NAME": ctx["name"], "PKG_VERSION": ctx["version"],
            "VARIANT": ctx["variant"], "ARCH": ctx["arch"],
            "CPPFLAGS": f"{cppflags} {env.get('CPPFLAGS', '')}".strip(),
            "LDFLAGS": ldflags,
            "PKG_CONFIG_PATH": pc, "PKG_CONFIG_LIBDIR": pc,
            "PATH": f"{deps}/bin:{env.get('PATH', '')}",
            "SOURCE_DATE_EPOCH": str(self.epoch),
            "LC_ALL": "C", "TZ": "UTC",
        })
        for k, v in target.recipe.env.items():
            env[k] = self.engine.render(v, ctx)
        return env

    def _build_one(self, target: BuildTarget) -> BuildResult:
        r, v = target.recipe, target.version
        logger.info("=== Building %s (%s) ===", target.key, r.kind.value)

        work = self.work_dir(target)
        if work.exists():
            shutil.rmtree(work)
        stage = work / "stage"
        srcdir = work / "src"
        stage.mkdir(parents=True)
        prefix = self.prefix_for(target)
        ctx = self._context(target, work, srcdir, stage, prefix)

        archive = self._fetch_source(target, ctx)
        self._extract(archive, srcdir, r.source.strip_components)
        self._apply_patches(target, srcdir)

        env = self._env(target, ctx)
        for step in r.steps:
            script = self.engine.render(step.run, ctx)
            self.runner.run(f"{r.name}:{step.name}", script, env, srcdir, fakeroot=step.fakeroot)

        content_root = stage / prefix.lstrip("/")
        if not content_root.is_dir():
            raise BuildError(f"{target.key}: nothing installed under DESTDIR{prefix} "
                             f"(did the install step honour DESTDIR?)")

        self._cleanup_tree(content_root)
        self._verify(target, content_root)
        self._strip(content_root)

        artifact = package_artifact(
            self._ref(target), content_root, self.output_dir, self._manifest(target, prefix, archive, ctx),
            epoch=self.epoch,
            sbom=self.config.defaults.packaging.generate_sbom,
        )

        self._smoke_tests(target, content_root, env, ctx)

        if r.kind == PackageKind.LIBRARY:
            self._install_library(target, artifact.tarball)

        if self.config.defaults.build.cleanup_on_success:
            shutil.rmtree(work, ignore_errors=True)

        result = BuildResult(target, artifact)
        self.results.append(result)
        logger.info("Artifact: %s (sha256 %s)", artifact.tarball, artifact.sha256)
        return result

    # ----------------------------------------------------------------- source

    def _fetch_source(self, target: BuildTarget, ctx: Dict) -> Path:
        r, v = target.recipe, target.version
        url = self.engine.render(v.url or r.source.url, ctx)
        dest = Path(self.config.path.downloads) / url.rsplit("/", 1)[-1]

        ctx["source_url"] = url
        if dest.exists() and v.sha256 and sha256_file(dest) == v.sha256:
            logger.info("Source cached: %s", dest.name)
            ctx["source_sha256"] = v.sha256
            return dest

        digest = download(url, dest, expected_sha256=v.sha256)
        if not v.sha256:
            if r.status == Status.STABLE:
                dest.unlink(missing_ok=True)
                raise BuildError(f"{target.key}: stable package without sha256")
            logger.warning("%s: no sha256 pinned; downloaded %s (sha256=%s). "
                           "Run `distillery lock %s`.", target.key, dest.name, digest, r.name)
        ctx["source_sha256"] = digest
        return dest

    @staticmethod
    def _extract(archive: Path, dest: Path, strip: int) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tar:
            members = []
            for m in tar.getmembers():
                parts = Path(m.name).parts
                if len(parts) <= strip:
                    continue
                m.name = "/".join(parts[strip:])
                if m.islnk():
                    lp = Path(m.linkname).parts
                    m.linkname = "/".join(lp[strip:])
                members.append(m)
            tar.extractall(dest, members=members, filter="tar")

    def _apply_patches(self, target: BuildTarget, srcdir: Path) -> None:
        r, v = target.recipe, target.version
        for patch in r.patches:
            w = patch.when
            if (w.variant and w.variant != target.variant) or (w.version and w.version != v.version):
                continue
            if patch.file:
                pfile = self.patches_dir / r.name / patch.file
                if not pfile.exists():
                    raise BuildError(f"{r.name}: patch file not found: {pfile}")
            else:
                pfile = Path(self.config.path.downloads) / patch.url.rsplit("/", 1)[-1]
                download(patch.url, pfile, expected_sha256=patch.sha256)
            logger.info("Applying patch %s%s", pfile.name, f" ({patch.comment})" if patch.comment else "")
            proc = subprocess.run(["patch", f"-p{patch.strip}", "-i", str(pfile)], cwd=srcdir)
            if proc.returncode != 0:
                raise BuildError(f"{r.name}: patch {pfile.name} failed")

    # --------------------------------------------------------- post-processing

    @staticmethod
    def _cleanup_tree(root: Path) -> None:
        for p in list(root.rglob("*.la")):
            p.unlink()
        for name in ("info", "man", "doc"):
            for d in (root / name, root / "share" / name):
                if d.is_dir():
                    shutil.rmtree(d)

    def _verify(self, target: BuildTarget, root: Path) -> None:
        r = target.recipe
        shared = find_shared_objects(root)
        if r.link == LinkMode.STATIC:
            offenders = find_dynamic_executables(root)
            if offenders:
                raise BuildError(
                    f"{target.key}: link=static but these executables need a dynamic loader:\n  "
                    + "\n  ".join(offenders))
            if r.kind == PackageKind.LIBRARY and shared:
                logger.warning("%s: shared objects shipped by a static library: %s", target.key, shared)

    @staticmethod
    def _strip(root: Path) -> None:
        strip = shutil.which("strip")
        if not strip:
            return
        for p in iter_files(root):
            info = read_elf(p)
            if info is None or not os.access(p, os.W_OK):
                continue
            flag = "--strip-unneeded" if os.access(p, os.X_OK) else "--strip-debug"
            subprocess.run([strip, flag, str(p)], stderr=subprocess.DEVNULL)

    def _manifest(self, target: BuildTarget, prefix: str, archive: Path, ctx: Dict) -> Dict:
        r, v = target.recipe, target.version
        variant_cfg = self.config.get_variant(target.variant)
        return {
            "schema": "distillery-manifest/1",
            "name": r.name, "version": v.version, "variant": target.variant,
            "arch": self.arch, "libc": "musl", "kind": r.kind.value, "link": r.link.value,
            "status": r.status.value, "prefix": prefix,
            "license": r.license, "website": r.website, "summary": r.summary,
            "depends_on": ctx["dep"],
            "source": {"url": ctx.get("source_url"), "sha256": v.sha256 or ctx.get("source_sha256"),
                       "file": archive.name},
            "builder": {
                "image": variant_cfg.get_resolved_image() if variant_cfg else None,
                "alpine": variant_cfg.metadata.alpine_version if variant_cfg else None,
                "musl": variant_cfg.metadata.musl_version if variant_cfg else None,
            },
        }

    def _smoke_tests(self, target: BuildTarget, root: Path, env: Dict[str, str], ctx: Dict) -> None:
        env = dict(env, ARTIFACT_ROOT=str(root))
        for i, t in enumerate(target.recipe.smoke_tests, 1):
            script = self.engine.render(t.run, ctx)
            self.runner.run(f"{target.recipe.name}:smoke-{i}", script, env, root)
