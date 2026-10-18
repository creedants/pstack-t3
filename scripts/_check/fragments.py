import re
import urllib.parse
from pathlib import Path

from .vocabulary import Layout

FRAGMENT_DIRS = ("brigade", "brigade-admin", "pstack-runtime")
# A markdown link target holding a #, in-file (#x) or file-plus-fragment (other.md#x).
# The destination holds no space. An optional title in double quotes, single quotes, or parens is excluded.
# A backslash escapes the next character inside a title.
FRAGMENT_LINK = re.compile(r"\]\(\s*([^\s)]*#[^\s)]*)\s*(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\((?:[^)\\]|\\.)*\))?\s*\)")
VENDOR_SKILLS_PATH = Path(__file__).resolve().parents[2] / "vendor/pstack/skills"


def heading_slug(heading):
    kept = "".join(char for char in heading.lower() if char.isalnum() or char in " -")
    return kept.replace(" ", "-")


_BLOCKQUOTE_PREFIX = re.compile(r"^ {0,3}> ?")
_LIST_PREFIX = re.compile(r"^ {0,3}(?:[-+*]|\d+[.)])")
_BACKTICK_RUN = re.compile(r"`+")


def _list_content(line):
    """Marker indent and content indent of a list item line, or None.

    The marker is one of -, +, or *, or a number with . or ). Trailing spaces
    after the marker count toward the content indent. Five or more count as one.
    """
    match = _LIST_PREFIX.match(line)
    if match is None:
        return None
    rest = line[match.end():]
    if rest != "" and rest[0] not in " \t":
        return None
    indent = len(line) - len(line.lstrip(" "))
    width = match.end() - indent
    if rest.startswith("\t"):
        content = (indent + width + 4) // 4 * 4
    else:
        spaces = len(rest) - len(rest.lstrip(" "))
        if spaces == 0 or spaces > 4:
            spaces = 1
        content = indent + width + spaces
    return indent, content


def _fence_open(line):
    if line.startswith("\t"):
        return None
    count = len(line) - len(line.lstrip(" "))
    if count > 3:
        return None
    rest = line[count:]
    if not rest.startswith("```") and not rest.startswith("~~~"):
        return None
    marker = rest[0]
    run = 0
    while run < len(rest) and rest[run] == marker:
        run += 1
    if run < 3:
        return None
    after = rest[run:]
    if marker == "`" and "`" in after:
        return None
    return (marker, run)


def _is_fence_close(line, marker, length):
    if line.startswith("\t"):
        return False
    count = len(line) - len(line.lstrip(" "))
    if count > 3:
        return False
    rest = line[count:]
    if not rest.startswith(marker):
        return False
    run = 0
    while run < len(rest) and rest[run] == marker:
        run += 1
    if run < length:
        return False
    return rest[run:].strip() == ""


def _visible_lines(text):
    """Yield source line numbers with container prefixes stripped, skipping fenced lines.

    One scan drives both headings and links. It yields each visible line number
    and content with the container prefix stripped. At most one container level is stripped per line. That is
    one blockquote marker plus one optional space, or one list indent through its
    content indent. Fence indentation is measured within the stripped content. A fence
    records its container kind and indent. It ends at its containing block boundary.
    A list fence ends at a new item at the same level or a line outside the item.
    A blockquote fence ends at a non-blank line with no quote marker. An unclosed fence outside
    any container hides the rest of the file.
    """
    fence = None
    item = None
    for number, line in enumerate(text.splitlines(), 1):
        current = line
        while True:
            if fence is None:
                if current.strip() == "":
                    yield number, current
                    break
                if current.startswith("\t"):
                    yield number, current
                    break
                quote = _BLOCKQUOTE_PREFIX.match(current)
                if quote is not None:
                    item = None
                    stripped = current[quote.end():]
                    opened = _fence_open(stripped)
                    if opened is None:
                        yield number, stripped
                    else:
                        fence = (*opened, "quote", 0, 0)
                    break
                listed = _list_content(current)
                if listed is not None:
                    item = listed
                    stripped = current[listed[1]:]
                    opened = _fence_open(stripped)
                    if opened is None:
                        yield number, stripped
                    else:
                        fence = (*opened, "list", listed[0], listed[1])
                    break
                if item is not None and current[: item[1]].strip() == "":
                    stripped = current[item[1]:]
                    opened = _fence_open(stripped)
                    if opened is None:
                        yield number, stripped
                    else:
                        fence = (*opened, "list", item[0], item[1])
                    break
                item = None
                opened = _fence_open(current)
                if opened is None:
                    yield number, current
                else:
                    fence = (*opened, "plain", 0, 0)
                break
            marker, length, kind, findent, fcontent = fence
            if current.strip() == "" or current.startswith("\t"):
                break
            if kind == "quote":
                quote = _BLOCKQUOTE_PREFIX.match(current)
                if quote is None:
                    fence = None
                    item = None
                    continue
                if _is_fence_close(current[quote.end():], marker, length):
                    fence = None
                break
            if kind == "list":
                listed = _list_content(current)
                if listed is not None:
                    if listed[0] <= findent:
                        fence = None
                        continue
                    break
                if current[:fcontent].strip() == "":
                    if _is_fence_close(current[fcontent:], marker, length):
                        fence = None
                    break
                fence = None
                continue
            if _is_fence_close(current, marker, length):
                fence = None
            break


