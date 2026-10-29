# `scaffold`: parameters and template from the configuration file an application ships

Declaring `runtime.env` by hand means reading the application's documentation and copying names, defaults and descriptions into
YAML. When the application ships a configuration file whose shape is regular (one setting per line, `key value`, `key = value`,
`[sections]`, flat YAML), the file already contains all of it. `scaffold` reads it and writes two things:

1. **`runtime.env` entries**: name `BH_<PACKAGE>_<KEY>`, type, default, `cli` flag, `file_key` (formats Rosetta reads), and the
   description taken from the comment above (or beside) the setting;
2. **a template** (`assets/<package>/<file>.tmpl`): the same file with each chosen value replaced by `{{ NAME }}`, registered in
   `runtime.templates`, which Shaker renders at `init`. Everything else, comments included, is kept as the project wrote it.

```sh
autobuilder.py scaffold --package valkey --from valkey.conf --list                  # what it would expose, nothing written
autobuilder.py scaffold --package valkey --from valkey.conf                         # the YAML on stdout
autobuilder.py scaffold --package valkey --from valkey.conf --write \
    --exclude '^(include|loadmodule|user|aclfile)$' --also '^(requirepass|maxmemory|tls-.*)$' \
    --default 'dir={{ prefix }}/data'                                                # packages.yaml + assets/valkey/valkey.conf.tmpl
```
The package must already exist in `packages.yaml` (variant, source, commands); `scaffold` only adds the `runtime.env` entries and
the template. Re-running it adds what is new and **never changes an existing entry**, so hand edits survive and a newer sample
file from upstream only contributes its new settings. The exact command that produced a package is kept as a comment above it in
`packages.yaml`.

## Formats

| `--format` | Line | Example | Detected |
|---|---|---|---|
| `space` | `key value` | valkey/redis, mosquitto, httpd-style | default |
| `equals` | `key = value` (quotes, trailing `# comment`) | postgresql.conf | lines with `=` |
| `ini` | `[section]` then `key = value`; name `BH_PKG_SECTION_KEY`, `file_key section.key` | my.cnf | a `[section]` line |
| `yaml` | scalar leaves of nested mappings; lists and what is nested in them are not settings | prometheus.yml | `.yml` / `.yaml` |

## What is inferred

| From | To |
|---|---|
| `yes/no`, `on/off`, `true/false` | `enum:yes,no` (the application's own tokens, not Shaker's `bool`) |
| integer; key ending in `port` | `uint` / `int`; `port` when the value is a valid port, the setting is active and the comment does not say `0` disables it |
| a list in the comment above (`one of: a, b, c`, or `one of:` followed by one value per line) containing the default | `enum:a,b,c` |
| value is a path, or key ends in `dir`/`file`/`path` | `path` |
| key mentions `pass`, `secret`, `token`, `key`, `credential` | `secret`: never gets a default |
| anything else | `string` |

Correct a type with `--hint key=enum:a,b` (repeatable). Set or replace a default with `--default key=value` (for example a path
that must live under `{{ prefix }}` for the service account).

## Active settings and commented examples

Sample files document most options as comments. They are **examples, not defaults** (`# tls-port 6379` would open a TLS port
if rendered as written), so:

* active settings (uncommented) get their value as default;
* a setting found only as a comment becomes **optional**: no default, and its template line is
  `{{? NAME }}key {{ NAME }}`, emitted by Shaker only when the parameter is set. A bare `#key` (documented with no value) works the
  same way;
* `--commented strict` takes `#key value` / `#key = value` (no space after `#`, how sample files usually write them) and bare
  `#key`; `--commented loose` also takes `# key value`, and prose can match: use it with `--also`, which names the commented
  settings to take on top of all the active ones;
* a commented example of a directive that is also set (`# port 0` next to `port 6379`) is documentation, not a second setting.

Directives that may repeat (`save`, `include`, `rename-command`) are left out: a repeating directive is a list, not a parameter.

## What stays manual

* **Which settings to expose.** `--include`, `--exclude`, `--also`, `--max`: a policy decision, with a reasonable default of
  "every active setting".
* **Defaults that cannot work for a service account** (a PID file under `/var/run`, a data directory `./`): `--default`.
* **Things that are not in a configuration file**: command-line flags (Prometheus's `--web.listen-address`), the user, directories,
  the run command, health checks, hooks. They are the `runtime:` skeleton of the package, written once.
* **Configuration that is not a flat list of settings**: HAProxy's `global` / `frontend` / `backend` DSL, nginx's nested blocks,
  Apache's `<Directory>` sections. There is no file to derive them from; write the template and a handful of parameters by hand.

## Reviewing the result

`--list` first. Then read the generated template once against the upstream file (`diff` shows only the replaced values), run
`shaker up` with a few parameters changed, and `shaker verify`. Defaults rendered into the file equal the file's own values, so a
package built from an unmodified sample behaves exactly like the application's defaults.
