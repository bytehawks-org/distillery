import io
import hashlib
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bhdistillery.model import Catalog  # noqa: E402


def make_tarball(path, files, top="demo-1.0"):
    """files: {relative name: text}; every entry lives below `top`/ like a release tarball."""
    with tarfile.open(path, "w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(data))
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


CONFIG = {"config": {
    "root": "/opt/bytehawks", "link": "/opt/bh", "core_path": "/opt/bytehawks/core",
    "default_download_path": "/opt/bytehawks/var/cache/distillery",
    "default_src_path": "/opt/bytehawks/var/build",
    "log_path": "/opt/bytehawks/var/log/distillery",
    "libraries": {"default_prefix": "/opt/bytehawks/deps"},
    "applications": {"default_prefix": "/opt/bytehawks/{{ name }}"},
}}


def package(url, sha256, **extra):
    base = {
        "variant": [{"name": "stable", "major_version": 1, "patch_version": 0, "full_version": "1.0", "sha256": sha256}],
        "git": {"repo": "file://x", "release_download_url_template": url},
        "commands": {
            "configure": "echo configured > configured.txt",
            "make": "echo built > built.txt",
            "install": 'mkdir -p {{ prefix }}/bin && cp built.txt {{ prefix }}/bin/demo.txt',
        },
        "tags": ["application"],
    }
    base.update(extra)
    return base


def catalog(packages, root):
    from bhdistillery.model import apply_root
    return Catalog(apply_root(CONFIG, root), {"package": packages}, jobs=1)


class TempDir:
    def __enter__(self):
        self._t = tempfile.TemporaryDirectory()
        return Path(self._t.name)

    def __exit__(self, *exc):
        self._t.cleanup()


import http.server
import json as _json
import subprocess
import threading


def git_repo_with_tags(path, tags):
    """A local git repository with the given tags, usable as `git ls-remote` target."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"], "HOME": str(path)}
    run = lambda *a: subprocess.run(["git", *a], cwd=path, env=env, check=True, capture_output=True)
    run("init", "-q")
    run("commit", "-q", "--allow-empty", "-m", "x")
    for tag in tags:
        run("tag", tag)
    return str(path)


class FakeServer:
    """Tiny threaded HTTP server: routes = {(method, path-prefix): callable(handler, body) -> (status, headers, bytes)}.
    Records every request as (method, path, headers)."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append((self.command, self.path, dict(self.headers), body))
                for (method, prefix), fn in outer.routes.items():
                    if method == self.command and self.path.startswith(prefix):
                        status, headers, payload = fn(self, body)
                        self.send_response(status)
                        for key, value in headers.items():
                            self.send_header(key, value)
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                        return
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_PUT = do_HEAD = _serve

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def json_response(data, status=200, headers=None):
    return status, {"Content-Type": "application/json", **(headers or {})}, _json.dumps(data).encode()
