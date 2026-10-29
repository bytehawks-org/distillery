"""Derive `runtime.env` entries and a configuration template from the configuration file an application ships.

Configuration files with a predictable shape (one setting per line: `key value`, `key = value`, ini sections, flat YAML) already
say what the parameters are, what their defaults are and, in the comments above them, what they do. `scaffold` reads such a file
and proposes:

  * the `runtime.env` entries (name, type, default, `cli` flag, `file_key`, description), types inferred from the values;
  * a template: the same file with the chosen values replaced by `{{ BH_<PKG>_<KEY> }}`, rendered by Shaker at `init`.
    Settings with no default (secrets, optional ones) become `{{? NAME }}key {{ NAME }}`: the line exists only when set.

Nothing is invented: a setting appears only if it is in the file. Choosing *which* settings to expose (`--include`,
`--exclude`, `--max`, `--commented`) and correcting an inferred type (`--hint key=enum:a,b`) remain human decisions; everything
else is mechanical, and re-running against a newer file only adds what is new (existing entries are never touched).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

FORMATS = ("space", "equals", "ini", "yaml")
SECRET_RE = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|private[_-]?key|credential)", re.I)
BOOL_PAIRS = ({"yes", "no"}, {"on", "off"}, {"true", "false"}, {"enabled", "disabled"})
IDENT = r"[A-Za-z_][A-Za-z0-9_.\-]*"


class ScaffoldError(ValueError):
    pass


@dataclass
class Setting:
    key: str                       # as written
    section: str = ""
    value: str = ""                # inner value (quotes removed)
    quote: str = ""                # quote character around the value in the file, if any
    doc: str = ""
    active: bool = True            # False: found as a commented default
    line: int = 0
    repeated: bool = False
    type: str = "string"
    secret: bool = False
    name: str = ""
    span: tuple = field(default=(0, 0))        # (start, end) of the value in the line, for the template

    @property
    def path(self):
        return f"{self.section}.{self.key}" if self.section else self.key


# ------------------------------------------------------------------------------------------------------ parsing

def detect_format(text, filename=""):
    name = Path(filename).name.lower()
    if name.endswith((".yml", ".yaml")):
        return "yaml"
    body = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if any(re.match(r"^\s*\[[^\]]+\]\s*$", l) for l in body):
        return "ini"
    equals = sum(1 for l in body if re.match(rf"^\s*{IDENT}\s*=", l))
    return "equals" if equals >= max(1, len(body) // 3) else "space"


def _doc_above(lines, index):
    """The contiguous comment lines right above `index`, as one sentence."""
    collected = []
    i = index - 1
    while i >= 0 and lines[i].lstrip().startswith("#"):
        text = lines[i].lstrip().lstrip("#").strip()
        if re.match(r"^[a-z][\w.\-]*(\s+[^\s.]+){0,3}$", text):
            i -= 1                                           # an example directive (`tls-port 6379`), not prose
            continue
        if text and not re.match(r"^[#=\-*~_]{3,}$", text):
            collected.append(text)
        elif not text and collected:
            break
        i -= 1
    collected.reverse()
    if len(collected) > 1 and len(collected[0].split()) <= 3 and not collected[0].endswith((".", ":", ";", ",", ")")):
        collected.pop(0)                                    # a section heading ("# Network") glued to the description
    return " ".join(collected)


def _enum_from_doc(lines, index, default):
    """Allowed values when the comment above lists them (`one of: a, b, c` or `one of:` followed by one value per line).
    Only returned when it contains the default, so prose is not mistaken for a list."""
    block, i = [], index - 1
    while i >= 0 and lines[i].lstrip().startswith("#"):
        block.append(lines[i].lstrip().lstrip("#").strip())
        i -= 1
    block.reverse()
    for n, text in enumerate(block):
        m = re.search(r"\b(?:one of|values?|options?)\s*:\s*(.*)$", text, re.I)
        if not m:
            continue
        options = []
        if m.group(1):
            options = [w.strip(" ,.;()'\"") for w in re.split(r"[,|/]|\s+or\s+", m.group(1))]
        else:
            for later in block[n + 1:]:
                word = re.match(r"^([a-z][a-z0-9_\-]*)\b", later)
                if not word:
                    break
                options.append(word.group(1))
        options = [o for o in options if re.fullmatch(r"[A-Za-z0-9_\-]+", o)]
        if len(options) >= 2 and default in options:
            return options
    return []


def _quoted(raw):
    """(inner, quote, start offset of the inner value in raw, end offset) for a value token."""
    if len(raw) >= 2 and raw[0] in "'\"" and raw[-1] == raw[0]:
        return raw[1:-1], raw[0], 1, len(raw) - 1
    return raw, "", 0, len(raw)


def _split_value(rest):
    """Value of `key = rest` or `key rest`, without the trailing ` # comment`: (raw, length consumed)."""
    rest = rest.rstrip()
    m = re.match(r"^('(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\")", rest)
    if m:
        return m.group(1), m.end()
    m = re.search(r"\s+#", rest)
    raw = rest[:m.start()] if m else rest
    return raw.rstrip(), len(raw.rstrip())


def parse(text, fmt, commented="no"):
    """Settings of a file. A setting found only as a commented default is an *example*, not a default: it gets no default value
    and its template line is conditional. `commented`: 'no' (active lines only), 'strict' (also `#key = value` written with no space after
    the #, the way sample files document defaults), 'loose' (also `# key value`; prose can match, review the result)."""
    if fmt not in FORMATS:
        raise ScaffoldError(f"unknown format '{fmt}' (use {', '.join(FORMATS)})")
    lines = text.splitlines()
    settings, section, yaml_stack, list_floor = [], "", [], None

    for number, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        active = not stripped.startswith("#")
        body, offset = line, 0

        if not active:
            if commented == "no":
                continue
            m = re.match(r"^(\s*)#(\s*)(.*)$", line)
            if commented == "strict" and m.group(2):
                continue
            body, offset = m.group(3), m.end(3) - len(m.group(3))
            if fmt == "yaml" or not re.match(rf"^{IDENT}\s*(=|\s)\s*\S", body or " "):
                continue
            if commented == "loose" and fmt == "space" and (body.endswith(".") or len(body.split()) > 8):
                continue
        elif fmt in ("ini",):
            sec = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
            if sec:
                section = sec.group(1).strip()
                continue

        if fmt == "yaml":
            if not active:
                continue
            lead = len(line) - len(line.lstrip())
            if line.lstrip().startswith("-"):                      # a list item and everything nested in it is not a setting
                if list_floor is None or lead < list_floor:      # keep the outermost list: its items nest deeper lists
                    list_floor = lead
                continue
            if list_floor is not None:
                if lead > list_floor:
                    continue
                list_floor = None
            m = re.match(r"^(\s*)([A-Za-z0-9_.\-]+):\s*(.*)$", body)
            if not m:
                continue
            indent = len(m.group(1))
            while yaml_stack and yaml_stack[-1][0] >= indent:
                yaml_stack.pop()
            if not m.group(3) or m.group(3).startswith("#"):
                yaml_stack.append((indent, m.group(2)))
                continue
            raw, used = _split_value(m.group(3))
            if raw[:1] in "[{|>&*!-" and raw[:1] != "-" or raw.startswith("- "):
                continue
            inner, quote, qs, qe = _quoted(raw)
            start = m.start(3) + qs
            setting = Setting(m.group(2), ".".join(k for _, k in yaml_stack), inner, quote, _doc_above(lines, number), True,
                              number, span=(start, start + len(inner)))
        else:
            sep = r"\s*=\s*" if fmt in ("equals", "ini") else r"\s+"
            m = re.match(rf"^(\s*)({IDENT}){sep}(.*)$", body)
            if not m:
                continue
            raw, used = _split_value(m.group(3))
            inner, quote, qs, qe = _quoted(raw)
            start = offset + m.start(3) + qs
            setting = Setting(m.group(2), section if fmt == "ini" else "", inner, quote, _doc_above(lines, number), active,
                              number, span=(start, start + len(inner)))
        if not setting.doc:
            trailing = re.search(r"\s#\s*(.+)$", line[setting.span[1]:])
            if trailing:
                setting.doc = trailing.group(1).strip()
        settings.append(setting)

    # a commented example of a directive that is also set (`# port 0` next to `port 6379`) is documentation, not a second setting;
    # and only the first of several commented examples of one key is kept
    active_paths = {s.path for s in settings if s.active}
    kept, example_paths = [], set()
    for s in settings:
        if not s.active and (s.path in active_paths or s.path in example_paths):
            continue
        example_paths.add(s.path) if not s.active else None
        kept.append(s)
    settings = kept
    seen = {}
    for s in settings:
        if s.path in seen:
            s.repeated = seen[s.path].repeated = True          # directives that may repeat (`save`, `include`) are not parameters
        seen.setdefault(s.path, s)
    return settings, lines


# ------------------------------------------------------------------------------------------------------ inference

def infer(setting, hints=None, lines=None):
    """Fills `type`, `secret` and the default policy of a setting."""
    hints = hints or {}
    value, key = setting.value.strip(), setting.key
    setting.secret = bool(SECRET_RE.search(key))
    if setting.path in hints or key in hints:
        setting.type = hints.get(setting.path, hints.get(key))
        return
    if setting.secret:
        setting.type = "secret"
    elif lines is not None and _enum_from_doc(lines, setting.line, value):
        setting.type = "enum:" + ",".join(_enum_from_doc(lines, setting.line, value))
    elif re.fullmatch(r"-?\d+", value):
        if re.search(r"(^|[_.\-])port$", key, re.I) and 0 < int(value) < 65536 and setting.active \
                and not re.search(r"\b(port\s+)?0\b", setting.doc):
            # 1..65535. Not for a documented `0` (= disabled) nor for an optional (commented) one, where "off" is a likely value
            setting.type = "port"
        else:
            setting.type = "uint" if int(value) >= 0 else "int"
    elif value.lower() in {x for pair in BOOL_PAIRS for x in pair}:
        pair = next(p for p in BOOL_PAIRS if value.lower() in p)
        order = sorted(pair, key=lambda w: w not in ("yes", "on", "true", "enabled"))
        order = [w.upper() if value.isupper() else w for w in order]
        setting.type = "enum:" + ",".join(order)
    elif value.startswith(("/", "./", "~/")) or re.search(r"(dir|directory|file|path)$", key, re.I) and "/" in value:
        setting.type = "path"
    else:
        setting.type = "string"


def env_name(package, setting):
    base = re.sub(r"[^A-Za-z0-9]+", "_", setting.path).strip("_").upper()
    return f"BH_{re.sub(r'[^A-Za-z0-9]+', '_', package).upper()}_{base}"


def select(settings, include="", exclude="", maximum=0, also=""):
    """Candidates to expose: not repeated, matching --include (a regex on the key or section.key), not --exclude.
    With `also`, settings found only as commented defaults are taken just when they match it (the active ones all stay)."""
    chosen = []
    for s in settings:
        if s.repeated:
            continue
        if not s.active and also:
            if not re.search(also, s.path):
                continue
        elif include and not re.search(include, s.path):
            continue
        if exclude and re.search(exclude, s.path):
            continue
        chosen.append(s)
    return chosen[:maximum] if maximum else chosen


def short(text, limit=160):
    text = re.sub(r"\s+", " ", text.replace("|", "/")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    sentence = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:sentence + 1] if sentence > 40 else cut).rstrip(",;:") + ("" if sentence > 40 else "...")


