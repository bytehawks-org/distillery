# Roadmap

Status legend: ✅ done · 🚧 partial · ⬜ planned. Shared with
[Shaker's roadmap](https://github.com/bytehawks-org/shaker/blob/main/ROADMAP.md); **(contract)** = needs a
matching change in the bundle schema / Shaker.

## Where we are (v0.4.0)

| Phase | Scope | State |
|---|---|---|
| 1 | Catalog + strict templates + dry-run orchestrator | ✅ (0.1) |
| 2 | `runtime.env` → `rosetta.map`, `/opt/bytehawks` layout | ✅ (0.2) |
| 3 | **Executable builds**: fetch, SHA-256, safe extract, stages, logs | ✅ |
| 3 | **Dependencies**: `depends_on`, ordering, cycles, build environment | ✅ |
| 3 | **Recipe hash** (Merkle) and idempotent builds | ✅ |
| 3 | **Bundle contract v1**: `runtime.spec`, `manifest.json/env`, assets | ✅ |
| 5 | Real chain built end to end: openssl → zlib → pcre2 → nginx 1.28 | ✅ |
| 6 | **Upstream watcher** (`outdated`, `bump`, daily workflow, PR per package) | ✅ code + tests; ⬜ first run on GitHub |
| 6 | **Artifact stores** (dir, GitHub Actions artifacts, Nexus raw), `restore`, `publish`, `inventory` | ✅ dir/github (API listing verified); 🚧 Nexus tested on a fake server only |
| 6 | **Affected-only build chain** (`stale`, `chain`, build workflow libs → apps) | ✅ code + tests; ⬜ first run on GitHub |
| 6 | Installed-file inventory (`files.sha256`, `verify`) | ✅ (Shaker `verify --files` ✅) |
| 7 | **Shaker integration**: platform check, `mutable`, sealed + signed bundle, `shaker restore` (docs/INTEGRATION.md) | ✅ tested with a real NiFi chain; ✅ `shaker up` (dependency order), ⬜ Shaker as a catalog unit / package |
| 7 | **Binary repack** (zip sources, hook install, `variant.*`): NiFi 2.9 on Temurin 21 built and run through Shaker | ✅ (x64 only; see "Binary repack gaps") |

## Binary repack gaps (found building NiFi)

| Item | Detail |
|---|---|
| ✅ Assets in the recipe hash | `assets/<pkg>/` (templates, hooks) is hashed: a hook change makes the unit stale and rebuilds it |
| ✅ Per-arch URL/checksum | `arch_map` + `{{ arch_name }}` and `sha256: {amd64: ..., arm64: ...}`; temurin pinned for both (aarch64 hash, signature and ELF type checked, not executed: no arm64 host) |
| ⬜ `temurin` follows upstream | no `upstream:` block: build number (`variant.build`) is not updated by `bump`; tags are `jdk-21.0.9+10` |
| ✅ GPG signature | nifi (Apache `.asc`, KEYS) and temurin (`.sig`, key fetched by pinned fingerprint) verified; a new release manager fails until the fingerprint is reviewed |
| 🚧 Large artifacts | measured: NiFi artifact = 803 MB (same as the zip: JAR/NAR are already compressed, gzip level 1/6/9 differ by < 1%), ~55 s to pack. Compression is not the lever; storage is: GitHub Actions quotas (≈ 0.5–1 GB on small plans) fit one NiFi at best → use Nexus/OCI for such packages |
| ⬜ `files.sha256` vs runtime edits | `verify` flags `conf/*` the hook rewrites at every start; needs a "mutable" marker in `runtime` |
| ⬜ NiFi extras | keystore/truststore parameters (today NiFi self-signs at start), logging to stdout, cluster mode, `nifi-toolkit`, `nifi-registry`, healthcheck on `/nifi-api` |

## 0.4 - supply chain & provenance

| Item | Detail |
|---|---|
| ⬜ SBOM | emit CycloneDX (and SPDX) per unit: components = source + dependencies with hashes; ship as `sbom.cdx.json` in the bundle and list it in `bundle.sha256` (**contract**: Shaker `verify` can report it) |
| 🚧 Source signature verification | ✅ OpenPGP (`signature:` block, pinned signer fingerprints, checked in `build` before extraction and in `bump` before pinning; nifi, temurin); ⬜ minisign/cosign, ⬜ enforcement for the older packages (openssl, nginx, zlib, pcre2) |
| 🚧 Installed-file inventory | ✅ `files.sha256`, `autobuilder.py verify`, Shaker `verify --files`, `mutable` paths; ⬜ mode/owner |
| ⬜ Vulnerability gate | optional `grype`/`trivy` run on SBOM in CI; fail on severity threshold |
| ✅ Reproducible-build check | `autobuilder.py repro`: two builds with fixed `SOURCE_DATE_EPOCH`/TZ/locale, every installed file compared (zlib verified; see docs) |
| ✅ Update watcher | `outdated` + `bump` + `watch-upstream.yml` |
| ⬜ Variant lifecycle | `eol:` dates per variant, warning in `plan`/`list` |

## 0.4.x - closing the automation loop

| Item | Detail |
|---|---|
| ⬜ **First real run** | execute `ci`, `build` (glibc + musl legs), `watch-upstream` on GitHub, fix what only the platform reveals (same-run artifact visibility, JavaScript actions in the Alpine container, PR token behaviour): [docs/FIRST-RUN.md](docs/FIRST-RUN.md) is the checklist |
| ⬜ Nexus on a real instance | run `publish`/`inventory`/`restore` against Nexus 3 OSS, adjust listing/pagination quirks |
| ⬜ `prune` | remove superseded artifacts older than N days / keep last K per unit in a store; scheduled |
| ⬜ Failure notification | open/update an issue when a scheduled build or a bump fails; weekly digest of outdated series (`newer_series`) |
| ⬜ Auto-merge policy | opt-in auto-merge of patch bumps when ci + build are green **and** a signature check passed |
| 🚧 Artifact signing | ✅ OpenPGP signature of the sidecar (tarball sha256 + recipe), local keyring or CI secret, verified by `restore`/`chain` against pinned fingerprints, `sign` command; ⬜ cosign/minisign, ⬜ signing the bundle itself, ⬜ first run in CI |
| ⬜ More store backends | GitHub Releases (long retention), S3/MinIO, OCI registry (ORAS), Artifactory |
| ⬜ Non-git upstreams | `upstream.type: html` (regex on an index page, e.g. nginx.org/download), `pypi`, `url-json` |
| 🚧 Matrix per architecture | ✅ per-arch checksum when sources differ; ⬜ `arm64` runners in the same workflows |
| ⬜ Release pins | `release.yaml`: a frozen set of recipe hashes = a reproducible, signed distribution (what Shaker deployments target) |

## 0.5 - build engine

| Item | Detail |
|---|---|
| 🚧 Multi-arch / musl | ✅ platform = arch × libc in hashes, names, manifests, pins, packaging; OpenSSL target per arch; Temurin pinned for 4 platforms; Debian + Alpine CI legs; ⬜ **real compilation on musl** (openssl, zlib, pcre2, nginx on Alpine), ⬜ NiFi on musl (native libraries of some NARs), ⬜ arm64 runners, ⬜ cross-compilation |
| ✅ Sandboxed builds | `--sandbox auto\|bwrap\|unshare`: read-only system, private `/tmp`, no network after the fetch (unshare verified; bwrap implemented, not run here) |
| ⬜ Parallel independent units | schedule the DAG with `--max-units`, per-unit logs |
| ✅ Remote artifact cache | content-addressed by recipe hash (`--store`, `chain`) with dir/GitHub/Nexus backends; more backends below |
| ⬜ Patches | `patches:` list (checksummed) applied in `pre` with `patch -p1`, part of the recipe hash |
| 🚧 git sources | ✅ commit-pinned (`ref_template` + `commit`), `bump`, `checksum`; ⬜ submodules, ⬜ signed-tag verification |
| ⬜ Non-tarball sources | zip, zst, xz explicit; local directories for development |
| ⬜ `check` policy | run the test-suite for release builds by default |
| 🚧 Toolchain pinning | ✅ compiler, linker, make, libc, OS recorded in manifest and sidecar (`builder_image` from `BH_BUILDER_IMAGE`); ⬜ enforcing a toolchain / pinned builder image |

## 0.6 - delivery

| Item | Detail |
|---|---|
| ⬜ `image` | generate a multi-stage Dockerfile / OCI image: builder stage, runtime stage (distroless/slim) with Shaker as ENTRYPOINT, labels from the manifest (version, recipe, source hashes) |
| 🚧 `package` | ✅ deb / rpm / apk via nfpm from `files.sha256`, naming templates, exact dependencies, conflicts, Shaker post-install, package signing; ✅ real nfpm 2.48 (catalog unit): deb extracted and `java` run, rpm headers and apk `.PKGINFO` checked, NiFi chain (803 MB deb, exact `Depends`, postinst) ; ⬜ installed with dpkg/rpm/apk on real hosts, ⬜ repository metadata (apt/createrepo/APKINDEX), ⬜ musl units for Alpine, ✅ nfpm as a catalog unit (`build --package nfpm`) |
| ⬜ `k8s` | Helm/Kustomize skeleton from `rosetta.map` and `runtime.spec` (**contract**) |
| ⬜ Release manifest | one signed index of all bundles/versions of a release, consumed by Shaker for multi-package `up` |
| ⬜ `docs <pkg>` | generate the parameter reference page from `runtime.env` |

## Catalog

| Package | State |
|---|---|
| openssl 3.0.18 / 3.5.4 / 3.6.0 | ✅ (3.5.4 built; others pinned, not yet built) |
| zlib 1.3.1, pcre2 10.43 | ✅ built |
| nginx 1.28.0 | ✅ built, runtime complete, run through Shaker |
| nifi 2.9.0 on temurin 21.0.9 | ✅ built, signed sources, run through Shaker |
| valkey 9.1.3 (git source, TLS), prometheus 3.15.1 (release asset), mosquitto 2.1.2 (git source, with cjson 1.7.19) | ✅ built, run through Shaker (parameters and templates from `scaffold`), `verify --files` ok; glibc only so far |
| postgresql, haproxy, mariadb, php-fpm, nodejs, python | ⬜ candidates. postgresql from git needs flex (not on the build host); haproxy's configuration is a DSL `scaffold` cannot read |
| fully static variants | 🚧 `--linkage static`: ✅ glibc (nginx, valkey without Lua, mosquitto built and run), ⬜ musl static compilation, ⬜ Shaker accepting a static musl bundle on a glibc host, ⬜ `image` from static artifacts (scratch/distroless) |

## 1.0 criteria

* bundle schema 1 frozen; versioned negotiation; compatibility tests with the previous Shaker release;
* signature + SBOM + reproducibility checks in the default release pipeline;
* multi-arch builds with an artifact cache;
* ≥ 5 packages built and run in containers and on VMs through Shaker: ✅ 5 services (nginx, nifi, valkey, prometheus, mosquitto) built and run through Shaker on a VM-like host; ⬜ in containers and on musl;
* documented process for CVE-driven rebuilds (bump → `plan` → rebuild → publish).

## Known gaps today (honest list)

* OpenSSL targets are mapped for amd64 and arm64 but only amd64 was built here; no cross-compilation.
* Build commands are trusted shell; `--sandbox` confines them (read-only system, no network) but is off by default.
* No signature/SBOM; `checksum`/`bump` trust the download they just made (cross-checked with upstream's checksum when published).
* The workflows pass `actionlint` and their commands are tested, but have not run on GitHub yet; Nexus has only been tested against a fake server.
* GitHub artifacts expire (≤ 90 days): GitHub is a cache, Nexus (or another store) is needed for an archive.
* openssl 3.0.18 and 3.6.0 variants have never been built here.
* `file://` and `https://` only; no mirrors/fallback URLs, no retries, no resume.
* Units build sequentially within a job (the workflow parallelizes applications across jobs).
* Hook scripts shipped in `assets/` must be copied by a `post` stage (no `hooks:` file copier yet).
