import subprocess
import unittest
from pathlib import Path

from helpers import *  # noqa: F401,F403
from bhdistillery import build, bump, fetch


def make_repo(path, files, tag="v1.0", annotated=False):
    """A local git repository with one commit holding `files`, tagged; returns (path, commit)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": __import__("os").environ["PATH"], "HOME": str(path), "GIT_CONFIG_GLOBAL": "/dev/null"}
    run = lambda *a: subprocess.run(["git", *a], cwd=path, env=env, check=True, capture_output=True, text=True).stdout.strip()
    if not (path / ".git").exists():
        run("init", "-q")
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    run("add", "-A")
    run("commit", "-q", "-m", "release")
    run("tag", "-f", *(["-a", "-m", "release"] if annotated else []), tag)
    return str(path), run("rev-parse", "HEAD")


def git_package(repo, commit, **extra):
    base = {
        "variant": [{"name": "stable", "major_version": 1, "patch_version": 0, "full_version": "1.0", "commit": commit}],
        "git": {"repo": repo, "ref_template": "v{{ full_version }}"},
        "commands": {"make": "echo built > built.txt",
                     "install": "mkdir -p {{ prefix }}/bin && cp built.txt src.txt {{ prefix }}/bin/"},
        "tags": ["application"],
    }
    base.update(extra)
    return base


class GitSourceTests(unittest.TestCase):
    def test_build_from_a_pinned_commit(self):
        with TempDir() as t:
            repo, commit = make_repo(t / "up", {"src.txt": "hello\n", ".github/x.yml": "a"}, annotated=True)
            cat = catalog({"demo": git_package(repo, commit)}, t / "root")
            unit = cat.unit("demo")
            cat.plan(unit)
            self.assertEqual(build.build_unit(unit, t / "assets", log=lambda m: None), "built")
            self.assertEqual((t / "root" / "demo" / "bin" / "src.txt").read_text(), "hello\n")
            self.assertFalse((Path(unit.src_dir) / ".git").exists())            # an export, not a checkout
            manifest = __import__("json").loads((unit.bundle_dir / "manifest.json").read_text())
            self.assertEqual(manifest["source_commit"], commit)
            self.assertTrue(manifest["source"].endswith("#v1.0"))

    def test_wrong_pin_and_moved_tag_are_refused_before_anything_runs(self):
        with TempDir() as t:
            repo, commit = make_repo(t / "up", {"src.txt": "v1\n"})
            cat = catalog({"demo": git_package(repo, "0" * 40)}, t / "root")
            unit = cat.unit("demo")
            cat.plan(unit)
            with self.assertRaises(fetch.FetchError) as ctx:
                build.build_unit(unit, t / "assets", log=lambda m: None)
            self.assertIn("expected " + "0" * 40, str(ctx.exception))
            self.assertFalse((t / "root" / "demo").exists())

            cat = catalog({"demo": git_package(repo, commit)}, t / "root")
            unit = cat.unit("demo")
            cat.plan(unit)
            make_repo(t / "up", {"src.txt": "v1 but changed\n"})                  # the tag moves to a new commit
            with self.assertRaises(fetch.FetchError) as ctx:
                build.build_unit(unit, t / "assets", log=lambda m: None)
            self.assertIn("tag moved", str(ctx.exception))

    def test_unpinned_git_source_is_refused_unless_allowed(self):
        with TempDir() as t:
            repo, commit = make_repo(t / "up", {"src.txt": "x\n"})
            pkg = git_package(repo, "")
            cat = catalog({"demo": pkg}, t / "root")
            unit = cat.unit("demo")
            cat.plan(unit)
            with self.assertRaises(fetch.FetchError):
                build.build_unit(unit, t / "assets", log=lambda m: None)
            messages = []
            build.build_unit(unit, t / "assets", allow_unverified=True, log=messages.append)
            self.assertTrue(any(commit in m for m in messages))               # the commit to pin is printed

    def test_signature_block_is_not_accepted_with_git_sources(self):
        with TempDir() as t:
            repo, commit = make_repo(t / "up", {"src.txt": "x\n"})
            cat = catalog({"demo": git_package(repo, commit, signature={"keys_url": "file:///k", "fingerprints": ["A" * 40]})}, t / "r")
            unit = cat.unit("demo")
            cat.plan(unit)
            with self.assertRaises(build.BuildError):
                build.build_unit(unit, t / "assets", log=lambda m: None)

    def test_commit_of_a_tag_peels_annotated_tags_and_bump_pins_the_new_one(self):
        with TempDir() as t:
            repo, c1 = make_repo(t / "up", {"src.txt": "one\n"}, tag="v1.0", annotated=True)
            self.assertEqual(fetch.git_commit_of(repo, "v1.0"), c1)             # not the tag object
            repo, c2 = make_repo(t / "up", {"src.txt": "two\n"}, tag="v1.1", annotated=True)
            with self.assertRaises(fetch.FetchError):
                fetch.git_commit_of(repo, "v9.9")

            pkg = git_package(repo, c1)
            cat = catalog({"demo": pkg}, t / "root")
            unit = cat.unit("demo")
            source, commit = bump.git_pin(unit, "1.1")
            self.assertEqual((source, commit), (f"{repo} v1.1", c2))

            f = t / "packages.yaml"
            f.write_text("package:\n  demo:\n    variant:\n      - {name: stable, major_version: 1, patch_version: 0, "
                         f"full_version: '1.0', commit: {c1}}}\n")
            bump.apply_bump(f, "demo", "stable", "1.1", c2, key="commit")
            text = f.read_text()
            self.assertIn(f"commit: {c2}", text)
            self.assertNotIn("sha256", text)

    def test_plan_reports_the_commit_pin(self):
        with TempDir() as t:
            repo, commit = make_repo(t / "up", {"src.txt": "x\n"})
            import io
            import contextlib
            from bhdistillery import commands

            class Args:
                package, variant, tag = "demo", None, None

            for pin, expected in ((commit, "commit ok"), ("", "commit MISSING")):
                cat = catalog({"demo": git_package(repo, pin)}, t / "root")
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    commands.cmd_plan(cat, Args)
                self.assertIn(expected, out.getvalue())


if __name__ == "__main__":
    unittest.main()