def env_entries(package, settings, fmt, hints=None, lines=None):
    """runtime.env entries (dicts) for `settings`, names made unique."""
    entries, used = [], set()
    for s in settings:
        infer(s, hints, lines)
        name = env_name(package, s)
        while name in used:
            name += "_2"
        used.add(name)
        s.name = name
        entry = {"name": name, "type": s.type}
        if not s.secret and s.value != "" and s.active:           # a commented value is an example, not a default
            entry["default"] = int(s.value) if s.type in ("int", "uint", "port") else s.value
        if fmt in ("ini", "equals", "yaml"):
            entry["file_key"] = s.path
        entry["cli"] = "--" + re.sub(r"[^a-z0-9]+", "-", s.path.lower()).strip("-")
        if s.doc:
            entry["description"] = short(s.doc)
        entries.append(entry)
    return entries


# ------------------------------------------------------------------------------------------------------ template

def render_template(lines, settings, fmt):
    """The file with each chosen value replaced by its placeholder. A commented default becomes an active line. A setting
    without a default is emitted only when its parameter is set: `{{? NAME }}key {{ NAME }}`."""
    chosen = {s.line: s for s in settings if s.name}
    out = []
    for number, line in enumerate(lines):
        s = chosen.get(number)
        if s is None:
            out.append(line)
            continue
        optional = s.secret or s.value == "" or not s.active
        start, end = s.span
        if s.active:
            new = line[:start] + "{{ " + s.name + " }}" + line[end:]
        else:                                                    # `#key = value` -> `key = value`
            hash_at = line.index("#")
            new = line[:hash_at] + line[hash_at + 1:].lstrip(" ")
            shift = len(line) - len(new)
            new = new[:start - shift] + "{{ " + s.name + " }}" + new[end - shift:]
        if optional:
            indent = re.match(r"^\s*", new).group(0)
            new = indent + "{{? " + s.name + " }}" + new[len(indent):]
        out.append(new)
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------------------------------ merge

