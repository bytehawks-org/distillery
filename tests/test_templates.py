import unittest

from helpers import *  # noqa: F401,F403
from bhdistillery.templates import TemplateError, resolve_dict_templates, resolve_template


class TemplateTests(unittest.TestCase):
    ctx = {"name": "nginx", "git": {"repo": "https://x/{{ name }}"}, "v": 3}

    def test_simple_and_nested(self):
        self.assertEqual(resolve_template("{{ name }}-{{ v }}", self.ctx), "nginx-3")
        self.assertEqual(resolve_template("{{ git.repo }}/dl", self.ctx), "https://x/nginx/dl")

    def test_unresolvable_is_an_error(self):
        with self.assertRaises(TemplateError):
            resolve_template("{{ nope }}", self.ctx)

    def test_shaker_runtime_syntax_is_untouched(self):
        self.assertEqual(resolve_template("--port=${BH_X_PORT}", self.ctx), "--port=${BH_X_PORT}")

    def test_circular_reference(self):
        with self.assertRaises(TemplateError):
            resolve_template("{{ a }}", {"a": "{{ b }}", "b": "{{ a }}"})

    def test_dict_and_list(self):
        out = resolve_dict_templates({"a": ["{{ name }}", 1], "b": {"c": "{{ v }}"}}, self.ctx)
        self.assertEqual(out, {"a": ["nginx", 1], "b": {"c": "3"}})


if __name__ == "__main__":
    unittest.main()
