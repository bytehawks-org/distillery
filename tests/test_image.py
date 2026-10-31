import hashlib
import io
import json
import tarfile
import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery import image, sbom
from bhdistillery.build import build_unit

QUIET = lambda message: None
RUNTIME = {
    "user": {"name": "demo", "uid": 1010, "gid": 0, "home": "{{ prefix }}"},
    "dirs": [{"path": "{{ prefix }}/conf", "mode": "g+rwX", "owner": "1010:0"},
             {"path": "{{ prefix }}/run", "mode": "0750", "owner": "1010:0"}],
    "templates": [{"src": "demo.conf.tmpl", "dest": "{{ prefix }}/conf/demo.conf", "mode": "0640"}],
    "run": {"command": ["{{ prefix }}/bin/demo", "--port", "${BH_DEMO_PORT}", "-c", "{{ prefix }}/conf/demo.conf"], "stop_signal": "QUIT"},
    "env": [{"name": "BH_DEMO_PORT", "type": "port", "default": 8080},
            {"name": "BH_DEMO_NAME", "type": "string", "default": "web"},
            {"name": "BH_DEMO_TOKEN", "type": "secret"}],
}
TEMPLATE = "port={{ BH_DEMO_PORT }}\nname={{ BH_DEMO_NAME:-x }}\n{{? BH_DEMO_TOKEN }}token={{ BH_DEMO_TOKEN }}\nprefix={{ BH_PKG_PREFIX }}\n"


def built(t, runtime=None, extra_assets=None, **extra):
    digest = make_tarball(t / "d.tgz", {"README": "x"}, top="demo-1.0")
    url = (t / "d.tgz").as_uri()
    (t / "assets" / "demo").mkdir(parents=True)
    (t / "assets" / "demo" / "demo.conf.tmpl").write_text(TEMPLATE)
    pkgs = {"lib": package(url, digest, tags=["library"], license="Zlib",
                           commands={"install": "mkdir -p {{ prefix }}/lib && echo l > {{ prefix }}/lib/libx.a"}),
            "demo": package(url, digest, depends_on=["lib"], license="Apache-2.0", runtime=runtime or RUNTIME,
                            commands={"install": "mkdir -p {{ prefix }}/bin {{ prefix }}/etc && printf '#!/bin/sh\\n' > {{ prefix }}/bin/demo && "
                                                 "chmod 755 {{ prefix }}/bin/demo && ln -s demo {{ prefix }}/bin/alias"}, **extra)}
    cat = catalog(pkgs, t / "root")
    unit = cat.unit("demo")
    cat.plan(unit)
    for item in sbom.closure(unit) + [unit]:
        build_unit(item, t / "assets", log=QUIET)
    return sbom.closure(unit) + [unit]


def read_archive(path):
    """{name: bytes} of an OCI archive."""
    with tarfile.open(path) as archive:
        return {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}


class Members(dict):
    """name -> TarInfo of a layer, with the content of regular files read while the archive was open."""

    def text(self, name):
        return self.content[name].decode()


def layer_members(blob):
    result = Members()
    result.content = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        for member in archive.getmembers():
            result[member.name] = member
            if member.isfile():
                result.content[member.name] = archive.extractfile(member).read()
    return result, result


