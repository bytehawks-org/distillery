import re
import unittest
from pathlib import Path

from helpers import *  # noqa: F401,F403
from bhdistillery import bundle, scaffold as sc

SPACE = """\
# Network
# Accept connections on the specified port.
port 6379
bind 127.0.0.1 -::1

# Specify the verbosity level. This can be one of:
# debug (a lot of information)
# notice (moderately verbose)
# warning (only important messages)
loglevel notice
protected-mode yes
dir /var/lib/demo
save 3600 1
save 300 100
# requirepass foobared
logfile ""
"""

EQUALS = """\
#port = 5432                           # (change requires restart)
max_connections = 100                  # (change requires restart)
shared_buffers = 128MB                 # min 128kB
listen_addresses = 'localhost'         # what IP address(es) to listen on;
#password_encryption = scram-sha-256   # scram-sha-256 or md5
# this is just prose = not a setting
"""

INI = """\
[mysqld]
# The port.
port = 3306
datadir = /var/lib/mysql
[client]
port = 3306
"""

YAML = """\
global:
  scrape_interval: 15s   # How often to scrape.
  evaluation_interval: 15s
alerting:
  alertmanagers:
    - static_configs:
        - targets: []
scrape_configs:
  - job_name: "prometheus"
storage:
  retention: 15d
"""


def entries(text, fmt, **kw):
    settings, lines = sc.parse(text, fmt, kw.pop("commented", "no"))
    chosen = sc.select(settings, kw.pop("include", ""), kw.pop("exclude", ""))
    return chosen, lines, sc.env_entries("demo", chosen, fmt, kw.pop("hints", None), lines)


