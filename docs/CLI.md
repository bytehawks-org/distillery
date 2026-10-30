# CLI reference

```
python3 scripts/autobuilder.py <command> [options]
```
Exit codes: `0` success · `1` error (message on stderr, prefixed `error:`) · `2` usage.
Output: progress and build output on stdout, errors on stderr.

## Common options

| Option | Default | |
|---|---|---|
| `--package NAME` | required (not for `list`) | key in `packages.yaml` |
| `--variant NAME` | `stable`, else the first declared | alias names work (`legacy` for `old-legacy`) |
| `--tag library\|application` | the package's only tag | needed when the package has both |
| `--config FILE` | `<repo>/config.yaml` | |
| `--packages FILE` | `<repo>/packages.yaml` | |
| `--assets DIR` | `<repo>/assets` | where `assets/<package>/` live |
| `--root DIR` | from `config.yaml` | rebase every path ([CONFIG](CONFIG.md#--root)) |
| `--arch NAME` | detected (`amd64`, `arm64`) | architecture label, part of the recipe hash and of artifact names |
| `--linkage dynamic\|static` | `dynamic` (`BH_LINKAGE` overrides) | `static` builds the packages that define a `static:` recipe without a dynamic loader (platform `amd64-static`, `amd64-musl-static`), see [STATIC](STATIC.md) |
| `--libc glibc\|musl` | detected (`BH_LIBC` overrides): musl when `/lib/ld-musl-*` exists | C library the units are built for; part of the recipe hash, of the artifact name (`amd64-musl`) and of the manifest |

## `list`
```
$ autobuilder.py list
cjson        library                deps: -                        all=1.7.19
mosquitto    application            deps: openssl,cjson            stable=2.1.2
nfpm         application            deps: -                        stable=2.48.0
nginx        application            deps: openssl,zlib,pcre2       stable=1.28.0
nifi         application            deps: temurin                  stable=2.9.0
openssl      library,application    deps: -                        old-legacy/legacy=3.0.18, stable=3.5.4, stream=3.6.0
pcre2        library                deps: -                        all=10.43
prometheus   application            deps: -                        stable=3.15.1
temurin      application            deps: -                        stable=21.0.9
valkey       application            deps: openssl                  stable=9.1.3
zlib         library                deps: -                        all=1.3.1
```

## `plan`
Dependency-ordered build list with source status and recipe hash. Does not touch the disk.
```
$ autobuilder.py plan --package nginx
1. openssl 3.5.4 [stable/library] sha256 ok, up-to-date, recipe 90550aa41754
2. zlib 1.3.1 [all/library] sha256 ok, to build, recipe e4356b338760
3. pcre2 10.43 [all/library] sha256 ok, to build, recipe c2a03095f605
4. nginx 1.28.0 [stable/application] sha256 ok, to build, recipe f21cc55f620f
```
`sha256 MISSING` means the build would be refused.

## `build`

| Option | |
|---|---|
| `--with-deps` | build every dependency first (without it: a warning if dependencies are not built) |
| `--dry-run` | print for each unit: source URL, sha256, prefix, bundle dir and the **exact shell script**; downloads nothing, writes nothing |
| `--force` | rebuild even when the bundle's recipe hash matches |
| `--jobs N` | value of `{{ jobs }}` and `JOBS` (default: CPU count) |
| `--stages a,b,c` | subset/order among `pre configure make check install post` (default without `check`) |
| `--allow-unverified` | accept a variant without `sha256` and print the computed value. For development only |
| `--store SPEC` | (repeatable) restore units found in these stores instead of building them ([ARTIFACTS](ARTIFACTS.md)) |
| `--artifacts-dir DIR` | package every unit built into `DIR` (`<name>.tar.gz` + `.json`) |

Behaviour per unit: `[skip]` if up to date → otherwise download (cache by checksum) → verify → extract →
stages → bundle → `[done] ... in Ns`. On failure: `error: build failed (exit N), full log: <path>`; nothing
is written to the bundle.

Legacy invocation (`autobuilder.py --package X --tag Y`) is mapped to `build --dry-run`.

## `bundle`
Writes the Shaker bundle **without building** (parameters, spec, manifest without `built_at`, assets).
`--out DIR` overrides `<prefix>/.bytehawks[/<lib>]`. Useful to iterate on `runtime:` and templates and
re-test with Shaker against an existing build:

```sh
autobuilder.py bundle --package nginx --root /opt/bhtest
shaker verify nginx
```
A bundle without `built_at` is never considered "up to date" by `build`.

## `checksum`
Downloads the variant's source (no checksum required) and prints `<sha256>  <file>` for pinning.
Always cross-check with the upstream's published checksum or signature before committing it.
```
$ autobuilder.py checksum --package zlib
9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23  zlib-1.3.1.tar.gz
```

## Upstream tracking

### `outdated [--package P] [--variant V] [--format text|json] [--exit-code]`
Compares each variant's pinned version with the tags of its upstream repository (`git ls-remote --tags`,
filtered by `upstream.tag_regex`, restricted to the followed series). `--exit-code` returns 3 when something is outdated.
```
nginx      stable   1.28.0  follows 1.28  latest 1.28.3  OUTDATED  (newer series: 1.31.8, 1.30.6, 1.29.8)
openssl    stable   3.5.4   follows 3.5   latest 3.5.9   OUTDATED  (newer series: 4.0.3, 3.6.5)
```
Packages without `upstream:` are not listed. "Newer series" is informational: a bump never changes series.

### `bump [--package P] [--variant V] [--to VERSION] [--dry-run] [--summary-file F] [--format json]`
For every outdated variant (or `--to`): downloads the new source, computes its SHA-256, **cross-checks the
upstream-published checksum** when `upstream.checksum_url_template` exists (mismatch = error, nothing written),
then rewrites `version`, `patch_version` and `sha256` of that variant in the file given by `--packages`
(comments, quoting and flow/block style preserved; requires `ruamel.yaml`). `--dry-run` computes and verifies only.
`--summary-file` receives the JSON list used for the pull request body.

## Artifacts and automation

### `stale [--store S]... [--package P[,Q]] [--variant V] [--kind all|library|application] [--all] [--force] [--exit-code] [--format json] [--github-output FILE]`
Every catalog unit (each variant, under its publish role) whose exact recipe is not in the stores, **dependencies first**.
`--all` includes `present` ones, `--force` marks the selection `rebuild`. `--github-output` appends
`libs`, `apps` (`{"include":[...]}` matrices), `has_libs`, `has_apps`, `count` for GitHub Actions. `--exit-code`: 3 when stale.

### `chain [--store S]... [--artifacts-dir DIR] [--package P[,Q]] [--variant V] [--kind K] [--force] [--dry-run]`
Builds the stale units in dependency order; whatever is not stale is **restored** from the stores instead of rebuilt.
Built units are packaged into `--artifacts-dir` (which is also read as the first store, so units built a moment ago
are reused by the next one).

### `inventory [--store S]... [--format json]`
Lists the artifacts held by the stores (name, size, creation, expiry, store).

### `restore --package P [--variant V] [--tag T] [--with-deps] [--force] [--store S]...`
Installs a unit from the stores without compiling; refuses a unit not found, an unsafe tarball, a hash mismatch or another recipe.

### `package --package P [--variant V] [--tag T] [--format deb,rpm,apk] [--out DIR] [--with-deps] [--dry-run] [--nfpm PATH]`
Writes native packages of a **built** unit (`build` or `restore` first) into `--out` (default `dist/packages`) plus `SHA256SUMS`.
Names, release and metadata come from `packaging:` in `config.yaml`; see [PACKAGING](PACKAGING.md). `--with-deps` also packages
the dependencies. `--dry-run` prints names, file counts and dependencies without nfpm.

### `sign --from DIR [--force]`
Signs (`<name>.json.asc`, key from `artifacts.signing.key` or `BH_SIGNING_KEY`) every artifact of `DIR` that has no
signature yet; `--force` signs again. See [ARTIFACTS](ARTIFACTS.md#signatures).

### `publish --from DIR [--store S]... [--ignore-unsupported]`
Pushes the artifacts of `DIR` to the stores that do not have them yet (idempotent). `github` cannot be published to from a script.

### `verify --package P [--variant V] [--tag T] [--with-deps]`
Checks the installed files against `files.sha256` of the bundle: `ok` / `FAIL` with `missing <path>` / `modified <path>`; exit 1 on any problem.

### `affected --package P [--format json]`
`P` followed by every package that depends on it, directly or not.
