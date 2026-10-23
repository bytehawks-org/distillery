# Roadmap

Status legend: ✅ done · 🚧 partial · ⬜ planned. Shared with
[Shaker's roadmap](https://github.com/bytehawks-org/shaker/blob/main/ROADMAP.md); **(contract)** = needs a
matching change in the bundle schema / Shaker.

## Where we are (v0.3.0)

| Phase | Scope | State |
|---|---|---|
| 1 | Catalog + strict templates + dry-run orchestrator | ✅ (0.1) |
| 2 | `runtime.env` → `rosetta.map`, `/opt/bytehawks` layout | ✅ (0.2) |
| 3 | **Executable builds**: fetch, SHA-256, safe extract, stages, logs | ✅ |
| 3 | **Dependencies**: `depends_on`, ordering, cycles, build environment | ✅ |
| 3 | **Recipe hash** (Merkle) and idempotent builds | ✅ |
| 3 | **Bundle contract v1**: `runtime.spec`, `manifest.json/env`, assets | ✅ |
| 5 | Real chain built end to end: openssl → zlib → pcre2 → nginx 1.28 | ✅ |

## 0.4 - supply chain & provenance

| Item | Detail |
|---|---|
| ⬜ SBOM | emit CycloneDX (and SPDX) per unit: components = source + dependencies with hashes; ship as `sbom.cdx.json` in the bundle (**contract**: Shaker `verify` can report it) |
| ⬜ Source signature verification | per variant `signature:` (URL + key/fingerprint) checked with `gpg`/`minisign`/`cosign` before extraction |
| ⬜ Installed-file inventory | after `install`, `files.sha256` (path, mode, hash) of the prefix; Shaker `verify --files` detects tampering |
| ⬜ Vulnerability gate | optional `grype`/`trivy` run on SBOM in CI; fail on severity threshold |
| ⬜ Reproducible-build check | `build --repro`: build twice in clean dirs with `SOURCE_DATE_EPOCH`, compare file hashes |
| ⬜ Update watcher | `autobuilder.py outdated`: query upstream releases, propose `full_version` + `sha256` bumps (PR-ready diff) |
| ⬜ Variant lifecycle | `eol:` dates per variant, warning in `plan`/`list` |

## 0.5 - build engine

| Item | Detail |
|---|---|
| ⬜ Multi-arch | `arch` matrix (amd64, arm64): per-arch configure targets (replace hard-coded `linux-x86_64`), per-arch checksums only when sources differ |
| ⬜ Sandboxed builds | run stages in `bwrap`/`unshare`/container: network-less after fetch, read-only source |
| ⬜ Parallel independent units | schedule the DAG with `--max-units`, per-unit logs |
| ⬜ Remote artifact cache | content-addressed by recipe hash: `pull` before build, `push` after (S3/OCI/file) - turns CI into incremental builds |
| ⬜ Patches | `patches:` list (checksummed) applied in `pre` with `patch -p1`, part of the recipe hash |
| ⬜ git sources | `source: {git, rev}` with commit pinning; submodules |
| ⬜ Non-tarball sources | zip, zst, xz explicit; local directories for development |
| ⬜ `check` policy | run the test-suite for release builds by default |
| ⬜ Toolchain pinning | record compiler/libc versions in the manifest; optional builder image |

## 0.6 - delivery

| Item | Detail |
|---|---|
| ⬜ `image` | generate a multi-stage Dockerfile / OCI image: builder stage, runtime stage (distroless/slim) with Shaker as ENTRYPOINT, labels from the manifest (version, recipe, source hashes) |
| ⬜ `package` | produce rpm / deb / apk (nfpm) from the staged tree + bundle so legacy hosts install with their native package manager and Shaker `install` runs in post-install |
| ⬜ `k8s` | Helm/Kustomize skeleton from `rosetta.map` and `runtime.spec` (**contract**) |
| ⬜ Release manifest | one signed index of all bundles/versions of a release, consumed by Shaker for multi-package `up` |
| ⬜ `docs <pkg>` | generate the parameter reference page from `runtime.env` |

## Catalog

| Package | State |
|---|---|
| openssl 3.0.18 / 3.5.4 / 3.6.0 | ✅ (3.5.4 built; others pinned, not yet built) |
| zlib 1.3.1, pcre2 10.43 | ✅ built |
| nginx 1.28.0 | ✅ built, runtime complete, run through Shaker |
| postgresql, valkey, haproxy, mariadb, php-fpm, nodejs, python | ⬜ candidates (each needs a runtime contract and an init story) |
| musl toolchain / fully static variants | ⬜ idea for scratch/distroless images |

## 1.0 criteria

* bundle schema 1 frozen; versioned negotiation; compatibility tests with the previous Shaker release;
* signature + SBOM + reproducibility checks in the default release pipeline;
* multi-arch builds with an artifact cache;
* ≥ 5 packages built and run in containers and on VMs through Shaker;
* documented process for CVE-driven rebuilds (bump → `plan` → rebuild → publish).

## Known gaps today (honest list)

* Only `linux-x86_64` is encoded for openssl; no cross-compilation.
* Build commands are unsandboxed trusted shell.
* No signature/SBOM; `checksum` trusts the download it just made.
* openssl 3.0.18 and 3.6.0 variants have never been built here.
* `file://` and `https://` only; no mirrors/fallback URLs, no retries, no resume.
* Units build sequentially; no remote cache.
* Hook scripts shipped in `assets/` must be copied by a `post` stage (no `hooks:` file copier yet).
