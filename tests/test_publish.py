import json

import httpx
import pytest

from scripts.build.artifact import ArtifactRef
from scripts.config.models import GitHubRepositoryEndpoint, NexusRepositoryEndpoint, RepositoryConfig
from scripts.core.exceptions import PublishError
from scripts.publish import RepositoryManager

REF = ArtifactRef("nginx", "1.30.6", "stable")


class FakeNexus:
    """In-memory Nexus raw repository."""

    def __init__(self):
        self.files = {}
        self.puts = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "PUT":
            self.files[path] = request.content
            self.puts.append(path.rsplit("/", 1)[-1])
            return httpx.Response(201)
        if path in self.files:
            return httpx.Response(200, content=b"" if request.method == "HEAD" else self.files[path])
        return httpx.Response(404)


class FakeGitHub:
    def __init__(self):
        self.releases = {}   # tag -> {"id", "assets": {name: bytes}}
        self.auth = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("authorization"))
        path = request.url.path
        if request.method == "GET" and "/releases/tags/" in path:
            tag = path.rsplit("/", 1)[-1]
            rel = self.releases.get(tag)
            return httpx.Response(200, json=self._view(tag, rel)) if rel else httpx.Response(404)
        if request.method == "POST" and path.endswith("/releases"):
            tag = json.loads(request.content)["tag_name"]
            self.releases[tag] = {"id": len(self.releases) + 1, "assets": {}}
            return httpx.Response(201, json=self._view(tag, self.releases[tag]))
        if request.method == "POST" and path.endswith("/assets"):
            rid = int(path.split("/")[-2])
            tag = next(t for t, r in self.releases.items() if r["id"] == rid)
            self.releases[tag]["assets"][request.url.params["name"]] = request.content
            return httpx.Response(201, json={})
        if request.method == "GET" and "/assets/" in path:
            tag, name = path.rsplit("/", 1)[-1].split("|", 1) if "|" in path else (None, None)
        if request.method == "GET" and path.startswith("/dl/"):
            _, _, tag, name = path.split("/", 3)
            return httpx.Response(200, content=self.releases[tag]["assets"][name])
        return httpx.Response(500)

    @staticmethod
    def _view(tag, rel):
        return {"id": rel["id"], "assets": [
            {"name": n, "url": f"https://api.github.com/dl/{tag}/{n}"} for n in rel["assets"]]}


def endpoints():
    nexus = NexusRepositoryEndpoint(url="http://nexus.test/repository", repository="packages",
                                    username="u", password="p")
    gh = GitHubRepositoryEndpoint(repo="bytehawks-org/artifacts", token="tok")
    return nexus, gh


def manager(strategy, nexus_t, gh_t):
    nexus, gh = endpoints()
    cfg = RepositoryConfig(strategy=strategy, primary={"nexus": nexus}, fallback={"github": gh})
    return RepositoryManager(cfg, {"nexus": httpx.MockTransport(nexus_t), "github": httpx.MockTransport(gh_t)})


@pytest.fixture
def files(tmp_path):
    out = []
    for name in REF.all_files:
        p = tmp_path / name
        p.write_bytes(f"data:{name}".encode())
        out.append(p)
    return out


def test_mirror_all_publishes_everywhere_and_is_idempotent(files):
    n, g = FakeNexus(), FakeGitHub()
    m = manager("mirror-all", n, g)

    assert m.publish(REF, files) == {"nexus": "uploaded", "github": "uploaded"}
    assert n.puts[-1] == REF.filename                      # tarball uploaded last
    assert "/repository/packages/nginx/1.30/1.30.6/" + REF.filename in n.files
    assert set(g.releases["nginx-1.30.6"]["assets"]) == set(REF.all_files)
    assert "Bearer tok" in g.auth

    assert m.publish(REF, files) == {"nexus": "exists", "github": "exists"}


def test_mirror_all_fails_when_one_endpoint_is_down(files):
    n = FakeNexus()
    m = manager("mirror-all", n, lambda req: httpx.Response(401, text="bad credentials"))
    with pytest.raises(PublishError, match="github"):
        m.publish(REF, files)
    assert REF.filename in n.puts            # the healthy endpoint still got the artifact


def test_primary_with_fallback_uses_fallback_only_on_failure(files):
    g = FakeGitHub()
    down = lambda req: httpx.Response(401)
    m = manager("primary-with-fallback", down, g)
    status = m.publish(REF, files)
    assert status["nexus"].startswith("error") and status["github"] == "uploaded"


def test_fetch_prefers_primary_then_falls_back(files, tmp_path):
    n, g = FakeNexus(), FakeGitHub()
    manager("mirror-all", n, g).publish(REF, files)

    dest = tmp_path / "dl"
    assert manager("mirror-all", n, g).fetch(REF, dest)
    assert (dest / REF.filename).read_bytes() == f"data:{REF.filename}".encode()

    dest2 = tmp_path / "dl2"
    assert manager("mirror-all", FakeNexus(), g).fetch(REF, dest2)   # empty nexus -> github
    assert (dest2 / REF.filename).exists()


def test_missing_credentials_are_reported(files):
    nexus = NexusRepositoryEndpoint(url="http://nexus.test/repository", repository="packages")
    cfg = RepositoryConfig(strategy="primary-only", primary={"nexus": nexus})
    m = RepositoryManager(cfg, {"nexus": httpx.MockTransport(FakeNexus())})
    with pytest.raises(PublishError):
        m.publish(REF, files)


def test_exists_false_when_all_endpoints_fail():
    m = manager("mirror-all", lambda r: httpx.Response(404), lambda r: httpx.Response(404))
    assert m.exists(REF) is False
