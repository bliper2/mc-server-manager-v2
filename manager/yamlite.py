"""Just enough YAML for two jobs: reading the metadata of a plugin.yml, and changing single values in a plugin's own
config file without losing its comments, ordering or indentation. It is not a general parser: a file it cannot make
sense of is left untouched and the caller is told."""

import re

KEY_LINE = re.compile(r"""^(?P<indent> *)(?P<key>"[^"\r\n]+"|'[^'\r\n]+'|[^\s#:\-"'\[\]{}][^:#\r\n]*?)[ \t]*:(?P<rest>(?:[ \t].*)?)$""")
BLOCK_MARKERS = {">", "|", ">-", "|-", ">+", "|+"}
CLOSED_QUOTE = re.compile(r'(?<!\\)"\s*$')  # a closing double quote that is not escaped, at the end of a line


def split_comment(rest: str):
    """('value part', ' # comment' or '') for what follows a key's colon; a # inside quotes is not a comment.
    The comment keeps the whitespace before it, so putting the two parts back together restores the line."""
    quote = None
    for index, char in enumerate(rest):
        if quote:
            if char == quote and not (quote == '"' and rest[index - 1] == "\\"):
                quote = None
        elif char in "\"'" and (index == 0 or rest[index - 1] in " 	"):
            quote = char
        elif char == "#" and (index == 0 or rest[index - 1] in " 	"):
            value = rest[:index].rstrip()
            return value, rest[len(value):]
    return rest.rstrip(), ""


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), value[1:-1])
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_scalar(raw: str):
    text = raw.strip()
    if text in ("", "~", "null"):
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return unquote(text)
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    if re.fullmatch(r"[-+]?\d*\.\d+(?:[eE][-+]?\d+)?", text):
        return float(text)
    return text


def format_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError("A single-line value is expected")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _lines(text: str):
    return text.splitlines(keepends=True)


def find_path(lines, path):
    """(line index, match) of the key at `path` (a list of nested keys), or None. List items and block text are skipped."""
    stack = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = KEY_LINE.match(line.rstrip("\r\n"))
        if not match:
            continue
        indent, key = len(match.group("indent")), unquote(match.group("key"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if [name for _, name in stack] == list(path):
            return index, match
    return None


def get_value(text: str, path):
    """The scalar at `path`, or raises KeyError. A key that holds a nested map or a list raises ValueError."""
    found = find_path(_lines(text), path)
    if found is None:
        raise KeyError(".".join(path))
    value, _ = split_comment(found[1].group("rest"))
    if not value.strip():
        raise ValueError(f"{'.'.join(path)} is not a single value")
    return parse_scalar(value)


def set_value(text: str, path, value) -> str:
    """`text` with the scalar at `path` replaced, its comment and layout kept. KeyError if the key is missing."""
    lines = _lines(text)
    found = find_path(lines, path)
    if found is None:
        raise KeyError(".".join(path))
    index, match = found
    current, comment = split_comment(match.group("rest"))
    if not current.strip() or current.strip() in BLOCK_MARKERS:
        raise ValueError(f"{'.'.join(path)} is not a single value")
    line = lines[index]
    ending = line[len(line.rstrip("\r\n")):]
    body = line.rstrip("\r\n")
    head = body[:match.start("rest")]
    lines[index] = f"{head} {format_scalar(value)}{comment}{ending}"
    return "".join(lines)


def top_level(text: str) -> dict:
    """The top-level keys of a document as {key: string}: scalars, the first line of a block scalar (joined), and inline lists as raw text."""
    result = {}
    lines = _lines(text)
    index = 0
    while index < len(lines):
        raw = lines[index].rstrip("\r\n")
        match = KEY_LINE.match(raw)
        index += 1
        if not match or match.group("indent"):
            continue
        key = unquote(match.group("key"))
        value, _ = split_comment(match.group("rest"))
        value = value.strip()
        if value in BLOCK_MARKERS:
            parts = []
            while index < len(lines) and (not lines[index].strip() or lines[index].startswith((" ", "\t"))):
                parts.append(lines[index].strip())
                index += 1
            result[key] = " ".join(part for part in parts if part)
        elif value.startswith('"') and not CLOSED_QUOTE.search(value[1:]):
            # a double-quoted value that carries on over the next lines; a backslash at the end of a line joins them without a space
            text = value[1:]
            while index < len(lines):
                more = lines[index].strip()
                index += 1
                text = text[:-1] + more if text.endswith("\\") else f"{text} {more}"
                if CLOSED_QUOTE.search(more):
                    break
            result[key] = unquote('"' + text)
        else:
            result[key] = unquote(value) if value and value[0] in "\"'" else value
    return result


def block_list(text: str, key: str) -> list:
    """The strings under a top-level key: an inline list, a single value, or a block list (dashes may sit at the key's indent)."""
    lines = _lines(text)
    for index, line in enumerate(lines):
        match = KEY_LINE.match(line.rstrip("\r\n"))
        if not match or match.group("indent") or unquote(match.group("key")) != key:
            continue
        value, _ = split_comment(match.group("rest"))
        value = value.strip()
        if value.startswith("["):
            return [unquote(part) for part in value.strip("[]").split(",") if unquote(part)]
        if value:
            return [unquote(value)]
        items = []
        for later in lines[index + 1:]:
            item = re.match(r"^\s*-\s*(\S.*?)\s*$", later.rstrip("\r\n"))
            if item:
                items.append(unquote(split_comment(item.group(1))[0]))
            elif later.strip() and not later.lstrip().startswith("#"):
                break
        return items
    return []
