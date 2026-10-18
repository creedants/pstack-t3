import json
import re
from pathlib import Path

from .vocabulary import REQUIRED_TOOLS, SETUP_GATE
_VERSION_TOKEN = r"[0-9]+(?:\.[0-9]+)+"
_BADGE_LINE = re.compile(
    "(?P<indent>[ \t]*)"
    '<a href="upstream.json"><img src="https://img\\.shields\\.io/badge/upstream%20pstack-'
    "(?P<url>" + _VERSION_TOKEN + ")"
    '-555\\.svg" alt="upstream pstack '
    "(?P<alt>" + _VERSION_TOKEN + ")"
    '"></a>'
    "(?P<trail>[ \t]*)"
)

def _split_lines(text):
    parts = []
    for raw in text.splitlines(keepends=True):
        if raw.endswith("\r\n"):
            parts.append((raw[:-2], "\r\n"))
        elif raw.endswith("\n") or raw.endswith("\r"):
            parts.append((raw[:-1], raw[-1:]))
        else:
            parts.append((raw, ""))
    return parts


def _hits(text):
    hits = []
    for content, _ending in _split_lines(text):
        match = _BADGE_LINE.fullmatch(content)
        if match:
            hits.append(match)
    return hits


def _readme_text(path):
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def _pin_version(path):
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    if not isinstance(version, str) or version == "" or "\n" in version:
        return None
    return version


def rewritten_readme(path, version):
    if not isinstance(version, str) or re.fullmatch(_VERSION_TOKEN, version) is None:
        return None
    text = _readme_text(path)
    if text is None:
        return None
    parts = _split_lines(text)
    hit_index = None
    hit_match = None
    for index, (content, _ending) in enumerate(parts):
        match = _BADGE_LINE.fullmatch(content)
        if match:
            if hit_index is not None:
                return None
            hit_index = index
            hit_match = match
    if hit_index is None or hit_match is None:
        return None
    content, ending = parts[hit_index]
    url_start, url_end = hit_match.span("url")
    alt_start, alt_end = hit_match.span("alt")
    content = content[:alt_start] + version + content[alt_end:]
    content = content[:url_start] + version + content[url_end:]
    parts[hit_index] = (content, ending)
    return "".join(content + ending for content, ending in parts)


def badge_findings(readme, upstream):
    pin = _pin_version(upstream)
    if pin is None:
        return ["upstream.json has no version"]
    text = _readme_text(readme)
    hits = _hits(text) if text is not None else []
    if len(hits) != 1:
        return [f"README.md has no upstream badge and upstream.json version is {pin}"]
    url = hits[0].group("url")
    alt = hits[0].group("alt")
    if url == pin and alt == pin:
        return []
    return [f"README.md badge url {url} alt {alt} disagrees with upstream.json version {pin}"]


# `doctor` prints `{installed}/{expected} pstack-t3` for one harness.
_QUOTED_COUNT = re.compile(r"(\d+)/(\d+) pstack-t3")
# README states how many skills its link to docs/skills.md covers.
_STATED_COUNT = re.compile(r"\ball (\d+) skills\b")


def _shipped_skill_count(root):
    return sum(1 for _skill in root.glob("*/SKILL.md"))


def guide_count_findings(guide, root):
    """check_tree calls this only with its guide flag true, which the build and a bare CLI run keep and an explicit tree argument clears.

    The shipped count is every SKILL.md entry directly in a directory of the
    tree. A quote is a pair of numbers with a slash between them and pstack-t3
    after them, the shape link_health in scripts/install.py prints. A quote
    whose two numbers differ, or whose first number differs from the shipped
    count, is a finding that names the guide, the line, the quoted count, and
    the shipped count. A guide that quotes no doctor count is one finding. A
    guide that cannot be read is one finding and no line is read. An empty
    list means every quote agrees with the shipped count.
    """
    try:
        text = guide.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"docs/guide.md: could not be read, so its doctor count was not checked: {type(error).__name__}"]
    shipped = _shipped_skill_count(root)
    findings = []
    quoted = False
    for number, line in enumerate(text.splitlines(), 1):
        for match in _QUOTED_COUNT.finditer(line):
            quoted = True
            if match.group(1) != match.group(2) or int(match.group(1)) != shipped:
                findings.append(
                    f"docs/guide.md:{number}: quotes the doctor count {match.group(1)}/{match.group(2)}, "
                    f"and the shipped skill count is {shipped}"
                )
    if not quoted:
        findings.append(f"docs/guide.md: quotes no doctor count, and the shipped skill count is {shipped}")
    return findings