def _unambiguous(value):
    """A string that YAML 1.1 (PyYAML) would read as something else (`yes`, `no`, `on`, `1e3`, `null`) is double quoted,
    so the default of a setting reaches the bundle exactly as the application writes it."""
    import yaml
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString

    if not isinstance(value, str) or not value.strip():
        return value
    try:
        reinterpreted = not isinstance(yaml.safe_load(value), str)
    except yaml.YAMLError:
        return value                                   # not a plain scalar: the dumper quotes it itself
    return DoubleQuotedScalarString(value) if reinterpreted else value


def merge_into_packages(packages_file, package, entries, template=None):
    """Adds the missing entries to package.<package>.runtime.env (and the template to runtime.templates).
    Existing entries are never modified, so hand edits survive a re-run. Returns (added env names, template added?)."""
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    path = Path(packages_file)
    data = yaml.load(path.read_text())
    try:
        runtime = data["package"][package].setdefault("runtime", CommentedMap())
    except KeyError:
        raise ScaffoldError(f"package '{package}' is not in {packages_file}: declare it first (variant, git, commands)") from None
    env = runtime.setdefault("env", CommentedSeq())
    have = {item["name"] for item in env}
    added = []
    for entry in entries:
        if entry["name"] in have:
            continue
        item = CommentedMap({k: (_unambiguous(v) if k == "default" else v) for k, v in entry.items()})
        env.append(item)
        added.append(entry["name"])
    template_added = False
    if template:
        templates = runtime.setdefault("templates", CommentedSeq())
        if not any(t.get("src") == template["src"] for t in templates):
            templates.append(CommentedMap(template))
            template_added = True
    with open(path, "w") as handle:
        yaml.dump(data, handle)
    return added, template_added
