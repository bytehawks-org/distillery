# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Distillery is not tagged yet; entries are
grouped under `[Unreleased]` until the first release (see [ROADMAP.md](ROADMAP.md)).

## [Unreleased]

### Added
- **Build engine**: recipe-driven builds (`scripts/recipes`, `scripts/build`) with dependency resolution,
  fetch-or-build of library dependencies, patching, `DESTDIR` staging, smoke tests and optional `fakeroot`.
- **Static gate**: ELF-based verification that `link: static` packages contain no dynamic executables and
  that static applications ship no shared objects.
- **Runtime bundler** for `link: relocatable` packages (`bundle: true`): loader + libraries in `lib/bundled`,
  wrapped executables, runs from any directory on glibc and musl hosts.
- **Reproducible packaging**: deterministic `tar.gz`, `.sha256`, `.manifest.json` (file inventory, sources,
  builder) and CycloneDX 1.5 SBOM.
- **Publishing**: Nexus raw (hosted) and GitHub Releases clients; strategies `primary-only`,
  `primary-with-fallback`, `mirror-all`; immutable, idempotent uploads; dependency download from repositories.
- **CLI**: `validate`, `list`, `plan`, `build`, `publish`, `lock`, `check-updates`, `builder-matrix`, `image`.
- **CI/CD**: workflows `ci`, `builder-images`, `build-packages` (plan → libraries → applications, runs inside
  the builder image), `upstream-watch`.
- **Builder image** (`docker/builder/Dockerfile`): Alpine toolchain, Python 3.12 via uv, one image per variant.
- **K3s runner** setup: ARC scale set values and hook template (`deploy/arc/`).
- **Recipes**: zlib, pcre2, openssl, expat, oniguruma, curl, nginx (stable); libxml2, apr, apr-util, httpd,
  php (CLI+FPM, static OPcache), postgresql, mariadb, openjdk (experimental).
- **Upstream monitoring** with per-version `series` tracking.
- **Documentation**: README, `docs/` (architecture, CLI, config, recipes, linking model, artifacts, catalog,
  K3s runner, development, troubleshooting), ROADMAP, CHANGELOG.
- **Tests**: 32 pytest cases (recipes, builder, publishing, planner, bundler, CLI).
- `${VAR:-default}` and embedded `${VAR}` expansion in configuration values.
- Config options `defaults.build.executor|jobs|env`, `repository.strategy: mirror-all`, GitHub repository
  endpoint, `verify_tls` for Nexus.

### Changed
- Packages are now one YAML per package in `packages/` (replaces the single `packages.yaml`).
- `config.yaml`: repository primary is the in-cluster Nexus (`${NEXUS_URL}`), fallback is GitHub Releases
  (S3 uploader not implemented); builder image basename `distillery-builder`; toolchain flags (`-fPIC`,
  hardening, `-static`) defined centrally.
- `path.generated.applications` resolves everything except `{{ package.name }}` at load time.
- OpenSSL is built `no-atexit` and without zlib (static-linking constraints).
- `cli.py` is now the real CLI (it was a configuration smoke test).

### Fixed
- Duplicate `RepositoryConfig` class and missing imports in `scripts/config/models.py`.
- Recipe smoke tests no longer fail with exit 141 (SIGPIPE) under `pipefail`.
- libtool-built tools (pcre2, curl, libxml2) are linked fully static with `-all-static`.
- PHP: `--enable-pdo` required with `--disable-all`; OPcache can be built statically.

### Removed
- `packages/packages.yaml`, `packages/placeholder.txt`, `docs/template_packages.yaml`; `.idea/` is no longer tracked.
- Unused dependencies (`requests`, `python-dotenv`, `jsonschema`).

### Known issues
- Builder image and workflows have not run on a real Alpine/ARC environment yet.
- mariadb recipe untested; openjdk bundling untested.
- php/httpd/postgresql/libxml2/apr validated only with older upstream versions on a Debian musl toolchain.

## [0.1.0] — baseline

Initial scaffolding: configuration schema (`config.yaml`), pydantic models, template engine, configuration
loader and its reference documentation.
