# Changelog

All notable changes to Distillery. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/) (pre-1.0: minor versions may break).

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
