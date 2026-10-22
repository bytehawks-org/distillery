# distillery

Bytehawks **Distillery** — the source of truth and build engine for portable, reproducible
infrastructure building blocks: web servers, language runtimes and databases built from
source as **static** (or fully **relocatable**) Linux binaries, so the same artifact runs in a
`FROM scratch` container, on Alpine, or on any classic VM/bare-metal distro.

```
packages/*.yaml ──► distillery CLI ──► builder image (Alpine/musl) ──► tar.gz + sha256 + manifest + SBOM
                        │                                                   │
                  GitHub Actions on K3s (ARC)                  Nexus (raw)  +  GitHub Releases (mirror)
```

## Concepts

| Concept | Meaning |
|---|---|
| **Recipe** | One YAML per package in `packages/` (sources, versions, build steps, smoke tests). |
| **Variant** | Toolchain baseline = builder image: `old-legacy` (Alpine 3.15), `legacy` (3.20), `stable` (3.21), `stream` (3.22). A recipe maps each variant to a version. |
| **`link: static`** | Every executable is verified to have no dynamic loader (ELF check). The build **fails** otherwise. |
| **`link: relocatable`** | Cannot be static (dlopen-based servers, JVM). With `bundle: true` the musl loader and shared libraries are shipped in `lib/bundled/` and executables are wrapped, so it runs on glibc *and* musl hosts, from any directory. |
| **Library / application** | Libraries install in a shared deps prefix used by dependents; applications are self-contained trees under `/opt/bytehawks/apps/<name>`. |

Artifact: `<package>-<version>-<variant>-linux-<arch>.tar.gz` with a root `<package>-<version>/` directory,
plus `.sha256`, `.manifest.json` (sources, deps, file inventory with hashes, builder) and a CycloneDX
`.sbom.cdx.json`. Archives are reproducible (sorted, `root:root`, `SOURCE_DATE_EPOCH` from the commit).

## Package status

| Package | Kind | Link | Status |
|---|---|---|---|
| zlib, pcre2, openssl, expat, oniguruma, curl | lib | static | **stable** — built, verified static, smoke-tested |
| nginx | app | static | **stable** — built, verified static, smoke-tested |
| libxml2, apr, apr-util | lib | static | experimental — recipe only |
| httpd, php (CLI+FPM) | app | static | experimental — recipe only |
| postgresql, mariadb | app | relocatable + bundle | experimental — recipe only |
| openjdk (Temurin musl repack) | app | relocatable + bundle | experimental — recipe only |

*Experimental* packages are skipped by CI unless requested (`--include-experimental` / workflow input).
Promote one to `stable` (and pin its `sha256` with `lock`) after its first green CI run. Honest limits:

- **PostgreSQL / MariaDB** load modules with `dlopen()` (plpgsql, plugins) → they cannot be fully static; they ship as relocatable bundles.
- **OpenJDK** is the official Temurin musl build repackaged (a JVM cannot be statically linked), not compiled from source.
- Static glibc is *not* used in CI: the builder images are Alpine/musl, which is what makes static linking sound.

## CLI

```bash
pip install -r requirements.txt
python -m scripts.cli validate                    # config + recipes (cycles, variants, sha256...)
python -m scripts.cli list                        # package × version × variant
python -m scripts.cli plan --skip-existing        # what CI would build (JSON matrix)
python -m scripts.cli build nginx --variant stable            # in the builder image via Docker/Podman
python -m scripts.cli build nginx --executor native           # already inside the builder image
python -m scripts.cli build php --include-experimental --publish
python -m scripts.cli publish --dist dist         # upload everything in dist/
python -m scripts.cli lock php                    # download sources and pin sha256 in the recipe
python -m scripts.cli check-updates               # newer upstream releases
```

Dependencies are resolved in topological order. Each dependency is **fetched** from Nexus/GitHub when already
published, otherwise built from source (`--no-fetch` forces a rebuild). Local builds use `./work` as base path
(`--base-path`) and write artifacts to `./dist`.

## CI / CD (GitHub Actions on K3s)

| Workflow | Purpose |
|---|---|
| `ci.yml` | validate recipes + unit tests (GitHub-hosted). |
| `builder-images.yml` | builds the Alpine builder images (GHCR, + Harbor if configured); weekly refresh. |
| `build-packages.yml` | `plan` (skips what is already published) → `libraries` matrix → `applications` matrix, inside the builder image on the K3s runner scale set; publishes to **Nexus and GitHub Releases** (`mirror-all`). |
| `upstream-watch.yml` | weekly issue with newer upstream releases. |

Setup (ARC on K3s, Nexus repository, GitHub artifacts repo, secrets): [docs/k3s-runner.md](docs/k3s-runner.md).

## Documentation

- [docs/config.yaml.md](docs/config.yaml.md) — `config/config.yaml` reference
- [docs/packages.yaml.md](docs/packages.yaml.md) — recipe reference and how to add a package
- [docs/k3s-runner.md](docs/k3s-runner.md) — runner, Nexus and secrets setup

## Roadmap

SBOM is generated; signing (`generate_signature`, cosign) and arm64 builds are not implemented yet.
