# CLI reference

```bash
pip install -r requirements.txt          # Python ≥ 3.9 (the builder image ships 3.12)
python -m scripts.cli [GLOBAL OPTIONS] <command> [OPTIONS]
```

## Global options

| Option | Default | Meaning |
|---|---|---|
| `--config PATH` | `config/config.yaml` | Configuration file |
| `--packages-dir PATH` | `packages/` | Recipe directory |
| `--base-path PATH` | env `DISTILLERY_BASE_PATH` | Overrides `variables.artifact_base_path` (work/deps/apps root). Use it for local runs; CI uses `/opt/bytehawks` |
| `-v, --verbose` | off | DEBUG logging |

Logs go to stderr (JSON when not a TTY and not in GitHub Actions, else text). Exit code `0` = success, `1` =
any `DistilleryError` (message printed as `error: ...`) or a failed publish.

## Commands

### `validate`
Loads config and all recipes; checks unknown dependencies, cycles, unknown variants, a variant mapped to two
versions, missing `sha256` on stable packages, stable→experimental dependencies, file name = package name.

### `list [PACKAGE...] [--variant V]`
Prints package, version, variant, kind and status for every target (experimental included).

### `plan`
Emits the build matrix.

| Option | Meaning |
|---|---|
| `--package P` (repeatable) | Limit to packages (explicitly named experimental packages are included) |
| `--variant V` (repeatable) | Limit to variants |
| `--include-experimental` | Include all experimental packages |
| `--skip-existing` | Drop targets already present in the repositories |
| `--force` | Ignore `--skip-existing` |
| `--registry primary\|fallback` | Registry used for the `image` field of each row |
| `--format json\|github` | `github` writes `libraries`, `applications`, `has_libraries`, `has_applications` to `$GITHUB_OUTPUT` |

### `build [PACKAGE...]`
Builds packages with their dependencies.

| Option | Meaning |
|---|---|
| `--all` | All stable packages (instead of names) |
| `--variant V` | Variant (default `defaults.build.variant`) |
| `--all-variants` | Every variant that defines the package |
| `--include-experimental` | With `--all`: include experimental |
| `--executor native\|container` | Default from `defaults.build.executor` |
| `--output DIR` | Artifact directory (default `./dist`) |
| `--no-fetch` | Always build dependencies from source (no repository access) |
| `--publish` | After building, publish everything found in `--output` |

Local example (Docker/Podman, work dir `./work`):
```bash
python -m scripts.cli build nginx --variant stable --no-fetch
```
Inside the builder image (what CI does):
```bash
python3 -m scripts.cli --base-path /opt/bytehawks build nginx --variant stable --executor native --output dist --publish
```

### `publish [--dist DIR]`
Uploads every artifact in `DIR` (default `dist`) according to `repository.strategy`. Existing artifacts are
skipped. Writes a table to `$GITHUB_STEP_SUMMARY` when set.

### `lock PACKAGE [--refresh]`
Downloads the sources of every version and writes `sha256: "<hash>"` into `packages/<name>.yaml` (comments
and formatting are preserved). `--refresh` recomputes existing pins. Supports Jinja URLs (`replace`, `vars`, ...).

### `check-updates [--format md|json]`
Compares each version with upstream releases/tags (`upstream` block) within its `series`, ignoring
pre-releases. Prints a Markdown list or JSON. Uses `github.token` for rate limits.

### `builder-matrix [--format json|github]`
One row per variant: `variant`, `alpine_version`, `image_primary`, `image_fallback` (for `builder-images.yml`).

### `image VARIANT [--registry primary|fallback]`
Prints the builder image reference.

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `DISTILLERY_BASE_PATH` | all | Same as `--base-path` |
| `SOURCE_DATE_EPOCH` | build | Fixed timestamp for archives (default: HEAD commit time) |
| `CC`, `CXX`, ... | build | Inherited by recipe steps (e.g. `CC=musl-gcc` for local musl tests) |
| `NEXUS_URL` | publish/fetch | Nexus base URL incl. `/repository` |
| `NEXUS_USER`, `NEXUS_PASSWORD` | publish/fetch | Nexus credentials (required to publish) |
| `ARTIFACTS_GITHUB_TOKEN` | publish/fetch | PAT with Contents: write on the artifacts repo |
| `GITHUB_TOKEN` | check-updates | API rate limits |
| `GITHUB_OUTPUT`, `GITHUB_STEP_SUMMARY`, `GITHUB_ACTIONS` | plan/publish/executor | GitHub Actions integration |
| `HARBOR_USER`, `HARBOR_PASSWORD` | builder-images workflow | Optional Harbor push |

Any value in `config.yaml` may reference `${VAR}` or `${VAR:-default}`; an empty variable counts as unset.

## Notes

- Repository access (`fetch`, `plan --skip-existing`) retries network errors with backoff; use `--no-fetch`
  when no repository is reachable (local development).
- `build` in `container` mode runs one container per target and never needs repository credentials inside it
  unless fetching is enabled; publishing always happens on the host.
