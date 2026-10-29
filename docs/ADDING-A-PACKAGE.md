# Adding a package

Worked example: `valkey` (`make`-based, TLS through openssl), which is in the catalog. Every step is the same for any source
tarball; for a project that publishes no usable tarball, or when you want the pin to be a commit, see "Git sources" below.

## 1. Find a stable tarball URL and pin it

Prefer the project's official release asset. Then:

```sh
# add the variant WITHOUT sha256 first, then:
python3 scripts/autobuilder.py checksum --package valkey
# → <sha256>  valkey-8.0.1.tar.gz
```
Verify the value against the upstream's published checksum/signature, then write it into `sha256:`.
(`checksum` downloads through the same code path as a build, so it also proves the URL works.)

## Git sources (no tarball)

When there is no stable tarball, or a tag is the natural release, pin the **commit** instead of a hash:

```yaml
    variant:
      - {name: stable, major_version: 9.1, patch_version: 3, full_version: 9.1.3, commit: 29173b67508b2de4c0f0ed551f3b47cf19fc3ca8}
    git:
      repo: https://github.com/valkey-io/valkey
      ref_template: "{{ full_version }}"          # the tag; instead of release_download_url_template
```
`autobuilder.py checksum --package valkey` prints the commit the tag points to. A build clones the tag shallowly, refuses it unless
`HEAD` is exactly the pinned commit (a tag can be moved, a commit cannot), and exports the tree with `git archive` (no `.git`,
`export-ignore` honoured, submodules not included). `bump` pins the new tag's commit; `signature:` does not apply to git sources.

## 2. Declare source, dependencies, build

```yaml
  valkey:
    variant:
      - {name: stable, major_version: 8.0, patch_version: 1, full_version: 8.0.1, sha256: <pinned>}
    website: https://valkey.io/
    depends_on: [{name: openssl, variant: stable}]
    git:
      repo: https://github.com/valkey-io/valkey
      release_download_url_template: "<official asset URL with {{ full_version }}>"
    commands:
      make: make -j{{ jobs }} BUILD_TLS=yes OPENSSL_PREFIX={{ deps_prefix }}
      install: make install PREFIX={{ prefix }}
    tags: [application]
```
Checks while iterating:

```sh
autobuilder.py plan  --package valkey                        # order, pinned?
autobuilder.py build --package valkey --with-deps --dry-run  # read the generated script
autobuilder.py build --package valkey --with-deps --root /tmp/stage   # try it, unprivileged
```
Look at `/tmp/stage/var/log/distillery/valkey-8.0.1.log` on failure.
Rules: install **only** under `{{ prefix }}`; no `sudo`; prefer static linking of anything coming from
`deps` so the artifact does not depend on that directory at run time (verify with `ldd`).

## 3. Declare the runtime

```yaml
    runtime:
      user: {name: valkey, uid: 1001, gid: 0, home: "{{ prefix }}"}
      dirs:
        - {path: "{{ prefix }}/etc",  mode: "g+rwX", owner: "1001:0"}
        - {path: "{{ prefix }}/data", mode: "g+rwX", owner: "1001:0"}
      persistence: [{path: "{{ prefix }}/data", volume: /bytehawks/valkey/data}]
      templates: [{src: valkey.conf.tmpl, dest: "{{ prefix }}/etc/valkey.conf", mode: "0640"}]
      run:
        command: ["{{ prefix }}/bin/valkey-server", "{{ prefix }}/etc/valkey.conf"]
        stop_signal: TERM
      healthcheck: {tcp: {port: "${BH_VALKEY_PORT}"}}
      requires: [openssl]
      env:
        - {name: BH_VALKEY_PORT, type: port, default: 6379, aliases: [VALKEY_PORT, REDIS_PORT], file_key: server.port, cli: --port}
        - {name: BH_VALKEY_PASSWORD, type: secret, aliases: [VALKEY_PASSWORD, REDIS_PASSWORD], file_key: auth.password, cli: --password}
        - {name: BH_VALKEY_APPENDONLY, type: bool, default: true, file_key: persistence.appendonly}
```
Guidelines
* Name every parameter `BH_<PACKAGE>_<NAME>`; put **upstream/Bitnami names in `aliases`** so existing deployments keep working.
* Use the narrowest `type`: `port`, `enum`, `bool`, `path` validate before anything is written, and `path`/`enum`/`port` cannot carry a newline into a rendered config.
* Secrets: `type: secret` (masked everywhere) - operators will use `BH_VALKEY_PASSWORD_FILE`.
* `native_env` only when the application itself reads an environment variable.
* `run.command` is an argv list. Anything needing shell features goes into a small wrapper script in `assets/` copied by a `post` stage.

