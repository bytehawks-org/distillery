# Testing

```sh
pip install -r scripts/requirements.txt
cd tests && python3 -m unittest discover -v        # 108 tests, ~3 s, no network, no root (needs git; signature tests need gpg and are skipped without it)
python3 -W error::ResourceWarning -m unittest discover   # also checks for leaked file handles
```

`unittest` only (standard library). `tests/helpers.py` builds tiny tarballs in temp directories and
a catalog rooted in a temp dir (`apply_root`), so tests never touch `/opt`.

| File | Covers |
|---|---|
| `test_templates.py` | nested resolution, strictness, circular references, `${...}` untouched, dict/list |
| `test_model.py` | `apply_root`, prefix/bundle dir per tag, ambiguous tag, topological order + dedup, cycles, unknown dependency/variant, **recipe hash cascades to dependents**, prefix in hash |
| `test_fetch.py` | download + verify + cache hit without network, mismatch leaves nothing, unpinned refused/allowed, extraction strip, traversal and escaping-link rejection |
| `test_bundle.py` | `rosetta.map` columns, defaults/required/templates, invalid names/types/duplicates/`|`, `runtime.spec` records, bad hook stage, shell-string `run`, `emit` writes the whole contract |
| `test_build.py` | stage script, full build + bundle + log, skip / `--force` / changed recipe, dry-run side effects, failing command leaves no bundle, **wrong checksum never reaches the shell**, dependencies visible via `CPPFLAGS` |
| `test_upstream.py` | version parsing, series matching (`3.5` vs `3.50`), tag filtering/sorting, `check_unit` against a **real local git repository**, series override, error paths |
| `test_bump.py` | YAML edit keeps comments/flow style/other variants, two-part versions, series change refused, checksum computed and **cross-checked with the upstream-published one** (mismatch aborts) |
| `test_store.py` | artifact naming round-trip, `DirStore` (tamper detection), `GithubStore` against a **fake GitHub API** (filters expired/foreign/duplicates, token not forwarded to the redirect host), `NexusStore` against a **fake Nexus** (PUT/list/GET, basic auth), factory, multi-store |
| `test_artifacts.py` | snapshot diff, `files.sha256`, byte-identical tarballs, restore into a fresh root, unsafe/foreign artifacts refused, `stale → chain → stale`, **dependency cascade**, restore-instead-of-build, two-stage libs/apps output, `publish` idempotency, `verify` tamper detection, forced/multi-package selection |
| `test_cli.py` | `list`, `plan`, `build --dry-run` on the **real catalog** (every shipped source pinned, plans resolve), legacy invocation, error reporting |

## Workflows

`.github/workflows/*.yml` are linted by **actionlint** (CI job `workflows`; locally `pip install actionlint-py && actionlint`).
They cannot be executed locally: the Python commands they call are what the tests cover.

## Validation performed on 0.4.0 (real network)

* `outdated` against the real upstream repositories (`git ls-remote`): nginx 1.28.0→1.28.3, openssl 3.0.18→3.0.22 /
  3.5.4→3.5.9 / 3.6.0→3.6.5, pcre2 10.43→10.49, zlib 1.3.1→1.3.2.
* `bump` on a copy of the catalog: zlib 1.3.2 (real download, sha256 computed), openssl 3.5.9 (**sha256 confirmed
  by upstream's `.sha256` file**); the diff touched exactly the 3 lines per variant.
* `inventory`/`stale` against the real GitHub REST API of this repository (authenticated, empty store → everything `missing`).
* `chain` on the real catalog (zlib, pcre2) into a `dir:` store, deletion of the root, `restore`, `verify` OK,
  tampering with an installed header detected.
* Not exercised: a real Nexus, artifact download from real GitHub, the workflows on GitHub.

## Cross-repository contract

Shaker's `tests/test_integration.sh` builds a package from a local tarball with this repository
(`DISTILLERY_HOME`, defaults to `../distillery`) and exercises `shaker install/init/run/verify` on the result.

## CI

`.github/workflows/ci.yml`: unit tests on Python 3.9, 3.12, 3.13; a catalog consistency step
(`list`, `plan` for every package, fails on `MISSING`).

## Validation performed on 0.3.0 (manual, real network and compiler)

```
autobuilder.py build --package nginx --with-deps --root /opt/bhtest
  openssl 3.5.4 → zlib 1.3.1 → pcre2 10.43 → nginx 1.28.0      (≈1m40s on 4 cores)
  second run: [skip] ×4
nginx -V → "built with OpenSSL 3.5.4"; ldd → libc, libcrypt only
checksum --package zlib → 9a93b2b7...  (matches the pinned value)
```
then installed, run (as uid 1001 and as an arbitrary uid 54321/gid 0), verified and stopped with Shaker.
`openssl` 3.0.18/3.6.0 variants were pinned from upstream-published `.sha256` files but **not built**.

## Writing tests

Use `helpers.package(url, sha256, **extra)` and `catalog({...}, root)`; use `TempDir()` for the root
and for tarballs (`make_tarball`). Never use the network: serve with `Path.as_uri()` (`file://`).
