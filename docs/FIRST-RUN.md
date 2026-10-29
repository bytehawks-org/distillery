# First run on GitHub: checklist

The workflows pass `actionlint` in CI and every command they call is tested, but **they have never run on GitHub**. This is the
order that finds what only the platform shows, cheapest and most isolated first. Each step says what to run, what the design
predicts, and what to do otherwise. "Predicted" means predicted, not observed: write down what you actually see.

Legend: ☐ to do · every step is non-destructive (artifacts expire, pull requests can be closed, secrets can be removed).

## 0. Prerequisites (once, ~10 min)

| ☐ | Where | Setting |
|---|---|---|
| ☐ | Settings → Actions → General → Workflow permissions | "Allow GitHub Actions to create and approve pull requests" ✔ (`watch-upstream`) |
| ☐ | Settings → Actions → General → Artifact and log retention | 90 days |
| ☐ | Settings → Secrets and variables → Actions | optional: `BH_BOT_TOKEN` (PRs that trigger workflows), `BH_SIGNING_PRIVATE_KEY` + `BH_SIGNING_PASSPHRASE` (step 6), `NEXUS_*` (step 8) |
| ☐ | Settings → Branches | protect `main`: require `ci` and a review |
| ☐ | Billing → storage | a NiFi artifact is ~0.8 GB; GitHub artifacts count against the quota. Plan Nexus (step 8) before building NiFi on a small plan |
| ☐ | your workstation | `git push` of the branch; do **not** merge yet: all steps below run from the branch |

Evidence to keep for every step: the run URL, the first failing log line, and the fix commit.

## 1. `ci.yml` (push the branch)

☐ Open the `ci` run of the branch. Predicted: 3 jobs green + `workflows` + `test-musl`.

| ☐ | Job | Predicted | If it fails |
|---|---|---|---|
| ☐ | `test` (py 3.9 / 3.12 / 3.13) | 108 tests, 1 skipped (real `nfpm` absent) | a Python-version difference: fix in code, not in the matrix |
| ☐ | `test` → "catalog is consistent" | every package × amd64/arm64 × glibc/musl prints a plan with no `MISSING` | a missing pin: add the sha256 for that platform (`checksum --arch A --libc L`) |
| ☐ | `workflows` (actionlint) | clean. **Most likely to complain**: `uses:` job with `strategy.matrix` and `secrets: inherit` in `build.yml`; expression in `container:` of `build-platform.yml` | fix the expression; do not silence the linter |
| ☐ | `test-musl` (Alpine container) | green. Risks: JavaScript actions (`actions/checkout`) inside an Alpine container; `py3-virtualenv`; `pip` wheels for PyYAML / ruamel | if `checkout` fails on Alpine, install `nodejs` first (`apk add nodejs`) or use `git clone` with `GITHUB_TOKEN`; record which |

Stop here until `ci` is green. Everything after assumes it.

## 2. `build` — smallest unit, both libcs

☐ Actions → build → *Run workflow* on the branch: `packages = zlib`, `force = true`, `reason = first run`.

Predicted: one `build` run with two reusable-workflow legs, `platform (glibc)` and `platform (musl)`, each `plan → libs`
(no apps). Artifacts `bh--zlib--all--library--amd64--1.3.1--…` and `bh--zlib--all--library--amd64-musl--1.3.1--…`.

| ☐ | Check | Predicted | If not |
|---|---|---|---|
| ☐ | Both legs start; the run summary shows "Build plan (glibc)" and "(musl)" | yes | a reusable-workflow call error appears at the top of the run: read it, it names the line |
| ☐ | `libs` glibc: container `debian:12`, toolchain step installs `gnupg` | green | apt mirror hiccup: rerun once, then read the log |
| ☐ | `libs` musl: container `alpine:3.20`, `Toolchain (musl)` | green. **Risk**: JavaScript actions in the Alpine container (same as step 1) | see step 1; the fix goes into `build-platform.yml` |
| ☐ | `build zlib` on musl compiles with `build-base` | green | `zlib` is trivial; a failure here is the container, not the recipe |
| ☐ | "Verify the installed files" | `ok` | a mismatch means the build touched files after `files.sha256`: bug in Distillery, open an issue with the log |
| ☐ | Two artifacts uploaded, names differ only by `amd64` / `amd64-musl` | yes | the platform is missing from the name: `BH_LIBC` did not reach the step |