class ModeTests(unittest.TestCase):
    def test_symbolic_and_numeric_modes(self):
        self.assertEqual(image.apply_mode(0o755, "g+rwX", True), 0o775)
        self.assertEqual(image.apply_mode(0o644, "g+rwX", False), 0o664)
        self.assertEqual(image.apply_mode(0o755, "g+rwX", False), 0o775)        # X keeps execute for what already runs
        self.assertEqual(image.apply_mode(0o700, "0750", True), 0o750)
        self.assertEqual(image.apply_mode(0o600, "u+x,o+r", False), 0o704)
        with self.assertRaises(image.ImageError):
            image.apply_mode(0o755, "g=rw", True)

    def test_rules_apply_owner_recursively_and_numeric_mode_to_the_directory_only(self):
        rules = image.Rules([{"path": "/r/conf", "mode": "g+rwX", "owner": "1010:0"}, {"path": "/r/run", "mode": "0750", "owner": "1010:5"}])
        self.assertEqual(rules.apply("/r/conf", 0o755, True), (0o775, 1010, 0))
        self.assertEqual(rules.apply("/r/conf/a.cfg", 0o644, False), (0o664, 1010, 0))
        self.assertEqual(rules.apply("/r/run", 0o755, True), (0o750, 1010, 5))
        self.assertEqual(rules.apply("/r/run/pid", 0o644, False), (0o644, 1010, 5))
        self.assertEqual(rules.apply("/r/bin/x", 0o755, False), (0o755, 0, 0))


class TemplateTests(unittest.TestCase):
    def test_expand_and_render_follow_shakers_syntax(self):
        env = {"A": "1", "E": ""}
        self.assertEqual(image.expand("--p=${A} ${B:-dflt} ${E:-e}", env, "t"), "--p=1 dflt e")
        with self.assertRaises(image.ImageError):
            image.expand("${NOPE}", env, "t")
        self.assertEqual(image.render("a={{ A }}\n{{? B }}b={{ B }}\n{{? A }}c\nd={{ D:-x }}\n", env, "t"), "a=1\nc\nd=x\n")
        with self.assertRaises(image.ImageError):
            image.render("{{ MISSING }}", env, "t")


