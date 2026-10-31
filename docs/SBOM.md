# SBOM

Every real build writes a **CycloneDX 1.5** document, `sbom.cdx.json`, into the unit's bundle. Because the bundle is sealed
(`bundle.sha256`, and `bundle.sha256.asc` when you sign), the SBOM is authenticated together with the files it describes, travels in
every artifact and every [image](IMAGE.md), and Shaker's `verify` covers it like any other bundle file.

```sh
autobuilder.py sbom --package nginx --tag application            # to stdout, from the catalog: no build needed
autobuilder.py sbom --package nginx --tag application --out nginx.cdx.json
```

## What it says

| CycloneDX field | Content |
|---|---|
| `metadata.component` | the unit: `type` (`application` / `library`), name, version, `purl` `pkg:generic/<name>@<version>`, source SHA-256, licence (SPDX), links to the source archive, the repository and the website |
| `metadata.component.properties` | `bytehawks:recipe_sha256`, `:variant`, `:platform`, `:linkage`, and `:source_commit` for git sources |
| `components` | every unit it was built with, **transitively**, each once, with the same fields |
| `dependencies` | the graph: which unit depends on which |
| `metadata.tools` | Distillery's version and the **toolchain** that built it (`toolchain:cc`, `:ld`, `:make`, libc, OS... as recorded in `manifest.json`) |
| `metadata.timestamp` | the build time (`built_at`; with `--reproducible` the source's time) |
| `serialNumber` | `urn:uuid:` derived from the recipe hash: the same recipe always has the same number |

Licences come from the `license:` key of the package. A known SPDX identifier becomes `license.id`; a valid SPDX expression
(`Apache-2.0 OR MIT`) becomes `expression`; anything else is kept as a free-text `license.name`, so the document always validates.

## Checking it

The documents are validated against the CycloneDX 1.5 JSON schema in the test-suite when `cyclonedx-python-lib` is installed:

```sh
pip install "cyclonedx-python-lib[json-validation]"
python3 - <<'EOF'
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
print(JsonStrictValidator(SchemaVersion.V1_5).validate_str(open("nginx.cdx.json").read()) or "valid")
EOF
```

Feed the file to any scanner that reads CycloneDX (`grype sbom:nginx.cdx.json`, `trivy sbom nginx.cdx.json`, Dependency-Track).

## Limits

* The SBOM lists the **units Distillery built** and their sources. It does not look inside a binary repack (NiFi's bundled JARs) or
  list the files of the base image an [image](IMAGE.md) is built on: scan those with a tool that reads them.
* Components are `pkg:generic` purls: Distillery knows the upstream project and version, not a package ecosystem. Matching against
  vulnerability databases therefore relies on the scanner's name/version matching (CPE) rather than an ecosystem identifier.
* SPDX output is not produced; CycloneDX only.
