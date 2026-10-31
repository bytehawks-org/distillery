import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKER = ROOT / "docker"
NAMES = ("builder-glibc", "builder-musl", "runtime-glibc", "runtime-musl")


def arg_defaults(path):
    return dict(re.findall(r"^ARG (\w+)=(\S+)$", path.read_text(), re.M))


class DockerfileTests(unittest.TestCase):
    def test_every_dockerfile_exists_and_takes_its_base_as_an_argument(self):
        for name in NAMES:
            text = (DOCKER / f"{name}.Dockerfile").read_text()
            self.assertRegex(text, r"(?m)^ARG BASE=\S+\nFROM \$\{BASE\}$", name)
            self.assertIn("org.opencontainers.image.source", text, name)

    def test_builders_are_glibc_debian_and_musl_alpine(self):
        self.assertIn("debian", arg_defaults(DOCKER / "builder-glibc.Dockerfile")["BASE"])
        self.assertIn("alpine", arg_defaults(DOCKER / "builder-musl.Dockerfile")["BASE"])

    def test_nfpm_pins_match_the_catalog(self):
        catalog = yaml.safe_load((ROOT / "packages.yaml").read_text())["package"]["nfpm"]
        variant = catalog["variant"][0]
        for name in ("builder-glibc", "builder-musl"):
            args = arg_defaults(DOCKER / f"{name}.Dockerfile")
            self.assertEqual(args["NFPM_VERSION"], str(variant["full_version"]), name)
            self.assertEqual(args["NFPM_SHA256_AMD64"], variant["sha256"]["amd64"], name)
            self.assertEqual(args["NFPM_SHA256_ARM64"], variant["sha256"]["arm64"], name)

    def test_builders_carry_what_the_workflows_install_and_more(self):
        build = (ROOT / ".github" / "workflows" / "build-platform.yml").read_text()
        for name, tools in (("builder-glibc", re.search(r"apt-get install -y --no-install-recommends ([^\n]+)", build).group(1).split()),
                            ("builder-musl", re.search(r"apk add --no-cache ([^\n]+)", build).group(1).split())):
            text = (DOCKER / f"{name}.Dockerfile").read_text()
            for tool in tools:
                self.assertIn(tool, text, f"{name} lacks {tool}, which the workflow installs")
            self.assertIn("bubblewrap", text)                                  # the --sandbox backend

    def test_files_copied_into_the_images_exist(self):
        for name in NAMES:
            for source in re.findall(r"^COPY (\S+) ", (DOCKER / f"{name}.Dockerfile").read_text(), re.M):
                self.assertTrue((ROOT / source).exists(), f"{name}: COPY {source}")

    def test_workflow_builds_every_dockerfile(self):
        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "builder-images.yml").read_text())
        matrix = workflow["jobs"]["images"]["strategy"]["matrix"]
        built = {f"{image}-{libc}" for image in matrix["image"] for libc in matrix["libc"]}
        self.assertEqual(built, set(NAMES))


if __name__ == "__main__":
    unittest.main()
