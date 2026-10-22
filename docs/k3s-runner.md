# Running the pipeline on K3s

```
GitHub ──workflow──► ARC scale set "bytehawks-k3s" (K3s) ──► job container = builder image
                                                                  │ build, test, package
                                          ┌───────────────────────┴───────────────────────┐
                                  Nexus raw repo (in-cluster)                 GitHub Releases (bytehawks-org/artifacts)
```

Jobs run **inside** the builder image (`container:`), so the CLI is used with `--executor native`; no Docker
socket or DinD is needed on the node.

## 1. Runner scale set (ARC)

```bash
helm install arc oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller \
  -n arc-systems --create-namespace

kubectl create ns arc-runners
kubectl -n arc-runners create secret generic arc-github-app --from-literal=github_token=<PAT or GitHub App>
kubectl apply -f deploy/arc/hook-extension.yaml          # CPU/RAM of the builder container

helm install bytehawks-k3s oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
  -n arc-runners -f deploy/arc/values.yaml
```

`runnerScaleSetName` (`bytehawks-k3s`) is the `runs-on:` label. Override with the repository variable `RUNNER_LABEL`.
`maxRunners` bounds the parallel builds; size memory for the heaviest package (MariaDB ≈ 6 GiB).

## 2. Nexus

Nexus 3 inside the cluster (default URL `http://nexus.nexus.svc.cluster.local:8081/repository`).

1. Create a **raw (hosted)** repository named `bytehawks-packages`; set *Deployment policy → Disable redeploy*
   (published artifacts become immutable).
2. Create a role with `nx-repository-view-raw-bytehawks-packages-*` and a user for CI.
3. Repository variable `NEXUS_URL` (only if different from the default; must end with `/repository`),
   secrets `NEXUS_USER`, `NEXUS_PASSWORD`.

Layout: `<package>/<major.minor>/<version>/<package>-<version>-<variant>-linux-<arch>.tar.gz` (+ sidecars).

## 3. GitHub artifacts repository

Create `bytehawks-org/artifacts` (any content, at least one commit). Each package version becomes a release
`<package>-<version>` holding the assets of all variants. Create a fine-grained PAT with **Contents: write**
on that repository and store it as secret `ARTIFACTS_GITHUB_TOKEN`.

## 4. Builder images

`builder-images.yml` pushes `ghcr.io/bytehawks-org/distillery-builder:<variant>` (and Harbor when
`HARBOR_USER`/`HARBOR_PASSWORD` secrets exist). Run it once before the first build:
`Actions → Builder images → Run workflow`. The GHCR packages must be readable by the runner: link them to the
repository or make them public. Repository variables (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `RUNNER_LABEL` | `bytehawks-k3s` | `runs-on` label |
| `BUILDER_REGISTRY` | `fallback` | `primary` (Harbor) or `fallback` (GHCR) image references in the matrix |
| `PLAN_IMAGE` | `ghcr.io/bytehawks-org/distillery-builder:stable` | image of the `plan` job |
| `NEXUS_URL`, `HARBOR_URL` | see above | endpoints |

The workflows avoid JavaScript actions in container jobs (checkout is a plain `git fetch`), because the JS action
runtime does not work on every musl image.

## 5. Day-to-day

- Merge a recipe change to `main` → `build-packages` plans only what is **not yet published** and builds it.
- Manual run: *Actions → Build packages → Run workflow* (package list, variants, `force`, `include_experimental`, `publish`).
- A recipe step change without a version bump is not rebuilt automatically (artifacts are immutable): bump
  the version, or run with `force` after deleting the old artifact from Nexus/the release.
- `upstream-watch` opens an issue when a newer upstream release exists; then bump + `lock` + PR.
