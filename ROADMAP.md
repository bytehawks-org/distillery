# Roadmap

Status legend: ✅ done · 🔨 in progress · 🔜 planned · 💡 idea. Dates are intentions, not commitments.

## Now — make the pipeline real (v0.2)

| Item | Status |
|---|---|
| Build the builder images (`builder-images.yml`) on all four Alpine variants and fix Dockerfile issues | 🔜 |
| First end-to-end run on ARC/K3s: `plan → libraries → applications → Nexus + GitHub` | 🔜 |
| Pin `sha256` (`lock`) for every experimental recipe and promote php, httpd, postgresql, libxml2, apr, apr-util to **stable** after a green CI run | 🔜 |
| Validate MariaDB and the OpenJDK bundle on the real builder | 🔜 |
| Tag Distillery itself (`v0.2.0`), publish release notes from CHANGELOG | 🔜 |
| Consumer smoke tests on glibc hosts (Debian, Rocky, Ubuntu) and `scratch`/`busybox` containers in CI | 🔜 |

## Next — coverage and trust (v0.3)

| Item | Status |
|---|---|
| **Artifact signing** with cosign (keyless, GitHub OIDC) + `generate_signature`; verification in `fetch` | 🔜 |
| **arm64** builds (builder images + recipes + `linux-arm64` artifacts, native ARM runner or QEMU) | 🔜 |
| Provenance attestation (SLSA) for published artifacts | 💡 |
| PHP: gd (freetype/libpng/libjpeg/libwebp), zip (libzip), intl (ICU), sodium, pgsql (libpq), imagick; PHP extension matrix per variant | 🔜 |
| PostgreSQL: readline/libedit, ICU, lz4/zstd, PostGIS as separate recipe | 🔜 |
| nginx: HTTP/3, headers-more, brotli (as optional modules) | 💡 |
| Apache httpd: HTTP/2 (nghttp2), optional DSO flavour (relocatable) | 💡 |
| Redis/Valkey, HAProxy, Caddy-less proxies, Node.js, Python (relocatable), Go/Rust toolchains | 💡 |
| OpenJDK built from source for `stream` (JEP-based musl port) instead of Temurin repack | 💡 |
| Patch management: `packages/patches/` conventions, automatic patch refresh checks | 🔜 |

## Later — operations

| Item | Status |
|---|---|
| Automatic bump PRs from `upstream-watch` (edit version, `lock`, open PR) | 🔜 |
| Security feed: map SBOMs to CVEs (Grype/OSV) and rebuild affected packages | 💡 |
| Revision/rebuild counter (`-r1`) so a changed recipe can republish a version without deleting artifacts | 🔜 |
| S3-compatible repository uploader (schema already accepts it) | 💡 |
| Artifact index (`index.json`) per repository for discovery and a small consumer CLI (`distillery fetch nginx@stable`) | 💡 |
| OCI packaging: publish artifacts as OCI images/artifacts to Harbor/GHCR (`FROM bytehawks/nginx`) | 💡 |
| Helm chart / Kubernetes manifests for the building blocks (consumers of the artifacts) | 💡 |
| Build cache: share source downloads and deps between runners (PVC or Nexus proxy) | 🔜 |
| Retention policy for old variants (`support_until`) and end-of-life reporting | 💡 |
| Metrics: build duration, success rate, size per package (Prometheus pushgateway) | 💡 |

## Done (see [CHANGELOG](CHANGELOG.md))

- ✅ Recipe-driven build engine with dependency resolution and static gate
- ✅ Relocatable runtime bundler (validated with PostgreSQL)
- ✅ Reproducible archives, manifest and CycloneDX SBOM
- ✅ Nexus + GitHub Releases mirroring, immutable uploads
- ✅ CI workflows and K3s runner definition
- ✅ Documentation set and test suite

## Non-goals

- Distro packages (deb/rpm/apk) — the artifacts are tarballs/OCI by design.
- Supporting glibc-only toolchains for the builds: musl is what makes `static` verifiable.
- A general-purpose build system: recipes stay small shell scripts around upstream build systems.
