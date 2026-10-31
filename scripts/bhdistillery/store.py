"""Artifact stores: where built units are kept, looked up by content (recipe hash) and restored from.

An artifact is the set of files one unit installed plus its bundle, as a deterministic tar.gz, with a
JSON sidecar (sha256 of the tarball, manifest). Its identity is encoded in the name:

    <package>_<holder>_<variant>_<tag>_<platform>_<version>_<recipe12>        e.g.  nginx_bh_stable_application_amd64_1.28.0_a1146e116dea

so a store can answer "is this exact recipe already built?" from a listing alone. `_` separates the fields, so none of them may contain
one (hyphens and dots are fine: `old-legacy`, `amd64-musl-static`, `1.28.0`); the holder - who owns the artifacts, `bh` for ByteHawks - is
`config.artifacts.holder`, and the order of the seven fields is `config.artifacts.name_order`.

Backends
  dir:PATH   a directory (workflow-local `dist/`, tests, air-gapped sharing)
  github     GitHub Actions artifacts of a repository (listing/download through the REST API;
             upload is done by actions/upload-artifact, there is no upload API usable from a script)
  nexus      Sonatype Nexus 3 raw (hosted) repository: HTTP PUT/GET + REST listing
"""

import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOLDER = "bh"
SEP = "_"
FIELDS = ("package", "holder", "variant", "tag", "platform", "version", "recipe12")      # the default order of a name
HOLDER_RE = re.compile(r"^[a-z0-9]+([.-][a-z0-9]+)*$")          # no "_" (the separator), no "--", no leading or trailing hyphen
CHUNK = 1024 * 256


class StoreError(RuntimeError):
    pass


class Unsupported(StoreError):
    pass


def holder_of(config):
    """The holder identifier of a loaded config (`config.artifacts.holder`, default "bh"), validated."""
    holder = os.environ.get("BH_HOLDER") or (((config or {}).get("config", {}).get("artifacts", {}) or {}).get("holder")) or DEFAULT_HOLDER
    holder = str(holder)
    if not HOLDER_RE.match(holder):
        raise StoreError(f"artifacts.holder '{holder}' is not valid: lowercase letters, digits, '.' and single '-' (never '_')")
    return holder


def order_of(config):
    """The order of the name fields (`config.artifacts.name_order`): a permutation of FIELDS, default FIELDS."""
    order = (((config or {}).get("config", {}).get("artifacts", {}) or {}).get("name_order")) or list(FIELDS)
    if sorted(order) != sorted(FIELDS):
        raise StoreError(f"artifacts.name_order must list each of {', '.join(FIELDS)} exactly once (got {order})")
    return tuple(order)


@dataclass
class Entry:
    package: str
    variant: str
    tag: str
    arch: str
    version: str
    recipe12: str
    holder: str = DEFAULT_HOLDER
    order: tuple = FIELDS            # how the fields are laid out in the name
    store: str = ""
    ref: str = ""                    # backend specific: artifact id, path, file
    size: int = 0
    created_at: str = ""
    expires_at: str = ""
    sha256: str = ""
    signer: str = ""                 # fingerprint that signed the sidecar, set when a fetch verified it

    def __post_init__(self):
        for label, value in (("holder", self.holder), ("package", self.package), ("variant", self.variant), ("tag", self.tag),
                             ("platform", self.arch), ("version", self.version), ("recipe", self.recipe12)):
            if SEP in value or not value:
                raise StoreError(f"artifact {label} '{value}' cannot be part of a name: it is empty or contains '{SEP}', the field separator")

    @property
    def name(self):
        fields = {"package": self.package, "holder": self.holder, "variant": self.variant, "tag": self.tag,
                  "platform": self.arch, "version": self.version, "recipe12": self.recipe12}
        return SEP.join(fields[field] for field in self.order)

    def pattern(self):
        """A glob that matches the names of this holder in this layout (for listings that can only filter by pattern)."""
        return SEP.join(self.holder if field == "holder" else "*" for field in self.order)

    def key(self):
        return (self.package, self.variant, self.tag, self.arch)

    def as_dict(self):
        return {"name": self.name, "store": self.store, "package": self.package, "variant": self.variant,
                "tag": self.tag, "arch": self.arch, "version": self.version, "recipe12": self.recipe12,
                "size": self.size, "created_at": self.created_at, "expires_at": self.expires_at}


def entry_for(unit, platform):
    """Identity of a unit; `platform` fills the arch slot of the name ("amd64", or "amd64-musl")."""
    return Entry(unit.name, unit.variant, unit.tag, platform, str(unit.variant_data["full_version"]),
                 unit.recipe_sha256[:12], holder=holder_of({"config": unit.context["config"]}),
                 order=order_of({"config": unit.context["config"]}))


