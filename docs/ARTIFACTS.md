# Artifacts and stores

An **artifact** is one built unit, packaged so it can be restored on another machine without compiling:
the files the build installed plus its bundle, as a deterministic tarball with a JSON sidecar. A **store**
is a place that keeps artifacts and can list them.

## Identity

```
<holder>_<package>_<variant>_<tag>_<platform>_<version>_<recipe12>
bh_nginx_stable_application_amd64_1.28.3_a1146e116dea
```

* `holder` = `config.artifacts.holder` (default `bh`, ByteHawks; `BH_HOLDER` overrides): who owns the artifacts. A store only lists the names of
  its holder, so several projects can share one repository or Nexus without seeing each other's artifacts. Lowercase letters, digits, `.` and single `-`.
* `recipe12` = first 12 hex of the unit's recipe hash. Same name ⇔ same recipe, same prefix, same
  dependencies, same architecture. Changing a flag, the source hash or a dependency changes the name.
* `arch` is the **platform**: the plain architecture for glibc (`amd64`) and
  `<arch>-musl` for musl (`amd64-musl`, `arm64-musl`). A static build appends `-static` (`amd64-static`, `amd64-musl-static`, see [STATIC](STATIC.md)). A glibc and a musl build of the same recipe are two artifacts and can live in
  the same store. The sidecar records `arch`, `libc`, `linkage` and `platform`.
* `variant` is the first alias of the variant (`old-legacy`, not `legacy`).
* Names contain neither `/` nor `:` nor `|` and fit GitHub's artifact-name rules; the separator is `_`, so a field may
  contain hyphens and dots (`old-legacy`, `amd64-musl-static`) but never `_`: a name that would contain one is refused with an error (no name ever contains `--`).
* "Is this exact build available?" is answered from a **listing alone** - no download.

Status used by `stale`:

| Status | Meaning |
|---|---|
| `present` | a store holds exactly this name |
| `superseded` | the store holds other builds of the same package/variant/tag/arch (older recipe or version) |
| `missing` | nothing for this unit |
| `rebuild` | (`--force`) selected for rebuild although present |

## Content

`<name>.tar.gz`
* members relative to the root (`deps/lib/libz.a`, `nginx/sbin/nginx`, `nginx/.bytehawks/...`);
* only what the build **changed**: files created/modified between a snapshot before the stages and one after
  the bundle was written; new directories; the bundle including `files.sha256`;
* uid/gid 0, no names, mtime = `SOURCE_DATE_EPOCH` (or build time), gzip header mtime 0, members sorted:
  the same recipe built twice on a clean machine yields **byte-identical** tarballs (tested);
* never contains absolute paths, `..`, devices; links must stay inside the tree.

`<name>.json` (sidecar)
```json
{"schema": 1, "generator": "distillery/0.4.0", "name": "...", "package": "zlib", "variant": "all", "tag": "library",
 "arch": "amd64", "version": "1.3.1", "recipe_sha256": "<64 hex>", "root": "/opt/bytehawks", "prefix": "/opt/bytehawks/deps",
 "files": 11, "size": 99026, "sha256": "<tarball sha256>", "depends": [{"package": "...", "recipe_sha256": "..."}],
 "created_at": "2026-10-23T06:31:00Z"}
```

`files.sha256` (inside the bundle, also on the installed host): `<sha256>  <path relative to root>` per file,
`64 dashes  <dir>/` per directory. `autobuilder.py verify --package P [--with-deps]` compares the installed tree
with it (tamper / corruption check; Shaker will use it too).

**Root-bound.** Paths are baked into binaries, so an artifact is valid for the root it was built for. The
prefix is part of the recipe hash: `--root /tmp/stage` and `/opt/bytehawks` are different recipes and never
collide in a store.

## Commands

```sh
autobuilder.py build  --package nginx --with-deps --artifacts-dir dist     # build and package every unit built
autobuilder.py build  --package nginx --with-deps --store github           # restore what the store has, build the rest
autobuilder.py chain  --store github --artifacts-dir dist                  # build everything stale (CI)
autobuilder.py stale  --store github --format json                         # what is missing
autobuilder.py inventory --store nexus                                     # what is there
autobuilder.py restore --package nginx --with-deps --store nexus           # install without compiling
autobuilder.py publish --from dist --store nexus                           # push packaged units
autobuilder.py verify --package nginx --with-deps                          # installed files vs files.sha256
```

`--store` is repeatable and ordered ("first store that has it wins"): `--store dir:dist --store github --store nexus`.
Specs: `dir:PATH`, `github`, `github:owner/repo`, `nexus`, or a name from `config.yaml → artifacts.stores`.
`build` uses stores only when told to; `stale/inventory/chain/restore/publish` fall back to
`config.yaml → artifacts.default_stores` (default `[github]`).