### 3b. Let `scaffold` write the parameters and the template

If the application ships a sample configuration file with a regular shape, do not write `runtime.env` and the template by hand:

```sh
autobuilder.py scaffold --package valkey --from valkey.conf --list      # review what it would expose
autobuilder.py scaffold --package valkey --from valkey.conf --write --exclude '^(include|loadmodule|user)$' \
    --default 'dir={{ prefix }}/data'
```
84 parameters, their types, defaults, descriptions and the template come out of valkey's own `valkey.conf`; see [SCAFFOLD](SCAFFOLD.md)
for the formats, the inference rules and what stays manual (flags, user, directories, run command, health check).

## 4. Write the template(s)

`assets/valkey/valkey.conf.tmpl`:

```
port {{ BH_VALKEY_PORT }}
dir {{ BH_PKG_PREFIX }}/data
appendonly {{ BH_VALKEY_APPENDONLY }}
```
`BH_PKG_*` and every declared parameter are available. Booleans arrive as `true`/`false`: if the
application wants `yes`/`no`, either use an `enum:yes,no` parameter or map it in a hook.
Optional values: `{{ NAME:-default }}`. Conditional blocks are not supported by design - use a
parameter that holds the whole line, or a hook that renders it.

## 5. Hooks (optional)

```
assets/valkey/hooks/pre-init.d/10-wait-for-primary.sh      # runs at every start
assets/valkey/hooks/init.d/10-load-seed.sh                 # runs once per data volume
```
Copy them to the declared hook directories in a `post:` stage
(`mkdir -p {{ prefix }}/hooks/init.d && cp -a ../../...`), or mount them at deploy time.

## 6. Build, install, run, verify

```sh
autobuilder.py build --package valkey --with-deps --root /opt/bhtest
export BH_ROOT=/opt/bhtest
shaker install valkey
shaker env valkey --help-vars
shaker init valkey --port=6380
shaker verify valkey --port=6380 --no-health
shaker run valkey --port=6380 --daemon && shaker verify valkey --port=6380 && shaker stop valkey
```
Iterate on `runtime:`/templates without rebuilding: `autobuilder.py bundle --package valkey --root /opt/bhtest`.

### Binary repack (prebuilt distributions, e.g. Java)

When upstream ships a ready-to-run distribution, skip `configure`/`make`: declare only `install`
(`cp -a . {{ prefix }}/`), pin the archive (zip works) and describe the runtime as usual. A runtime it needs becomes
a unit of its own (`depends_on: [{name: temurin, variant: stable, tag: application}]`) and its path is exported with
`native_env` (`BH_NIFI_JAVA_HOME` → `JAVA_HOME`). Configuration that cannot be a template (hundreds of keys, changes
between releases) is applied by a `post_init` hook shipped in `assets/<pkg>/hooks/post_init/`: idempotent, run at every
start, writing durable state to a persisted data directory. Reference implementation: `nifi` + `temurin` in
`packages.yaml`, `assets/nifi/hooks/post_init/10-nifi-config.sh`.

```sh
autobuilder.py build --package nifi --with-deps --root /opt/bhtest
export BH_ROOT=/opt/bhtest BH_NIFI_PASSWORD='at-least-12-chars' BH_NIFI_SENSITIVE_PROPS_KEY='at-least-12-chars'
shaker install nifi && shaker run nifi        # https://localhost:8443/nifi, self-signed certificate
```

## 7. Test and document

* CI runs `plan` for every package of the catalog and fails on an unpinned source, so a package without `sha256` cannot be merged.
* Mention it in `CHANGELOG.md`.
* Containerize: see Shaker `docs/DEPLOYMENT.md`.

## Checklist

- [ ] official URL, `sha256` verified against upstream
- [ ] `depends_on` complete; no hidden host dependency (`ldd` clean)
- [ ] installs only under `{{ prefix }}`
- [ ] `--dry-run` script reviewed
- [ ] parameters typed, aliases for known dialects, secrets typed `secret`
- [ ] user/dirs `1001:0` + `g+rwX` (arbitrary uid works)
- [ ] `run.command` argv, `stop_signal`, `healthcheck`
- [ ] `shaker install/init/run/verify` all green on a staged root
