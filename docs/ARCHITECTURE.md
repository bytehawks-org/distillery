# Architecture

## 1. Role

Distillery owns everything that is true **at build time and independent of the host**: where a
program comes from, which exact bytes (SHA-256), what it is built with, how it is built and what
it needs to run. Its output is not just a binary tree but a **bundle** that lets Shaker operate
the program with zero application-specific code. Shaker owns everything that depends on the host
and the moment (parameters, secrets, users, volumes, process, health).

```
  git: packages.yaml + config.yaml + assets/<pkg>/          (reviewed, versioned, the source of truth)
         │
         ▼   autobuilder.py
  Catalog ── Unit ── plan ── fetch/verify ── extract ── stages ── bundle      (this repository)
         │
         ▼   artifacts + bundle in /opt/bytehawks/...   (image layer, rsync, tarball, ...)
  shaker install · init · run · verify                                      (Shaker repository)
```

## 2. Modules (`scripts/bhdistillery/`)

| Module | Responsibility |
|---|---|
| `templates.py` | strict `{{ dotted.path }}` resolver (recursive, cycle-safe, `TemplateError`) |
| `model.py` | YAML loading, `apply_root`, `Catalog` (packages, variants, tags, dependencies), `Unit`, `plan` (topological order), `recipe_hash` |
| `fetch.py` | `download` (SHA-256 verify, cache, partial files), `extract` (safe tar, strip components) |
| `build.py` | stage script assembly, build environment, execution with logging, orchestration of one unit (`build_unit`) |
| `bundle.py` | `rosetta.map`, `runtime.spec`, `manifest.json/env`, assets copy, up-to-date check |
| `scripts/autobuilder.py` | CLI only: argument parsing and error reporting |

## 3. Build pipeline of one unit

```
Unit(name, variant, tag)
  1. context      name, versions, prefix (by tag), src_dir, deps_prefix, jobs, git.* resolved
  2. recipe hash  sha256(schema, resolved recipe, variant, tag, prefix, arch, assets/<pkg>/ digest, dependency hashes)
  3. skip?        bundle/manifest.json has the same recipe hash and a built_at   → [skip]
  4. fetch        cache by checksum; else download to *.part, hash while streaming, mismatch → delete + error
  5. extract      fresh src_dir; refuse traversal / escaping links; strip_components (default 1)
  6. script       set -euo pipefail; cd src_dir; for stage in pre configure make [check] install post
  7. run          bash -c, cwd=src_dir, env = process env + PREFIX JOBS CPPFLAGS LDFLAGS PKG_CONFIG_PATH PATH + package env:
  8. bundle       rosetta.map, runtime.spec, manifest.json (built_at), manifest.env, assets/
```

Failure at any step stops that unit (and the plan); the bundle is written **last**, so a failed or
interrupted build never looks up to date. The full log is kept at `log_path/<pkg>-<version>.log`.

### Why one shell session
`./Configure`, `cd`, `export` and variables must survive from one stage to the next, exactly like a
hand-written build script. Stages are separate YAML keys only for readability, `--stages` selection
and per-stage logging markers (`==> stage: make`).

### Dependency visibility
Libraries install into the shared `libraries.default_prefix` (`/opt/bytehawks/deps`). A dependent unit
is built with:

```
CPPFLAGS="-I<deps>/include"   LDFLAGS="-L<deps>/lib -L<deps>/lib64"
PKG_CONFIG_PATH="<deps>/lib/pkgconfig:<deps>/lib64/pkgconfig"   PATH="<deps>/bin:$PATH"
```
nginx finds openssl, zlib and pcre2 through those include/library paths and links the static archives
(`libssl.a`, `libcrypto.a`, `libz.a`, `libpcre2-8.a`). `ldd` of the resulting binary lists only
`libc` and `libcrypt`: no runtime dependency on `deps/`.

## 4. Recipe hash and idempotency

```
recipe_sha256(unit) = SHA256( json(sort_keys){
    schema: <bundle schema>,
    recipe: <package definition, templates resolved, without the variant list>,
    variant: <selected variant, including its sha256>,
    tag, prefix, arch,
    assets: <digest of assets/<package>/: path, execute bit, content; omitted when there are none>,
    depends: [recipe_sha256(dep) for dep in dependencies]      # Merkle
})
```

* change a command, a flag, a runtime default, a source hash, the prefix (`--root`) → new hash → rebuild;
* change a library → its hash changes → **every dependent** rebuilds on the next `build --with-deps`;
* nothing changed → `[skip]`, no network, no compile;
* the hash is stored in `manifest.json` and copied by Shaker to its state (`installed_recipe`): *which
  exact recipe produced what runs on this host* is answerable offline.

`built_at` is deliberately not part of the hash. `SOURCE_DATE_EPOCH` fixes it for reproducible manifests.

### Artifacts, stores, automation (0.4)
The recipe hash is also the **cache key**: `bh_<pkg>_<variant>_<tag>_<arch>_<version>_<recipe12>`
names an artifact in a store, so "what is stale?" is a set difference between the catalog and a store listing
([ARTIFACTS](ARTIFACTS.md)). `build` can restore instead of compile (`ensure_unit`: up to date → skip, in a
store → restore, else build); `chain` applies that to every stale unit in dependency order. GitHub Actions
drive it on a schedule and on upstream news ([AUTOMATION](AUTOMATION.md)). Upstream discovery
(`upstream.py`) and the YAML-preserving edit (`bump.py`) feed it; `store.py` and `package.py` hold the backends
and the artifact format. The architecture of a unit does not change: stores only decide whether step 4-7
run or are replaced by a restore.

## 5. Design decisions

| Decision | Rationale |
|---|---|
| YAML on the build side, line-oriented text in the bundle | Python/PyYAML exist where builds run; Shaker must run on a bare image with only bash |
| Strict templates | a typo in a path must fail loudly, never produce `rm -rf /opt/bytehawks//` |
| Two template syntaxes (`{{ }}` here, `${VAR}` for Shaker) | build-time constants are resolved once and frozen into the bundle; runtime values stay symbolic |
| Checksums mandatory | provenance is a property of the catalog, not of the network at build time |
| Bundle written last | a bundle's presence = the artifact is complete |
| Libraries share a prefix, applications own theirs | static libs are build inputs for many apps; apps are deployed and removed as units |
| Real prefix baked in, `/opt/bh` never | relocating a symlink must not break RPATHs or generated configs |
| `--root` instead of DESTDIR staging | many builds (openssl `--openssldir`, nginx `--prefix`) bake paths at configure time; rebasing the whole tree keeps them correct |
| No parallel unit builds (yet) | each unit already runs `make -jN`; ordering/locking is simpler and safe |

## 6. Limits (see ROADMAP)

Linux only; build commands are trusted shell (no sandbox); no signature verification of sources;
no SBOM export; no cross-compilation or multi-arch matrix (the openssl `Configure linux-x86_64` target
is still hard-coded); no remote artifact cache; source must be a tarball URL (git checkouts not supported yet).
