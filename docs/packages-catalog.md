# Package catalog

Generated from `packages/*.yaml` at the time of writing; `python -m scripts.cli list` is authoritative.

| Package | Kind | Link | Status | Depends on | Versions (variants) |
|---|---|---|---|---|---|
| apr-util | library | static | experimental | apr, expat | 1.6.5 (all) |
| apr | library | static | experimental | - | 1.7.6 (all) |
| curl | library | static | stable | zlib, openssl | 8.23.0 (all) |
| expat | library | static | stable | - | 2.9.0 (all) |
| httpd | application | static | experimental | zlib, pcre2, openssl, apr, apr-util | 2.4.69 (all) |
| libxml2 | library | static | experimental | zlib | 2.15.4 (all) |
| mariadb | application | relocatable+bundle | experimental | zlib, openssl, pcre2 | 10.11.19 (old-legacy); 11.4.13 (legacy); 11.8.9 (stable); 12.3.3 (stream) |
| nginx | application | static | stable | zlib, pcre2, openssl | 1.26.3 (old-legacy); 1.28.3 (legacy); 1.30.6 (stable); 1.31.8 (stream) |
| oniguruma | library | static | stable | - | 6.9.10 (all) |
| openjdk | application | relocatable+bundle | experimental | - | 17.0.20.1 (old-legacy); 21.0.13 (legacy); 25.0.4.1 (stable); 26.0.2.1 (stream) |
| openssl | library | static | stable | - | 3.0.22 (old-legacy); 3.5.9 (legacy,stable); 3.6.5 (stream) |
| pcre2 | library | static | stable | - | 10.49 (all) |
| php | application | static | experimental | zlib, openssl, curl, libxml2, oniguruma | 8.2.35 (old-legacy); 8.3.36 (legacy); 8.4.27 (stable); 8.5.12 (stream) |
| postgresql | application | relocatable+bundle | experimental | zlib, openssl | 14.24 (old-legacy); 16.15 (legacy); 17.11 (stable); 18.6 (stream) |
| zlib | library | static | stable | - | 1.3.2 (all) |

## Notes per package

| Package | Details |
|---|---|
| zlib | `--static`. |
| pcre2 | JIT on, 8-bit only; `-all-static` so `pcre2grep/pcre2test` are static. |
| openssl | `no-shared no-tests no-docs no-atexit -static`, `--openssldir=/etc/ssl`, `--libdir=lib`; installs with `install_sw`. 3.0.x is end-of-life (2026-09) and only kept for `old-legacy`. |
| expat | library only (`--without-xmlwf`). |
| oniguruma | for PHP `mbregex`. |
| curl | libcurl + static `curl` tool; OpenSSL + zlib only (no nghttp2, brotli, zstd, psl, idn, ldap, ssh). CA bundle path `/etc/ssl/certs/ca-certificates.crt`. |
| libxml2 | zlib + iconv (libc), no python/lzma/icu. |
| nginx | http: ssl, v2, realip, gzip_static, stub_status, sub; stream (+ssl, realip); threads, file-aio, pcre-jit. HTTP/3 not enabled. Smoke test also runs `nginx -t`. |
| apr / apr-util | static, no DB drivers/crypto/ldap. |
| httpd | MPM event, `--enable-mods-static=most --disable-so` (no `LoadModule`), ssl, proxy(+fcgi), rewrite, deflate; HTTP/2 not enabled. |
| php | CLI + FPM. Extensions: bcmath, calendar, ctype, curl, dom, exif, fileinfo, filter, iconv, libxml, mbstring (+mbregex), mysqli, mysqlnd, opcache (built in, JIT on), openssl, pcntl, PDO, pdo_mysql, posix, session, SimpleXML, sockets, tokenizer, xml, xmlreader, xmlwriter, zlib. No gd/intl/zip/sodium/pgsql yet (roadmap). `php.ini-production` shipped as `etc/php.ini-production`; config path `$PREFIX/etc`, scan dir `etc/php.d`. |
| postgresql | relocatable bundle; OpenSSL, zlib; no readline/ICU yet; `world-bin` (server, clients, contrib modules). |
| mariadb | relocatable bundle; plugins such as rocksdb/mroonga/spider/connect/columnstore/tokudb/oqgraph/s3 disabled; mariabackup off; no systemd/uring/numa/wsrep. |
| openjdk | Eclipse Temurin musl tarball repackaged (JDK, `x64_alpine-linux`), bundled. Not compiled from source. |

## Validation status

| Level | Packages |
|---|---|
| **Built and smoke-tested** with a musl toolchain (Debian `musl-gcc`) and the shipped recipe | zlib, pcre2, openssl, expat, oniguruma, curl, nginx |
| Built and smoke-tested with **older upstream versions** (sources reachable from the dev sandbox) | php (8.3.6), httpd (2.4.58), apr (1.7.2), apr-util (1.6.3), libxml2 (2.9.14), postgresql (16.15, also functionally tested) |
| **Untested** | mariadb; openjdk bundling (sources pinned, repack verified); the builder Dockerfile; all workflows on a real ARC/K3s runner |

A package is promoted to `stable` after its first green run on the real builder images with pinned `sha256`.

## Variant policy

`old-legacy` / `legacy` / `stable` / `stream` follow the support horizon of the base toolchain (see
`support_until` in `config.yaml`). Recipes should map: oldest supported series → `old-legacy`, previous LTS →
`legacy`, current LTS → `stable`, newest release → `stream`. Update with `check-updates` + `lock`.
