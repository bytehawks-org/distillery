# The bundle: Distillery → Shaker contract

Schema version: **1** (`"schema": 1` in `manifest.json`). A bundle is a directory written by
Distillery after a successful build (or by `bundle`):

```
<prefix>/.bytehawks/            applications      e.g. /opt/bytehawks/nginx/.bytehawks/
<deps>/.bytehawks/<lib>/        libraries         e.g. /opt/bytehawks/deps/.bytehawks/openssl/
├── manifest.json               identity, provenance
├── manifest.env                BH_PKG_* for Shaker (flat)
├── files.sha256                every file/directory the build installed, with SHA-256 (tamper check)
├── bundle.sha256               SHA-256 of the files below and of files.sha256 (sha256sum format)
├── bundle.sha256.asc           optional OpenPGP signature of bundle.sha256
├── rosetta.map                 parameters
├── runtime.spec                runtime behaviour
└── assets/                     templates and hooks copied from distillery/assets/<package>/
```

Text files are UTF-8, `\n` line endings, `#` starts a comment line, fields are separated by `|`.
A `|` or newline inside a value is rejected by Distillery at emit time. Shaker parses them in bash
without external tools, which is why they are not YAML/JSON.

## `manifest.json`

```json
{
  "schema": 1,
  "generator": "distillery/0.3.0",
  "package": "nginx", "variant": "stable", "tag": "application", "version": "1.28.0",
  "prefix": "/opt/bytehawks/nginx",
  "source": "https://github.com/nginx/nginx/releases/download/release-1.28.0/nginx-1.28.0.tar.gz",
  "source_sha256": "c6b5c6b086c0df9d3ca3ff5e084c1d0ef909e6038279c71c1c3e985f576ff76a",
  "recipe_sha256": "a1146e11...",
  "depends": [{"package": "openssl", "variant": "stable", "version": "3.5.4", "recipe_sha256": "eb015c52..."}],
  "built_at": "2026-10-23T05:57:53Z"
}
```
`built_at` is present only for real builds (UTC; `SOURCE_DATE_EPOCH` if set). A bundle without it is
"emitted, not built" and never counts as up to date. Shaker only needs the file to exist (it marks a
valid bundle); everything it uses is in `manifest.env`.

## `manifest.env`

```
BH_PKG_NAME=nginx
BH_PKG_VERSION=1.28.0
BH_PKG_VARIANT=stable
BH_PKG_PREFIX=/opt/bytehawks/nginx
BH_PKG_RECIPE=a1146e116deab35c59f801307975b40ab20b9f6da9582b0a6c7e11b12c512fa6
BH_PKG_ARCH=amd64
BH_PKG_LIBC=glibc
```
`BH_PKG_ARCH` / `BH_PKG_LIBC` (`glibc` or `musl`) are where the build runs; Shaker refuses to install a bundle for another
platform. Added after the first release of schema 1: a consumer that does not know them ignores them.
Exported by Shaker; available to templates (`{{ BH_PKG_PREFIX }}`), hooks and the service.

## `files.sha256`

```
<64 hex>  deps/include/zlib.h              file or link (link: hash of the target text)
----------------------------------------------------------------  deps/share/    directory (new ones only)
```
Paths are relative to the root (`/opt/bytehawks`). Written by `build` (not by `bundle`); checked by
`autobuilder.py verify` and by `shaker verify --files`; not listed inside itself. Paths under a `mutable` record of
`runtime.spec` are skipped by both checks.

## `bundle.sha256` and `bundle.sha256.asc`

```
<64 hex>  manifest.json
<64 hex>  manifest.env
<64 hex>  rosetta.map
<64 hex>  runtime.spec
<64 hex>  files.sha256
<64 hex>  assets/<file>                    one line per asset
```
`sha256sum -c` format, paths relative to the bundle directory. Written at the end of every build (and by `bundle`, over what
exists then). With `artifacts.signing.key` configured the file is signed (`bundle.sha256.asc`, armored detached OpenPGP): the
signature authenticates the bundle and, through `files.sha256`, every installed file. Shaker checks the digest on `install` and
`verify`, and the signature against **its own** pinned fingerprints (`BH_SHAKER_TRUSTED_FINGERPRINTS`,
`BH_SHAKER_REQUIRE_SIGNATURE`). See [INTEGRATION](INTEGRATION.md).