def parse_name(name, store="", holder=DEFAULT_HOLDER, order=FIELDS, **extra):
    """Inverse of Entry.name; None when the name is not one of `holder`'s (in the layout `order`)."""
    parts = name.split(SEP)
    if len(parts) != len(order) or not all(parts):
        return None
    fields = dict(zip(order, parts))
    if fields["holder"] != holder:
        return None
    return Entry(fields["package"], fields["variant"], fields["tag"], fields["platform"], fields["version"], fields["recipe12"],
                 holder=holder, order=tuple(order), store=store, **extra)


def meta_bytes(meta):
    """The exact bytes of an artifact sidecar: what is written, signed and verified."""
    return (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode()


def signature_sibling(tarball):
    """Where the detached signature of an artifact's sidecar lives next to its tarball (may not exist)."""
    return Path(str(tarball)[:-len(".tar.gz")] + ".json.asc")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Store:
    kind = "abstract"
    label = ""
    policy = None                    # signature.ArtifactPolicy, set by open_store from config.artifacts.signing
    holder = DEFAULT_HOLDER          # set by open_store from config.artifacts.holder
    order = FIELDS                   # ... and from config.artifacts.name_order

    def inventory(self):
        raise NotImplementedError

    def fetch(self, entry, dest_dir):
        """Download the artifact tarball into dest_dir; returns (tarball path, meta dict)."""
        raise NotImplementedError

    def publish(self, entry, tarball, meta):
        raise Unsupported(f"{self.kind} store cannot publish from the command line")

    def find(self, entry):
        """The stored entry with the same identity (including recipe), or None."""
        for candidate in self.inventory():
            if candidate.name == entry.name:
                return candidate
        return None

    def _verified(self, entry, tarball, meta, meta_file=None, sig_file=None):
        expected = meta.get("sha256") or entry.sha256
        if expected and sha256_file(tarball) != expected:
            raise StoreError(f"{entry.name}: tarball does not match its recorded sha256")
        if self.policy is not None:
            entry.signer = self.policy.check(entry.name, meta_file, sig_file)
        return tarball, meta


# ----------------------------------------------------------------------------- dir

class DirStore(Store):
    kind = "dir"

    def __init__(self, path):
        self.path = Path(path)
        self.label = f"dir:{self.path}"

    def inventory(self):
        entries = []
        if not self.path.is_dir():
            return entries
        for meta_file in sorted(self.path.glob("*.json")):
            entry = parse_name(meta_file.stem, store=self.label, holder=self.holder, order=self.order, ref=str(meta_file.with_suffix(".tar.gz")))
            tarball = Path(entry.ref) if entry else None
            if entry and tarball.exists():
                meta = json.loads(meta_file.read_text())
                entry.size, entry.sha256 = tarball.stat().st_size, meta.get("sha256", "")
                entry.created_at = meta.get("created_at", "")
                entries.append(entry)
        return entries

    def fetch(self, entry, dest_dir):
        tarball = Path(entry.ref)
        meta_file = tarball.with_suffix("").with_suffix(".json")
        sig_file = signature_sibling(tarball)
        return self._verified(entry, tarball, json.loads(meta_file.read_text()), meta_file,
                              sig_file if sig_file.exists() else None)

    def publish(self, entry, tarball, meta):
        self.path.mkdir(parents=True, exist_ok=True)
        target = self.path / f"{entry.name}.tar.gz"
        if Path(tarball).resolve() != target.resolve():
            shutil.copyfile(tarball, target)
        (self.path / f"{entry.name}.json").write_bytes(meta_bytes(meta))
        signature = signature_sibling(tarball)
        if signature.exists() and signature.resolve() != (self.path / f"{entry.name}.json.asc").resolve():
            shutil.copyfile(signature, self.path / f"{entry.name}.json.asc")
        return str(target)


# ------------------------------------------------------------------------- github

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GithubStore(Store):
    """GitHub Actions artifacts. Mind the platform limits: artifacts expire (retention is configurable up
    to 90 days) and a listing only returns the live ones, so a unit older than the retention shows as
    missing and is rebuilt - which is exactly the desired reconcile behaviour for a cache, and the reason
    a long-term store (Nexus) is supported."""

    kind = "github"

    def __init__(self, repo=None, token=None, api_url=None):
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        self.api = (api_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
        self.label = f"github:{self.repo}"
        if not self.repo:
            raise StoreError("github store: repository unknown (set GITHUB_REPOSITORY or store.repo)")

    def _request(self, url, auth=True, follow=True):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "bytehawks-distillery"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
        return opener.open(urllib.request.Request(url, headers=headers), timeout=60)

    def inventory(self):
        entries, page = {}, 1
        while True:
            url = f"{self.api}/repos/{self.repo}/actions/artifacts?per_page=100&page={page}"
            try:
                with self._request(url) as response:
                    data = json.load(response)
            except urllib.error.URLError as exc:
                raise StoreError(f"github artifacts listing failed: {exc}") from exc
            for artifact in data.get("artifacts", []):
                entry = parse_name(artifact["name"], store=self.label, holder=self.holder, order=self.order, ref=str(artifact["id"]),
                                   size=artifact.get("size_in_bytes", 0), created_at=artifact.get("created_at", ""),
                                   expires_at=artifact.get("expires_at", ""))
                if entry and not artifact.get("expired"):
                    entries.setdefault(entry.name, entry)        # API order: newest first
            if len(data.get("artifacts", [])) < 100:
                return list(entries.values())
            page += 1

    def fetch(self, entry, dest_dir):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        url = f"{self.api}/repos/{self.repo}/actions/artifacts/{entry.ref}/zip"
        try:
            try:                                        # 302 to a signed blob URL: never send the token there
                response = self._request(url, follow=False)
            except urllib.error.HTTPError as exc:
                if exc.code not in (301, 302, 303, 307, 308):
                    raise
                response = self._request(exc.headers["Location"], auth=False)
            archive = dest_dir / f"{entry.name}.zip"
            with response, open(archive, "wb") as out:
                shutil.copyfileobj(response, out)
        except urllib.error.URLError as exc:
            raise StoreError(f"github artifact download failed: {exc}") from exc

        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            tar_name = next((n for n in names if n.endswith(".tar.gz")), None)
            meta_name = next((n for n in names if n.endswith(".json")), None)
            if not tar_name or not meta_name:
                raise StoreError(f"{entry.name}: unexpected artifact layout {names}")
            tarball = dest_dir / f"{entry.name}.tar.gz"
            tarball.write_bytes(zf.read(tar_name))
            meta_file = dest_dir / f"{entry.name}.json"
            meta_file.write_bytes(zf.read(meta_name))
            sig_name = next((n for n in names if n.endswith(".json.asc")), None)
            sig_file = None
            if sig_name:
                sig_file = dest_dir / f"{entry.name}.json.asc"
                sig_file.write_bytes(zf.read(sig_name))
        archive.unlink()
        return self._verified(entry, tarball, json.loads(meta_file.read_text()), meta_file, sig_file)

    def publish(self, entry, tarball, meta):
        raise Unsupported("GitHub artifacts are uploaded by actions/upload-artifact from the --artifacts-dir "
                          "directory; there is no upload API for scripts")


# --------------------------------------------------------------------------- nexus

class NexusStore(Store):
    """Nexus 3 *raw (hosted)* repository.

    layout: <prefix>/<arch>/<package>/<variant>/<tag>/<version>-<recipe12>/<name>.tar.gz (+ .json)
    listing: GET /service/rest/v1/assets?repository=<repo> (continuationToken pagination)
    auth: HTTP basic, NEXUS_USER / NEXUS_PASSWORD (or a user token pair)
    """

    kind = "nexus"

    def __init__(self, url, repository, prefix="bytehawks", user=None, password=None):
        self.url = url.rstrip("/")
        self.repository = repository
        self.prefix = prefix.strip("/")
        self.user = user or os.environ.get("NEXUS_USER", "")
        self.password = password or os.environ.get("NEXUS_PASSWORD", "")
        self.label = f"nexus:{self.url}/{self.repository}"

    def _path(self, entry):
        return "/".join((self.prefix, entry.arch, entry.package, entry.variant, entry.tag,
                         f"{entry.version}-{entry.recipe12}", entry.name))

    def _open(self, request):
        if self.user:
            import base64
            token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            request.add_header("Authorization", f"Basic {token}")
        request.add_header("User-Agent", "bytehawks-distillery")
        return urllib.request.urlopen(request, timeout=120)

    def _asset_url(self, path):
        return f"{self.url}/repository/{self.repository}/{urllib.parse.quote(path)}"

    def inventory(self):
        entries, token = {}, None
        while True:
            query = {"repository": self.repository}
            if token:
                query["continuationToken"] = token
            url = f"{self.url}/service/rest/v1/assets?{urllib.parse.urlencode(query)}"
            try:
                with self._open(urllib.request.Request(url)) as response:
                    data = json.load(response)
            except urllib.error.URLError as exc:
                raise StoreError(f"nexus listing failed: {exc}") from exc
            for asset in data.get("items", []):
                path = asset.get("path", "")
                if not path.startswith(self.prefix + "/") or not path.endswith(".tar.gz"):
                    continue
                entry = parse_name(path.rsplit("/", 1)[-1][:-len(".tar.gz")], store=self.label, holder=self.holder, order=self.order, ref=path,
                                   sha256=(asset.get("checksum") or {}).get("sha256", ""),
                                   created_at=asset.get("lastModified", "") or "")
                if entry:
                    entries[entry.name] = entry
            token = data.get("continuationToken")
            if not token:
                return list(entries.values())

    def fetch(self, entry, dest_dir):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        tarball = dest_dir / f"{entry.name}.tar.gz"
        try:
            with self._open(urllib.request.Request(self._asset_url(entry.ref))) as response, open(tarball, "wb") as out:
                shutil.copyfileobj(response, out)
            meta_file = dest_dir / f"{entry.name}.json"
            with self._open(urllib.request.Request(self._asset_url(entry.ref[:-len(".tar.gz")] + ".json"))) as response:
                meta_file.write_bytes(response.read())
            sig_file = None
            try:
                with self._open(urllib.request.Request(self._asset_url(entry.ref[:-len(".tar.gz")] + ".json.asc"))) as response:
                    sig_file = dest_dir / f"{entry.name}.json.asc"
                    sig_file.write_bytes(response.read())
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
        except urllib.error.URLError as exc:
            raise StoreError(f"nexus download failed: {exc}") from exc
        return self._verified(entry, tarball, json.loads(meta_file.read_text()), meta_file, sig_file)

    def publish(self, entry, tarball, meta):
        base = self._path(entry)
        uploads = [(".tar.gz", Path(tarball).read_bytes(), "application/gzip"),
                   (".json", meta_bytes(meta), "application/json")]
        signature = signature_sibling(tarball)
        if signature.exists():
            uploads.append((".json.asc", signature.read_bytes(), "application/pgp-signature"))
        for suffix, payload, ctype in uploads:
            request = urllib.request.Request(self._asset_url(base + suffix), data=payload, method="PUT",
                                             headers={"Content-Type": ctype})
            try:
                self._open(request).close()
            except urllib.error.URLError as exc:
                raise StoreError(f"nexus upload of {entry.name}{suffix} failed: {exc}") from exc
        return self._asset_url(base + ".tar.gz")


# ------------------------------------------------------------------------- factory

def open_store(spec, config=None):
    """Open a store and attach the artifact signature policy of the config (if any)."""
    from .signature import ArtifactPolicy

    store = _open_store(spec, config)
    store.policy = ArtifactPolicy.from_config(config)
    store.holder = holder_of(config)
    store.order = order_of(config)
    return store


def _open_store(spec, config=None):
    """spec: 'dir:PATH' | 'github' | 'github:owner/repo' | 'nexus' | a name from config.artifacts.stores."""
    stores = ((config or {}).get("config", {}).get("artifacts", {}) or {}).get("stores", {}) or {}

    if spec.startswith("dir:"):
        return DirStore(spec[4:])

    definition = stores.get(spec)
    if definition is None:
        kind, _, rest = spec.partition(":")
        definition = {"type": kind, **({"repo": rest} if kind == "github" and rest else {})}
    kind = definition.get("type")

    if kind == "dir":
        return DirStore(definition["path"])
    if kind == "github":
        return GithubStore(definition.get("repo"), api_url=definition.get("api_url"))
    if kind == "nexus":
        url = os.environ.get("NEXUS_URL") or definition.get("url")
        if not url:
            raise StoreError("nexus store: url missing (config artifacts.stores.<name>.url or NEXUS_URL)")
        return NexusStore(url, definition.get("repository", "bytehawks"), definition.get("prefix", "bytehawks"))
    raise StoreError(f"unknown store '{spec}'")


class MultiStore:
    """Ordered stores: the first one that has the entry wins (e.g. the run's own dist/, then github, then nexus)."""

    def __init__(self, stores):
        self.stores = stores

    def inventory(self):
        seen, merged = set(), []
        for store in self.stores:
            for entry in store.inventory():
                if entry.name not in seen:
                    seen.add(entry.name)
                    merged.append(entry)
        return merged

    def owner(self, entry):
        for store in self.stores:
            if store.label == entry.store:
                return store
        raise StoreError(f"no store for entry {entry.name}")