Integrity: every fetch re-hashes the tarball against the sidecar (and Nexus' own checksum when listed) and
refuses a mismatch; `restore` also refuses unsafe members and a bundle whose recipe differs from the one asked for.
This is **integrity, not authenticity**: anyone who can write to the store can write a consistent tarball +
sidecar. Restrict write access to CI; signature of artifacts/bundles is on the roadmap.

## Backends

### `dir:PATH`
A directory of `<name>.tar.gz` + `<name>.json`. The workflow-local `dist/`, air-gapped transfers, tests, a
shared NFS. `publish` copies, `inventory` lists, nothing else needed.

### `github` - GitHub Actions artifacts
| | |
|---|---|
| Listing | `GET /repos/{repo}/actions/artifacts` (paginated, expired ones ignored, newest wins) filtered on the holder prefix (`bh_`) |
| Download | `GET .../artifacts/{id}/zip` → 302 → signed blob URL. The token is **never forwarded** to the redirect target (tested). The zip holds the tarball and the sidecar |
| Upload | `actions/upload-artifact@v4` on `dist/<name>.*` in the workflow (there is no script-friendly upload API); `publish --store github` is therefore refused (use `--ignore-unsupported` in pipelines) |
| Auth | `GITHUB_TOKEN` or `GH_TOKEN` with `actions: read`; repository from `GITHUB_REPOSITORY` or `github:owner/repo` |
| Retention | per upload (`retention-days`, `vars.BH_ARTIFACT_RETENTION_DAYS`, ≤ 90). After expiry the unit is `missing` → rebuilt by the nightly reconcile |
| Fit | CI cache and short-lived distribution; not an archive |

### `nexus`
Sonatype Nexus Repository 3, **raw (hosted)** repository.

```yaml
# config.yaml
artifacts:
  default_stores: [github, nexus]
  stores:
    nexus: {type: nexus, url: https://nexus.example.com, repository: bytehawks, prefix: bytehawks}
```
```
<url>/repository/<repository>/<prefix>/<arch>/<package>/<variant>/<tag>/<version>-<recipe12>/<name>.tar.gz
                                                                                              /<name>.json
```
| | |
|---|---|
| Upload | `PUT` of tarball then sidecar, HTTP basic (`NEXUS_USER`/`NEXUS_PASSWORD`; a user token pair works) |
| Listing | `GET /service/rest/v1/assets?repository=<repo>` with `continuationToken` paging; entries are rebuilt from the path, the asset's `checksum.sha256` is used for verification |
| Download | `GET` of the tarball and the sidecar |
| Nexus setup | create a *raw (hosted)* repository (`bytehawks`), write policy "Allow redeploy" off (immutability: names are content-addressed), a role with `nx-repository-view-raw-bytehawks-add,edit,read,browse` for CI and `...-read,browse` for consumers; a cleanup policy by age for superseded versions |
| Enable in CI | repository variable `NEXUS_URL` (+ `config.yaml` repo/prefix), secrets `NEXUS_USER`, `NEXUS_PASSWORD`; `build.yml` then adds `--store nexus` and the `publish-nexus` job |
| Status | implemented against the documented Nexus 3 REST/raw API and tested with a fake server; **not yet exercised against a real Nexus** |

### Adding another backend
Subclass `Store` in `scripts/bhdistillery/store.py`: `inventory()`, `fetch(entry, dest)`, optionally
`publish(entry, tarball, meta)`; register it in `open_store`. Candidates: S3/MinIO, OCI registry (artifact per
unit with ORAS), GitHub Releases (long retention), Artifactory.

## Signatures

Every artifact can carry a detached OpenPGP signature of its sidecar: `<name>.json.asc`. The sidecar holds the sha256 of
the tarball, the recipe hash and the identity of the unit, so the one signature authenticates all of them (the tarball is
already checked against the sha256 on every fetch). Stores move the three files together: `dir` (copy), `github` (they
travel in the same Actions artifact), `nexus` (one more PUT/GET).

| Setting (`artifacts.signing` in `config.yaml`) | Meaning |
|---|---|
| `key` / `BH_SIGNING_KEY` | key id or fingerprint to sign with. Empty = artifacts are not signed. |
| `BH_SIGNING_PASSPHRASE_FILE` | passphrase file for non-interactive signing (gpg loopback pinentry); not needed with gpg-agent/pinentry on a workstation |
| `fingerprints` | trusted signers (full 40-hex **primary** key fingerprints) checked by `restore` and `chain` |
| `keys` | file or URL with their public keys; empty = exported from the local gpg keyring |
| `require` | `true` = refuse unsigned artifacts and artifacts signed by anyone else; `false` = unsigned is tolerated, a **bad** signature never is |

The same code path serves a developer (their own key in `~/.gnupg`) and CI (a key imported from a secret into a
throw-away `GNUPGHOME`, see AUTOMATION). A common local setup: build anywhere, `autobuilder.py sign --from dist` on a trusted
workstation, `publish` to your own store; consumers pin your fingerprint and set `require: true`.

```sh
BH_SIGNING_KEY=ABCD…  autobuilder.py build --package nifi --with-deps --artifacts-dir dist     # signs what it packs
autobuilder.py sign --from dist                                                                # sign what is still unsigned
autobuilder.py restore --package nifi --store dir:/srv/bh-store                                # verifies, then installs
```
Signing proves the artifact was produced by the holder of the key, not that its contents are harmless: source signatures
(`signature:` in `packages.yaml`) and review still matter. Use a dedicated signing subkey and keep the primary key offline;
pin the primary fingerprint.

## Restoring without Distillery

`shaker restore <artifact.tar.gz>` (Shaker) checks what `restore` here checks, with only bash and tar: sha256 of the tarball, the
signature of the sidecar against fingerprints pinned in the consumer's environment, platform and root, safe member names and links;
nothing is written before all pass. See [INTEGRATION](INTEGRATION.md).

## Housekeeping

Superseded artifacts accumulate (every recipe change is a new name). GitHub: retention does it. Nexus: a
cleanup policy by age/last-download. `inventory` lists everything with date and expiry to script your own pruning.
