# Package recipes

One file per package: `packages/<name>.yaml` (the file name must equal `package.name`).
See [`docs/template_package.yaml`](template_package.yaml) for a complete example (nginx).

```yaml
version: 1.0.0
schema: "distillery-package"

package:
  name: nginx
  kind: application            # library | application
  summary: "HTTP and reverse proxy server"
  license: "BSD-2-Clause"
  website: "https://nginx.org/"
  status: stable               # stable | experimental (experimental is built only on request)
  link: static                 # static | relocatable
  bundle: false                # relocatable only: bundle musl loader + shared libs
  depends_on: [zlib, pcre2, openssl]   # libraries only
  prefix: null                 # optional install prefix override

  source:
    type: tarball              # tarball | repack (binary distribution to repackage)
    url: "https://.../nginx-{{ version }}.tar.gz"
    strip_components: 1

  versions:                    # every variant maps to exactly one version
    - version: "1.30.6"
      sha256: "..."            # required for stable packages (`lock` fills it in)
      variants: [stable]       # variant names, or [all]
      series: "1.30"           # prefix tracked by `check-updates`
      vars: {}                 # extra template variables -> {{ vars.key }}
      url: null                # overrides source.url for this version

  patches:
    - file: fix-musl.patch     # packages/patches/<name>/fix-musl.patch (or url + sha256)
      strip: 1
      when: {variant: old-legacy}

  env: {}                      # extra environment for the steps (Jinja allowed)

  steps:                       # run with `bash -euo pipefail`, cwd = extracted sources
    - name: configure
      run: ./configure --prefix="$PREFIX"
    - {name: build,   run: 'make -j"$JOBS"'}
    - {name: install, run: 'make install DESTDIR="$DESTDIR"'}

  smoke_tests:                 # run against the staged tree ($ARTIFACT_ROOT) before publishing
    - run: '"$ARTIFACT_ROOT/sbin/nginx" -V'

  upstream:
    type: github-releases      # github-releases | github-tags | none
    repo: nginx/nginx
    tag_regex: '^release-(\d+\.\d+\.\d+)$'   # one capture group = version
```

## Build environment

| Variable | Meaning |
|---|---|
| `$PREFIX` | final install prefix (compiled into the binaries) |
| `$DESTDIR` | staging root: **install into `$DESTDIR$PREFIX`**; the tarball is made from there |
| `$DEPS` | prefix of the installed dependency libraries (`include/`, `lib/`, `bin/*-config`) |
| `$JOBS`, `$PKG_NAME`, `$PKG_VERSION`, `$VARIANT`, `$ARCH` | build info |
| `$CFLAGS $CXXFLAGS $LDFLAGS $CPPFLAGS` | from `defaults.build.env` + `-I$DEPS/include -L$DEPS/lib` |
| `$PKG_CONFIG_PATH`, `$PKG_CONFIG` | point to `$DEPS`; `pkg-config --static` is used |
| `$ARTIFACT_ROOT` | smoke tests only: the staged package tree |

Jinja variables available in `url`, `env`, `steps` and `smoke_tests`: `name`, `version`, `major_minor`, `variant`,
`arch`, `jobs`, `prefix`, `destdir`, `deps_prefix`, `srcdir`, `dep` (name → version of each dependency), `vars`.
Because the scripts are Jinja templates, avoid `{{`, `{%` and `${#` in shell code (use `${var}` forms).

## Shell pitfalls

- Steps run with `set -euo pipefail`: `cmd | grep -q x` can die with exit 141 (SIGPIPE) because `grep -q` exits early.
  Use `grep -q x <<<"$(cmd)"` in smoke tests.

## Static linking checklist

- Autotools projects using libtool swallow `-static`; build with `make LDFLAGS="$LDFLAGS -all-static"` (see `pcre2`, `curl`).
- A static OpenSSL needs `-ldl -lpthread` at link time: `env: {LIBS: "-ldl -lpthread"}`.
- Do not enable zlib *inside* OpenSSL if a dependent's configure test cannot link `-lz` after `-lcrypto`.
- Software that needs `dlopen()` at runtime (database servers with extensions, JVM) → `link: relocatable` + `bundle: true`; set
  `env.LDFLAGS` without `-static` for those recipes.
- If the build fails with *"link=static but these executables need a dynamic loader"*, the listed binaries were linked dynamically.

## Adding a package

1. `packages/<name>.yaml` with `status: experimental`.
2. `python -m scripts.cli lock <name>` pins the `sha256` of every version.
3. `python -m scripts.cli build <name> --variant stable` (container) and fix until green.
4. Switch to `status: stable`, open a PR; CI builds and publishes it.
