"""OCI image of a built unit: one layer per unit (dependencies first), the runtime contract as the image configuration.

The result is an *OCI archive* (`oci-layout`, `index.json`, `blobs/sha256/*` in a tar) that `docker load`, `podman load` and
`skopeo copy oci-archive:` read. Nothing here talks to a daemon or a registry.

Layers are cut from the installed tree, from the paths the build recorded in `files.sha256` (plus the bundle), after checking
that tree against it. Everything is deterministic - sorted, zero timestamps (the build time for `created`), no host owners - so the
same build gives the same image digest. The files keep their build paths: the image holds the tree under `/opt/bytehawks`
(or the `--root` it was built under), because paths are baked into the binaries.

Two ways to start the service:

  shaker  ENTRYPOINT is `shaker run <pkg>`: parameters, templates, hooks and persistence work as on any host. The image needs bash
          (a base layout with it, see `--base`) and a copy of Shaker (`--shaker-dir`).
  direct  ENTRYPOINT is the package's own command, `${BH_X}` expanded from the parameter defaults and templates rendered with them
          at build time. No Shaker, no shell: with a static unit the image can be `FROM scratch`. Packages that need hooks or
          persistence are refused.
"""

import calendar
import gzip
import hashlib
import io
import json
import re
import shutil
import stat
import tarfile
import tempfile
import time
from pathlib import Path

from . import __version__, package as artifact
from .fetch import sha256_file

OCI_LAYOUT = {"imageLayoutVersion": "1.0.0"}
MEDIA_INDEX = "application/vnd.oci.image.index.v1+json"
MEDIA_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
MEDIA_CONFIG = "application/vnd.oci.image.config.v1+json"
MEDIA_LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
ARCH = {"amd64": "amd64", "arm64": "arm64"}


class ImageError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- the bundle, as a consumer sees it

def parse_spec(path):
    """runtime.spec -> {'user': (name, uid, gid, home), 'dirs': [...], 'templates': [...], 'hooks': [...], 'run': [...], ...}."""
    spec = {"user": None, "dirs": [], "templates": [], "hooks": [], "persist": [], "run": [], "stopsignal": "", "pidfile": ""}
    for line in Path(path).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        kind, *fields = line.split("|")
        if kind == "user":
            spec["user"] = (fields[0], int(fields[1]), int(fields[2] or 0), fields[3] if len(fields) > 3 else "")
        elif kind == "dir":
            spec["dirs"].append({"path": fields[0], "mode": fields[1], "owner": fields[2] if len(fields) > 2 else ""})
        elif kind == "template":
            spec["templates"].append({"src": fields[0], "dest": fields[1], "mode": fields[2] if len(fields) > 2 else "0644"})
        elif kind == "hook":
            spec["hooks"].append({"stage": fields[0], "directory": fields[1]})
        elif kind == "persist":
            spec["persist"].append({"path": fields[0], "volume": fields[1]})
        elif kind == "run":
            spec["run"] = fields
        elif kind == "stopsignal":
            spec["stopsignal"] = fields[0]
        elif kind == "pidfile":
            spec["pidfile"] = fields[0]
    return spec


def parse_map(path):
    """rosetta.map -> {env name: {'default': str, 'type': str, 'flags': str, 'native_env': str}}."""
    result = {}
    for line in Path(path).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        f = (line.split("|") + [""] * 9)[:9]
        result[f[0]] = {"type": f[1], "default": f[2], "flags": f[3], "native_env": f[7]}
    return result


def parse_env(path):
    return dict(l.split("=", 1) for l in Path(path).read_text().splitlines() if "=" in l)


VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
TPL = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\s*\}\}")
COND = re.compile(r"^\{\{\?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def values(parameters, manifest_env):
    """The environment a template or a command line sees: the parameter defaults and BH_PKG_*."""
    env = {name: info["default"] for name, info in parameters.items() if info["default"] != ""}
    env.update(manifest_env)
    return env


def expand(text, env, where):
    def one(match):
        name, default = match.group(1), match.group(2)
        if env.get(name):
            return env[name]
        if default is not None:
            return default
        raise ImageError(f"{where}: ${{{name}}} has no default (direct mode needs every parameter it uses to have one)")
    return VAR.sub(one, text)


def render(text, env, where):
    """Shaker's template syntax: {{ NAME }}, {{ NAME:-default }}, and a line starting {{? NAME }} only when NAME is set."""
    out = []
    for line in text.splitlines(keepends=True):
        cond = COND.match(line)
        if cond:
            if not env.get(cond.group(1)):
                continue
            line = line[cond.end():]

        def one(match):
            name, default = match.group(1), match.group(2)
            if env.get(name):
                return env[name]
            if default is not None:
                return default
            raise ImageError(f"{where}: {{{{ {name} }}}} is not set and has no default")
        out.append(TPL.sub(one, line))
    return "".join(out)


# ------------------------------------------------------------------------------------------------------------- modes and owners

CLAUSE = re.compile(r"^([ugoa]*)\+([rwxX]+)$")
SHIFT = {"u": 6, "g": 3, "o": 0}
BITS = {"r": 4, "w": 2, "x": 1}


def apply_mode(current, spec, is_dir):
    """New mode from a numeric ('0750') or symbolic ('g+rwX,o+rX') spec; X = execute for directories and for anything already executable."""
    spec = spec.strip()
    if re.fullmatch(r"[0-7]{3,4}", spec):
        return int(spec, 8)
    mode = current
    for clause in spec.split(","):
        match = CLAUSE.match(clause)
        if not match:
            raise ImageError(f"unsupported mode '{spec}' (use octal or [ugoa]+[rwxX])")
        who = match.group(1) or "a"
        for letter in ("ugo" if "a" in who else who):
            for perm in match.group(2):
                if perm == "X" and not (is_dir or current & 0o111):
                    continue
                mode |= BITS["x" if perm == "X" else perm] << SHIFT[letter]
    return mode


class Rules:
    """`dir` records of runtime.spec applied to the tree: owner recursively, a symbolic mode recursively, a numeric mode to the directory only."""

    def __init__(self, dirs):
        self.dirs = []
        for d in dirs:
            uid, _, gid = (d["owner"] or "").partition(":")
            self.dirs.append((d["path"].rstrip("/"), d["mode"], int(uid) if uid else None, int(gid) if gid else None))

    def apply(self, abs_path, mode, is_dir):
        uid, gid = 0, 0
        for path, spec, owner_uid, owner_gid in self.dirs:
            exact = abs_path == path
            inside = abs_path.startswith(path + "/")
            if not (exact or inside):
                continue
            if owner_uid is not None:
                uid = owner_uid
            if owner_gid is not None:
                gid = owner_gid
            numeric = re.fullmatch(r"[0-7]{3,4}", spec) is not None
            if exact or not numeric:
                mode = apply_mode(mode, spec, is_dir)
        return mode, uid, gid


# ------------------------------------------------------------------------------------------------------------------------ layers

def tree_paths(unit):
    """Absolute paths of everything the unit installed (files.sha256 + the bundle), directories included."""
    root = Path(unit.context["config"]["root"])
    paths = {}
    listing = unit.bundle_dir / "files.sha256"
    for line in listing.read_text().splitlines():
        _, _, rel = line.partition("  ")
        paths[str(root / rel.rstrip("/"))] = rel.endswith("/")
    paths[str(listing)] = False
    for extra in unit.bundle_dir.rglob("*"):
        paths[str(extra)] = extra.is_dir() and not extra.is_symlink()
    return paths


def _entry(archive, name, source, mode, uid, gid, is_dir, extra=None, mtime=0):
    info = tarfile.TarInfo(name)
    info.mtime, info.uid, info.gid, info.uname, info.gname = mtime, uid, gid, "", ""
    if is_dir:
        info.type, info.mode = tarfile.DIRTYPE, mode
        archive.addfile(info)
    elif extra is not None:                                    # content given as bytes
        info.size, info.mode = len(extra), mode
        archive.addfile(info, io.BytesIO(extra))
    elif source.is_symlink():
        info.type, info.linkname, info.mode = tarfile.SYMTYPE, str(source.readlink()), 0o777
        archive.addfile(info)
    else:
        info.size, info.mode = source.stat().st_size, mode
        with open(source, "rb") as handle:
            archive.addfile(info, handle)


def write_layer(work, label, entries, rules, mtime=0, base=None):
    """entries: [(path in the image, is_dir, content bytes or None, mode or None, source path or None)]; without a source the
    file is read from the same path on this host. Returns (blob path, digest, diff_id, size)."""
    work = Path(work)
    plain = work / f"{label}.tar"
    members = {path: (is_dir, extra, mode, source) for path, is_dir, extra, mode, source in entries}
    # every ancestor directory below the filesystem root, so the layer can be applied on an empty base; one the base already has
    # keeps the base's mode and owner (a layer must not turn /tmp into 0755)
    inherited = {}
    for path in list(members):
        parent = Path(path).parent
        while str(parent) != "/":
            if str(parent) not in members:
                members[str(parent)] = (True, None, 0o755, None)
                existing = base.directory(str(parent)) if base else None
                if existing:
                    inherited[str(parent)] = existing
            parent = parent.parent
    with tarfile.open(plain, "w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(members):
            is_dir, extra, mode, source = members[path]
            src = Path(source or path)
            if mode is None:
                mode = stat.S_IMODE(src.lstat().st_mode) if (extra is None and (src.exists() or src.is_symlink())) else 0o644
            explicit = mode
            if path in inherited:
                mode, uid, gid = inherited[path][:3]
            else:
                mode, uid, gid = rules.apply(path, mode, is_dir)
            if extra is not None:                          # a rendered template: its own mode, as `shaker init` sets it after `install`
                mode = explicit
            _entry(archive, path.lstrip("/"), src, mode, uid, gid, is_dir, extra, mtime)
    diff_id = sha256_file(plain)
    blob = work / f"{label}.tar.gz"
    with open(plain, "rb") as src, open(blob, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as gz:
            shutil.copyfileobj(src, gz, 1 << 20)
    plain.unlink()
    return blob, sha256_file(blob), diff_id, blob.stat().st_size


# --------------------------------------------------------------------------------------------------------------------- the image

def read_base(layout):
    """(config dict, [layer descriptors]) of a single-image OCI layout directory."""
    layout = Path(layout)
    index = json.loads((layout / "index.json").read_text())
    manifests = index["manifests"]
    if len(manifests) != 1:
        raise ImageError(f"{layout}: expected one image in the base layout, found {len(manifests)} (extract a single platform)")
    digest = manifests[0]["digest"].split(":", 1)[1]
    manifest = json.loads((layout / "blobs" / "sha256" / digest).read_text())
    if manifest.get("mediaType", MEDIA_MANIFEST) != MEDIA_MANIFEST:
        raise ImageError(f"{layout}: the base is not an OCI image manifest ({manifest.get('mediaType')})")
    config = json.loads((layout / "blobs" / "sha256" / manifest["config"]["digest"].split(":", 1)[1]).read_text())
    return config, manifest["layers"]


class BaseTree:
    """What the base image already contains: directories keep their mode and owner, /etc/passwd and /etc/group are extended."""

    def __init__(self, layout, descriptors):
        self.layout, self.members, self.layers = Path(layout) if layout else None, {}, []
        for layer in descriptors:
            blob = self.layout / "blobs" / "sha256" / layer["digest"].split(":", 1)[1]
            self.layers.append(blob)
            with tarfile.open(blob, "r:*") as archive:
                for member in archive.getmembers():
                    name = "/" + member.name.lstrip("./").rstrip("/")
                    self.members[name] = (member.mode, member.uid, member.gid, member.isdir())

    def directory(self, path):
        info = self.members.get(path)
        return info if info and info[3] else None

    def read(self, path):
        """The newest content of a regular file of the base, or None."""
        wanted = path.lstrip("/")
        for blob in reversed(self.layers):
            with tarfile.open(blob, "r:*") as archive:
                for member in archive.getmembers():
                    if member.name.lstrip("./") == wanted and member.isfile():
                        return archive.extractfile(member).read().decode(errors="replace")
        return None


def account_entries(spec, base):
    """/etc/passwd and /etc/group of the base with the service account added (nothing if it is already there)."""
    if not spec["user"]:
        return []
    name, uid, gid, home = spec["user"]
    entries = []
    passwd = (base.read("/etc/passwd") if base else None) or ""
    if not any(l.split(":")[0] == name or (len(l.split(":")) > 2 and l.split(":")[2] == str(uid)) for l in passwd.splitlines()):
        passwd += ("" if passwd.endswith("\n") or not passwd else "\n") + f"{name}:x:{uid}:{gid}:{name} service:{home or '/'}:/sbin/nologin\n"
        entries.append(("/etc/passwd", False, passwd.encode(), 0o644, None))
    group = (base.read("/etc/group") if base else None) or ""
    if gid != 0 and not any(len(l.split(":")) > 2 and l.split(":")[2] == str(gid) for l in group.splitlines()):
        group += ("" if group.endswith("\n") or not group else "\n") + f"{name}:x:{gid}:\n"
        entries.append(("/etc/group", False, group.encode(), 0o644, None))
    return entries


def build(units, out, *, tag, entrypoint="shaker", shaker_dir=None, base=None, log=print):
    """Write the OCI archive `out` for units (dependencies first, the service last). Returns a dict describing it."""
    main = units[-1]
    root = Path(main.context["config"]["root"])
    spec = parse_spec(main.bundle_dir / "runtime.spec")
    if not spec["run"]:
        raise ImageError(f"{main.name} has no run command: only services can be an image")
    parameters = parse_map(main.bundle_dir / "rosetta.map")
    env_pkg = parse_env(main.bundle_dir / "manifest.env")
    manifest = json.loads((main.bundle_dir / "manifest.json").read_text())
    rules = Rules(spec["dirs"] + [{"path": str(root / "var"), "mode": "g+rwX", "owner": f"0:{spec['user'][2] if spec['user'] else 0}"}]
                  if entrypoint == "shaker" else spec["dirs"])
    gid = spec["user"][2] if spec["user"] else 0

    base_config, base_layers = (read_base(base) if base else ({}, []))
    base_tree = BaseTree(base, base_layers) if base else None
    built_at = manifest.get("built_at")
    mtime = calendar.timegm(time.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ")) if built_at else 0
    work = Path(tempfile.mkdtemp(prefix="bh-image-"))
    try:
        layers = []                                     # (blob, digest, diff_id, size, created_by)
        for unit in units:
            entries = [(p, d, None, None, None) for p, d in tree_paths(unit).items()]
            if unit is main:
                entries += _service_entries(main, spec, parameters, env_pkg, entrypoint, root, gid)
                entries += account_entries(spec, base_tree)
            label = f"{len(layers):02d}-{unit.name}"
            layers.append(write_layer(work, label, entries, rules, mtime, base_tree) + (f"distillery {unit.name} {unit.variant_data['full_version']}",))
            log(f"[layer] {unit.name} {unit.variant_data['full_version']}: {layers[-1][3]} bytes")
        if entrypoint == "shaker":
            if not shaker_dir:
                raise ImageError("--entrypoint shaker needs --shaker-dir (a Shaker checkout to copy into the image)")
            shaker_target = root / "core" / "shaker"
            entries = [(str(shaker_target / p.relative_to(shaker_dir)), p.is_dir() and not p.is_symlink(), None, None, p)
                       for p in sorted(Path(shaker_dir).rglob("*")) if ".git" not in p.relative_to(shaker_dir).parts]
            var = [(str(root / "var" / sub).rstrip("/"), True, None, 0o775, None) for sub in ("", "lib", "lib/shaker", "log", "run")]
            layers.append(write_layer(work, "90-shaker", entries + var, rules, mtime, base_tree)
                          + ("shaker",))
            log(f"[layer] shaker: {layers[-1][3]} bytes")

        env = list((base_config.get("config") or {}).get("Env") or [])
        env_map = {e.split("=", 1)[0]: e for e in env}
        if entrypoint == "shaker":
            env_map["BH_ROOT"] = f"BH_ROOT={root}"
            env_map["PATH"] = "PATH=" + ":".join([str(root / "core" / "shaker" / "bin"), env_map.get("PATH", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin").split("=", 1)[1]])
        values_for_env = values(parameters, env_pkg)
        if entrypoint == "direct":
            for name, info in parameters.items():
                if info["native_env"] and values_for_env.get(name):
                    env_map[info["native_env"]] = f"{info['native_env']}={values_for_env[name]}"
        config_env = list(env_map.values())

        if entrypoint == "shaker":
            ep = [str(root / "core" / "shaker" / "bin" / "shaker"), "run", main.name]
        else:
            ep = [expand(a, values_for_env, "run") for a in spec["run"]]
        created = manifest.get("built_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(0))
        labels = {
            "org.opencontainers.image.title": main.name,
            "org.opencontainers.image.version": str(main.variant_data["full_version"]),
            "org.opencontainers.image.created": created,
            "org.opencontainers.image.source": manifest.get("source", ""),
            "org.opencontainers.image.revision": manifest.get("source_commit", "") or manifest.get("source_sha256", ""),
            "org.opencontainers.image.licenses": main.package.get("license", ""),
            "org.opencontainers.image.vendor": "ByteHawks",
            "io.bytehawks.recipe": main.recipe_sha256,
            "io.bytehawks.platform": main.context["platform"],
            "io.bytehawks.linkage": main.context["linkage"],
            "io.bytehawks.sbom": str(main.bundle_dir / "sbom.cdx.json"),
            "io.bytehawks.generator": f"distillery/{__version__}",
        }
        if (main.package.get("website") or main.context.get("website")):
            labels["org.opencontainers.image.url"] = main.context.get("website", "")
        container = dict(base_config.get("config") or {})
        container.update({"Entrypoint": ep, "Cmd": [], "Env": config_env, "WorkingDir": str(main.prefix),
                          "Labels": {**(container.get("Labels") or {}), **{k: v for k, v in labels.items() if v}}})
        if spec["user"]:
            container["User"] = f"{spec['user'][1]}:{spec['user'][2]}"
        if spec["stopsignal"]:
            container["StopSignal"] = "SIG" + spec["stopsignal"]
        history = list(base_config.get("history") or []) + [{"created": created, "created_by": layer[4]} for layer in layers]
        config = {
            "created": created,
            "architecture": ARCH.get(main.context["arch"], main.context["arch"]),
            "os": "linux",
            "config": container,
            "rootfs": {"type": "layers", "diff_ids": list((base_config.get("rootfs") or {}).get("diff_ids") or [])
                       + ["sha256:" + layer[2] for layer in layers]},
            "history": history,
        }
        config_bytes = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        config_digest = hashlib.sha256(config_bytes).hexdigest()
        descriptors = list(base_layers) + [{"mediaType": MEDIA_LAYER, "digest": "sha256:" + l[1], "size": l[3]}
                                           for l in layers]
        image_manifest = {"schemaVersion": 2, "mediaType": MEDIA_MANIFEST,
                          "config": {"mediaType": MEDIA_CONFIG, "digest": "sha256:" + config_digest, "size": len(config_bytes)},
                          "layers": descriptors}
        manifest_bytes = json.dumps(image_manifest, sort_keys=True, separators=(",", ":")).encode()
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        index = {"schemaVersion": 2, "mediaType": MEDIA_INDEX,
                 "manifests": [{"mediaType": MEDIA_MANIFEST, "digest": "sha256:" + manifest_digest, "size": len(manifest_bytes),
                                "annotations": {"org.opencontainers.image.ref.name": tag}}]}
        _write_archive(Path(out), index, manifest_digest, manifest_bytes, config_digest, config_bytes, layers, base, base_layers, descriptors)
        return {"out": str(out), "tag": tag, "manifest": "sha256:" + manifest_digest, "layers": len(descriptors),
                "entrypoint": ep, "user": container.get("User", ""), "size": Path(out).stat().st_size}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _service_entries(main, spec, parameters, env_pkg, entrypoint, root, gid):
    """Extra paths of the service layer: the directories of the contract and, in direct mode, the rendered templates."""
    entries = [(d["path"], True, None, 0o755, None) for d in spec["dirs"]]
    if entrypoint != "direct":
        return entries
    if spec["hooks"] and any(Path(h["directory"]).is_dir() and any(Path(h["directory"]).iterdir()) for h in spec["hooks"]):
        raise ImageError(f"{main.name} ships hooks: they need Shaker (--entrypoint shaker)")
    if spec["persist"]:
        raise ImageError(f"{main.name} declares persistence: it needs Shaker (--entrypoint shaker) or a volume of your own")
    env = values(parameters, env_pkg)
    for t in spec["templates"]:
        source = main.bundle_dir / "assets" / t["src"]
        text = render(source.read_text(), env, t["src"])
        entries.append((t["dest"], False, text.encode(), int(t["mode"], 8), None))
    return entries


def _write_archive(out, index, manifest_digest, manifest_bytes, config_digest, config_bytes, layers, base, base_layers, manifest_layers):
    out.parent.mkdir(parents=True, exist_ok=True)

    def add_bytes(archive, name, data):
        info = tarfile.TarInfo(name)
        info.size, info.mtime, info.mode = len(data), 0, 0o644
        archive.addfile(info, io.BytesIO(data))

    with tarfile.open(out, "w", format=tarfile.PAX_FORMAT) as archive:
        add_bytes(archive, "oci-layout", json.dumps(OCI_LAYOUT).encode())
        add_bytes(archive, "index.json", json.dumps(index, sort_keys=True, separators=(",", ":")).encode())
        # docker-save compatibility: `docker load` with the classic image store reads this list, the OCI layout is ignored there
        names = [f"blobs/sha256/{layer['digest'].split(':', 1)[1]}" for layer in manifest_layers]
        tag = index["manifests"][0]["annotations"]["org.opencontainers.image.ref.name"]
        add_bytes(archive, "manifest.json", json.dumps([{"Config": f"blobs/sha256/{config_digest}", "RepoTags": [tag], "Layers": names}],
                                                      separators=(",", ":")).encode())
        blobs = {manifest_digest: manifest_bytes, config_digest: config_bytes}
        for name in sorted(blobs):
            add_bytes(archive, f"blobs/sha256/{name}", blobs[name])
        if base:
            for layer in base_layers:
                digest = layer["digest"].split(":", 1)[1]
                archive.add(str(Path(base) / "blobs" / "sha256" / digest), arcname=f"blobs/sha256/{digest}",
                            filter=_zero)
        for blob, digest, _, size, _ in layers:
            info = tarfile.TarInfo(f"blobs/sha256/{digest}")
            info.size, info.mtime, info.mode = size, 0, 0o644
            with open(blob, "rb") as handle:
                archive.addfile(info, handle)


def _zero(info):
    info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
    return info


def check_ready(units):
    """Problems that make an image of `units` meaningless: not built, or modified since."""
    problems = []
    for unit in units:
        from . import bundle
        if not bundle.is_up_to_date(unit):
            problems.append(f"{unit.name}: not built for this recipe (build or restore it first)")
        elif artifact.verify_files(unit):
            problems.append(f"{unit.name}: installed files differ from the build ({len(artifact.verify_files(unit))} problem(s)); run verify")
    return problems
