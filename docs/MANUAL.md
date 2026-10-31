# Distillery user manual

A hands-on guide, from the first build to a signed, restorable release. Every command and every output below was run
for real on a throw-away project (`demolib` + `demoweb`, two tiny packages built from local tarballs), so you can follow
along without network access. The [reference documents](../README.md#documentation) stay the place to look up an option;
this manual shows how the pieces fit.

| Part | Subject |
|---|---|
| [1](#1-the-model-in-five-minutes) | The model: units, recipe hash, bundle, artifact, store |
| [2](#2-setup) | Setup and the shell function used in the examples |
| [3](#3-your-first-package) | Writing and building a package; dry run; idempotence |
| [4](#4-dependencies-variants-and-the-recipe-hash) | Dependencies, variants, what changes a hash |
| [5](#5-the-runtime-contract) | Parameters, templates, health: what Shaker will use. `scaffold` |
| [6](#6-sources) | Tarballs, zip, git, per-architecture pins, GPG-verified sources |
| [7](#7-following-upstream) | `outdated` and `bump` |
| [8](#8-artifacts-and-stores) | `--artifacts-dir`, `publish`, `stale`, `chain`, `restore`, `verify` |
| [9](#9-signing) | Signing artifacts and bundles, trust at the consumer |
| [10](#10-native-packages) | deb / rpm / apk |
| [11](#11-glibc-musl-and-static) | Platforms |
| [11b](#11b-confined-builds-reproducibility-sbom-and-images) | Sandbox, `repro`, SBOM, images |
| [12](#12-automation-on-github) | The workflows |
| [13](#13-with-shaker-or-without) | Delivering to a host |
| [14](#14-troubleshooting) | Messages you will meet |
| [15](#15-cheat-sheet) | One page |

The matching guide for the other half is the [Shaker manual](https://github.com/bytehawks-org/shaker/blob/main/docs/MANUAL.md).

---

## 1. The model in five minutes

Distillery is a build tool whose input is **declarative**: a catalog (`packages.yaml`) says where a package's source comes
from, how to build it and how it behaves at runtime. Everything else is derived.

| Term | Meaning |
|---|---|
| **package** | an entry of `packages.yaml`; has one or more **variants** (versions or flavours) and one or more **tags** (`library`, `application`) |
| **unit** | package + variant + tag: the thing that gets built. Libraries install in a shared prefix (`deps`), applications each in their own (`/opt/bytehawks/<name>`) |
| **recipe hash** | SHA-256 over everything that influences the result: the recipe, the variant, the prefix, the platform and the hashes of the dependencies. Same hash = same artifact; a changed library changes the hash of everything built on it |
| **bundle** | `<prefix>/.bytehawks/`: `manifest.json/env`, `rosetta.map` (parameters), `runtime.spec` (user, dirs, command, health), `files.sha256`, `bundle.sha256(.asc)`. Written next to every build. It is the contract with Shaker ([BUNDLE](BUNDLE.md)) |
| **artifact** | the installed tree of a unit as a deterministic `.tar.gz` plus a `.json` sidecar (and `.json.asc` when signed). Named by the recipe hash, so a store can be asked "do you have exactly this build?" |
| **store** | a place that holds artifacts: a directory, GitHub Actions artifacts, a Nexus raw repository |
| **platform** | architecture × C library × linkage: `amd64`, `amd64-musl`, `amd64-static`, `amd64-musl-static` |

```
packages.yaml ─► plan ─► fetch+sha256 ─► extract ─► configure/make/install ─► bundle ─► artifact ─► store
                                                                                          │
                                       stale = catalog − store ─► chain (build only that, restore the rest)
```

All paths live under one **root** (`/opt/bytehawks` by default). `--root DIR` rebases the whole tree, which is what the
examples use (`/tmp/dw`): it lets you build without privileges and without touching the system.

## 2. Setup

Requirements: Python ≥ 3.9, PyYAML (`ruamel.yaml` for `bump`), `git`, `bash`, `make` and whatever toolchain your packages need;
`gpg` for signatures; `nfpm` for native packages ([PACKAGING](PACKAGING.md)).

```sh
git clone https://github.com/bytehawks-org/distillery /opt/bytehawks/core/distillery
pip install -r /opt/bytehawks/core/distillery/scripts/requirements.txt
```

The examples run in a project directory that holds its own catalog, assets and configuration, and use this function
(`--root` goes on every command; options always follow the sub-command):

```sh
cd ~/demo        # packages.yaml  assets/  config.yaml  (a copy of the repository's config.yaml)
ab() { python3 /opt/bytehawks/core/distillery/scripts/autobuilder.py "$1" \
         --config ~/demo/config.yaml --packages ~/demo/packages.yaml --assets ~/demo/assets --root /tmp/dw "${@:2}"; }
```

Run from the repository itself, `python3 scripts/autobuilder.py list` uses the bundled `packages.yaml` (nginx, nifi, valkey, ...)
and needs none of those flags.

## 3. Your first package

Two small sources, packed as `demolib-2.0.tar.gz` and `demoweb-1.0.tar.gz` (a `Makefile` each; `demoweb` ships a 15-line
Python HTTP server). A **library** first - the simplest possible recipe:

```yaml
package:
  demolib:
    variant:
      - {name: all, major_version: 2, patch_version: 0, full_version: "2.0", sha256: <sha256sum of the tarball>}
    license: Apache-2.0
    git:
      repo: https://example.com/demolib
      release_download_url_template: "file:///srv/src/{{ name }}-{{ full_version }}.tar.gz"   # https:// in real life
    commands:
      install: make install PREFIX={{ prefix }}
    tags: [library]
```

Rules that matter, all enforced:

* every variant **pins the SHA-256** of its source; without it the build refuses (`plan` shows `sha256 MISSING`). Get it with
  `ab checksum --package <name>`;
* `{{ ... }}` is resolved by Distillery and an unknown name is an **error**, never an empty string; `${...}` is left alone for Shaker;
* install **into `{{ prefix }}`**, never into `/usr`;
* commands run in one shell session, `set -euo pipefail`, in the order `pre configure make check install post`
  (`check` only when asked). Missing stages are skipped.

Look before you build:

```console
$ ab list
demolib      library                deps: -                        all=2.0
demoweb      application            deps: demolib                  stable=1.0

$ ab plan --package demoweb
1. demolib 2.0 [all/library] sha256 ok, to build, recipe 9643d43809df
2. demoweb 1.0 [stable/application] sha256 ok, to build, recipe 7d0eaad55e4f

$ ab build --package demoweb --dry-run        # the exact script, nothing downloaded or written
[plan] demoweb 1.0 (stable, application)
  source   file:///srv/src/demoweb-1.0.tar.gz
  sha256   bb55e045dbf67d1ea886b0280030a927ade70bfc50d28b1cf56e71290500481e
  prefix   /tmp/dw/demoweb
  bundle   /tmp/dw/demoweb/.bytehawks
  ----- script -----
  set -euo pipefail
  cd /tmp/dw/var/build/demoweb-1.0
  echo "==> stage: make"
  make
  ...
```

Build, with the dependency first (`--with-deps`), and keep an artifact of each unit:

```console
$ ab build --package demoweb --with-deps --artifacts-dir dist
[build] demolib 2.0 (all, library)
[done] demolib 2.0 (all, library) in 0s, bundle: /tmp/dw/deps/.bytehawks/demolib, 11 paths recorded
[pack] dist/bh--demolib--all--library--amd64--2.0--9643d43809df.tar.gz (1274 bytes, sha256 f95216bac6fb)
[build] demoweb 1.0 (stable, application)
[done] demoweb 1.0 (stable, application) in 0s, bundle: /tmp/dw/demoweb/.bytehawks, 13 paths recorded
[pack] dist/bh--demoweb--stable--application--amd64--1.0--7d0eaad55e4f.tar.gz (2139 bytes, sha256 df9739763ee4)
```

Builds are **idempotent**: the same command again prints `[skip] demoweb 1.0: bundle up to date (7d0eaad55e4f)`. `--force`
rebuilds. A failing stage stops the build, prints the path of the full log (`/tmp/dw/var/log/distillery/<name>-<version>.log`) and
leaves no bundle behind.

What is on disk:

```
/tmp/dw/deps/share/demolib/greeting.txt         the library
/tmp/dw/deps/.bytehawks/demolib/                its bundle
/tmp/dw/demoweb/{bin,www}/                      the application
/tmp/dw/demoweb/.bytehawks/                     manifest.json manifest.env rosetta.map runtime.spec files.sha256 bundle.sha256 assets/
```

## 4. Dependencies, variants and the recipe hash

`demoweb` declares `depends_on: [demolib]` and, in its `install` stage, reads the library from `{{ deps_prefix }}`:

```yaml
    depends_on: [demolib]               # or {name: openssl, variant: stable} / {name: x, tag: application}
    commands:
      install: |
        make install PREFIX={{ prefix }}
        cp {{ deps_prefix }}/share/demolib/greeting.txt {{ prefix }}/www/index.html
```

The build environment already points at the dependencies (`CPPFLAGS`, `LDFLAGS`, `PKG_CONFIG_PATH`, `PATH`), so a
`./configure` finds them. `ab plan` orders by dependency and rejects cycles.

**Variants** are alternative versions of one package (`stable`, `legacy`, `stream`; a name can be a list of aliases). Pick with
`--variant NAME`; `stable`, else the first one, is the default. Extra keys of a variant are free-form, are part of the hash and are
available as `{{ variant.<key> }}`.

**What changes a recipe hash** (and therefore makes a unit *stale*): any edit of the package entry, the variant, the prefix, the
platform, the content of `assets/<package>/` and - transitively - the hash of every dependency. Edit the library and both change:

```console
$ ab stale --store dir:store --all           # after adding a LICENSE line to demolib's install stage
superseded demolib    all          library     2.0        55e279306126
superseded demoweb    stable       application 1.0        a82edd3c9566
```

`superseded` = the store has an older recipe of the unit, `missing` = it has none, `present` = up to date. This is the mechanism
that lets CI rebuild **only what is affected** ([section 8](#8-artifacts-and-stores)); `ab affected --package demolib --tag library`
lists the dependents.

## 5. The runtime contract

The `runtime:` block describes the application for [Shaker](https://github.com/bytehawks-org/shaker); a library may have none.
`demoweb`:

```yaml
    runtime:
      user: {name: demoweb, uid: 1010, gid: 0, home: "{{ prefix }}"}          # gid 0 = arbitrary-uid / OpenShift convention
      dirs:
        - {path: "{{ prefix }}/conf", mode: "g+rwX", owner: "1010:0"}
        - {path: "{{ prefix }}/run",  mode: "g+rwX", owner: "1010:0"}
      templates:
        - {src: demoweb.conf.tmpl, dest: "{{ prefix }}/conf/demoweb.conf", mode: "0640"}   # assets/demoweb/demoweb.conf.tmpl
      run:
        command: ["{{ prefix }}/bin/demoweb", "{{ prefix }}/conf/demoweb.conf"]            # an argv list, never a shell string
        stop_signal: TERM
        pidfile: "{{ prefix }}/run/demoweb.pid"
      healthcheck:
        tcp: {host: 127.0.0.1, port: "${BH_DEMOWEB_PORT}"}
      env:
        - {name: BH_DEMOWEB_PORT, type: port, default: 8080, aliases: [DEMOWEB_PORT], cli: --port, description: TCP port to listen on}
        - {name: BH_DEMOWEB_BIND, type: string, default: 127.0.0.1, cli: --bind, description: Address to bind}
        - {name: BH_DEMOWEB_ROOT, type: path, default: "{{ prefix }}/www", cli: --root-dir, description: Directory served}
```

and the template `assets/demoweb/demoweb.conf.tmpl`, rendered by Shaker with the resolved parameters:

```
# generated by shaker from the parameters, do not edit
bind={{ BH_DEMOWEB_BIND }}
port={{ BH_DEMOWEB_PORT }}
root={{ BH_DEMOWEB_ROOT }}
```

Parameters are named `BH_<PACKAGE>_<NAME>`, typed (`string secret int uint port bool path enum:a,b`), may have `aliases` (the dialect
of the upstream or of Bitnami images), a `cli` flag, a `file_key` for legacy config files and a `native_env` twin. Compiled into the
bundle they look like this:

```
$ cat /tmp/dw/demoweb/.bytehawks/rosetta.map
BH_DEMOWEB_PORT|port|8080||DEMOWEB_PORT||--port||TCP port to listen on
BH_DEMOWEB_BIND|string|127.0.0.1||||--bind||Address to bind
BH_DEMOWEB_ROOT|path|/tmp/dw/demoweb/www||||--root-dir||Directory served
$ cat /tmp/dw/demoweb/.bytehawks/runtime.spec
user|demoweb|1010|0|/tmp/dw/demoweb
dir|/tmp/dw/demoweb/conf|g+rwX|1010:0
...
run|/tmp/dw/demoweb/bin/demoweb|/tmp/dw/demoweb/conf/demoweb.conf
stopsignal|TERM
pidfile|/tmp/dw/demoweb/run/demoweb.pid
health|tcp|127.0.0.1|${BH_DEMOWEB_PORT}
```

Full field list: [PACKAGES](PACKAGES.md#runtime). Hooks (`pre_init`, `first_init`, `post_init`), `persistence`, `mutable` and
`requires` follow the same pattern.

### `scaffold`: do not type the parameters

If the application ships a configuration file whose shape is predictable (`key value`, `key = value`, ini, flat YAML), let
Distillery derive the parameters and the template from it:

```console
$ cat demo.conf
# listen port
port 8080
# bind address
bind 127.0.0.1
# log level: debug|info|warn
loglevel info

$ ab scaffold --package demoweb --from demo.conf
demo.conf: format space, 3 settings found, 3 selected
- name: BH_DEMOWEB_PORT
  type: port
  default: 8080
  cli: --port
- name: BH_DEMOWEB_BIND
  ...
--write would add these to package 'demoweb' (existing entries untouched) and create assets/demoweb/demo.conf.tmpl (6 lines), rendered to {{ prefix }}/conf/demo.conf
```

Without `--write` nothing changes. Types are inferred from the values (`port`, `bool`, `uint`, `path`, else `string`), so the log level above
came out as a `string`; correct it with `--hint loglevel=enum:debug,info,warn`, and steer the rest with
`--include/--exclude/--also/--default`. valkey's 84 parameters and mosquitto's 109 in the bundled catalog were
produced this way: [SCAFFOLD](SCAFFOLD.md).

## 6. Sources

| Source | How |
|---|---|
| Release tarball / zip | `git.release_download_url_template` + `sha256` per variant. The hash is checked **before** anything is extracted or run; extraction refuses path traversal and links that escape. Zip works the same (`strip_components` applies to both) |
| Git tag | `git.ref_template: "v{{ full_version }}"` + `commit: <40 hex>` per variant instead of `sha256`. The build clones the tag shallowly and **refuses** it unless `HEAD` is the pinned commit (valkey, mosquitto, cjson in the catalog) |
| Different file per architecture | `arch_map: {amd64: x64, arm64: aarch64}`, `{{ arch_name }}` in the URL and `sha256: {amd64: ..., arm64: ...}`; keys `amd64-musl` for sources that differ by C library, `libc_map` for their name (temurin) |
| Binary repack | nothing to compile: a `post` or `install` stage that copies the unpacked release (nifi, temurin, prometheus) |
| Upstream signature | `signature:` block, below |

Pin the value of a new source with `ab checksum --package X [--arch arm64]` (it prints the SHA-256 of what it downloaded; compare
it with the upstream's published hash before you commit it).

### Verify the upstream's OpenPGP signature

```yaml
    signature:
      type: gpg
      url_template: "{{ git.release_download_url_template }}.asc"
      keys_url: https://downloads.apache.org/nifi/KEYS
      fingerprints: [3FE925C2BED744021E6169A2F92A93B30C07C6D5]      # full 40-hex PRIMARY fingerprints: the trust anchor
```

The signature is checked after the download and before extraction, and again before `bump` pins a new hash. Keys are imported into
a throw-away keyring; the signature is accepted only if it is good, the key is neither expired nor revoked and its **primary
fingerprint is in your list** - the key server cannot add a signer. A release signed by a new manager fails until you review the
key and add the fingerprint. Take fingerprints from a trusted channel, not from the download itself.

## 7. Following upstream

```yaml
    upstream:
      tag_regex: '^v(\d+\.\d+\.\d+)$'                        # group 1 = version; pre-releases must not match
      checksum_url_template: "{{ git.release_download_url_template }}.sha256"      # optional cross-check
```

```sh
ab outdated --format json            # new releases in the series each variant follows (3.5 follows 3.5.x)
ab bump --package openssl --dry-run  # what would change
ab bump --package openssl            # edits packages.yaml: version + the verified SHA-256 (or commit)
```

`outdated` and `bump` read tags with `git ls-remote` (no API, no token). A variant follows the series named by its
`major_version` (override with `track:`); newer majors are reported, never adopted silently. If upstream publishes a checksum, `bump`
refuses a value that differs. The scheduled workflow does this daily and opens one PR per package ([section 12](#12-automation-on-github)).

## 8. Artifacts and stores

An artifact is what lets the next build, or another machine, skip the compilation.

```
bh--demoweb--stable--application--amd64--1.0--7d0eaad55e4f.tar.gz     the tree, deterministic (sorted, zeroed times/owners)
bh--demoweb--stable--application--amd64--1.0--7d0eaad55e4f.json       sidecar: package, platform, recipe, tarball sha256, size, ...
bh--demoweb--stable--application--amd64--1.0--7d0eaad55e4f.json.asc   optional detached signature of the sidecar
```

`<prefix>--<package>--<variant>--<tag>--<platform>--<version>--<recipe12>`: the name **is** the identity. Stores are chosen with
`--store SPEC` (repeatable, first match wins) or `artifacts.default_stores`: `dir:PATH`, `github[:owner/repo]`, `nexus`
([ARTIFACTS](ARTIFACTS.md)).

```console
$ ab publish --from dist --store dir:store
[push] bh--demolib--all--library--amd64--2.0--9643d43809df -> store/bh--demolib--all--library--amd64--2.0--9643d43809df.tar.gz
[push] bh--demoweb--stable--application--amd64--1.0--7d0eaad55e4f -> store/bh--demoweb--stable--application--amd64--1.0--7d0eaad55e4f.tar.gz
published 2 artifact(s)

$ ab inventory --store dir:store
demolib    all          library     amd64  2.0        9643d43809df        1479  2026-10-30T08:21:10  exp -          dir:store
demoweb    stable       application amd64  1.0        7d0eaad55e4f        2345  2026-10-30T08:21:10  exp -          dir:store

$ ab stale --store dir:store --all
present    demolib    all          library     2.0        9643d43809df
present    demoweb    stable       application 1.0        7d0eaad55e4f
```

The everyday loop is **`chain`**: compare the catalog with the stores, build what is `missing` or `superseded` in dependency
order (libraries serially, applications independently), restore everything else, and optionally pack and publish what it built:

```sh
ab chain --store dir:store --artifacts-dir dist        # CI: restore what exists, build only the rest
ab stale --store dir:store --exit-code                 # exit 3 when something is stale (gate a pipeline)
```

On another machine - or after wiping the root - `restore` installs a unit and its dependencies without building anything:

```console
$ ab restore --package demoweb --with-deps --store dir:store
[restore] demolib 2.0 from dir:store (bh--demolib--all--library--amd64--2.0--9643d43809df)
[verify] bh--demolib--all--library--amd64--2.0--9643d43809df signed by A7F44A4763E0F51B851548BA70F9957CE632215A
restored 12 entries into /tmp/dw
[restore] demoweb 1.0 from dir:store (bh--demoweb--stable--application--amd64--1.0--7d0eaad55e4f)
...
$ ab verify --package demoweb --with-deps          # every installed file against files.sha256
ok   demolib 2.0 (0 problem(s))
ok   demoweb 1.0 (0 problem(s))
```

Restore checks the tarball hash against the sidecar, the signature, the recipe and the paths before writing anything, and
**requires the same root** the artifact was built under: absolute paths are baked into the files. GitHub Actions artifacts expire
(≤ 90 days) - they are a cache; for an archive use Nexus or a directory you back up. `ab sign --from DIR` signs artifacts that have no
signature yet.

## 9. Signing

Two things get signed with the same key: the **bundle** (`bundle.sha256.asc`, which through `files.sha256` covers every installed
file) and each **artifact sidecar** (`.json.asc`, which holds the tarball hash and the recipe). In `config.yaml`:

```yaml
config:
  artifacts:
    signing:
      key: "A7F44A4763E0F51B851548BA70F9957CE632215A"     # or BH_SIGNING_KEY; a key of your keyring, or an imported secret in CI
      fingerprints: ["A7F44A4763E0F51B851548BA70F9957CE632215A"]     # who you trust when restoring: full PRIMARY fingerprints
      keys: ""                                            # file/URL with their public keys; empty = the local keyring
      require: false                                      # true = refuse unsigned or foreign artifacts
```

```console
$ ab build --package demoweb --with-deps --artifacts-dir dist
[build] demolib 2.0 (all, library)
[sign] bundle.sha256.asc with A7F44A4763E0F51B851548BA70F9957CE632215A
...
[sign] bh--demoweb--stable--application--amd64--1.0--7d0eaad55e4f.json.asc with A7F44A4763E0F51B851548BA70F9957CE632215A
```

Use a dedicated signing subkey with the primary key offline, and pin the **primary** fingerprint everywhere. Trust is always
configured at the **consumer** (here `fingerprints`, for Shaker `BH_SHAKER_TRUSTED_FINGERPRINTS`); it is never read from the bundle or
the artifact. Details and limits: [SECURITY](SECURITY.md).

## 10. Native packages

`package` turns a built unit into deb / rpm / apk with [nfpm](https://nfpm.goreleaser.com) (it is a catalog unit too:
`ab build --package nfpm`). Names come from templates in `config.yaml`, dependencies are exact (`bytehawks-demolib (= 2.0-1)`), and
the post-install script runs `shaker install <pkg>` when Shaker is present.

```console
$ ab package --package demoweb --with-deps --dry-run
[plan] bytehawks-demolib_2.0-1_amd64.deb  (8 files, depends: -)
[plan] bytehawks-demolib-2.0-1.x86_64.rpm  (8 files, depends: -)
[plan] bytehawks-demoweb_1.0-1_amd64.deb  (10 files, depends: bytehawks-demolib (= 2.0-1))
[plan] bytehawks-demoweb-1.0-1.x86_64.rpm  (10 files, depends: bytehawks-demolib = 2.0-1)
```

(The `apk` is planned only for units built with `--libc musl`; deb/rpm only for glibc builds.) A staged `--root` build produces a
package that installs to exactly those staged paths - build with the default root for packages meant for `/opt/bytehawks`. Naming,
signing of packages and limits: [PACKAGING](PACKAGING.md).

## 11. glibc, musl and static

`--libc glibc|musl` (default: detected from the host, or `BH_LIBC`) is part of the recipe hash and of the artifact name
(`amd64` for glibc, `amd64-musl`), so both live in the same store. Build musl on an Alpine image, glibc on the **oldest** Debian
you intend to support. `--linkage static` additionally builds, for the packages that define a `static:` recipe, binaries without a
dynamic loader (`amd64-static`, `amd64-musl-static`); the build checks the ELF headers and fails if a declared binary came out
dynamic. Static on musl is the self-contained option; on glibc it still needs the same glibc at run time for NSS, and valkey
loses Lua. Read [STATIC](STATIC.md) before you rely on it.

## 11b. Confined builds, reproducibility, SBOM and images

```sh
ab build --package zlib --sandbox auto            # read-only system, private /tmp, no network (bwrap, else unshare, else none)
ab repro --package nginx --with-deps --sandbox auto
# reproducible: nginx 1.28.0, 22 paths identical in two builds
ab sbom  --package nginx --tag application --out nginx.cdx.json      # CycloneDX 1.5; real builds also keep it in the bundle
ab image --package nginx --tag application --linkage static --entrypoint direct      # OCI archive, FROM scratch
docker load -i nginx-1.28.0-amd64-static.oci.tar && docker run -d --network host bytehawks/nginx:1.28.0-static
```
`repro` builds the unit twice with a fixed `SOURCE_DATE_EPOCH`, `TZ` and locale and compares every installed file; a build that embeds
the clock is reported as `different`. `build.sandbox` / `build.reproducible` in `config.yaml` make them the default. The toolchain
that built a unit (compiler, linker, make, libc, OS) is recorded in `manifest.json`. See [SECURITY](SECURITY.md), [SBOM](SBOM.md),
[IMAGE](IMAGE.md).

## 12. Automation on GitHub

| Workflow | When | What |
|---|---|---|
| `ci` | push / PR | unit tests on Python 3.9, 3.12 and 3.13 and on Alpine, `actionlint`, every package planned for amd64/arm64 × glibc/musl (and static) with all sources pinned |
| `watch-upstream` | daily | `outdated` + `bump`, one PR per package |
| `build` | push to the catalog / nightly | `stale` against the stores, libraries then applications, per C library, publish |

Setup, secrets (`BH_SIGNING_KEY`, store credentials) and the first run: [AUTOMATION](AUTOMATION.md) and [FIRST-RUN](FIRST-RUN.md).

## 13. With Shaker, or without

| You have | Do |
|---|---|
| Distillery only | `ab restore` / `ab verify` on the host, or `ab package` and `apt install ./bytehawks-*.deb`. Run the service as you like |
| Shaker only | `shaker restore dist/<artifact>.tar.gz`, then `shaker install/init/run`. No Python, no catalog |
| Both | build → sign → publish; on the host `shaker restore` (checks signature, platform, root), `shaker up <app> --run`, `shaker verify <app> --files --require-signature` |

The consumer side of the example project, with outputs, is in the
[Shaker manual](https://github.com/bytehawks-org/shaker/blob/main/docs/MANUAL.md#7-artifacts-and-trust); the contract itself is
[INTEGRATION](INTEGRATION.md) and [BUNDLE](BUNDLE.md).

## 14. Troubleshooting

| Message / symptom | Cause and fix |
|---|---|
| `sha256 MISSING` in `plan`, build refuses | the variant has no pin (or none for this platform): `ab checksum --package X [--arch A]`, put it in `packages.yaml` |
| `error: ... unresolved template variable` | a `{{ name }}` that Distillery does not know; the list is in [PACKAGES](PACKAGES.md#template-context). Shaker's own `${BH_X}` must not be written as `{{ }}` |
| `build failed (exit N), full log: ...` | open that log; the failing stage and the compiler output are there |
| `[skip] ... bundle up to date` but you changed something | the hash covers recipe, variant, assets and dependencies, not your working tree. `--force`, or check that the edited file is part of the recipe |
| `unrecognized arguments: plan` | options go **after** the sub-command: `autobuilder.py plan --root ...` |
| a git source is refused | the tag no longer points at the pinned `commit` (retagged upstream?). Do not bypass; review and re-pin |
| signature rejected after a release | a new signer: review the key, add its primary fingerprint to `signature.fingerprints` |
| `restore` refuses: root differs | the artifact was built under another `--root`; rebuild under the root you install into |
| `error: X: built as static but ...: dynamically linked` | the static recipe did not produce a static binary: [STATIC](STATIC.md) |
| `package` warns about the build root | you built with `--root`; the package would install to those paths |

## 15. Cheat sheet

```sh
ab list | plan --package P | build --package P [--with-deps] [--dry-run|--force] [--artifacts-dir D] [--store S]
ab checksum --package P [--arch A]            ab outdated | bump [--package P] [--dry-run]
ab stale --store S [--all|--exit-code]        ab chain --store S [--artifacts-dir D]
ab publish --from D --store S                 ab inventory --store S        ab sign --from D
ab restore --package P --with-deps --store S  ab verify --package P --with-deps
ab package --package P --with-deps [--dry-run]            ab scaffold --package P --from FILE [--write]
ab repro --package P [--with-deps]            ab sbom --package P [--out F]            ab image --package P [--entrypoint shaker|direct] [--base DIR]
common: --root DIR  --libc glibc|musl  --linkage dynamic|static  --arch A  --variant V  --tag library|application
```

Exit codes: `0` ok, `1` error (message on stderr), `2` usage, `3` for `stale --exit-code` / `outdated --exit-code` when there is work to do.
