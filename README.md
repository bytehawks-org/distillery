# distillery

> Declarative, verifiable, dependency-aware builds that produce **base artifacts plus the runtime
> contract** consumed by [Shaker](https://github.com/bytehawks-org/shaker).

Distillery is the *source of truth and build half* of the ByteHawks ecosystem. A package is declared
once in YAML: where the source comes from and its SHA-256, what it depends on, how it is built, and
how it behaves at runtime (user, directories, parameters, templates, hooks, command, health). Distillery
builds it and writes a **bundle** next to the artifact; Shaker then installs, initializes, runs and
audits it on containers and on legacy VM/servers, without any application-specific script.

```
packages.yaml ──► plan (deps first) ──► fetch + verify sha256 ──► extract ──► configure/make/install
                                                                        │
                                              <prefix>/.bytehawks/ ◄────┘  manifest.json  manifest.env  files.sha256
                                                                           rosetta.map   runtime.spec  assets/
                                                   │
                          artifact (tar.gz + json) ▼   github artifacts · nexus · dir        ◄── GitHub Actions
                                                                                                   watch-upstream (daily)
upstream tags ──► outdated ──► bump ──► PR ──► build: stale = catalog − store ──► chain          build (push / nightly)
```

## Highlights

| | |
|---|---|
| **Verified sources** | every variant pins a SHA-256; an unpinned source refuses to build; the hash is checked **before** any build command runs; safe tar extraction (no traversal, no escaping links) |
| **Dependency-aware** | `depends_on` with variants, topological plan, cycle detection, one shell session per build with `CPPFLAGS/LDFLAGS/PKG_CONFIG_PATH/PATH` pointing at already-built libraries |
| **Idempotent & traceable** | *recipe hash* (Merkle over the recipe, variant, prefix and dependencies' hashes): unchanged → skipped, a library change rebuilds its dependents; recipe hash, source hash, dependencies and build time recorded in `manifest.json` |
| **Declarative runtime contract** | `runtime:` section → `rosetta.map` (parameters, aliases, CLI, config-file keys) and `runtime.spec` (user, dirs, templates, hooks, run, health) |
| **Relocatable builds** | `--root DIR` rebases `/opt/bytehawks` for unprivileged builds, staging and tests |
| **Strict templates** | `{{ var }}` that cannot be resolved fails the build instead of reaching a shell |
| **Follows upstream** | scheduled GitHub workflow detects new releases (`git ls-remote`, per-variant series), bumps the catalog (new SHA-256, cross-checked with the one upstream publishes) and opens one PR per package |
| **Rebuilds only what is affected** | catalog units are compared with the artifact stores by recipe hash: a new `zlib` makes `zlib` and `nginx` stale, not `openssl`; libraries build serially in dependency order, applications in parallel |
| **Artifact stores** | content-addressed, deterministic tarballs; GitHub Actions artifacts today, **Nexus raw repository** implemented, any backend behind one small interface; restore instead of compile; `verify` installed files |
| **glibc and musl** | every unit builds for both C libraries (`--libc`, Debian and Alpine builder images): separate, content-addressed artifacts (`amd64` / `amd64-musl`), deb/rpm from glibc builds and apk from musl ones |
| **Shaker, or not** | the bundle carries platform, `mutable` paths, a digest and an optional signature that [Shaker](https://github.com/bytehawks-org/shaker) checks (`verify --files`, `restore`); each tool also works without the other ([INTEGRATION](docs/INTEGRATION.md)) |
| **Signed** | optional detached OpenPGP signature of every artifact (local key or CI secret); `restore`/`chain` accept only pinned signers; sources are verified too (`signature:` in the catalog) |
| **Tested** | 108 unit/integration tests (fake GitHub and Nexus servers, real git remotes); a real chain **openssl 3.5.4 → zlib → pcre2 → nginx 1.28.0** was built from source with it |

## Layout

```
/opt/bytehawks                  real root (never use the /opt/bh symlink inside artifacts)
/opt/bh -> /opt/bytehawks       convenience symlink
├── core/distillery             this repository
├── core/shaker                 the runtime counterpart
├── deps/                       libraries (shared prefix) and deps/.bytehawks/<lib>/ bundles
├── <package>/                  application prefix, <package>/.bytehawks/ = its bundle
└── var/                        cache/distillery (downloads), build (sources), log/distillery
```

## Quick start

```sh
pip install -r scripts/requirements.txt          # PyYAML, ruamel.yaml

python3 scripts/autobuilder.py list                                   # catalog
python3 scripts/autobuilder.py plan  --package nginx                  # dependency order, pinned?, up to date?
python3 scripts/autobuilder.py build --package nginx --with-deps --dry-run   # see exactly what would run
python3 scripts/autobuilder.py build --package nginx --with-deps      # build (idempotent)
python3 scripts/autobuilder.py build --package nginx --with-deps --root /tmp/stage   # unprivileged / staged
python3 scripts/autobuilder.py checksum --package zlib                # pin a new source
```

Then, on the target (see Shaker): `shaker install nginx && shaker run nginx --port=8080`.

## A package in 40 lines

```yaml
package:
  zlib:
    variant:
      - {name: all, major_version: 1.3, patch_version: 1, full_version: 1.3.1,
         sha256: 9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23}
    git:
      repo: https://github.com/madler/zlib
      release_download_url_template: "{{ git.repo }}/releases/download/v{{ full_version }}/{{ name }}-{{ full_version }}.tar.gz"
    commands:
      configure: ./configure --prefix={{ prefix }} --static
      make: make -j{{ jobs }}
      install: make install
    tags: [library]
```

An application adds `depends_on` and a `runtime:` section (see `nginx` in `packages.yaml` and
[docs/PACKAGES.md](docs/PACKAGES.md)).

## Commands

| Command | Purpose |
|---|---|
| `list` | packages, tags, dependencies, variants |
| `plan` | ordered build list with pinned/up-to-date status and recipe hashes |
| `build` | fetch → verify → extract → stages → bundle; `--with-deps --dry-run --force --root --jobs --stages --allow-unverified` |
| `bundle` | write only the Shaker bundle (no build) |
| `checksum` | download a source and print its SHA-256 for pinning |
| `outdated` / `bump` | compare with upstream releases / update `packages.yaml` (version + verified SHA-256) |
| `stale` / `chain` / `affected` | what the stores lack / build it in dependency order / who depends on a package |
| `package` | deb / rpm / apk of a built unit, named by templates in `config.yaml` (needs nfpm), see [PACKAGING](docs/PACKAGING.md) |
| `sign` | OpenPGP signature of artifacts that have none yet |
| `inventory` / `restore` / `publish` / `verify` | list a store / install from it / push to it / check installed files |

Reference: [docs/CLI.md](docs/CLI.md).

## Documentation

| Document | Content |
|---|---|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | modules, build pipeline, recipe hash, design decisions |
| [PACKAGES](docs/PACKAGES.md) | complete `packages.yaml` reference (variants, git, commands, depends_on, runtime) |
| [CONFIG](docs/CONFIG.md) | `config.yaml`, paths, `--root` |
| [AUTOMATION](docs/AUTOMATION.md) | the scheduled workflows: watch upstream, rebuild what is affected, setup, operation |
| [INTEGRATION](docs/INTEGRATION.md) | Distillery + Shaker, or each alone: what Distillery emits, what Shaker checks, chain of trust, setups |
| [PACKAGING](docs/PACKAGING.md) | deb / rpm / apk: naming templates, contents, dependencies, signing, limits |
| [ARTIFACTS](docs/ARTIFACTS.md) | artifact format and identity, stores (dir, GitHub, Nexus), restore, integrity |
| [CLI](docs/CLI.md) | every command and option, exit codes |
| [BUNDLE](docs/BUNDLE.md) | the Distillery → Shaker contract: file formats, `rosetta.map`, `runtime.spec`, `manifest.*` |
| [ADDING-A-PACKAGE](docs/ADDING-A-PACKAGE.md) | tutorial: from tarball to a package Shaker can run |
| [SECURITY](docs/SECURITY.md) | supply-chain model, guarantees, limits |
| [TESTING](docs/TESTING.md) | test suite, CI, validation performed |
| [ROADMAP](ROADMAP.md) / [CHANGELOG](CHANGELOG.md) | where it is going / what changed |

## Requirements

Python ≥ 3.9, PyYAML, ruamel.yaml (only `bump`), `git` (only `outdated`/`bump`), `bash`, and the toolchain each package needs (`gcc make perl` for openssl, ...).
Network access to the source URLs (HTTPS, `file://` also supported). Linux.

## License

Apache-2.0, see [LICENSE](LICENSE).
