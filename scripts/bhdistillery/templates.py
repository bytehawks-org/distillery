"""{{ dotted.path }} template resolution against a context dictionary.

Strict by design: a placeholder that cannot be resolved raises TemplateError, so a typo in
packages.yaml fails the build instead of reaching a shell command. Text that is meant for
Shaker at runtime uses the ${NAME} syntax, which is never touched here.
"""

import re

PLACEHOLDER = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


class TemplateError(ValueError):
    pass


def _lookup(path, context):
    value = context
    for key in path.split("."):
        try:
            value = value[key]
        except (KeyError, TypeError, IndexError):
            raise TemplateError(f"cannot resolve '{{{{ {path} }}}}'") from None
    return value


def resolve_template(template, context, max_depth=10):
    """Resolve placeholders repeatedly, so values may themselves contain placeholders."""
    if not isinstance(template, str):
        return template

    for _ in range(max_depth):
        resolved = PLACEHOLDER.sub(lambda m: str(_lookup(m.group(1).strip(), context)), template)
        if resolved == template:
            return resolved
        template = resolved

    raise TemplateError(f"template still unresolved after {max_depth} passes (circular reference?): {template[:80]}")


def resolve_dict_templates(data, context):
    """Resolve every string inside nested dicts and lists."""
    if isinstance(data, dict):
        return {k: resolve_dict_templates(v, context) for k, v in data.items()}
    if isinstance(data, list):
        return [resolve_dict_templates(item, context) for item in data]
    if isinstance(data, str):
        return resolve_template(data, context)
    return data
