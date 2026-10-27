# Changelog

All notable changes to Distillery. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/) (pre-1.0: minor versions may break).

## [Unreleased]

### Added
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
