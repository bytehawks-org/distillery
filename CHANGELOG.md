# Changelog

All notable changes to Distillery. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/) (pre-1.0: minor versions may break).

## [0.4.1] - 2026-10-23

### Fixed (found by the first runs on GitHub)
- `build.yml` `plan` failed whenever something was stale: the last command of the summary block was
  `[ ... ] && echo`, which returns 1 under `bash -e` and failed the step after the plan had been computed. It is an `if` now.
- Workflow scripts: `$STORES` expanded unquoted (shellcheck SC2086, reported by actionlint in the `workflows` job):
  now split into an array; `awk -v p=$p` quoted in `ci.yml`.

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