☐ Run it again **without** `force`. Predicted: `plan` reports `present`, no job builds (content addressing). If it rebuilds,
the recipe hash is not stable between runs: capture both `stale.json` summaries and diff them.

## 3. A chain: libraries then an application

☐ Dispatch with `packages = nginx`, `force = false`. Predicted per leg: `libs` builds `openssl → zlib → pcre2` **serially**
(`max-parallel: 1`), then `apps` builds `nginx`.

| ☐ | Check | Why it matters |
|---|---|---|
| ☐ | In `apps`, nginx **restores** its libraries from the store instead of rebuilding them (`[restore]` lines) | the "same-run artifact visibility" question: libs uploaded seconds earlier must be listable by the REST API in the next job. If `restore` finds nothing and rebuilds, GitHub's listing lags: add a short retry/backoff in `GithubStore.inventory` |
| ☐ | musl leg: `openssl` configures with `linux-x86_64` and `no-shared`; nginx links | the first real compile on musl. Typical failures: missing `linux-headers`, `perl` modules, `-static` quirks. Fix the recipe or the toolchain list; record |
| ☐ | `verify --with-deps` green | |
| ☐ | Artifact sizes plausible (nginx ~ a few MB) | |
| ☐ | Then `packages = valkey` and `packages = mosquitto` (git sources: the runner needs outbound `git` to github.com; `cjson` is built first as a library), and `packages = prometheus` (release asset, no compiler) | each leg green; on musl the first real compile of valkey and mosquitto |

## 4. Consume what CI produced (the point of all this)

☐ Download one glibc and one musl artifact set (`.tar.gz` + `.json` [+ `.json.asc`]) from the run.

| ☐ | Where | Command | Predicted |
|---|---|---|---|
| ☐ | Debian container | `shaker restore <nginx artifact>` then `shaker install nginx` and `shaker verify nginx --files` | `restore` ok, `verify` ok |
| ☐ | Alpine container | same with the musl artifact. Needs `bash` and GNU `tar` (`apk add bash tar`) | **first real BusyBox/Alpine run of `shaker restore`**: if it fails on a BusyBox option, fix `lib/artifact.libsh` and add the case to `tests/test_artifact.sh` |
| ☐ | Debian container | restore the **musl** artifact | refused: "built for musl, this host uses glibc" |
| ☐ | either | `autobuilder.py restore --package nginx --store dir:<dir>` | same result as Shaker's |

## 5. NiFi (large, Java, signed sources)

☐ Dispatch `packages = nifi`. Predicted per leg: no `libs` job (Temurin is an *application*, not a library), and an `apps` matrix
with two entries, `temurin` and `nifi`, that run **in parallel**. `chain --package nifi` plans nifi's dependencies too, so while
Temurin is not in the store yet the nifi job builds (or restores) it as well: expect Temurin to be built twice at the same time
(harmless, content-addressed, the artifact names do not collide). Note whether that happens; if it costs too much, make the
dependency an ordered stage (a `libs`-like first pass for application dependencies).

| ☐ | Check | Notes |
|---|---|---|
| ☐ | Source signatures: log lines `signature ok: … signed by 3B04…` (Temurin) and `3FE9…` (NiFi) | the runner needs outbound HTTPS to `keyserver.ubuntu.com` and `downloads.apache.org`; blocked egress = failure with a clear message |
| ☐ | musl leg downloads the **Alpine** JRE (`…alpine-linux…`) | verify in the log |
| ☐ | Artifact upload of ~0.8 GB | watch the storage quota; if rejected, go to step 8 first |
| ☐ | **Open question**: does NiFi start on musl? | restore the musl artifacts in an Alpine container and `shaker up nifi --run`. NARs with glibc-only native libraries may fail; record exactly which, then decide: Alpine unsupported for NiFi, or add `gcompat` |

## 6. Signing (optional but this is what makes the chain of trust real)

☐ Create a dedicated signing subkey (primary key offline), export it, add `BH_SIGNING_PRIVATE_KEY` (armored) and
`BH_SIGNING_PASSPHRASE`; set the **primary** fingerprint in `config.yaml → artifacts.signing.fingerprints`.

