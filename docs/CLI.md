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

## `list`
```
$ autobuilder.py list
nginx        application            deps: openssl,zlib,pcre2   stable=1.28.0
openssl      library,application    deps: -                    old-legacy/legacy=3.0.18, stable=3.5.4, stream=3.6.0
pcre2        library                deps: -                    all=10.43
zlib         library                deps: -                    all=1.3.1
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