class ParseTests(unittest.TestCase):
    def test_format_detection(self):
        self.assertEqual(sc.detect_format(SPACE), "space")
        self.assertEqual(sc.detect_format(EQUALS), "equals")
        self.assertEqual(sc.detect_format(INI), "ini")
        self.assertEqual(sc.detect_format("a: 1", "prometheus.yml"), "yaml")

    def test_space_format_types_defaults_and_docs(self):
        chosen, lines, env = entries(SPACE, "space")
        by = {e["name"]: e for e in env}
        self.assertEqual([s.key for s in chosen], ["port", "bind", "loglevel", "protected-mode", "dir", "logfile"])     # `save` repeats
        self.assertEqual((by["BH_DEMO_PORT"]["type"], by["BH_DEMO_PORT"]["default"]), ("port", 6379))
        self.assertEqual(by["BH_DEMO_PORT"]["description"], "Accept connections on the specified port.")
        self.assertEqual(by["BH_DEMO_PROTECTED_MODE"]["type"], "enum:yes,no")
        self.assertEqual(by["BH_DEMO_LOGLEVEL"]["type"], "enum:debug,notice,warning")         # listed in the comment above
        self.assertEqual(by["BH_DEMO_DIR"]["type"], "path")
        self.assertEqual(by["BH_DEMO_BIND"]["default"], "127.0.0.1 -::1")
        self.assertNotIn("file_key", by["BH_DEMO_PORT"])                                         # `key value` is not a format Rosetta reads
        self.assertEqual(by["BH_DEMO_PORT"]["cli"], "--port")
        self.assertNotIn("default", by["BH_DEMO_LOGFILE"])                                       # empty value: no default

    def test_commented_defaults_strict_and_loose(self):
        self.assertEqual([s.key for s in sc.parse(SPACE, "space", "no")[0]].count("requirepass"), 0)
        loose = {s.key: s for s in sc.parse(SPACE, "space", "loose")[0]}
        self.assertFalse(loose["requirepass"].active)
        chosen, lines, env = entries(SPACE, "space", commented="loose", include="^requirepass$")
        self.assertEqual((env[0]["type"], "default" in env[0]), ("secret", False))               # a secret never gets a default

    def test_equals_format_with_quotes_and_trailing_comments(self):
        chosen, lines, env = entries(EQUALS, "equals", commented="strict")
        by = {e["name"]: e for e in env}
        self.assertEqual(sorted(by), ["BH_DEMO_LISTEN_ADDRESSES", "BH_DEMO_MAX_CONNECTIONS", "BH_DEMO_PASSWORD_ENCRYPTION",
                                      "BH_DEMO_PORT", "BH_DEMO_SHARED_BUFFERS"])                  # the prose line is not a setting
        self.assertEqual(by["BH_DEMO_LISTEN_ADDRESSES"]["default"], "localhost")                  # quotes are not part of the value
        self.assertEqual(by["BH_DEMO_PORT"]["type"], "port")
        self.assertEqual(by["BH_DEMO_SHARED_BUFFERS"]["default"], "128MB")
        self.assertEqual(by["BH_DEMO_MAX_CONNECTIONS"]["file_key"], "max_connections")

    def test_ini_sections_make_names_and_file_keys(self):
        chosen, lines, env = entries(INI, "ini")
        by = {e["name"]: e for e in env}
        self.assertEqual(sorted(by), ["BH_DEMO_CLIENT_PORT", "BH_DEMO_MYSQLD_DATADIR", "BH_DEMO_MYSQLD_PORT"])
        self.assertEqual(by["BH_DEMO_MYSQLD_PORT"]["file_key"], "mysqld.port")

    def test_yaml_leaves_only(self):
        chosen, lines, env = entries(YAML, "yaml")
        self.assertEqual([e["file_key"] for e in env],
                         ["global.scrape_interval", "global.evaluation_interval", "storage.retention"])   # lists and nesting of lists skipped
        self.assertEqual(env[0]["description"], "How often to scrape.")          # a trailing comment documents the setting

    def test_hint_overrides_the_inferred_type(self):
        _, _, env = entries(SPACE, "space", include="^loglevel$", hints={"loglevel": "enum:debug,notice"})
        self.assertEqual(env[0]["type"], "enum:debug,notice")

    def test_include_exclude_and_max(self):
        settings, _ = sc.parse(SPACE, "space")
        self.assertEqual([s.key for s in sc.select(settings, include="^(port|bind)$")], ["port", "bind"])
        self.assertEqual([s.key for s in sc.select(settings, exclude="log")][:3], ["port", "bind", "protected-mode"])
        self.assertEqual(len(sc.select(settings, maximum=2)), 2)

    def test_names_are_unique_and_valid_for_the_bundle(self):
        text = "[a]\nport = 1\n[b]\nport = 2\n[a-b]\nx_y = 1\n[a_b]\nx.y = 2\n"
        chosen, lines, env = entries(text, "ini")
        names = [e["name"] for e in env]
        self.assertEqual(len(names), len(set(names)))
        unit_runtime = {"env": env}
        cat = catalog({"demo": package("file:///x.tgz", "a" * 64, runtime=unit_runtime)}, "/r")
        unit = cat.unit("demo")
        cat.plan(unit)
        bundle.rosetta_lines(unit)                                  # raises on an invalid name, type or duplicate


