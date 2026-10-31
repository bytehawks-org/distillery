# Images

`autobuilder.py image` turns a **built** service into an OCI image: one layer per unit (dependencies first, the service last), the
runtime contract (user, command, stop signal) as the image configuration, the [SBOM](SBOM.md) and the sealed bundle inside. The output
is an *OCI archive* (`oci-layout`, `index.json`, `blobs/sha256/*`, plus the `manifest.json` that `docker load` wants) that
`docker load`, `podman load` and `skopeo copy oci-archive:` read. Nothing talks to a daemon or a registry.

```sh
autobuilder.py build --package nginx --tag application --with-deps --linkage static    # as usual
autobuilder.py image --package nginx --tag application --linkage static --entrypoint direct
docker load -i nginx-1.28.0-amd64-static.oci.tar
docker run -d --network host bytehawks/nginx:1.28.0-static
```

The unit has to be built (or restored) for the **current recipe**, and its installed files have to match `files.sha256`; otherwise the
command stops and says which unit and why. The layers are cut from the paths the build recorded, so an image is exactly what the build
produced - and the same build gives the same image digest (sorted entries, no host owners, timestamps from `built_at`).

Files keep their **build paths**: the tree lives at `/opt/bytehawks/...` (or the `--root` it was built under) because paths are baked into
the binaries. Build for the root you want in the image.

## Two ways to start the service

| | `--entrypoint shaker` (default) | `--entrypoint direct` |
|---|---|---|
| `ENTRYPOINT` | `<root>/core/shaker/bin/shaker run <pkg>` | the package's own command, `${BH_X}` expanded from the parameter defaults |
| Parameters at run time | environment, `--flags` after the image name, `*_FILE` secrets, aliases: everything Shaker does | none: defaults are fixed in the image |
| Templates, hooks, persistence | rendered / run at start by `shaker init` | templates are rendered **at image build** with the defaults; packages with hooks or persistence are refused |
| Needs in the image | bash and coreutils-class tools (a `--base`) and a Shaker checkout (`--shaker-dir`, default `<core_path>/shaker`) | nothing: `FROM scratch` works with a static unit |
| Fits | services you configure at deploy time | a fixed, minimal appliance: static nginx, valkey, mosquitto |

Options: `--base LAYOUT` builds on an existing image given as an OCI layout directory (its layers come first, its environment is kept);
`--image-tag NAME:TAG` (default `bytehawks/<name>:<version>`, with the platform suffix when it is not the plain one); `--out FILE`.

```sh
skopeo copy docker://debian:12-slim oci:/tmp/base:latest        # or: docker save base | tar -x -C /tmp/base
autobuilder.py image --package nginx --tag application --entrypoint shaker --shaker-dir /opt/bytehawks/core/shaker --base /tmp/base
docker run -e BH_NGINX_PORT=9000 bytehawks/nginx:1.28.0         # Shaker initializes and starts nginx
```

## What goes into the image

* **Users**: the service account of the contract is added to the base's `/etc/passwd` (and `/etc/group` for a non-zero gid), the base's own
  entries untouched. `User` is `uid:gid`.
* **Directories** of the contract get their owner and mode (`g+rwX`, `0750`, ...) as `shaker install` would set them; for Shaker mode the
  state tree `var/{lib/shaker,log,run}` is group-writable so any uid of the service group can run `init`.
* **Directories of the base** that the layers pass through keep the base's mode and owner (`/tmp` stays `1777`).
* **Labels**: `org.opencontainers.image.{title,version,created,source,revision,licenses,url,vendor}` from the manifest, and
  `io.bytehawks.{recipe,platform,linkage,sbom,generator}`. `io.bytehawks.sbom` is the path of the CycloneDX document inside the image.
* **Config**: `Entrypoint`, empty `Cmd` (so `docker run image --port=9000` reaches Shaker), `WorkingDir` = the prefix, `StopSignal` from the
  contract, `Env` (`BH_ROOT` and `PATH` for Shaker mode; the `native_env` defaults for direct mode).

## Measured

A static nginx (glibc, Ubuntu 24.04, `--linkage static`, built in the `unshare` sandbox) as a `FROM scratch` image, with docker 29 on this
host: 4 layers (openssl 7.8 MB, zlib 0.1 MB, pcre2 1.7 MB, nginx 5.7 MB; 15 MB archive), `docker run` → master and workers as uid 1001,
`HTTP/1.1 200 OK`. The same unit in Shaker mode on a base made of bash and coreutils (17 MB): Shaker renders `nginx.conf` from
`BH_NGINX_PORT=8081`, drops nothing (already uid 1001), nginx answers on 8081.

## Limits

* One platform per image (the unit's); no multi-arch index. Build per architecture and combine with your registry tooling.
* The base is a *single-image* OCI layout directory; Distillery neither pulls nor pushes. Use `skopeo`/`docker save` for that.
* Whiteouts of the base are not interpreted when reading `/etc/passwd`; a base that deletes the file in a later layer is not detected.
* No `HEALTHCHECK`: a scratch image has no tool to run one. Probe the TCP port declared by the `health` record from outside.
* A static glibc service still needs a compatible glibc for NSS lookups (see [STATIC](STATIC.md)); prefer a musl static build for scratch images.
