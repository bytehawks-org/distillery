# Automation: watching upstream and rebuilding what is affected

Two scheduled workflows keep the artifact store aligned with upstream releases and with the catalog.

```
                ┌────────────────────── watch-upstream (daily 05:17 UTC) ──────────────────────┐
                │ outdated ──► bump (per package) ──► PR  bot/upstream-<pkg>  ──► ci + build   │
                └───────────────────────────────────────────────────────────────┬──────────────┘
                                                                                │ dispatch (branch)
   push main / dispatch / nightly 03:41 UTC                                     ▼
                ┌──────────────────────────────── build ──────────────────────────────────────┐
  catalog ─────►│ plan: stale = catalog units whose recipe hash is NOT in the stores          │
  stores  ─────►│   │                                                                          │
                │   ├─► libs  (matrix, serial, dependency order)  chain → verify → upload     │
                │   └─► apps  (matrix, parallel; restore libs from the store) → verify → upload│
                │                                          └─► publish-nexus (if NEXUS_URL set)│
                └──────────────────────────────────────────────────────────────────────────────┘
```

The point of the design: **nothing tells the build what to rebuild**. The build asks one question per
catalog unit - "does a store hold an artifact with exactly this recipe hash?" - and the Merkle recipe hash
([ARCHITECTURE](ARCHITECTURE.md#4-recipe-hash-and-idempotency)) makes the answer correct for a dependency
change too. A new `zlib` release makes `zlib` stale; `nginx` becomes stale because its hash includes zlib's.
`openssl` and `pcre2` stay untouched.

## Workflows

### `watch-upstream.yml` - find news and propose them

| Step | What happens |
|---|---|
| `detect` | `autobuilder.py outdated --format json`. Each variant follows its series (`track`, default its `major_version`): `stable` 3.5.4 → 3.5.9 is an update, 3.6.x/4.0.x is only listed as "newer series". Step summary shows the table |
| `bump` (matrix: one job per package) | `autobuilder.py bump --package P`: downloads the new source, computes its SHA-256, **cross-checks it with the checksum upstream publishes** when `upstream.checksum_url_template` is declared (aborts on mismatch), rewrites only the variant lines of `packages.yaml` (comments and layout preserved) |
| pull request | `peter-evans/create-pull-request`: branch `bot/upstream-<pkg>` (reused/updated on the next run), body with versions, hashes, source URLs and the "review the release notes, verify the signature" reminder |
| start checks | explicit `workflow_dispatch` of `ci.yml` and `build.yml` on the bot branch (see "GITHUB_TOKEN" below) |

One PR per package: updates are reviewed and merged independently and each triggers only its own chain.
Manual run: *Actions → watch-upstream → Run workflow* (`package` to restrict, `dry_run` to report only).

### `build.yml` / `build-platform.yml` - make the stores match the catalog

`build.yml` holds the triggers and calls `build-platform.yml` once per C library (`matrix.libc: [glibc, musl]`, `fail-fast: false`):
the glibc leg runs in a Debian image, the musl leg in an Alpine image (`vars.BH_BUILDER_IMAGE_MUSL`, default `alpine:3.20`;
`vars.BH_BUILDER_IMAGE`, default `debian:12`, for glibc). The leg sets `BH_LIBC`, so `stale`, `chain` and `verify` plan and build
for that platform; artifact names carry it (`…_amd64_…` / `…_amd64-musl_…`). The table describes one leg.

| Job | Runs | Does |
|---|---|---|
| `plan` | always | `stale --github-output`: compares every catalog unit with `--store github [--store nexus]`; outputs the stale libraries and applications as matrices, in dependency order; writes the plan to the run summary |
| builder images | on `docker/` changes, weekly | `builder-images.yml` builds and pushes the images that the jobs below can start from ([BUILDER-IMAGES](BUILDER-IMAGES.md)) |
| `libs` | if any stale library | **serial** matrix (`max-parallel: 1`), `chain --package P --variant V --kind library`: builds the unit, restoring its (already built) dependencies from the store; `verify` the installed files against `files.sha256`; uploads `<pkg>_bh_<variant>_<tag>_<arch>_<version>_<recipe12>` |
| `apps` | after `libs` (also if it was skipped), never after a failure | parallel matrix, same, for applications; libraries come from the store (including those uploaded seconds ago by `libs`) |
| `publish-nexus` | if the variable `NEXUS_URL` is set | downloads the run's artifacts and `publish --store nexus` |

Triggers: **dispatch** (inputs: `packages`, `force`, `reason`), **push to main** touching
`packages.yaml config.yaml assets/** scripts/**` (a merged change that was not built yet - normally a no-op
because the bump branch already produced the artifacts, content addressing makes branch→main a promotion,
not a rebuild), **nightly schedule** (reconcile: GitHub artifacts expire, a build may have failed, the
catalog may have been edited without a build).

Builds run in a container (`vars.BH_BUILDER_IMAGE`, default `debian:12`; musl: `vars.BH_BUILDER_IMAGE_MUSL`, default `alpine:3.20`).
Binaries built against a glibc run on that glibc or newer, musl ones on that musl or newer: **build on the oldest distribution you
intend to support**. Not exercised on GitHub yet: JavaScript actions (`checkout`, `upload-artifact`) inside an Alpine container are the
first thing to check on the first run of the musl leg.

The order in which to exercise all of this for the first time, with the predicted result of each step, is in
[FIRST-RUN](FIRST-RUN.md).

## One-time repository setup

| Where | Setting |
|---|---|
| Settings → Actions → General → *Workflow permissions* | "Allow GitHub Actions to create and approve pull requests" ✔ (needed by `watch-upstream`) |
| Settings → Actions → General → *Artifact and log retention* | raise to 90 days (maximum) for GitHub as store; also `vars.BH_ARTIFACT_RETENTION_DAYS` |
| Secret `BH_BOT_TOKEN` (optional) | fine-grained PAT or GitHub App token with `contents:write` + `pull-requests:write`: PRs opened with it trigger `pull_request` workflows normally. Without it the workflows dispatch `ci` and `build` explicitly |
| Variables `BH_BUILDER_IMAGE`, `BH_BUILDER_IMAGE_MUSL` (optional) | container images of the glibc and musl build jobs |
| Variable `NEXUS_URL`, secrets `NEXUS_USER`, `NEXUS_PASSWORD` (optional) | enables the Nexus store and `publish-nexus` ([ARTIFACTS](ARTIFACTS.md#nexus)) |
| Branch protection on `main` | require `ci` and a review: the bot proposes, a human merges |

Permissions used (declared in the files): `build.yml` `contents: read, actions: read`;
`watch-upstream.yml` `contents: write, pull-requests: write, actions: write`.

### GITHUB_TOKEN and triggered workflows
GitHub does not start workflows from events caused by `GITHUB_TOKEN` (a PR created by the default token
does not fire `pull_request`). `watch-upstream` therefore calls `gh workflow run ci.yml|build.yml --ref <bot branch>`
(`workflow_dispatch` is exempt from that rule). With `BH_BOT_TOKEN` the PR triggers work as for a human and
the dispatch is redundant but harmless (the second `build` finds everything present).

### Signing artifacts in CI (optional)

Add the repository secrets `BH_SIGNING_PRIVATE_KEY` (armored export of a dedicated signing subkey,
`gpg --armor --export-secret-subkeys <fpr>`) and, if protected, `BH_SIGNING_PASSPHRASE`; optionally the variable
`BH_SIGNING_KEY` (otherwise the imported key is used). The `libs` and `apps` jobs import it into a throw-away
`GNUPGHOME`, sign every artifact they pack and remove the key before uploading. Without the secret nothing is signed (forks,
pull requests). Put the **primary** fingerprint in `artifacts.signing.fingerprints` of `config.yaml` (and `require: true`
once every producer signs) so the next jobs and your consumers verify what they restore.

## Operating it

| I want to... | Do |
|---|---|
| see what is outdated now | `autobuilder.py outdated` (locally) or run `watch-upstream` with `dry_run` |
| build a new package I just added | merge it: push to main triggers `build`; or dispatch `build` with `packages: mypkg` |
| rebuild one package although stored (toolchain change) | dispatch `build` with `packages: nginx`, `force: true` |
| rebuild everything | change something in the recipe (e.g. a `CFLAGS` in `env:`) - the hash cascades |
| stop auto-updates of one package | remove its `upstream:` block (or pin `track` to a series with no news) |
| move to a new series (openssl 3.6 → 3.7) | edit the variant by hand: `major_version`, `track`, `full_version`, `sha256` - bumps never cross series on their own |
| what is in the store | `autobuilder.py inventory --store github` (needs `GITHUB_TOKEN`/`GH_TOKEN`) |
| why is X stale | `autobuilder.py stale --all --store github` shows `present / superseded / missing` per unit |
| pause everything | disable the workflows in the Actions tab |

## Behaviour worth knowing

* **Idempotent and re-runnable**: every job is safe to re-run; `chain` skips what the stores already hold.
* **A failed library stops the chain** (`fail-fast` for `libs`), applications that depend on it are not
  attempted; unrelated applications still build.
* **Bump PRs update in place**: if upstream ships 3.5.10 while 3.5.9's PR is open, the same branch/PR moves to 3.5.10.
* **Only releases matching `tag_regex`** are considered; pre-releases are excluded by the regex.
* **Cost control**: no stale unit → `plan` only (a few seconds); scheduled runs on a quiet catalog are nearly free.

## Limits and honest caveats

* GitHub Actions artifacts **expire** (≤ 90 days). The nightly reconcile rebuilds expired units, which is
  correct but means GitHub alone is a cache, not an archive. For releases you need to keep, use Nexus
  (or any store implemented behind the same interface).
* Artifacts uploaded by `actions/upload-artifact` are scoped to a run; this design lists and downloads them
  repository-wide through the REST API (`GITHUB_TOKEN` with `actions: read`). Cross-run download works that
  way; a unit uploaded seconds earlier in the same run is expected to be visible to the next matrix job -
  this is the one behaviour that can only be confirmed on GitHub itself (see ROADMAP "first run").
* Not yet executed on GitHub: the workflows pass `actionlint`, the Python behind them is covered by tests
  (fake GitHub and Nexus servers, real git remotes, real downloads), but the first scheduled run should be
  watched.
* Builds are not sandboxed; the catalog is trusted code (review bump PRs: a changed source hash is exactly
  the thing an attacker would want in a PR).
* `upstream` uses `git ls-remote --tags`: a tag can exist before the release tarball is uploaded;
  `bump` then fails on the download and tries again at the next run.
