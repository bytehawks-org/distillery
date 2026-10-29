# Static linking

`--linkage static` (or `BH_LINKAGE=static`) builds the packages that define a `static:` recipe without a dynamic loader.
Linkage is a third part of the platform, after the architecture and the C library:

| Platform | Meaning |
|---|---|
| `amd64` | glibc, dynamic (as always) |
| `amd64-musl` | musl, dynamic |
| `amd64-static` | glibc, static (see the limits below) |
| `amd64-musl-static` | musl, static: the preferred way to get a self-contained binary |

Static artifacts are separate artifacts (own name, recipe hash and manifest `linkage`), so they live in the same stores next to the
dynamic ones.

## What is static already

Libraries of the catalog (`openssl`, `zlib`, `pcre2`, `cjson`) ship archives only, so every application already links them in.
What `--linkage static` removes is the dependency on the **C library**: `ldd` says "not a dynamic executable".
A package without a `static:` block has nothing to link differently: it keeps its dynamic identity and is built and stored once
for both linkages (a static `nginx` depends on the same `openssl` artifact as a dynamic one).

## Recipe: `static:`

```yaml
nginx:
  commands: { configure: ..., make: ..., install: ... }       # the default (dynamic) recipe
  static:
    binaries: [sbin/nginx]                  # paths below the prefix that must come out without a dynamic loader
    commands:                               # replace the named stages, the others are kept
      configure: ./configure ... --with-ld-opt="-L{{ deps_prefix }}/lib -static -ldl -lpthread"
    env: { LDFLAGS: ... }                   # merged over the package env
```

* The override is applied only with `--linkage static`; `linkage` and the resulting recipe are part of the recipe hash.
  A dynamic unit hashes exactly as before.
* After the install stage the build reads the ELF headers of `binaries` and **fails** if one has a `PT_INTERP` or `DT_NEEDED`
  entry (`error: nginx: built as static but sbin/nginx: dynamically linked (...)`). A static build that silently came out dynamic cannot be published.
* `manifest.json` records `"linkage"`, `manifest.env` has `BH_PKG_LINKAGE`, the artifact sidecar has `linkage`.

## Limits

| | glibc static | musl static |
|---|---|---|
| NSS (`getpwnam`, `getgrnam`, `initgroups`, `getaddrinfo`) | still loads `libnss_*` with `dlopen` at run time: **needs the same glibc version on the host**; the linker warns | built in, no host dependency |
| `dlopen` (plugins, modules) | does not work | does not work |
| Runs on another distribution | only with a compatible glibc | yes |

Consequences for the catalog:

| Package | Static build |
|---|---|
| nginx | works; the `user` directive and the resolver use NSS (glibc: see above). Dynamic modules are not available (none are built) |
| valkey | works **without Lua**: its scripting engine is found with `dlopen(NULL)`/`dlsym()` and aborts at start in a static executable, so `BUILD_LUA=no` (`EVAL`, `FUNCTION` return `ERR Could not find scripting engine 'lua'`) |
| mosquitto | broker, `mosquitto_pub/sub/rr/passwd` static, `libmosquitto.a`; no plugins (dynamic security, etc.) |
| prometheus | already a static Go binary, no `static:` block needed |
| nifi, temurin | not applicable: a JVM and its `.so` files |

## Checked

Built and run on glibc 2.39: nginx (`-t`), valkey (`PING`, `SET`/`GET`), mosquitto (broker, `pub`/`sub` round trip). musl static
needs an Alpine builder (`BH_LIBC=musl` on the Alpine image of the workflows) and has not been compiled yet.
