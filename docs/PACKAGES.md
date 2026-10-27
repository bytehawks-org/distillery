# `packages.yaml` reference

```yaml
package:
  <name>:
    variant:     [ ... ]
    website:     <url>                       # informational
    git:         { ... }                     # where the source comes from
    depends_on:  [ ... ]
    strip_components: 1                      # default 1
    env:         { KEY: value }              # extra build environment (templated)
    commands:    { configure, make, check, install, pre, post }
    runtime:     { user, dirs, persistence, templates, hooks, run, healthcheck, requires, env }
    upstream:    { type, repo, tag_regex, checksum_url_template }     # release tracking
    publish_tag: library | application                                # role under which artifacts are published
    tags:        [ library | application ]
```

## Template context

`{{ ... }}` is resolved by Distillery, strictly (unresolvable → error), recursively, in every string of
the package. `${...}` is **not** touched (left for Shaker).

| Variable | Value |
|---|---|
| `name`, `package_name` | package key |
| `tag` | `library` or `application` for this build |
| `full_version`, `major_version`, `patch_version` | from the selected variant |
| `prefix`, `default_prefix` | install prefix: `libraries.default_prefix` or `applications.default_prefix` |
| `deps_prefix` | `libraries.default_prefix` (where dependencies are installed) |
| `src_dir` | `<default_src_path>/<name>-<full_version>` (cwd of the build) |
| `jobs` | CPU count or `--jobs` |
| `git.*` | the (resolved) `git:` block |
| `config.*` | the `config:` block of `config.yaml` |
| `website` | |

## `variant`

```yaml
variant:
  - name: stable                 # or a list of aliases: [old-legacy, legacy]
    major_version: 3.5
    patch_version: 4
    full_version: 3.5.4
    sha256: 967311f8...          # REQUIRED to build
```
Selection: `--variant NAME` (default `stable`, otherwise the first declared). Any extra key is kept
in the recipe hash. The `sha256` is of the downloaded tarball exactly as served.

Extra variant keys are available as `{{ variant.<key> }}` (e.g. `build: 10` → `{{ variant.build }}`) and are part
of the recipe hash.

## `upstream` and `track` - following new releases

```yaml
upstream:
  type: git-tags                                   # default (the only type today)
  repo: https://github.com/openssl/openssl         # default: git.repo
  tag_regex: '^openssl-(\d+\.\d+\.\d+)$'         # group 1 = version. Pre-releases must not match
  checksum_url_template: "{{ git.release_download_url_template }}.sha256"   # optional
```
* Tags come from `git ls-remote --tags --refs <repo>` (no API, no token, any git host).
* A variant **follows a series**: the prefix named by `track` (default: its `major_version`). `stable` with
  `major_version: 3.5` follows `3.5.x`; `track: "3"` would follow any `3.x`. Newer versions outside the series
  are only reported by `outdated`.
* `checksum_url_template` (optional): URL of a file whose first word is the SHA-256 published by upstream
  (OpenSSL publishes `<tarball>.sha256`). `bump` refuses to write a hash that differs from it.
* `checksum_url_template` may point to a sha256 (64 hex) or sha512 (128 hex) file; the pin is always sha256.
* Quote versions that end in 0 in YAML (`"1.30"`), otherwise they load as numbers.
* Remove the block to stop tracking a package.

## `signature` - OpenPGP verification of the source

```yaml
signature:
  type: gpg                                              # the only type today
  url_template: "{{ git.release_download_url_template }}.asc"      # default; use .sig where upstream does
  keys_url: https://downloads.apache.org/nifi/KEYS       # public keys: any URL, key server included
  fingerprints:                                          # REQUIRED: full 40-hex primary fingerprints = the trust anchor
    - 3FE925C2BED744021E6169A2F92A93B30C07C6D5           # Pierre Villard
```
* Checked in `build` right after the download and **before** extraction or any build command, and in `bump` before
  a new checksum is pinned. Failure = no build, no bump.
* The keys are imported into a throw-away keyring; the signature is accepted only if it is good, the key is not
  expired or revoked, and its **primary** fingerprint is in `fingerprints`. The key host can therefore not add a signer.
* A release signed by someone new (a new release manager) fails until a human reviews the key and adds the fingerprint.
* Needs `gpg` on the builder. Pin fingerprints from a trusted channel (upstream site, web of trust), not from the same download.

## `arch_map` and per-architecture sources

```yaml
arch_map: {amd64: x64, arm64: aarch64}      # our arch name -> the upstream's; {{ arch_name }} in templates
variant:
  - name: stable
    sha256: {amd64: aeab55..., arm64: 1d0410...}      # or a plain string when the source is the same everywhere
```
Keys are `<arch>` or, for sources that differ by C library, `<arch>-musl` (`amd64-musl`): the most specific one wins, so a source
that is the same on both libcs needs only the `<arch>` entries. `libc_map: {glibc: linux, musl: alpine-linux}` gives `{{ libc_name }}`
(Temurin names its Alpine builds that way); `{{ libc }}` (`glibc`/`musl`) and `{{ platform }}` (`amd64`, `amd64-musl`) are always available.
A platform without a pin is "not pinned": the build refuses. `checksum --arch arm64` prints the value to pin;
`bump` recomputes every architecture of a map. `arch` is part of the recipe hash, so artifacts stay per architecture.

## `publish_tag`
Which role the CI builds and stores for the package: `library` if the package can be one, else its first tag.
Override for packages with both tags that should be published as applications.

## `git`

