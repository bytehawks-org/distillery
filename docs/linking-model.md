# Linking model: `static` and `relocatable`

Portability is a **verified property**, not an intention. Every recipe declares a `link` mode and the builder
enforces it.

## `link: static`

Fully static executables (musl `libc.a`). They need no loader, no shared libraries and no particular distro.

**Enforced by the static gate** (`scripts/build/verify.py`):
- every executable ELF in the staged tree must have **no `PT_INTERP`** program header (static or static-PIE);
- an *application* must not install shared objects (`*.so`, `*.so.N`): a static binary cannot `dlopen` them;
- libraries only produce a warning for shipped `.so` files.

A violation fails the build with the list of offending files.

### Making a build static
- Toolchain flags come from `defaults.build.env`: `LDFLAGS=-static`, `-fPIC`, hardening, `-ffile-prefix-map`.
- libtool swallows `-static`: pass `LDFLAGS="$LDFLAGS -all-static"` at `make` time (pcre2, curl, libxml2).
- A static OpenSSL needs `-ldl -lpthread` on the link line: `env: {LIBS: "-ldl -lpthread"}`.
- `pkg-config --static` is the default `PKG_CONFIG`, so `Libs.private` is honoured.
- OpenSSL is built `no-shared no-atexit`; zlib is not linked into it (a dependent's link test cannot put `-lz`
  after `-lcrypto`).
- Everything is compiled with `-fPIC`, so the same static libraries can also be linked into shared modules of
  `relocatable` packages.

### Worked example: PHP
`--disable-all` plus an explicit extension list; OPcache is hard-wired as a shared `zend_extension`, so the recipe
patches `ext/opcache/config.m4`, adds the missing `php_opcache.h` and registers the zend extension from a tiny
internal module (`opcache-static`), then runs `buildconf --force`. The smoke test asserts that `opcache` is
enabled and the gate asserts no `.so` was installed.

## `link: relocatable`

For software that cannot be static because it loads code at runtime: database servers (PostgreSQL extensions,
MariaDB plugins) and the JVM. With `bundle: true` the builder runs the **runtime bundler**
(`scripts/build/bundle.py`) after the install step:

1. Collect every dynamic ELF in the tree (executables **and** `.so` modules).
2. Resolve dependencies with the **target's own loader** (`ld-musl-<arch>.so.1 --list FILE`, with the tree's
   library directories on `LD_LIBRARY_PATH`); an unresolved library aborts the build.
3. Copy the loader and every library that lives outside the tree into `lib/bundled/`
   (`libc.musl-<arch>.so.1` becomes a symlink to the loader).
4. Rename each dynamic executable `X` → `X.bin` and write a POSIX `sh` wrapper `X`:
   ```sh
   exec "$root/lib/bundled/ld-musl-x86_64.so.1" --library-path "$root/lib/bundled:$root/lib" "$here/X.bin" "$@"
   ```
   The wrapper resolves symlinks and computes `root` from its own location, so the tree runs from **any
   directory** and on **glibc and musl hosts** alike (only `/bin/sh` is required).
5. The gate then requires that no unwrapped dynamic executable is left.

Properties:
- Shared modules loaded with `dlopen` work, because they run under the bundled loader and library path.
- `argv[0]`/`/proc/self/exe` of the real binary is `X.bin` inside the same directory, so layouts based on
  `bin/../share` (PostgreSQL) keep working.
- Recipes for relocatable packages must **not** use `-static`: override `env.LDFLAGS` (see `postgresql.yaml`).

Validated: PostgreSQL 16.15 built with a musl toolchain, extracted elsewhere on a glibc host and run under
`env -i`: `initdb`, server start, `psql`, `CREATE EXTENSION pgcrypto` (dlopen) and OpenSSL all work.
The JVM and MariaDB bundles are **not yet validated** (see [packages-catalog.md](packages-catalog.md)).

## Choosing a mode

| Situation | Mode |
|---|---|
| Single binary or fixed feature set, no `dlopen` | `static` |
| Needs loadable extensions/plugins or a JVM | `relocatable` + `bundle: true` |
| Library consumed by other recipes | always `static` |

## Limits

- Only the CPU architecture of the builder is produced (amd64 today).
- Wrappers need `/bin/sh`; the bundle needs a Linux kernel recent enough for the bundled musl (any current one).
- Locale/NSS: static musl has no NSS plug-ins; user/group lookups read `/etc/passwd` directly, DNS uses musl's
  resolver (no `nsswitch.conf`, no `search` randomisation).
- TLS roots: OpenSSL uses `/etc/ssl` (`--openssldir`) and curl `/etc/ssl/certs/ca-certificates.crt`; mount or
  install a CA bundle in `FROM scratch` images.
