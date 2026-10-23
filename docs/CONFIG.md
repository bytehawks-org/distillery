# `config.yaml` reference

Global paths and prefixes. Every path lives under `root`, so one switch (`--root`) relocates the
whole tree.

```yaml
config:
  root: /opt/bytehawks                       # real root. Required: --root rebases everything under it
  link: /opt/bh                              # convenience symlink (created by `shaker bootstrap`); never baked in artifacts
  core_path: /opt/bytehawks/core             # where distillery and shaker live
  default_download_path: /opt/bytehawks/var/cache/distillery   # tarball cache (kept, reused by checksum)
  default_src_path: /opt/bytehawks/var/build                    # extraction directories (recreated for each build)
  log_path: /opt/bytehawks/var/log/distillery                   # <pkg>-<version>.log
  libraries:
    default_prefix: /opt/bytehawks/deps                         # all libraries install here
  applications:
    default_prefix: /opt/bytehawks/{{ name }}                   # one prefix per application
```

| Key | Used for |
|---|---|
| `root` | the base that `--root` replaces; must be a prefix of every other path you want rebased |
| `link` | documentation and Shaker; not under `root`, so `--root` leaves it alone |
| `core_path` | informational / tooling |
| `default_download_path` | `fetch.download` cache; a file with the pinned checksum is reused, no network |
| `default_src_path` | `<this>/<name>-<version>` is the build directory; deleted and recreated for each build |
| `log_path` | full build log of every unit |
| `libraries.default_prefix` | prefix of `tag: library` units; also `deps_prefix` for dependents |
| `applications.default_prefix` | prefix template of `tag: application` units (`{{ name }}` allowed) |

## `artifacts`

```yaml
config:
  artifacts:
    default_stores: [github]          # used by stale/inventory/chain/restore/publish when --store is absent
    stores:
      github: {type: github}          # repo from GITHUB_REPOSITORY (or `repo: owner/name`), token GITHUB_TOKEN/GH_TOKEN
      nexus:  {type: nexus, url: https://nexus.example.com, repository: bytehawks, prefix: bytehawks}
```
Credentials are never in the file: `GITHUB_TOKEN`/`GH_TOKEN`, `NEXUS_USER`, `NEXUS_PASSWORD`, optional `NEXUS_URL`.
Details: [ARTIFACTS](ARTIFACTS.md).

## `--root`

`autobuilder.py --root /tmp/stage ...` rewrites every value that equals `root` or starts with `root/`:
`/opt/bytehawks/deps` → `/tmp/stage/deps`, `/opt/bytehawks/{{ name }}` → `/tmp/stage/{{ name }}`.
The recipe hash includes the prefix, so a staged build and a production build are different recipes
(they are *meant* to be: paths are baked at configure time).

Uses: unprivileged CI builds, tests (`/tmp/...`), building an image layer in a scratch directory,
side-by-side environments. To ship a staged tree to `/opt/bytehawks`, **rebuild with the real root**
(do not copy a tree built for another prefix: RPATHs, `--openssldir`, `--conf-path` are absolute).

## The `/opt/bh` symlink

A human convenience: `ls /opt/bh/nginx`, `PATH=/opt/bh/nginx/sbin:$PATH`. It must never appear in a
build command, a configuration file or an RPATH; use `{{ prefix }}` (which resolves to the real path).

## Custom config

`--config FILE` selects another file with the same schema (for example one that points `root` at
`/srv/bytehawks`). `--packages FILE` and `--assets DIR` do the same for the catalog and its templates.
