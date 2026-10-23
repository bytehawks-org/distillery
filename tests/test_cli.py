import contextlib
import io
import unittest

from helpers import *  # noqa: F401,F403
import autobuilder


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = autobuilder.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_list_plan_and_dry_run_on_the_real_catalog(self):
        code, out, _ = run("list")
        self.assertEqual(code, 0)
        self.assertIn("nginx", out)

        code, out, _ = run("plan", "--package", "nginx", "--root", "/tmp/never-created")
        self.assertEqual(code, 0)
        order = [line.split()[1] for line in out.splitlines()]
        self.assertEqual(order[-1], "nginx")
        self.assertLess(order.index("openssl"), order.index("nginx"))
        self.assertNotIn("MISSING", out)                   # every shipped source is pinned

        code, out, _ = run("build", "--package", "nginx", "--with-deps", "--dry-run", "--root", "/tmp/never-created")
        self.assertEqual(code, 0)
        self.assertIn("--prefix=/tmp/never-created/nginx", out)

    def test_legacy_invocation_is_a_dry_run(self):
        code, out, _ = run("--package", "zlib", "--tag", "library", "--root", "/tmp/never-created")
        self.assertEqual(code, 0)
        self.assertIn("[plan] zlib", out)

    def test_errors_are_reported_not_raised(self):
        code, _, err = run("plan", "--package", "ghost")
        self.assertEqual(code, 1)
        self.assertIn("ghost", err)
        code, _, err = run("build", "--package", "zlib", "--dry-run", "--stages", "wat")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
