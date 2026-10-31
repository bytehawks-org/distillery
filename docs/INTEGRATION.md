# Distillery and Shaker: working together, or alone

Distillery **builds and describes**, Shaker **installs, runs and audits**. They share one small, additive contract (the
[bundle](BUNDLE.md) and the [artifact](ARTIFACTS.md)) and each is useful without the other.

| You have | Works | How |
|---|---|---|
| Distillery only | build, artifacts, stores, signing, deb/rpm/apk | the packages run `shaker install` after installation only if Shaker is there; otherwise they print what to run. `autobuilder.py restore` installs an artifact, `autobuilder.py verify` checks the files |
| Shaker only | install, init, run, verify, **restore** | a hand-written bundle (`runtime.spec`, `rosetta.map`, `manifest.env`) is enough; every integrity check below is skipped when its file is absent. `shaker restore` unpacks a Distillery artifact produced elsewhere |
| Both | all of it, plus the chain of trust below | |

## What Distillery writes, what Shaker reads

| Distillery emits | Shaker uses it for | If absent |
|---|---|---|
| `manifest.env`: `BH_PKG_ARCH`, `BH_PKG_LIBC` | `install` refuses a build for another architecture or C library (a glibc build does not run on Alpine); `verify` reports it. Override: `BH_SHAKER_ALLOW_PLATFORM_MISMATCH=true` | no check |
| `runtime.spec`: `mutable\|path` (from `runtime.mutable`) | `verify --files` skips what the runtime rewrites on purpose (NiFi's `conf/`) | every file is checked |
| `bundle.sha256` (sha256sum format) over the contract files, assets and `files.sha256` | `verify` and `install` refuse a bundle edited after the build | no check |
| `bundle.sha256.asc` (OpenPGP, when `artifacts.signing.key` is set) | accepted only from pinned signers: `BH_SHAKER_TRUSTED_FINGERPRINTS`, `BH_SHAKER_REQUIRE_SIGNATURE=true` / `--require-signature` | unsigned bundles install unless a signature is required |
| `manifest.env`: `BH_PKG_DEPENDS` (direct dependencies with their recipe) | `shaker up <pkg>`: sets the dependencies up first, in order, and refuses a dependency that is not the build recorded | no dependencies |
| `files.sha256` | `verify --files`: every installed file, link and directory against the build | skipped |
| artifact `<name>.tar.gz` + `.json` (+ `.json.asc`) | `shaker restore`: sha256, signature, platform, root and paths are checked before anything is written | — |
| package `post-install` (deb/rpm/apk) | runs `shaker install <pkg>`; `packaging.shaker.package` can make services depend on the package that provides Shaker | prints the command to run |

## The chain of trust

```
upstream source ──signature:──► build ──► files.sha256 (hash of every installed file)
                                   └────► bundle.sha256 (hash of the bundle files + files.sha256) ──► bundle.sha256.asc  (your key)
                                   └────► artifact .json (sha256 of the tarball, recipe, platform)  ──► .json.asc        (your key)

consumer:  shaker restore   checks .json.asc → tarball sha256 → platform/root → safe paths → unpack → bundle.sha256
           shaker install   checks platform + bundle.sha256 (+ .asc when required)
           shaker verify --files --require-signature   re-checks everything on the running host, any time
```
Trust is **configured at the consumer** (pinned fingerprints in its environment), never read from the bundle or the artifact
itself. Signing key: a dedicated signing subkey, primary key offline; pin the **primary** fingerprint.

## Typical setups

```sh
# 1. build host (or CI): build, seal, sign, pack
BH_SIGNING_KEY=<fpr> autobuilder.py build --package nifi --with-deps --artifacts-dir dist

# 2. target host or container image: only Shaker is needed
export BH_SHAKER_TRUSTED_FINGERPRINTS=<primary fingerprint> BH_SHAKER_REQUIRE_SIGNATURE=true
shaker restore dist/bh_temurin_...tar.gz && shaker restore dist/bh_nifi_...tar.gz
shaker up nifi --run                              # temurin first, then nifi: install + init + start; parameters: BH_NIFI_PASSWORD, BH_NIFI_SENSITIVE_PROPS_KEY, ...
shaker verify nifi --files --require-signature    # audit at any time

# 3. legacy host with a package manager: deb / rpm / apk
autobuilder.py package --package nifi --with-deps  &&  apt install ./bytehawks-nifi_*.deb      # post-install: shaker install
```
Build and install under the same root (`/opt/bytehawks` unless `--root` / `BH_ROOT`): paths are baked into the files, and both
`shaker restore` and the recipe hash enforce it.

## Versioning of the contract

The bundle `schema` stays **1**: every addition above is a new record, field or file that older consumers ignore (Shaker warns
about unknown `runtime.spec` records and skips what it does not know). Removing or renaming something requires `schema` 2.
Both repositories test the contract: Distillery's `tests/test_bundle.py`, `tests/test_signing.py` and Shaker's
`tests/test_integration.sh`, which builds a package with Distillery and consumes it (`verify --files`, `restore`).