class TemplateTests(unittest.TestCase):
    @staticmethod
    def render(template, values):
        def line(l):
            m = re.match(r"^(\s*)\{\{\?\s*(\w+)\s*\}\}(.*)$", l)
            if m:
                return None if not values.get(m.group(2)) else m.group(1) + m.group(3)
            return l
        out = [line(l) for l in template.splitlines()]
        return "\n".join(re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(values[m.group(1)]), l) for l in out if l is not None) + "\n"

    def test_template_with_defaults_reproduces_the_original_active_settings(self):
        for text, fmt in ((SPACE, "space"), (EQUALS, "equals"), (INI, "ini"), (YAML, "yaml")):
            chosen, lines, env = entries(text, fmt, exclude="^(save|logfile)$")
            template = sc.render_template(lines, chosen, fmt)
            self.assertIn("{{ BH_DEMO_", template, fmt)
            defaults = {e["name"]: e.get("default", "") for e in env}
            original = "\n".join(lines) + "\n"
            self.assertEqual(self.render(template, defaults), original, f"{fmt}: defaults must give back the file")

    def test_parameters_change_only_their_own_lines(self):
        chosen, lines, env = entries(EQUALS, "equals", commented="strict")
        template = sc.render_template(lines, chosen, "equals")
        values = {e["name"]: e.get("default", "") for e in env}
        values["BH_DEMO_LISTEN_ADDRESSES"] = "*"
        values["BH_DEMO_PORT"] = 6000
        out = self.render(template, values).splitlines()
        self.assertIn("listen_addresses = '*'         # what IP address(es) to listen on;", out)
        self.assertIn("port = 6000                           # (change requires restart)", out)       # `#port` became active
        self.assertIn("shared_buffers = 128MB                 # min 128kB", out)

    def test_optional_settings_are_conditional_lines(self):
        chosen, lines, env = entries(SPACE, "space", commented="loose", include="^(requirepass|logfile)$")
        template = sc.render_template(lines, chosen, "space")
        self.assertIn("{{? BH_DEMO_REQUIREPASS }}requirepass {{ BH_DEMO_REQUIREPASS }}", template)
        self.assertIn('{{? BH_DEMO_LOGFILE }}logfile "{{ BH_DEMO_LOGFILE }}"', template)         # quotes stay around the value
        off = self.render(template, {"BH_DEMO_REQUIREPASS": "", "BH_DEMO_LOGFILE": ""})
        self.assertNotIn("requirepass", off)
        on = self.render(template, {"BH_DEMO_REQUIREPASS": "s3cret", "BH_DEMO_LOGFILE": "/x.log"})
        self.assertIn("requirepass s3cret", on)


class MergeTests(unittest.TestCase):
    def test_merge_adds_only_what_is_missing_and_keeps_hand_edits(self):
        with TempDir() as t:
            f = t / "packages.yaml"
            f.write_text("package:\n  demo:\n    # my comment\n    runtime:\n      env:\n"
                         "        - {name: BH_DEMO_PORT, type: port, default: 9999, description: edited by hand}\n")
            chosen, lines, env = entries(SPACE, "space", exclude="^save$")
            tpl = {"src": "demo.conf.tmpl", "dest": "{{ prefix }}/conf/demo.conf", "mode": "0640"}
            added, template_added = sc.merge_into_packages(f, "demo", env, tpl)
            self.assertNotIn("BH_DEMO_PORT", added)
            self.assertTrue(template_added)
            text = f.read_text()
            self.assertIn("default: 9999", text)
            self.assertIn("edited by hand", text)
            self.assertIn("# my comment", text)
            again, template_again = sc.merge_into_packages(f, "demo", env, tpl)
            self.assertEqual((again, template_again), ([], False))                 # idempotent
            self.assertEqual(f.read_text().count("name: BH_DEMO_BIND"), 1)

    def test_merge_needs_the_package_to_exist(self):
        with TempDir() as t:
            f = t / "packages.yaml"
            f.write_text("package:\n  other: {}\n")
            with self.assertRaises(sc.ScaffoldError):
                sc.merge_into_packages(f, "demo", [], None)


class CommandTests(unittest.TestCase):
    def test_list_and_write(self):
        import contextlib
        import io
        from bhdistillery import commands

        with TempDir() as t:
            (t / "demo.conf").write_text(SPACE)
            (t / "packages.yaml").write_text("package:\n  demo:\n    variant: []\n")

            class Args:
                package, source, format, commented = "demo", str(t / "demo.conf"), None, "no"
                include, exclude, max, hint = "", "^save$", 0, None
                template, dest, mode = None, None, "0640"
                assets, packages = str(t / "assets"), str(t / "packages.yaml")
                list, write = True, False

            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                commands.cmd_scaffold(None, Args)
            self.assertIn("BH_DEMO_PORT", out.getvalue())
            self.assertEqual((t / "packages.yaml").read_text().count("BH_DEMO"), 0)      # --list writes nothing

            Args.list, Args.write = False, True
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                commands.cmd_scaffold(None, Args)
            self.assertTrue((t / "assets" / "demo" / "demo.conf.tmpl").is_file())
            self.assertIn("BH_DEMO_PORT", (t / "packages.yaml").read_text())
            self.assertIn("src: demo.conf.tmpl", (t / "packages.yaml").read_text())


if __name__ == "__main__":
    unittest.main()