```yaml
git:
  repo: https://github.com/openssl/openssl
  release_download_url_template: "{{ git.repo }}/releases/download/{{ name }}-{{ full_version }}/{{ name }}-{{ full_version }}.tar.gz"
```
`release_download_url_template` is required for `build`/`checksum`. `https://`, `http://` and `file://`
URLs work; the archive may be a tarball (any compression) or a zip (`strip_components` applies to both). The archive is cached as `<default_download_path>/<basename of the URL>`; other keys are free-form.

## `depends_on`

```yaml
depends_on:
  - zlib                              # default variant, role library (if the package is one)
  - {name: openssl, variant: stable}
  - {name: foo, variant: all, tag: application}
```
A dependency is a full unit: it is built (`--with-deps`) before the dependent, once per
(name, variant, tag), and its recipe hash feeds the dependent's.

## `commands`

Shell snippets, templates resolved, executed in this order in one session with `cwd=src_dir`:

| Stage | Default? | Typical |
|---|---|---|
| `pre` | yes | patches, `autoreconf` |
| `configure` | yes | `./configure --prefix={{ prefix }}` |
| `make` | yes | `make -j{{ jobs }}` |
| `check` | **no** (`--stages ...,check`) | `make test` |
| `install` | yes | `make install` |
| `post` | yes | strip, remove docs, symlinks |

Missing stages are skipped. Environment available: `PREFIX`, `JOBS`, `CPPFLAGS`, `LDFLAGS`,
`PKG_CONFIG_PATH`, `PATH` (with `<deps>/bin` first), plus the package `env:` map.
Use `set -e` semantics: the first failing command stops the build. Install **into `{{ prefix }}`**;
never into `/usr`.

## `runtime`

The contract with Shaker. Everything is optional; a library may have none.

```yaml
runtime:
  user:        {name: nginx, uid: 1001, gid: 0, home: "{{ prefix }}"}
  dirs:        [{path: "{{ prefix }}/run", mode: "g+rwX", owner: "1001:0"}]
  persistence: [{path: "{{ prefix }}/data", volume: /bytehawks/data}]
  templates:   [{src: nginx.conf.tmpl, dest: "{{ prefix }}/conf/nginx.conf", mode: "0640"}]
  hooks:       {pre_init: "{{ prefix }}/hooks/pre-init.d", first_init: ".../init.d", post_init: ".../post-init.d"}
  run:         {command: ["{{ prefix }}/sbin/nginx", "-c", "...", "-g", "daemon off;"], stop_signal: QUIT, pidfile: "{{ prefix }}/run/nginx.pid"}
  healthcheck: {tcp: {host: 127.0.0.1, port: "${BH_NGINX_PORT}"}}      # or {command: [argv...]}
  requires:    [openssl]
  env:         [ ...parameters... ]
```

| Key | Meaning |
|---|---|
| `user` | service account; `gid: 0` = OpenShift/arbitrary-uid convention |
| `dirs` | created by `shaker install`; `owner` `uid:gid` recursive; numeric `mode` = directory only, symbolic (`g+rwX`) = recursive |
| `persistence` | `path` inside the image → `volume` mount; seeded on first use then symlinked |
| `templates` | `src` relative to `assets/<package>/`, rendered at `shaker init` with the parameters; `mode` of the result |
| `hooks` | directories of scripts for `pre_init` (always), `first_init` (once), `post_init` (always). Files in `assets/<package>/hooks/<stage>/` are copied there (mode 0755) when the bundle is emitted |
| `run.command` | **argv list** (a string is rejected: no shell); `${BH_X}` expanded by Shaker from parameters |
| `run.stop_signal` | used by `shaker stop` and the systemd unit (`TERM` default) |
| `run.pidfile` | for `--daemon` / `stop` |
| `healthcheck` | `tcp: {host, port}` (default host 127.0.0.1) or `command: [argv]` |
| `requires` | tools that must exist (PATH, `<prefix>/bin`, `<deps>/bin`) for `shaker verify` |

### `runtime.env` - parameters

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | **must** be `BH_<PACKAGE>_<NAME>` (package uppercased, `-` → `_`), `[A-Z][A-Z0-9_]*`, unique |
| `type` | no (`string`) | `string secret int uint port bool path enum:a,b,c` |
| `default` | no | scalar; booleans become `true`/`false` |
| `required` | no | `true` → resolution fails without a value |
| `aliases` | no | list of other env names accepted (Bitnami/upstream dialects) |
| `file_key` | no | key in legacy config files (`section.key`) |
| `cli` | no | `--flag` accepted by `shaker run/env/exec` |
| `native_env` | no | variable the application itself reads, exported with the same value |
| `description` | no | shown by `shaker env <pkg> --help-vars` |

Forbidden inside any `runtime` string that goes into the bundle: `|` and newlines (the format is line oriented).

## `tags`

`library` → prefix `libraries.default_prefix`, bundle `<deps>/.bytehawks/<name>/`;
`application` → prefix `applications.default_prefix`, bundle `<prefix>/.bytehawks/`.
A package with both (openssl) needs `--tag` on the command line.

## Complete example: nginx (abridged)

```yaml
nginx:
  variant: [{name: stable, major_version: 1.28, patch_version: 0, full_version: 1.28.0, sha256: c6b5c6b0...}]
  depends_on: [{name: openssl, variant: stable}, zlib, pcre2]
  git: {repo: https://github.com/nginx/nginx,
        release_download_url_template: "{{ git.repo }}/releases/download/release-{{ full_version }}/{{ name }}-{{ full_version }}.tar.gz"}
  commands:
    configure: |
      ./configure --prefix={{ prefix }} --with-http_ssl_module --with-http_v2_module \
        --with-cc-opt="-I{{ deps_prefix }}/include" --with-ld-opt="-L{{ deps_prefix }}/lib"
    make: make -j{{ jobs }}
    install: make install
  runtime: { ... see packages.yaml ... }
  tags: [application]
```
