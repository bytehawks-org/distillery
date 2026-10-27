# Native packages: deb, rpm, apk

`autobuilder.py package` turns a **built** unit into packages for the host's package manager. The file list is the one
the build recorded (`files.sha256` of the bundle), the metadata comes from the catalog and from `packaging:` in
`config.yaml`, and the files are written by [nfpm](https://nfpm.goreleaser.com) (MIT, one Go binary).

```sh
autobuilder.py build   --package nifi --with-deps                    # default root /opt/bytehawks
autobuilder.py package --package nifi --with-deps --out dist/packages
#   bytehawks-temurin_21.0.9-1_amd64.deb   bytehawks-temurin-21.0.9-1.x86_64.rpm   bytehawks-temurin-21.0.9-r1.apk
#   bytehawks-nifi_2.9.0-1_amd64.deb       bytehawks-nifi-2.9.0-1.x86_64.rpm       bytehawks-nifi-2.9.0-r1.apk   SHA256SUMS
autobuilder.py package --package nifi --format deb,apk --dry-run     # names, file counts, dependencies; writes nothing
```
`--with-deps` also writes the dependencies (needed to install the application). Without it only the named unit.

## Naming

Names are templates in `config.yaml`, strict like the rest of Distillery (an unknown variable is an error):

```yaml
packaging:
  release: 1
  name_template: "bytehawks-{{ name }}{{ variant_suffix }}"        # the package name (deb Package, rpm Name, apk pkgname)
  file_name_template:
    deb: "{{ pkg_name }}_{{ pkg_version }}-{{ release }}_{{ pkg_arch }}.deb"
    rpm: "{{ pkg_name }}-{{ pkg_version }}-{{ release }}.{{ pkg_arch }}.rpm"
    apk: "{{ pkg_name }}-{{ pkg_version }}-r{{ release }}.apk"
```

| Variable | Value |
|---|---|
| `name`, `variant`, `tag` | package key, selected variant, role |
| `variant_suffix` | `""` for the default variant (`stable`), `-<variant>` for the others |
| `version`, `major_version`, `recipe12` | `full_version` of the variant, its major, first 12 hex of the recipe hash |
| `arch` | our name: `amd64`, `arm64` |
| `release` | `packaging.release` (deb revision, rpm release, apk `-rN`) |
| `pkg_name`, `pkg_version` | the resolved package name and version (file names only) |
| `pkg_arch` | the format's own name: deb `amd64`/`arm64`; rpm and apk `x86_64`/`aarch64` (file names only) |
| `format` | `deb`, `rpm` or `apk` (file names only) |

Names must be valid in all three formats (lowercase letters, digits, `+ . _ -`); the version must start with a digit.
Raise `release` when you republish the same version (a new recipe hash with the same version needs a higher release, or
the package manager will not upgrade). Putting `{{ recipe12 }}` in the file name makes files unique per build.

## What goes in a package

* Every file and symlink the build installed, owned by `root:root`, at the **same absolute paths** (`/opt/bytehawks/<name>/…`),
  including `<prefix>/.bytehawks/` (manifest, `rosetta.map`, `runtime.spec`, `files.sha256`) and the hooks.
* An application owns its directories. A library shares `/opt/bytehawks/deps` with the others, so it owns only its bundle directory.
* `depends` = the units it was built against, **pinned to the exact version-release** in each format's syntax
  (`pkg (= v-r)` deb, `pkg = v-r` rpm, `pkg=v-rR` apk). `conflicts` = the other variants of the same package (same prefix).
* Metadata: `description`, `license`, `website` (homepage) from the package entry; `maintainer`, `vendor`, `section` from `packaging:`.
* A post-install script that runs `shaker install <name>` when the unit has a `runtime:` and Shaker is present (`BH_ROOT` = the
  build root); if not, it only prints the command to run. Turn off with `packaging.shaker.post_install: false`.

## Signing the packages

nfpm signs per format; configure the key files and give the passphrase through `NFPM_PASSPHRASE`:

```yaml
packaging:
  signing:
    deb: {key_file: /secure/pkg-signing.asc, key_id: ABCD1234}          # armored OpenPGP secret key
    rpm: {key_file: /secure/pkg-signing.asc}
    apk: {key_file: /secure/apk-signing.rsa.pem, key_name: bytehawks@example.com}   # apk uses an RSA key, not OpenPGP
```
This signs the **packages** for the package managers, independently of the artifact signatures (`artifacts.signing`), which
protect the Distillery stores.

## nfpm

`package` looks for `--nfpm`, `BH_NFPM`, `packaging.nfpm`, then `PATH`. The catalog has it as a unit:
`autobuilder.py build --package nfpm` installs the pinned, checksum-verified release in `/opt/bytehawks/nfpm/bin/nfpm`, which is found
automatically under the build root.

## Limits

* **Build into a clean root.** The file list is what the build *changed*; rebuilding an application over an existing prefix
  records only the files that differ (Distillery warns). CI containers are clean; locally remove the prefix first.
* Build with the default root. A unit built with `--root /x` has `/x` baked into its configuration and RPATHs; `package` warns, and
  the package installs to exactly those paths.
* The package does not declare a dependency on the C library: build on the **oldest** distribution you intend to support
  (the build workflow uses `debian:12`), as with the tarball artifacts.
* A package is the whole unit, in the format of the host family: deb for Debian/Ubuntu, rpm for Fedora/RHEL/SUSE, apk for Alpine —
  but the binaries are glibc builds. Alpine (musl) needs units built on Alpine, which is not covered yet.
* Repository metadata (`apt` Packages/Release, `createrepo`, `APKINDEX`) is not generated; publish the files to your own
  repository tooling (Nexus, aptly, pulp, …).
