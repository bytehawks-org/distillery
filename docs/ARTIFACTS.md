# Artifacts and stores

An **artifact** is one built unit, packaged so it can be restored on another machine without compiling:
the files the build installed plus its bundle, as a deterministic tarball with a JSON sidecar. A **store**
is a place that keeps artifacts and can list them.

## Identity

```
bh--<package>--<variant>--<tag>--<arch>--<version>--<recipe12>
bh--nginx--stable--application--amd64--1.28.3--a1146e116dea
```

* `recipe12` = first 12 hex of the unit's recipe hash. Same name ⇔ same recipe, same prefix, same
  dependencies, same architecture. Changing a flag, the source hash or a dependency changes the name.
* `variant` is the first alias of the variant (`old-legacy`, not `legacy`).
* Names contain neither `/` nor `:` nor `|` and fit GitHub's artifact-name rules; the separator is `--`
  (package and variant names must not contain it).
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
| Listing | `GET /repos/{repo}/actions/artifacts` (paginated, expired ones ignored, newest wins) filtered on the `bh--` prefix |
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

## Housekeeping

Superseded artifacts accumulate (every recipe change is a new name). GitHub: retention does it. Nexus: a
cleanup policy by age/last-download. `inventory` lists everything with date and expiry to script your own pruning.
