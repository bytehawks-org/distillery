# Builder and runtime images

A CI job that starts from `debian:12` or `alpine:3.20` installs a toolchain, creates a Python environment and downloads nfpm **every time**.
The images in `docker/` already contain all of it, so a job starts working immediately and every run uses the same toolchain.

| Image | Dockerfile | Contains | Used for |
|---|---|---|---|
| `bytehawks-builder:glibc` | `docker/builder-glibc.Dockerfile` | Debian 12: gcc/make/perl, flex/bison, python3 + PyYAML + ruamel.yaml in `/opt/venv`, git, gpg, jq, **bubblewrap** (the `--sandbox` backend), xz/zip/unzip/patch/file, **nfpm** (pinned, checksum-verified) | glibc builds (`amd64`, `amd64-static`), deb/rpm packages |
| `bytehawks-builder:musl` | `docker/builder-musl.Dockerfile` | the same on Alpine 3.20 | musl builds (`amd64-musl`, `amd64-musl-static`), apk packages |
| `bytehawks-runtime:glibc` | `docker/runtime-glibc.Dockerfile` | `debian:12-slim` + CA certificates and time zones: bash and coreutils are already there | `autobuilder.py image --entrypoint shaker --base` |
| `bytehawks-runtime:musl` | `docker/runtime-musl.Dockerfile` | Alpine + bash, coreutils, util-linux, tar | the same, for musl artifacts |

The code is **not** baked in: a job checks the repository out and runs its own `scripts/autobuilder.py`, so the images never go stale
against the catalog. `docker/builder-*.Dockerfile` pin nfpm to the same version and SHA-256 as the `nfpm` unit of `packages.yaml`; a test
(`tests/test_docker.py`) fails if they drift, and also if the images lack a package the workflow installs.

## Build them

```sh
docker build -f docker/builder-glibc.Dockerfile -t bytehawks-builder:glibc .
docker build -f docker/builder-musl.Dockerfile  -t bytehawks-builder:musl  .
docker build -f docker/runtime-glibc.Dockerfile -t bytehawks-runtime:glibc .

# use one locally: the repository is mounted, the toolchain is in the image
docker run --rm -e BH_BUILDER_IMAGE=bytehawks-builder:glibc -v "$PWD":/work -w /work bytehawks-builder:glibc \
  python scripts/autobuilder.py chain --store dir:store --artifacts-dir dist
```
`BASE` selects the base image (`--build-arg BASE=debian:bookworm-20260101` to pin one by tag or digest). The build context is the repository root
(the Dockerfiles copy `scripts/requirements.txt`). Set `BH_BUILDER_IMAGE` when you run builds: Distillery records it in the manifest
(`toolchain.builder_image`).

## In CI

`.github/workflows/builder-images.yml` builds the four images on every pull request that touches `docker/` (to prove they still build), and on
push to `main`, weekly and on demand pushes them to `ghcr.io/<owner>/bytehawks-{builder,runtime}:{glibc,musl}` (amd64 and arm64, plus a
`-<sha>` tag). To use them, set the repository variables to the pushed references:

| Variable | Value |
|---|---|
| `BH_BUILDER_IMAGE` | `ghcr.io/<owner>/bytehawks-builder:glibc` |
| `BH_BUILDER_IMAGE_MUSL` | `ghcr.io/<owner>/bytehawks-builder:musl` |

`build-platform.yml` already runs its jobs in `vars.BH_BUILDER_IMAGE` / `vars.BH_BUILDER_IMAGE_MUSL`; its toolchain steps notice that gcc,
gpg and git are present and skip the installation, and the Python step reuses `/opt/venv`. Without the variables the workflow falls back to
the plain images and installs the toolchain as before. A private package needs the repository to be allowed to pull it (the package's
"Manage Actions access", or make it public).

## A runtime base for images

```sh
docker build -f docker/runtime-glibc.Dockerfile -t bytehawks-runtime:glibc .
docker save bytehawks-runtime:glibc | tar -x -C /tmp/base                       # an OCI layout directory
autobuilder.py image --package nginx --tag application --entrypoint shaker --shaker-dir /opt/bytehawks/core/shaker --base /tmp/base
```
See [IMAGE](IMAGE.md).

## Limits

* The image content depends on the distribution's repositories at build time; the weekly rebuild picks up their updates. For a frozen toolchain pin `BASE`
  by digest and keep the image tag you built from.
* The four Dockerfiles were linted with `docker build --check` and then built by the `builder-images` workflow on GitHub (pull request run,
  linux/amd64, no push): all four succeeded. They could not be built where they were written (Debian and Alpine mirrors unreachable there).
  The arm64 build and the push to ghcr.io happen for the first time on the first run from `main` or a manual dispatch.
* No SBOM of the images themselves yet: scan them with your registry's scanner.
