# distillery

Bytehawks "Distillery": the source of truth and build base for artifacts.
Counterpart of [Shaker](https://github.com/bytehawks-org/shaker), which applies at runtime what Distillery declares.

## Layout

```
/opt/bytehawks/core/distillery   this repository (config.yaml, packages.yaml, scripts/)
/opt/bytehawks/deps              libraries
/opt/bytehawks/<package>         applications; <package>/.bytehawks/ = bundle for Shaker
/opt/bh -> /opt/bytehawks        convenience symlink (never baked into binaries)
```

## Usage

```sh
python3 scripts/autobuilder.py build  --package openssl --variant stable --tag library
python3 scripts/autobuilder.py bundle --package openssl --variant stable --tag library
```

`bundle` writes `<prefix>/.bytehawks/` containing:

* `rosetta.map`: compiled from the package `runtime.env` section (see below)
* `manifest.json`: package, variant, version, source URL, tarball sha256, recipe sha256 (traceability)

## `runtime.env` (declarative parameters)

```yaml
runtime:
  env:
    - name: BH_OPENSSL_CONF            # must start with BH_<PACKAGE>_
      type: path                       # string secret int uint port bool path enum:a,b
      default: "{{ default_prefix }}/etc/ssl/openssl.cnf"
      required: false
      aliases: [OPENSSL_CONF]          # other env names accepted as input
      file_key: openssl.conf           # key in legacy config files (section.key)
      cli: --conf                      # container/pod argument
      native_env: OPENSSL_CONF         # variable the application itself reads
      description: OpenSSL configuration file
```

Shaker resolves it with precedence CLI > env > `*_FILE` > alias > config file > default.
See Shaker `docs/ROSETTA.md`.

## Roadmap

Building is still dry-run (command execution is commented out in `autobuilder.py`);
variants do not carry a `sha256` yet; `depends_on` resolution is not implemented.