def _strip_code_spans(line):
    """Remove single-line code spans from a line.

    Maximal backtick runs scan left to right. A span is removed only between
    complete runs of equal length. A run with no equal partner stays literal.
    """
    runs = [(match.start(), match.end() - match.start()) for match in _BACKTICK_RUN.finditer(line)]
    kept = []
    pos = 0
    index = 0
    while index < len(runs):
        start, length = runs[index]
        closer = index + 1
        while closer < len(runs) and runs[closer][1] != length:
            closer += 1
        if closer == len(runs):
            kept.append(line[pos : start + length])
            pos = start + length
            index += 1
        else:
            kept.append(line[pos:start])
            pos = runs[closer][0] + length
            index = closer + 1
    kept.append(line[pos:])
    return "".join(kept)


def _fragment_link_targets(line):
    return FRAGMENT_LINK.findall(_strip_code_spans(line))


def _heading_slugs(text):
    slugs = set()
    for _number, line in _visible_lines(text):
        if line.startswith("\t"):
            continue
        count = len(line) - len(line.lstrip(" "))
        if count > 3:
            continue
        stripped = line[count:]
        if not stripped.startswith("#"):
            continue
        base = heading_slug(stripped.lstrip("#").strip())
        slug = base
        counter = 1
        while slug in slugs:
            slug = f"{base}-{counter}"
            counter += 1
        slugs.add(slug)
    return slugs


def _vendor_skill_names(vendor_path):
    if not vendor_path.is_dir():
        return frozenset()
    return frozenset(path.name for path in vendor_path.iterdir() if path.is_dir())


def fragment_findings(root, layout=None):
    """Report a fragment link whose fragment names no heading in its target after slugging.

    The scan covers every markdown file under the tree's brigade,
    brigade-admin, and pstack-runtime skill directories, at any depth. A link
    is a markdown link target that holds a #. Single-line code spans are removed before
    link extraction. A span is removed only between complete runs of equal backtick
    length. An optional title after the destination is recognized and
    excluded from the destination. A title may hold escaped delimiters. A shared fence scanner hides fenced lines.
    It tracks marker character and opening length. It recognizes tilde and
    backtick fences. It closes on a matching fence of sufficient length.
    It allows up to three spaces indent inside one blockquote or list level.
    An unclosed fence ends at its containing block boundary. Outside any container
    it hides the rest of the file. A link or heading inside a fence is not seen.
    A percent-encoded fragment is decoded before matching. A destination query
    is stripped before resolving. The finding names the decoded fragment and
    the resolved file. A link whose
    fragment is empty, as in (other.md#),
    is skipped. A link with no # is not extracted. A link whose path before the # is empty or holds only a query,
    as in (#terms) or (?raw=1#terms), is
    checked against the file that holds it. Otherwise the query is stripped and the path resolves against
    the holding file's directory. A path that does not resolve to an existing
    file, one whose target is not a .md file, one whose target is outside the
    tree, and one whose target .md lives in a vendor-derived skill are skipped.
    A skill is vendor-derived when its directory name is a directory name under
    vendor/pstack/skills in the checkout that holds this file. The heading slug
    lowercases the heading, keeps only alphanumeric characters, spaces, and
    hyphens, and replaces each space with a hyphen, after stripping the leading
    # characters and surrounding whitespace. Anchors are allocated in document
    order. The first heading keeps its slug. A duplicate appends -1, then -2,
    until the anchor is free. A heading whose natural slug is taken gets the
    next free suffix. A fragment that is not one of the
    target's slugs is a finding that names the holding file relative to the
    tree, the line, the fragment, and the target file relative to the tree.
    An empty list means no checked fragment link failed.
    """
    root = Path(root).resolve()
    vendor = _vendor_skill_names(layout.vendor_skills_path if layout is not None else VENDOR_SKILLS_PATH)
    slugs_by_file = {}
    findings = []
    for name in FRAGMENT_DIRS:
        for file in sorted((root / name).rglob("*.md")):
            if not file.is_file():
                continue
            rel = file.relative_to(root).as_posix()
            for number, line in _visible_lines(file.read_text()):
                for target in _fragment_link_targets(line):
                    path, raw_fragment = target.split("#", 1)
                    if raw_fragment == "":
                        continue
                    fragment = urllib.parse.unquote(raw_fragment)
                    if path == "":
                        target_file = file
                    else:
                        clean_path = path.split("?", 1)[0]
                        if clean_path == "":
                            target_file = file
                        else:
                            target_file = (file.parent / clean_path).resolve()
                            if not target_file.is_file() or target_file.suffix != ".md":
                                continue
                    try:
                        target_rel = target_file.relative_to(root)
                    except ValueError:
                        continue
                    if target_rel.parts[0] in vendor:
                        continue
                    if target_file not in slugs_by_file:
                        slugs_by_file[target_file] = _heading_slugs(target_file.read_text())
                    if fragment not in slugs_by_file[target_file]:
                        findings.append(
                            f"{rel}:{number}: fragment #{fragment} names no heading in {target_rel.as_posix()}"
                        )
    return findings
