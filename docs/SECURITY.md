# Security

Distillery is the root of trust of the ecosystem: what it builds is what Shaker later runs as PID 1.

## Guarantees

| Threat | Mitigation |
|---|---|
| Tampered or substituted source tarball | SHA-256 pinned per variant in the catalog (code-reviewed); mismatch → file deleted, build aborted before extraction and before any command |
| Building an unverified source by accident | missing `sha256` → refusal; the only way around is the explicit `--allow-unverified` (prints the hash) |
| Malicious archive (zip-slip, symlink escape) | absolute paths, `..` and links leaving the tree are rejected; Python's `data` filter is applied when available |
| Partial/interrupted download used as source | download goes to `*.part`, renamed only after the hash matches |
| Typos turning into destructive commands | unresolved `{{ }}` is an error (never an empty string in a path) |
| Stale or half-built artifact looks installed | bundle (with `built_at`) is written last; recipe hash compared on every build |
| Silent drift between what was reviewed and what was built | recipe hash covers commands, variant, prefix and dependencies; recorded in `manifest.json` and in Shaker's state |
| Injection through the bundle formats | `|` and newlines are rejected in every emitted field; `run` must be an argv list, not a shell string |
| Hard-wired `/opt/bh` | never used in artifacts, so symlink games cannot redirect an installed tree |

## Automation (0.4)

| Risk | Control |
|---|---|
| A malicious or hijacked upstream tag/tarball enters the catalog through an automatic bump | the bot only **proposes** a pull request; the checksum is cross-checked against the one upstream publishes when available; the PR text asks for release-notes and signature review; protect `main` with required review. Packages with a `signature:` block (nifi, temurin) are verified against pinned signer fingerprints before a bump can pin them; never enable auto-merge for bump PRs of packages without it |
| Workflow token abuse | least-privilege `permissions:` per workflow (`build`: `contents: read, actions: read`); `build` has no `pull_request_target`, no fork triggers; secrets are only passed to the steps that use them |
| Token leaked to the artifact blob host | the GitHub store never forwards `Authorization` to the redirect target (test `test_download_never_sends_the_token_to_the_blob_host`) |
| Poisoned or corrupted store | every fetch re-hashes against the sidecar; `restore` refuses unsafe members and a foreign recipe; `verify` re-checks installed files. This is **integrity only** - writers to the store are trusted. Restrict write access (Nexus role, CI only) |
| Bot PRs trigger nothing / trigger too much | explicit `workflow_dispatch` on the bot branch (or `BH_BOT_TOKEN`); content-addressed artifacts make a repeated build a no-op |
| Cache poisoning across branches | the artifact name is the recipe hash: a branch cannot supply an artifact for a recipe it did not define, but any branch with `actions: write` can upload any name - treat the store as writable by the repository's maintainers only |

## Trust boundaries

* **Build commands are trusted shell** (as in any build system). The catalog is code: review changes
  to `packages.yaml` like code, protect the branch, and require the checksum change in the same review
  as the URL/version change.
* Builds run with the invoking user's privileges and network. Use an unprivileged user and `--root`;
  for hostile sources use a throw-away container/VM (sandboxing is on the roadmap).
* `file://` URLs are accepted (tests, air-gapped mirrors) - they are pinned by checksum like any other.
* The SHA-256 proves "same bytes as when the maintainer pinned them", not "upstream is honest":
  declare `signature:` (OpenPGP, pinned signer fingerprints) so `build` and `bump` check it. `checksum` only prints the value; packages without `signature:` (openssl, nginx, zlib, pcre2) are still integrity-only.

## Signed artifacts

`artifacts.signing` signs the sidecar of every artifact and makes `restore`/`chain` accept only pinned signers
(`require: true`). It authenticates *who built it* and detects tampering in a store; it does not vouch for the sources
(see `signature:`). Keep the signing key in a secret store (CI) or a hardware token (workstation); a leaked key is revoked
by removing its fingerprint from the consumers' config.

## Sealed bundles

Every build writes `bundle.sha256` over the bundle files and `files.sha256`, and signs it when a key is configured. A consumer that
pins the primary fingerprint (Shaker: `BH_SHAKER_TRUSTED_FINGERPRINTS`, `BH_SHAKER_REQUIRE_SIGNATURE`) can verify, at any time and
without Distillery, that the installed tree is what the pipeline built (`shaker verify --files --require-signature`). `mutable`
paths are excluded by design, so keep that list short and reviewed. Trust is never read from the bundle itself.

## Not covered yet

Source signatures other than OpenPGP (minisign/cosign) and for the older packages · SBOM export (CycloneDX/SPDX) · vulnerability
scanning of the produced tree · reproducible-build verification (two builds, bit-identical) ·
build sandboxing (network-less, read-only source) · signing the bundle. All on the [ROADMAP](../ROADMAP.md).

## Operational advice

* Pin `PyYAML` and run `pip install --require-hashes` in CI.
* Build in CI from a clean container; publish the resulting tree/image with its `manifest.json`.
* Keep the download cache on a non-shared path: it is trusted by checksum, but nothing else.
* Rotate: when a CVE lands, bump `full_version` + `sha256` → recipe hashes cascade to every dependent → rebuild list is `plan`.

## Reporting

Open a private security advisory on the GitHub repository.