class ImageTests(unittest.TestCase):
    def build(self, t, units, **kw):
        out = t / "out" / "img.tar"
        kw.setdefault("entrypoint", "direct")
        result = image.build(units, out, tag="bytehawks/demo:1.0", log=QUIET, **kw)
        return result, read_archive(out)

    def test_oci_layout_is_consistent(self):
        with TempDir() as t:
            units = built(t)
            result, files = self.build(t, units)
            self.assertEqual(json.loads(files["oci-layout"]), {"imageLayoutVersion": "1.0.0"})
            index = json.loads(files["index.json"])
            self.assertEqual(index["manifests"][0]["annotations"]["org.opencontainers.image.ref.name"], "bytehawks/demo:1.0")
            blob = lambda d: files["blobs/sha256/" + d.split(":", 1)[1]]
            mdesc = index["manifests"][0]
            self.assertEqual(hashlib.sha256(blob(mdesc["digest"])).hexdigest(), mdesc["digest"].split(":")[1])
            self.assertEqual(len(blob(mdesc["digest"])), mdesc["size"])
            manifest = json.loads(blob(mdesc["digest"]))
            config = json.loads(blob(manifest["config"]["digest"]))
            self.assertEqual(hashlib.sha256(blob(manifest["config"]["digest"])).hexdigest(), manifest["config"]["digest"].split(":")[1])
            self.assertEqual(len(manifest["layers"]), len(config["rootfs"]["diff_ids"]), 2)
            import gzip
            for layer, diff_id in zip(manifest["layers"], config["rootfs"]["diff_ids"]):
                data = blob(layer["digest"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), layer["digest"].split(":")[1])
                self.assertEqual(len(data), layer["size"])
                self.assertEqual("sha256:" + hashlib.sha256(gzip.decompress(data)).hexdigest(), diff_id)
            self.assertEqual((config["os"], config["architecture"]), ("linux", units[-1].context["arch"]))

    def test_configuration_comes_from_the_contract(self):
        with TempDir() as t:
            units = built(t)
            _, files = self.build(t, units)
            manifest = json.loads(files["blobs/sha256/" + json.loads(files["index.json"])["manifests"][0]["digest"].split(":")[1]])
            config = json.loads(files["blobs/sha256/" + manifest["config"]["digest"].split(":")[1]])["config"]
            prefix = units[-1].prefix
            self.assertEqual(config["Entrypoint"], [f"{prefix}/bin/demo", "--port", "8080", "-c", f"{prefix}/conf/demo.conf"])
            self.assertEqual((config["User"], config["StopSignal"], config["WorkingDir"]), ("1010:0", "SIGQUIT", prefix))
            self.assertEqual(config["Labels"]["org.opencontainers.image.title"], "demo")
            self.assertEqual(config["Labels"]["io.bytehawks.recipe"], units[-1].recipe_sha256)
            self.assertEqual(config["Labels"]["org.opencontainers.image.licenses"], "Apache-2.0")
            self.assertTrue(config["Labels"]["io.bytehawks.sbom"].endswith("sbom.cdx.json"))

    def test_layers_hold_the_tree_with_contract_owners_and_the_rendered_template(self):
        with TempDir() as t:
            units = built(t)
            _, files = self.build(t, units)
            manifest = json.loads(files["blobs/sha256/" + json.loads(files["index.json"])["manifests"][0]["digest"].split(":")[1]])
            lib, _ = layer_members(files["blobs/sha256/" + manifest["layers"][0]["digest"].split(":")[1]])
            svc, archive = layer_members(files["blobs/sha256/" + manifest["layers"][1]["digest"].split(":")[1]])
            root = units[-1].context["config"]["root"].lstrip("/")
            self.assertIn(f"{root}/deps/lib/libx.a", lib)
            prefix = units[-1].prefix.lstrip("/")
            self.assertIn(f"{prefix}/bin/demo", svc)
            self.assertEqual(svc[f"{prefix}/bin/demo"].mode, 0o755)
            self.assertEqual(svc[f"{prefix}/bin/alias"].issym(), True)
            self.assertEqual((svc[f"{prefix}/conf"].uid, svc[f"{prefix}/conf"].gid, svc[f"{prefix}/conf"].mode), (1010, 0, 0o775))
            self.assertEqual(svc[f"{prefix}/run"].mode, 0o750)
            self.assertIn(f"{prefix}/.bytehawks/sbom.cdx.json", svc)
            rendered = archive.text(f"{prefix}/conf/demo.conf")
            self.assertIn("port=8080\nname=web\n", rendered)
            self.assertNotIn("token", rendered)                                   # optional line, secret without default
            self.assertIn(f"prefix={units[-1].prefix}", rendered)
            self.assertEqual(svc[f"{prefix}/conf/demo.conf"].mode, 0o640)
            self.assertEqual(svc[f"{prefix}/conf/demo.conf"].uid, 1010)

    def test_same_inputs_same_archive(self):
        with TempDir() as t:
            units = built(t)
            first = self.build(t, units)[1]
            second = self.build(t, units)[1]
            self.assertEqual(first.keys(), second.keys())
            self.assertEqual(first["index.json"], second["index.json"])

    def test_shaker_mode_adds_a_shaker_layer_and_the_entrypoint(self):
        with TempDir() as t:
            units = built(t)
            shaker = t / "shaker"
            (shaker / "bin").mkdir(parents=True)
            (shaker / "bin" / "shaker").write_text("#!/bin/bash\n")
            (shaker / "bin" / "shaker").chmod(0o755)
            _, files = self.build(t, units, entrypoint="shaker", shaker_dir=shaker)
            manifest = json.loads(files["blobs/sha256/" + json.loads(files["index.json"])["manifests"][0]["digest"].split(":")[1]])
            config = json.loads(files["blobs/sha256/" + manifest["config"]["digest"].split(":")[1]])["config"]
            root = units[-1].context["config"]["root"]
            self.assertEqual(config["Entrypoint"], [f"{root}/core/shaker/bin/shaker", "run", "demo"])
            self.assertIn(f"BH_ROOT={root}", config["Env"])
            self.assertTrue(any(e.startswith("PATH=" + root + "/core/shaker/bin:") for e in config["Env"]))
            self.assertEqual(len(manifest["layers"]), 3)
            top, _ = layer_members(files["blobs/sha256/" + manifest["layers"][2]["digest"].split(":")[1]])
            self.assertIn(f"{root.lstrip('/')}/core/shaker/bin/shaker", top)
            self.assertEqual(top[f"{root.lstrip('/')}/var/lib/shaker"].mode, 0o775)

    def test_shaker_mode_needs_a_shaker_checkout(self):
        with TempDir() as t:
            with self.assertRaises(image.ImageError):
                self.build(t, built(t), entrypoint="shaker")

    def test_direct_mode_refuses_what_needs_shaker(self):
        with TempDir() as t:
            runtime = {**RUNTIME, "persistence": [{"path": "{{ prefix }}/data", "volume": "/data"}]}
            with self.assertRaises(image.ImageError):
                self.build(t, built(t, runtime=runtime))
        with TempDir() as t:
            runtime = {**RUNTIME, "run": {"command": ["{{ prefix }}/bin/demo", "${BH_DEMO_TOKEN}"]}}
            with self.assertRaises(image.ImageError) as ctx:
                self.build(t, built(t, runtime=runtime))
            self.assertIn("BH_DEMO_TOKEN", str(ctx.exception))

    def test_a_service_without_a_command_is_not_an_image(self):
        with TempDir() as t:
            with self.assertRaises(image.ImageError):
                self.build(t, built(t, runtime={"user": RUNTIME["user"]}))

    def test_base_layout_layers_come_first_and_its_environment_is_kept(self):
        with TempDir() as t:
            units = built(t)
            first = t / "base-image.tar"
            image.build(units, first, tag="base:1", entrypoint="direct", log=QUIET)
            layout = t / "layout"
            with tarfile.open(first) as archive:
                archive.extractall(layout, filter="data")
            # make the "base" carry an environment variable to inherit
            index = json.loads((layout / "index.json").read_text())
            mfile = layout / "blobs" / "sha256" / index["manifests"][0]["digest"].split(":")[1]
            manifest = json.loads(mfile.read_text())
            cfile = layout / "blobs" / "sha256" / manifest["config"]["digest"].split(":")[1]
            cfg = json.loads(cfile.read_text())
            cfg["config"]["Env"] = ["LANG=C.UTF-8"]
            cdata = json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode()
            cdigest = hashlib.sha256(cdata).hexdigest()
            (layout / "blobs" / "sha256" / cdigest).write_bytes(cdata)
            manifest["config"] = {"mediaType": image.MEDIA_CONFIG, "digest": "sha256:" + cdigest, "size": len(cdata)}
            mdata = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            mdigest = hashlib.sha256(mdata).hexdigest()
            (layout / "blobs" / "sha256" / mdigest).write_bytes(mdata)
            index["manifests"][0]["digest"], index["manifests"][0]["size"] = "sha256:" + mdigest, len(mdata)
            (layout / "index.json").write_text(json.dumps(index))
            _, files = self.build(t, units, base=layout)
            top = json.loads(files["blobs/sha256/" + json.loads(files["index.json"])["manifests"][0]["digest"].split(":")[1]])
            self.assertEqual(len(top["layers"]), 4)                                # 2 from the base + 2 ours
            config = json.loads(files["blobs/sha256/" + top["config"]["digest"].split(":")[1]])
            self.assertIn("LANG=C.UTF-8", config["config"]["Env"])
            self.assertEqual(len(config["rootfs"]["diff_ids"]), 4)

    def test_unbuilt_or_modified_units_are_not_ready(self):
        with TempDir() as t:
            units = built(t)
            self.assertEqual(image.check_ready(units), [])
            (t / "root" / "demo" / "bin" / "demo").write_text("tampered")
            problems = image.check_ready(units)
            self.assertTrue(any("differ from the build" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
