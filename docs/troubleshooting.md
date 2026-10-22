# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error: <pkg>@<variant>: link=static but these executables need a dynamic loader` | Executable linked dynamically (libtool dropped `-static`) | `make LDFLAGS="$LDFLAGS -all-static"` (libtool) or `EXTRA_LDFLAGS_PROGRAM="-all-static"` (PHP) |
| `link=static but shared objects were installed` | Build produced loadable modules (`.so`) | Build the feature in statically, disable it, or switch to `link: relocatable` + `bundle: true` |
| `stable package without sha256` / `stable packages require sha256` | Missing pin | `python -m scripts.cli lock <pkg>` |
| `sha256 mismatch for <url>` | Upstream changed the archive or wrong pin | Verify upstream; `lock --refresh` only after checking |
| `Step '<pkg>:<step>' failed with exit code 141` | SIGPIPE: `cmd \| grep -q` under `pipefail` | `grep -q pat <<<"$(cmd)"` |
| `unbound variable` in a step | `set -u`; variable not exported | Use the documented variables; `CFLAGS/CXXFLAGS/CPPFLAGS/LDFLAGS` are always defined |
| Jinja `UndefinedError` / odd `{#` behaviour in a step | Shell text parsed as Jinja | Avoid `{{`, `{%`, `${#`; use `{% raw %}...{% endraw %}` if unavoidable |
| `bundle: unresolved library ...` | A dependency of a relocatable binary is missing in the builder | `apk add` it in `docker/builder/Dockerfile` (rebuild images) |
| nginx/curl configure: "SSL modules require the OpenSSL library" | Link test failed, see `objs/autoconf.err` / `config.log` in the kept work dir | Add `-ldl -lpthread` via `env.LIBS`; check library order |
| `libpq must not be calling any function which invokes exit` | libcrypto linked into `libpq.so` registers `atexit` | OpenSSL must be built `no-atexit` (already the case) |
| `Nothing installed under DESTDIR<prefix>` | Install step ignored `DESTDIR` | Use `make install DESTDIR="$DESTDIR"` (PHP: `INSTALL_ROOT=`) |
| `PublishError ... HTTP 401/403` | Missing/invalid credentials or token scope | Check `NEXUS_USER/PASSWORD`, `ARTIFACTS_GITHUB_TOKEN` (Contents: write on the artifacts repo) |
| `Publish of ... failed on: github` with `mirror-all` | One endpoint failed; the other already has the artifact | Fix the cause and re-run `publish`; existing files are skipped |
| `no credentials (NEXUS_USER / NEXUS_PASSWORD)` | Publishing without secrets (e.g. fork PR) | Run publish only where secrets exist |
| Build hangs ~7 s per dependency locally | Repository unreachable, retries with backoff | `--no-fetch` |
| Matrix job skipped in Actions | `has_libraries/applications` is false: nothing to build (all published) | Use `force` input or bump the version |
| `container:` job cannot pull the image | GHCR package not readable by the runner, or Harbor image with GHCR credentials | Link/make the package public; set `BUILDER_REGISTRY` consistently |
| Checkout step fails in container job | Repo private and token lacks access | The workflow uses `github.token`; check repository permissions |
| `docker run` permission errors on `work/` | Host dir not writable by uid 1024 | `chown -R 1024:1024 work dist` or use a rootless runtime |
| Archives differ between two builds | Different `SOURCE_DATE_EPOCH` or toolchain | Set `SOURCE_DATE_EPOCH`; compare manifests' `file` hashes |
| `Cannot find 'version: X' entry` in `lock` | Version entry not written as `- version: "X"` | Keep the documented list format |
| PHP static link fails with `_dl_find_object` | glibc-flavoured `libgcc_eh` (non-Alpine musl-gcc) | Use the Alpine builder image; locally add `--disable-opcache-jit` |

Useful: `python -m scripts.cli -v ...` (DEBUG), the kept work dir (`<base>/build/...`), and
`python -m scripts.cli validate` after any recipe edit.
