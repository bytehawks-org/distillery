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

## Trust boundaries

* **Build commands are trusted shell** (as in any build system). The catalog is code: review changes
  to `packages.yaml` like code, protect the branch, and require the checksum change in the same review
  as the URL/version change.
* Builds run with the invoking user's privileges and network. Use an unprivileged user and `--root`;
  for hostile sources use a throw-away container/VM (sandboxing is on the roadmap).
* `file://` URLs are accepted (tests, air-gapped mirrors) - they are pinned by checksum like any other.
* The SHA-256 proves "same bytes as when the maintainer pinned them", not "upstream is honest":
  verify upstream signatures when pinning (`checksum` prints the value, it does not check a signature).

## Not covered yet

Source signature verification (GPG/minisign/cosign) · SBOM export (CycloneDX/SPDX) · vulnerability
scanning of the produced tree · reproducible-build verification (two builds, bit-identical) ·
build sandboxing (network-less, read-only source) · signing the bundle. All on the [ROADMAP](../ROADMAP.md).

## Operational advice

* Pin `PyYAML` and run `pip install --require-hashes` in CI.
* Build in CI from a clean container; publish the resulting tree/image with its `manifest.json`.
* Keep the download cache on a non-shared path: it is trusted by checksum, but nothing else.
* Rotate: when a CVE lands, bump `full_version` + `sha256` → recipe hashes cascade to every dependent → rebuild list is `plan`.

## Reporting

Open a private security advisory on the GitHub repository.
