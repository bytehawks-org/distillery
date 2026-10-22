# Development guide

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m scripts.cli validate
python -m pytest -q
```
Python ≥ 3.9 works for the CLI; the builder image uses 3.12 (installed with `uv`).

## Test suite (`tests/`)

| File | Covers |
|---|---|
| `test_recipes.py` | schema, cycles, unknown deps/variants, stable needs sha, variant overlap, ordering |
| `test_builder.py` | end-to-end fake lib+app build, static gate, shared-object gate, reproducible archives, dependency fetch, sha mismatch, hardened container command |
| `test_publish.py` | Nexus and GitHub clients against in-memory fakes, every strategy, idempotency, fallback, missing credentials |
| `test_misc.py` | env expansion, artifact naming, ELF detection, tarball determinism, planner, upstream series logic, runtime bundler |
| `test_cli.py` | `validate`, `plan` outputs, `lock` rewriting |

Fake builds need `gcc`; tests skip otherwise. No network is used.

## Validating a recipe locally

1. Add `packages/<name>.yaml` with `status: experimental`.
2. Pin sources: `python -m scripts.cli lock <name>`.
3. Build in the real builder image (needs Docker/Podman and access to the registry):
   ```bash
   docker build -f docker/builder/Dockerfile --build-arg ALPINE_VERSION=3.21 -t distillery-builder:stable .
   # point the image to your local tag or push it, then:
   python -m scripts.cli build <name> --variant stable --no-fetch
   ```
4. Iterate on failures; work directories are kept on failure under `<base>/build/<name>-<version>-<variant>/`
   (`src/` has the extracted sources and `config.log`).
5. Check the artifact: `tar -tzf dist/<name>/<ver>/*.tar.gz`, run the binary, read the manifest.

### Quick musl check without Docker (Debian/Ubuntu)
Useful when no builder image is available:
```bash
sudo apt-get install -y musl-tools
sudo ln -s /usr/include/linux /usr/include/x86_64-linux-musl/linux      # kernel headers for musl-gcc
sudo ln -s /usr/include/asm-generic /usr/include/x86_64-linux-musl/asm-generic
sudo ln -s /usr/include/x86_64-linux-gnu/asm /usr/include/x86_64-linux-musl/asm
CC=musl-gcc python -m scripts.cli --base-path /tmp/dstl build nginx --executor native --output /tmp/dist --no-fetch
```
Limits: C only (no musl C++), and Debian's `libgcc_eh` is glibc-flavoured (breaks PHP JIT linking).

### Testing a recipe against other source versions
Copy `packages/` to a temp directory, change `versions` and set `source.url` to `file:///path/to/tarball`, then
use `--packages-dir`. This is how php/httpd/postgresql were validated offline.

## Conventions

- Code: type-hinted Python, small modules, errors derived from `DistilleryError`, no global state.
- Comments and docs in English; user-facing placeholders `example.com`, `192.168.x.x`.
- Recipes: steps use shell variables (`$PREFIX`, `$DESTDIR`, `$DEPS`, `$JOBS`) rather than Jinja where possible;
  avoid `{{`, `{%` and `${#` in shell. Smoke tests use `grep -q pat <<<"$(cmd)"` (SIGPIPE under `pipefail`).
- Every behavioural change gets a test and a CHANGELOG entry (`[Unreleased]`).
- Commits: imperative subject, body explains *why*. Branch `develop` → PR to `main`.

## Releasing

Distillery itself is not versioned by tags yet (see ROADMAP). Update `CHANGELOG.md`, make sure `ci.yml` is
green, merge to `main`; `build-packages.yml` then publishes whatever is new.