| ☐ | Check | Predicted |
|---|---|---|
| ☐ | Re-run step 2 with `force = true` | the "Signing key" step imports the key, the `[sign]` lines appear, the artifact set now has `.json.asc`, the bundle has `bundle.sha256.asc`; the "Remove the signing key" step runs even on failure |
| ☐ | A **pull request from a fork** (or a run without the secret) | no signing, no failure, the job says "artifacts will not be signed" |
| ☐ | On a consumer: `BH_SHAKER_TRUSTED_FINGERPRINTS=<primary> shaker restore … --require-signature` then `shaker verify … --files --require-signature` | ok; with another fingerprint: refused |
| ☐ | Passphrase prompts or `gpg: signing failed: Inappropriate ioctl` in the log | the loopback pinentry flags are the fix; they are already passed, so this means the key has a passphrase and `BH_SIGNING_PASSPHRASE` is wrong or empty |

Then set `require: true` in `config.yaml` once every producer signs.

## 7. `watch-upstream`

☐ Dispatch with `dry_run = true`. Predicted: a report of every package with `upstream:`; nothing is pushed.

☐ Dispatch with `package = zlib`, `dry_run = false` **only when an update exists** (or temporarily lower the pinned version on a
scratch branch). Predicted: a branch `bot/upstream-zlib`, one pull request, `ci` and `build` dispatched on that branch.

| ☐ | Check | If not |
|---|---|---|
| ☐ | PR created | the repository setting from step 0 is off |
| ☐ | `ci` and `build` start on the bot branch | `GITHUB_TOKEN` events do not trigger workflows; the explicit `gh workflow run` fallback should have fired; otherwise set `BH_BOT_TOKEN` |
| ☐ | For `nifi`: the bump step logs `signature ok` before pinning, and fails on an unpinned release manager | expected to fail loudly the day a new release manager signs: review the key, add the fingerprint |

## 8. Nexus (long-term store)

☐ Set `vars.NEXUS_URL`, `secrets.NEXUS_USER` / `NEXUS_PASSWORD`; re-run step 2.

Predicted: `publish-nexus` uploads `.tar.gz`, `.json` and `.json.asc` for each artifact; `inventory --store nexus` lists them;
`restore --store nexus` on a clean root works. **Never tested against a real Nexus** (only a fake server): pagination, raw
repository layout, credentials, large uploads (~0.8 GB) are the unknowns. Record the Nexus version.

## 9. Packages (not in CI yet)

`package` has no workflow job. Locally or in a scratch job, on a **glibc** build:

| ☐ | Command | Predicted |
|---|---|---|
| ☐ | `autobuilder.py build --package nifi --with-deps` then `package --package nifi --with-deps` | `deb` + `rpm` (and no `apk`: glibc) |
| ☐ | Debian container: `apt install ./bytehawks-temurin_*.deb ./bytehawks-nifi_*.deb` | installs; post-install prints the `shaker install` hint if Shaker is absent, runs it if present |
| ☐ | Fedora/Rocky container: `dnf install ./…rpm` | installs; `rpm -qpR` shows the exact-version dependency |
| ☐ | Alpine container, **musl** build: `apk add --allow-untrusted ./…apk` | installs. `--allow-untrusted` is needed until the apk signing key is configured |

First time ever that a package is installed by a real package manager: expect small metadata surprises (file ownership, directory
modes, conflicts between variants).

## 10. The nightly schedule

☐ Leave it for two nights. Predicted: `plan` reports everything `present`, no builds. Anything else is drift: an expired
GitHub artifact (expected after 90 days) or an unstable recipe hash (a bug).

## Triage order when something fails

1. Is it the **container** (Alpine/Debian setup, JavaScript actions, missing tool)? Fix `build-platform.yml`.
2. Is it the **platform** (GitHub API lag, quota, egress to a key server)? Add a retry or document the limit.
3. Is it the **recipe** (a compile error on musl)? Fix `packages.yaml`, rerun only that package.
4. Is it **Distillery or Shaker** (a command behaves differently from the tests)? Reproduce locally with the same container
   image, add the failing case to the tests **first**, then fix.

When all boxes are ticked, merge the branch, then delete this file or keep it as the template for the next platform (arm64
runners are the next one).
