# Artifacts and repositories

## File set

For every package × version × variant:

| File | Content |
|---|---|
| `<package>-<version>-<variant>-linux-<arch>.tar.gz` | The artifact |
| `<...>.tar.gz.sha256` | `<hash>  <file>` (sha256sum format) |
| `<package>-<version>-<variant>-linux-<arch>.manifest.json` | Build metadata and file inventory |
| `<package>-<version>-<variant>-linux-<arch>.sbom.cdx.json` | CycloneDX 1.5 SBOM (if `generate_sbom`) |

Example: `nginx-1.30.6-stable-linux-amd64.tar.gz`.

## Tarball layout

```
<package>-<version>/
  .distillery/package.json      # name, version, variant, arch, libc, kind, link, prefix, depends_on, built_at
  bin/ sbin/ lib/ ...           # content of the install prefix
```
- Entries sorted, `root:root`, fixed mtime (`SOURCE_DATE_EPOCH`), gzip without timestamp → reproducible.
- Applications are built with prefix `/opt/bytehawks/apps/<name>` compiled in. Where a program allows it, pass
  the location at runtime (e.g. `nginx -p DIR -c conf/nginx.conf`). Relocatable bundles do not depend on it.
- Library artifacts hold the prefix content (`include/`, `lib/*.a`, `lib/pkgconfig`, `bin/*-config`) and are
  extracted into the deps prefix by dependents.

## Manifest (`*.manifest.json`)

```json
{
  "schema": "distillery-manifest/1",
  "name": "nginx", "version": "1.30.6", "variant": "stable", "arch": "amd64", "libc": "musl",
  "kind": "application", "link": "static", "status": "stable", "prefix": "/opt/bytehawks/apps/nginx",
  "license": "BSD-2-Clause", "website": "...", "summary": "...",
  "depends_on": {"zlib": "1.3.2", "pcre2": "10.49", "openssl": "3.5.9"},
  "source": {"url": "...", "sha256": "...", "file": "nginx-1.30.6.tar.gz"},
  "builder": {"image": "...:stable", "alpine": "3.21", "musl": "1.2.5"},
  "built_at": "2025-11-17T06:16:30Z",
  "files": [{"path": "sbin/nginx", "type": "file", "size": 8752040, "sha256": "..."},
            {"path": "lnk", "type": "symlink", "target": "bin/x"}],
  "artifact": {"filename": "...tar.gz", "sha256": "...", "size": 0}
}
```

## Repositories and strategy

Configured in `config.repository` (see [config.yaml.md](config.yaml.md)):

| `strategy` | Publish behaviour |
|---|---|
| `primary-only` | Primary endpoints only; failure = error |
| `primary-with-fallback` | First endpoint that succeeds; fallbacks used when earlier ones fail |
| `mirror-all` (default) | Every endpoint must succeed (Nexus **and** GitHub); all are attempted, then an error is raised if any failed |

Reads (dependency fetch, existence checks) always try primary then fallback and tolerate unreachable endpoints.
Uploads are **immutable**: when the main tarball already exists, nothing is uploaded. The tarball is uploaded
**last**, so its presence means the artifact set is complete.

### Nexus (raw hosted)
`<NEXUS_URL>/<repository>/<path_template>/<file>` with the default `path_template`
`{package}/{major_minor}/{full_version}`:
```
http://nexus.nexus.svc.cluster.local:8081/repository/bytehawks-packages/nginx/1.30/1.30.6/nginx-1.30.6-stable-linux-amd64.tar.gz
```
Upload is HTTP `PUT` with basic auth; existence is a `HEAD`. Disable *redeploy* on the repository.

### GitHub Releases
Repository `repository.fallback.github.repo` (default `bytehawks-org/artifacts`). One release per package
version, tag from `tag_template` (`{package}-{full_version}` → `nginx-1.30.6`); the assets of **all variants**
live in that release. Releases are created on demand; a creation race between parallel jobs is handled.
```
https://github.com/bytehawks-org/artifacts/releases/download/nginx-1.30.6/nginx-1.30.6-stable-linux-amd64.tar.gz
```

## Consuming an artifact

```bash
base=https://github.com/bytehawks-org/artifacts/releases/download/nginx-1.30.6
f=nginx-1.30.6-stable-linux-amd64.tar.gz
curl -fsSLO "$base/$f" -O "$base/$f.sha256" && sha256sum -c "$f.sha256"
tar -xzf "$f" -C /opt && /opt/nginx-1.30.6/sbin/nginx -V
```

In a Dockerfile (static packages need nothing else):
```dockerfile
FROM scratch
ADD https://nexus.example.com/repository/bytehawks-packages/nginx/1.30/1.30.6/nginx-1.30.6-stable-linux-amd64.tar.gz /
```
Relocatable bundles need `/bin/sh`, so use a minimal base (e.g. `busybox`/`alpine`) instead of `scratch`.
Verify `sha256` (and optionally the manifest file inventory) in your own pipeline; signatures are on the roadmap.

## SBOM

CycloneDX 1.5 with the package component (purl `pkg:generic/<name>@<version>`, license, website, source URL and
hash) and its build-time dependencies. Upstream dependency graphs of the sources themselves are not expanded.
