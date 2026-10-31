# Changelog

All notable changes to Distillery. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/) (pre-1.0: minor versions may break).

## [Unreleased]

### Added
- **Builder and runtime images** (`docker/`): `builder-{glibc,musl}` with the whole toolchain, python environment, gpg, bubblewrap and checksum-verified nfpm;
  `runtime-{glibc,musl}` as a `--base` for `image`. `builder-images.yml` builds them on pull requests and publishes to ghcr.io; `build-platform.yml`
  skips its toolchain installation when the image already has it and records `BH_BUILDER_IMAGE`. See `docs/BUILDER-IMAGES.md`.

### Changed
- **Artifact names** are `<package>_<holder>_<variant>_<tag>_<platform>_<version>_<recipe12>`: the field separator is `_` instead of `--`, so no
  name contains a double hyphen, and the holder (`bh` for ByteHawks) is `artifacts.holder` / `BH_HOLDER`. A field containing `_` is refused. Stores list only
  their holder's names. The layout is `artifacts.name_order` (default: holder right after the package, `nginx_bh_stable_application_amd64_1.28.0_a1146e116dea`).
  Artifacts named the old way are not recognised (nothing had been published yet); `stale --github-output` also emits `holder` and the `pattern` that matches them.

### Fixed
- nginx: `conf/nginx.conf` (installed by `make install`, rendered over by `shaker init`) is `mutable`, so `shaker verify --files` passes after init.
- **The recipe hash depended on the number of CPU cores** (`make -j{{ jobs }}` was resolved with the machine's value before hashing), so an
  artifact built on a 4-core runner was `superseded` for a 16-core one. `jobs` is now a constant in the hash; the build still uses the real value.
  Hashes of units that use `{{ jobs }}` change once (nothing had been published yet).

### Added
- **SBOM.** Every real build writes `sbom.cdx.json` (CycloneDX 1.5: the unit, every unit it was built with, hashes, licences, toolchain) into the
  bundle, listed in `bundle.sha256`; `autobuilder.py sbom` prints it from the catalog. Validated against the CycloneDX schema in the tests.
- **`image`.** OCI archive of a built service and its dependencies (one layer per unit): ENTRYPOINT `shaker run` (with `--base` and
  `--shaker-dir`) or the package's own command (`--entrypoint direct`, `FROM scratch` with a static unit); contract user, directories,
  labels and stop signal in the config; deterministic. Loaded and run with docker: a static nginx from scratch, and the Shaker mode on a bash base
  with `shaker verify --files` passing inside the container. See `docs/IMAGE.md`, `docs/SBOM.md`.
- **Sandboxed builds.** `--sandbox auto|bwrap|unshare` / `build.sandbox`: stages run with a read-only system, private `/tmp`, own PID/IPC/UTS
  namespaces and no network, writable only in the source tree, the prefix and the log directory; `build: {network: true}` per package.
- **Reproducibility.** `--reproducible` / `build.reproducible` fixes `SOURCE_DATE_EPOCH`, `TZ` and the locale; new `repro` command builds a unit
  twice and compares every installed file (exit 3 on a difference).
- **Toolchain record.** `manifest.json` and the artifact sidecar carry `toolchain` (cc, ld, ar, make, libc, OS, machine, `builder_image`, plus the
  package's `toolchain:` list); recorded, not part of the recipe hash.
- **User manual** (`docs/MANUAL.md`) with outputs of real runs; docs review (stale counts and examples).
- **Static linking.** `--linkage static` / `BH_LINKAGE`: packages with a `static:` recipe (commands/env overrides, `binaries`) are built
  without a dynamic loader; platform `amd64-static` / `amd64-musl-static`, `linkage` in the recipe hash (dynamic hashes unchanged), manifest,
  `manifest.env` and sidecar; an ELF check fails a build whose declared binaries are still dynamic. nginx, valkey (without Lua: its engine
  needs `dlsym`) and mosquitto have a static recipe; built and run on glibc. See `docs/STATIC.md`.
- **Binary repack: NiFi on Temurin.** `nifi` 2.9.0 (Apache `bin.zip`, nothing compiled) and `temurin` 21.0.9
  (JRE tarball, x64 only) in the catalog; the pair was built with `--root`, then installed, initialized, started
  and verified through Shaker (UI 200, login, REST API, `verify` 10/10). Runtime: `BH_NIFI_*` parameters
  (port, host, proxy hosts, single-user credentials, sensitive props key, heap, data dir, JAVA_HOME), durable
  state under `BH_NIFI_DATA_DIR`, `post_init` hook that renders the parameters into the shipped configuration.
- **Zip sources.** `fetch.extract` handles zip archives (same traversal/link checks as tar; Unix modes restored
  from `external_attr`, setuid/setgid/sticky dropped).
- **Hook installation.** `assets/<pkg>/hooks/<stage>/*` is copied (executable) into the hook directory declared in
  `runtime.hooks` when the bundle is emitted: no `post` stage copying needed.
- **`{{ variant.<key> }}`** in templates: any extra key of the selected variant (e.g. `build: 10`).
- **Git sources.** `git.ref_template` + `commit` instead of `release_download_url_template` + `sha256`: shallow clone of the tag, refused unless
  `HEAD` is the pinned commit, `git archive` export; `plan`, `checksum` and `bump` understand it. Used by valkey, mosquitto and cjson.
- **`scaffold`** ([docs/SCAFFOLD.md](docs/SCAFFOLD.md)): derives `runtime.env` entries and a template from a sample configuration file
  (`key value`, `key = value`, ini, flat YAML): inferred types (port, uint, enum from yes/no and from the documentation, path, secret),
  descriptions from comments, commented examples as optional settings (conditional `{{? NAME }}` lines, no default), `--include`,
  `--exclude`, `--also`, `--hint`, `--default`, idempotent merge that never changes an existing entry.
- **New packages**: `valkey` 9.1.3, `prometheus` 3.15.1, `mosquitto` 2.1.2 and the library `cjson` 1.7.19; each built and run through Shaker
  (valkey with a password and a memory limit, prometheus with a changed retention and scrape interval, a real MQTT message through
  mosquitto). Services now have distinct uids (nifi 1002, valkey 1003, prometheus 1004, mosquitto 1005) so they can share a host. The
  OpenSSL static libraries are built with `-fPIC` (they can be linked into shared objects); its recipe hash changes.
- **Shaker integration** ([docs/INTEGRATION.md](docs/INTEGRATION.md)), additive to bundle schema 1: `manifest.env` carries `BH_PKG_ARCH` and
  `BH_PKG_LIBC` (also in `manifest.json`); `runtime.mutable` becomes `mutable|path` records (`verify` and Shaker's `verify --files` skip
  them; set for NiFi's `conf/`); every build writes `bundle.sha256` over the bundle and `files.sha256` and signs it
  (`bundle.sha256.asc`) when `artifacts.signing.key` is set; the seal is part of artifacts and packages. `packaging.shaker.package` makes
  services depend on a package that provides Shaker (optional: without Shaker the post-install only prints what to run).
  `manifest.env` lists the direct dependencies with their recipe (`BH_PKG_DEPENDS`) for `shaker up`.
- **glibc and musl.** New platform dimension `libc` (`--libc`, `BH_LIBC`, detected from the host): part of the recipe hash and of the manifest;
  artifact identity `…--amd64--…` (glibc, names unchanged) and `…--amd64-musl--…`; `sha256` maps accept `<arch>` and `<arch>-musl` keys;
  `libc_map` / `{{ libc_name }}`, `{{ libc }}`, `{{ platform }}` in templates. Temurin is pinned for the four platforms (Alpine builds
  included), OpenSSL's Configure target follows the architecture. `package` ties deb/rpm to glibc and apk to musl.
  Workflows: `build.yml` now calls `build-platform.yml` once per libc (Debian and Alpine images); `ci.yml` checks that every source is
  pinned for amd64/arm64 × glibc/musl and runs the test-suite on Alpine. Hashes of existing units change once (libc joins the recipe).
- **Native packages.** `package` writes deb, rpm and apk of a built unit with nfpm: file list from `files.sha256`, exact-version
  dependencies in each format's syntax, conflicts between variants, optional post-install running `shaker install`, optional package
  signing, `SHA256SUMS`. Package and file names are templates in `config.yaml` (`packaging.name_template`, `file_name_template`,
  `release`) with per-format architecture names. See docs/PACKAGING.md. `nfpm` 2.48.0 is a catalog unit (`build --package nfpm`, sha256 per arch); output checked with the real binary
  (deb unpacked and run, rpm and apk metadata read). Unit tests use a stand-in and run the real binary only when it is installed.
- Warning when an application is built into a non-empty prefix (unchanged files are not recorded in artifacts and packages);
  `license` added to the catalog entries.
- **Signed artifacts.** `artifacts.signing` (`key`, `fingerprints`, `keys`, `require`): a detached OpenPGP signature of the
  artifact sidecar (`<name>.json.asc`), made by `build --artifacts-dir` when a key is configured (`BH_SIGNING_KEY`, passphrase via
  `BH_SIGNING_PASSPHRASE_FILE`) or later with the new `sign --from DIR`; carried by the dir, GitHub and Nexus stores; verified by
  `restore`/`chain` against pinned primary fingerprints (`require: true` refuses unsigned and foreign artifacts; a bad signature
  is never tolerated). Same path for a local keyring and for CI (key imported from the `BH_SIGNING_PRIVATE_KEY` secret by
  `build.yml`). Sidecars are now written by a single function (`meta_bytes`) so published copies keep verifying.
- **GPG verification of sources** (`signature:` block, module `signature.py`): pinned signer fingerprints,
  keys fetched into a throw-away keyring, checked before extraction in `build` and before pinning in `bump`.
  Enabled for `nifi` (Apache KEYS) and `temurin` (Adoptium key); verified on the real releases.
- **Per-architecture sources.** `arch_map` / `{{ arch_name }}` and `sha256: {amd64, arm64}`; `bump` recomputes
  every architecture; `temurin` is pinned for amd64 and arm64.
- **Assets in the recipe hash.** The content (and execute bit) of `assets/<package>/` is part of the recipe hash:
  editing a template or hook makes the unit stale. Packages without assets keep their hash; `nginx` has assets,
  so its hash changes once. `Catalog(..., assets_root=)`, `--assets` on every command.
- **`bump` cross-check with sha512.** `checksum_url_template` may point to a sha256 or a sha512 file (Apache
  publishes sha512 only); the pin stays sha256.

## [0.4.0] - 2026-10-23

### Added
- **Upstream watcher.** `outdated` compares every variant with its upstream (`upstream:` block,
  `git ls-remote --tags`, `tag_regex`, followed series = `track` or `major_version`); `bump` downloads the new
  source, computes the SHA-256, cross-checks it with the checksum upstream publishes (`checksum_url_template`)
  and rewrites only the variant lines of `packages.yaml` (ruamel.yaml, comments and layout preserved).
  `upstream:` declared for openssl, zlib, pcre2, nginx.
- **Artifacts and stores.** Deterministic `tar.gz` + JSON sidecar per unit, content-addressed name
  `bh--<package>--<variant>--<tag>--<arch>--<version>--<recipe12>`; backends `dir:PATH`, `github`
  (Actions artifacts: REST listing/download, token never forwarded to the blob host) and `nexus`
  (Nexus 3 raw repository: PUT/GET + REST listing); `MultiStore` (first match wins); integrity check on every fetch.
- **Commands** `stale`, `chain`, `affected`, `inventory`, `restore`, `publish`, `verify`, `outdated`, `bump`;
  `build --store --artifacts-dir` (restore instead of compile, package what is built).
- **Affected-only chain.** Staleness = catalog unit whose exact recipe hash is not in the stores (the Merkle
  hash makes dependents stale automatically); `stale --github-output` emits libs/apps matrices in dependency order.
- **`files.sha256`** in every bundle (hash of everything a build installed) and `verify` against the disk.
- **GitHub Actions**: `watch-upstream.yml` (daily; one PR per package with news, then dispatches ci and build on
  the bot branch), `build.yml` (plan → libs serial → apps parallel → optional Nexus publish; triggers:
  dispatch, push to main, nightly reconcile), `ci.yml` extended (actionlint, `-W error::ResourceWarning`).
- Architecture is part of the recipe hash and of artifact names (`--arch`, default detected).
- Docs: `AUTOMATION.md`, `ARTIFACTS.md`; CLI/PACKAGES/CONFIG/BUNDLE/ARCHITECTURE/SECURITY/TESTING updated.
- 29 new tests (63 total): fake GitHub and Nexus servers, real git remotes, artifact round-trips.

### Changed
- `autobuilder.py` is now argument parsing only; commands live in `bhdistillery/commands.py`.
- Variant names in units/artifacts/manifests are canonical (first alias): `old-legacy`, not `legacy`.
- `publishable_units` and `stale` order units dependencies first.
- `requirements.txt` adds `ruamel.yaml`; `git` is needed by `outdated`/`bump` and the tests.

## [0.3.0] - 2026-10-23

### Added
- **Executable builds.** `build` downloads, verifies, extracts and runs the declared stages
  (`pre configure make check install post`) in one `bash -euo pipefail` session per unit, streaming output
  to the console and `log_path/<pkg>-<version>.log`. `--dry-run` prints the exact plan and script.
- **Pinned sources.** `sha256` per variant; unpinned sources are refused (`--allow-unverified` for
  development); checksum verified before extraction and before any command; cache hit by checksum;
  `checksum` command to pin a new source.
- **Safe extraction.** Rejects absolute paths, `..` and links escaping the tree; strips the top directory.
- **Dependencies.** `depends_on` (`name` or `{name, variant, tag}`), topological ordering, cycle
  detection, deduplication, `--with-deps`; dependent builds see libraries through `CPPFLAGS`, `LDFLAGS`,
  `PKG_CONFIG_PATH`, `PATH`.
- **Recipe hash** (Merkle over recipe, variant, prefix and dependency hashes) and **idempotent builds**
  (`[skip] ... bundle up to date`, `--force`).
- **Bundle contract v1**: `manifest.json`, `manifest.env`, `rosetta.map`, `runtime.spec`, `assets/`;
  `runtime.user/dirs/persistence/templates/hooks/run/healthcheck/requires/env`.
- Commands `list`, `plan`, `bundle`, `checksum`; options `--root`, `--jobs`, `--stages`, `--assets`.
- `--root` rebasing of every path in `config.yaml` for unprivileged and staged builds.
- Packages: **zlib 1.3.1**, **pcre2 10.43**, **nginx 1.28.0** (with a complete `runtime:` and
  `assets/nginx/nginx.conf.tmpl`); sha256 for all openssl variants.
- Strict template resolution (unresolved `{{ }}` is an error); `${VAR}` left for Shaker.
- Test suite (34 tests) and CI workflow; documentation set; `ROADMAP.md`.

### Changed
- `autobuilder.py` split into the `bhdistillery` package (`templates model fetch build bundle`) with
  sub-commands. The legacy invocation (`--package ... --tag ...` without sub-command) still works and is a dry run.
- Paths in `config.yaml`: downloads `var/cache/distillery`, sources `var/build`, logs `var/log/distillery`
  (no longer `/tmp` and `deps/src`).
- Libraries get their bundle in `<deps>/.bytehawks/<lib>/`; applications in `<prefix>/.bytehawks/`.
- OpenSSL: `--libdir=lib`, `make install_sw install_ssldirs`, parallel make.
- Default variant: `stable` if present, otherwise the first declared.

### Fixed
- OpenSSL download URL pointed at `releases/tag/` (an HTML page) instead of `releases/download/`.
- `regquirements.txt` renamed to `requirements.txt`, only PyYAML (argparse/pathlib are stdlib).

## [0.2.0] - 2026-10-23

### Added
- `runtime.env` section compiled to `rosetta.map` and `manifest.json` (`bundle` sub-command) for Shaker.
- `/opt/bytehawks` layout keys in `config.yaml` (`root`, `link`, `core_path`).

## [0.1.0] - 2025-11-10

### Added
- Initial catalog (`openssl` with legacy/stable/stream variants), `config.yaml`, template resolver and
  a dry-run build orchestrator (`autobuilder.py`).