## `rosetta.map`

```
# env|type|default|flags|aliases|file_key|cli|native_env|description
BH_NGINX_PORT|port|8080||NGINX_HTTP_PORT_NUMBER|server.port|--port||HTTP listen port
BH_NGINX_SERVER_TOKENS|enum:on,off|off|||server.tokens|--server-tokens||Expose the nginx version
```

| # | Field | Rules |
|---|---|---|
| 1 | env | `BH_<PACKAGE>_<NAME>`, `[A-Z][A-Z0-9_]*`, unique per package |
| 2 | type | `string secret int uint port bool path enum:a,b,c` |
| 3 | default | text; empty = none; booleans `true`/`false` |
| 4 | flags | comma list; only `required` today |
| 5 | aliases | comma list of env names |
| 6 | file_key | text |
| 7 | cli | `--flag` |
| 8 | native_env | env name |
| 9 | description | free text (no `|`) |

Semantics (precedence, `_FILE`, validation, provenance): Shaker `docs/ROSETTA.md`.
An unknown type or malformed name makes Shaker refuse the map (`rosetta_load_map`).

## `runtime.spec`

One record per line, first field is the record type. Unknown records are ignored with a warning (forward
compatibility). `${NAME}` / `${NAME:-default}` inside fields is expanded by Shaker at run time from the
resolved parameters and `BH_PKG_*`; `{{ }}` has already been resolved by Distillery.

| Record | Fields | Used by |
|---|---|---|
| `user` | `name\|uid\|gid\|home` | install, run (privilege drop), verify, unit |
| `dir` | `path\|mode\|owner` | install (create, chown -R, chmod), verify |
| `persist` | `path\|volume` | init |
| `template` | `src\|dest\|mode` | init (render from `assets/<src>`), verify (drift) |
| `hook` | `stage\|directory` (`pre_init`, `first_init`, `post_init`) | install (create dir), init |
| `run` | `argv0\|argv1\|...` | run |
| `stopsignal` | `SIG` (without `SIG` prefix) | stop, unit |
| `pidfile` | `path` | run --daemon, stop |
| `health` | `tcp\|host\|port` or `cmd\|argv0\|...` | verify |
| `requires` | `tool` | verify |
| `mutable` | `path` (a file or a directory) | verify --files (skipped): paths the runtime rewrites on purpose |

Real example (nginx):

```
user|nginx|1001|0|/opt/bytehawks/nginx
dir|/opt/bytehawks/nginx/run|g+rwX|1001:0
dir|/opt/bytehawks/nginx/html|g+rX|1001:0
template|nginx.conf.tmpl|/opt/bytehawks/nginx/conf/nginx.conf|0640
hook|pre_init|/opt/bytehawks/nginx/hooks/pre-init.d
hook|first_init|/opt/bytehawks/nginx/hooks/init.d
hook|post_init|/opt/bytehawks/nginx/hooks/post-init.d
run|/opt/bytehawks/nginx/sbin/nginx|-c|/opt/bytehawks/nginx/conf/nginx.conf|-g|daemon off;
stopsignal|QUIT
pidfile|/opt/bytehawks/nginx/run/nginx.pid
health|tcp|127.0.0.1|${BH_NGINX_PORT}
requires|openssl
```

Notes
* `run` is an argv vector: an argument containing spaces stays one argument (`daemon off;` above).
* `dir` modes: numeric (`0750`) → that directory only; symbolic (`g+rwX`) → recursive `chmod -R`.
* Hooks are listed even if the package ships none, so `install` creates the directory for operators to mount scripts.

## `assets/`

A verbatim copy of `distillery/assets/<package>/`. Templates use Shaker's `{{ NAME }}` /
`{{ NAME:-default }}` (rendered with all resolved parameters and `BH_PKG_*`). Hook scripts shipped by
the package live here too (copy them to the hook directories in a `post` build stage).

## Compatibility rules

* A producer may add records/fields at the end of a line and new record types; consumers ignore
  what they do not know (with a warning). Removing or reordering requires `schema` + 1.
* Shaker reads the bundle of the build it is shipped with; mixing versions across a schema bump is unsupported.
* Both repositories test the contract: Distillery's `test_bundle.py` (formats) and Shaker's
  `tests/test_integration.sh` (Distillery builds, Shaker consumes).