def readme_count_findings(readme, root):
    """check_tree calls this only with its guide flag true, which the build and a bare CLI run keep and an explicit tree argument clears.

    The shipped count is every SKILL.md entry directly in a directory of
    the tree. A stated count is the digits between `all ` and ` skills`
    where `all` starts at a word boundary and `skills` ends at one, the
    phrase the link to docs/skills.md in README.md uses. The match is case
    sensitive. A stated count that differs from the shipped count is a
    finding that names README.md, the line, the stated count, and the
    shipped count. A README that states no skill count is one finding. A
    README that cannot be read is one finding and no line is read. An empty
    list means every stated count agrees with the shipped count.
    """
    try:
        text = readme.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"README.md: could not be read, so its skill count was not checked: {type(error).__name__}"]
    shipped = _shipped_skill_count(root)
    findings = []
    stated = False
    for number, line in enumerate(text.splitlines(), 1):
        for match in _STATED_COUNT.finditer(line):
            stated = True
            if int(match.group(1)) != shipped:
                findings.append(
                    f"README.md:{number}: states all {match.group(1)} skills, and the shipped skill count is {shipped}"
                )
    if not stated:
        findings.append(f"README.md: states no skill count, and the shipped skill count is {shipped}")
    return findings


# The setup gate string in REQUIRED_TOOLS names the minimum T3 Code version.
_GATE_MINIMUM = re.compile(r"T3 Code (" + _VERSION_TOKEN + r") or later")
# A document states the minimum with T3 Code, an optional Markdown link, the version in
# optional backticks, then "or later", the shape README.md and docs/guide.md use.
_NAMED_MINIMUM = re.compile(
    r"T3 Code(?:\]\([^)\n]*\))?\s+`?(" + _VERSION_TOKEN + r")`?\s+or later\b"
)


def _setup_gate_version():
    """The minimum T3 Code version in the setup gate string REQUIRED_TOOLS requires in setup-pstack/SKILL.md. None when no required entry matches that string."""
    for sentence in REQUIRED_TOOLS.get(SETUP_GATE, ()):
        match = _GATE_MINIMUM.fullmatch(sentence)
        if match:
            return match.group(1)
    return None


def minimum_version_findings(readme, guide):
    """Report a minimum T3 Code version stated in README.md or docs/guide.md that differs from the setup gate's.

    The gate version comes from the setup gate string REQUIRED_TOOLS requires in
    setup-pstack/SKILL.md. A stated version is the one a line names between T3 Code
    and "or later", in a plain sentence or a Markdown link with optional backticks.
    A stated version that differs from the gate is one finding that names the file,
    the line, the stated version, and the gate version. A gate string that no entry
    under SETUP_GATE fully matches is one finding that names the setup skill and
    says the minimum version could not be read. A file that cannot be read adds no
    finding, because readme_count_findings or guide_count_findings already names it.
    An empty list means no stated minimum in a readable file differs from the gate version.
    """
    gate = _setup_gate_version()
    if gate is None:
        return [f"{SETUP_GATE}: the setup gate's T3 Code minimum version could not be read"]
    findings = []
    for label, path in (("README.md", readme), ("docs/guide.md", guide)):
        for number, line in enumerate((_readme_text(path) or "").splitlines(), 1):
            for match in _NAMED_MINIMUM.finditer(line):
                if match.group(1) != gate:
                    findings.append(
                        f"{label}:{number}: states the T3 Code minimum version {match.group(1)}, "
                        f"and the setup gate requires {gate}"
                    )
    return findings


