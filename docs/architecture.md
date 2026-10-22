# Architecture

## Goal

Produce **portable infrastructure building blocks** (web servers, runtimes, databases) from source, as
Linux binaries that run unchanged in a `FROM scratch`/Alpine container and on classic glibc VMs. The recipes
(YAML) are the single source of truth; the pipeline is reproducible and publishes to two independent stores.

## Overview

```
 packages/*.yaml          config/config.yaml
        │                        │
        └──────────┬─────────────┘
                   ▼
            distillery CLI (scripts/)
   plan ─► build (deps first) ─► verify ─► package ─► publish
                   │                                     │
     builder image (Alpine/musl, 1 per variant)   ┌──────┴───────┐
                                                  ▼              ▼
                                         Nexus raw repo   GitHub Releases
                                          (in-cluster)    (bytehawks-org/artifacts)
```

## Components

| Path | Responsibility |
|---|---|
| `config/config.yaml` | Global settings: variants, paths, toolchain flags, registries, artifact repositories |
| `packages/<name>.yaml` | One recipe per package (sources, versions per variant, steps, smoke tests) |
| `packages/patches/<name>/` | Optional local patch files referenced by recipes |
| `scripts/config/` | Config models (pydantic), loader, Jinja template engine |
| `scripts/recipes/` | Recipe models, loader, cross-validation, dependency resolver |
| `scripts/build/builder.py` | Orchestrates one build (see below) |
| `scripts/build/executor.py` | Runs steps with `bash -euo pipefail` (optionally under `fakeroot`) |
| `scripts/build/verify.py` | ELF parsing and the static gate |
| `scripts/build/bundle.py` | Runtime bundler for `link: relocatable` |
| `scripts/build/packager.py` | Reproducible tarball, manifest, SBOM |
| `scripts/build/container.py` | Runs the CLI inside the builder image through Docker/Podman |
| `scripts/publish/` | Nexus and GitHub Releases clients, strategy manager, dist scanner |
| `scripts/planner.py` | CI matrix generation (what is not yet published) |
| `scripts/upstream.py` | Upstream release monitoring |
| `docker/builder/Dockerfile` | Builder image (Alpine toolchain + Python 3.12 venv) |
| `.github/workflows/` | `ci`, `builder-images`, `build-packages`, `upstream-watch` |
| `deploy/arc/` | Helm values and hook template for the K3s runner scale set |

## Concepts

- **Variant** — a toolchain baseline = one builder image (Alpine release). `old-legacy` 3.15, `legacy` 3.20,
  `stable` 3.21, `stream` 3.22. Each recipe maps every variant to exactly one version (e.g. nginx 1.30 on
  `stable`, 1.31 on `stream`).
- **Recipe** — declarative package definition; see [packages.yaml.md](packages.yaml.md).
- **Library vs application** — libraries are installed into a shared *deps prefix*
  (`/opt/bytehawks/deps/<variant>`) and consumed by dependents; applications are self-contained trees under
  `/opt/bytehawks/apps/<name>`. Only libraries can appear in `depends_on`.
- **Status** — `stable` packages are built by CI by default and must have pinned `sha256`; `experimental`
  ones are built only on request, and a stable package may not depend on an experimental one.

## Build pipeline (one target = package × version × variant)

1. **Resolve dependencies** (topological order). For each library: already installed in the deps prefix →
   skip; else try to **fetch** the artifact from the repositories (primary, then fallback) and verify its
   `.sha256`; else **build from source** (recursively). `--no-fetch` disables fetching.
2. **Fetch source** to `path.downloads`, verify `sha256` (cached by hash). Experimental packages without a pin
   are downloaded and the computed hash is logged.
3. **Extract** (`strip_components`) and apply **patches** (filtered by `when.variant/version`).
4. **Run steps** in order with the build environment ([packages.yaml.md](packages.yaml.md#build-environment)).
   Everything installs into `DESTDIR` (staging), never into the real prefix.
5. **Post-process**: remove `*.la`, `man/info/doc`; optional **bundle** (relocatable); **verify** (static
   gate); **strip** ELF files.
6. **Package**: reproducible `tar.gz`, `.sha256`, `.manifest.json`, `.sbom.cdx.json` into the output dir.
7. **Smoke tests** run against the staged tree (`$ARTIFACT_ROOT`) — a failing test aborts the build.
8. **Install** (libraries only): the produced tarball is extracted into the deps prefix so dependents see it.
   Installing from the artifact guarantees that what is consumed is what is published.
9. **Cleanup** the work directory on success (`cleanup_on_success`); kept on failure for inspection.

## Execution modes

| Mode | Where the CLI runs | Used for |
|---|---|---|
| `executor: native` | already inside the builder image | CI (job `container:` on K3s) and manual runs inside the image |
| `executor: container` | on the host; spawns `docker|podman run` of the builder image | local development (WSL, laptop) |

The container run is hardened from `option.runtime`: non-root `bh` (1024), `--cap-drop ALL`,
`no-new-privileges`, `tmpfs /tmp`, repo mounted read-only.

## CI flow (`build-packages.yml`)

```
plan ──► libraries (matrix, ≤4 parallel) ──► applications (matrix, ≤4 parallel)
```

- **plan** computes package × version × variant, drops everything already published (`--skip-existing`),
  and emits two matrices with the builder `image` of each row.
- **libraries/applications** run *inside* the builder image on the K3s runner. They build, smoke-test and
  publish. Applications start after libraries so dependencies can be fetched instead of rebuilt (they would
  be rebuilt transparently if missing).
- JavaScript actions are not used in container jobs (checkout is a plain `git fetch`) because the JS action
  runtime does not run on every musl image.
- A single concurrency group prevents two runs from publishing the same artifact.

## Immutability and reproducibility

- Published artifacts are never overwritten: uploads are skipped when the artifact already exists. To rebuild,
  bump the version (or delete the artifact and run with `force`).
- Archives are deterministic: sorted entries, `root:root`, fixed mtime = `SOURCE_DATE_EPOCH`
  (env, else the commit time of HEAD, else 1980-01-01), gzip `mtime=0`. Two builds of the same inputs in the
  same toolchain produce the same hash (covered by a test).
- Compiler flags map the build path out (`-ffile-prefix-map`).

## Design decisions

| Decision | Reason |
|---|---|
| musl/Alpine builders | `libc.a` of musl makes fully static executables sound; glibc static breaks NSS/iconv/dlopen |
| Build in a container image per variant | The variant *is* the toolchain; reproducible and cacheable |
| One recipe file per package | Reviewable diffs, `lock` can edit a single file, no giant YAML |
| Libraries installed from their own artifact | Dependents always build against exactly what gets published |
| Mirror to Nexus **and** GitHub | Local speed/control plus an off-site copy under the Bytehawks org |
| Fail-closed static gate | "static" must be a verified property, not an intention |
