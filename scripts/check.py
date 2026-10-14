#!/usr/bin/env python3
"""Lint a generated skills tree for Cursor-only leftovers and broken structure, and the test modules for the git environment fence.

check_tree judges the tree it is given, and when that tree contains `pstack-runtime/scripts/roles.py` it also reports library wording, machine values, and shared source for the checkout that contains this file. In that same case it reports a doctor count quoted in that checkout's `docs/guide.md` that disagrees with the number of skills in the tree. A tree without that file gets none of those findings. badge_findings reports a README upstream badge that is missing or that disagrees with the version in upstream.json. The default CLI run and the build add that repository check. An explicit tree argument gets no badge finding.
"""

import ast
import dataclasses
import importlib.util
import inspect
import json
import os
import pwd
import re
import socket
import sys

try:
    import yaml
except ImportError:  # the build still runs; frontmatter gets the shallow check only
    yaml = None
from pathlib import Path

# Each pair is a pattern and the reason in the finding.
FORBIDDEN = [
    (r"subagent_type", "Cursor Task schema; use delegate_task with role and target"),
    (r"environment: ?\"(?:cloud|local)\"", "Cursor cloud agents; T3 children run locally"),
    (r"cloud_base_branch", "Cursor cloud agents; name the branch in the brief"),
    (r"run_in_background", "Cursor Task flag; use delegate_task mode async"),
    (r"`Task`|Task tool|Task call|Task subagent|Task `model`", "Cursor Task tool; use delegate_task"),
    (r"pstack-models\.mdc|\.cursor/rules", "Cursor rule file; use roles.py and roles.json"),
    (r"(?<![\w/])/loop\b", "Cursor /loop; use schedule_task"),
    (r"AskQuestion", "Cursor question tool; use the host's question tool"),
    (r"cursor-team-kit|Cursor's built-in", "Cursor plugin dependency"),
    (r"agent-transcripts", "Cursor transcript store; use t3_thread_search and t3_thread_read"),
    (r"inherit-parent", "Cursor alias; use inherit"),
    (r"\b(?:claude-opus-5-5-max|claude-opus-5-5-xhigh|claude-fable-5-1-thinking-\w+|gpt-5\.6-sol-max|grok-4\.7-xhigh-fast|claude-opus-5-thinking-\w+)\b", "hard-coded Cursor model slug; resolve a role"),
    (r"cursor\.com/agents|cloud agent", "Cursor cloud agents; T3 child tasks or threads"),
    (
        r"(?i)\b(?:pick|choose|select|raise|lower|change|use|set)\b"
        r"(?:(?![.!?]).){0,180}?"
        r"(?:reasoning level|effort level|fast or standard speed|fast mode|standard speed)"
        r"|\b(?:pick|choose|select)\b(?:(?![.!?]).){0,60}?\beffort\b",
        "pick, choose, select, raise, lower, change, use, or set has at most 180 intervening characters before reasoning level, effort level, fast or standard speed, fast mode, or standard speed, "
        "or pick, choose, or select has at most 60 intervening characters before effort; both matches are case-insensitive, stay on one line, and have no '.', '!', or '?' between the verb and dial; "
        "point at the Subagents section through an override",
    ),
]
FRONTMATTER_REQUIRED = ("name", "description")
# Generated files that teach a PR wait must name T3's watch tools. Absent files are not checked.
REQUIRED_TOOLS = {
    "pstack-runtime/SKILL.md": ("watch_pull_request", "unwatch_pull_request"),
    "landing/SKILL.md": ("watch_pull_request", "unwatch_pull_request"),
    "brigade/SKILL.md": ("watch_pull_request", "unwatch_pull_request"),
    "poteto-mode/playbooks/babysit.md": ("watch_pull_request",),
    "poteto-mode/playbooks/shipping.md": ("watch_pull_request",),
    "poteto-mode/playbooks/autonomous-run.md": ("watch_pull_request",),
    "poteto-mode/playbooks/orchestrate.md": ("watch_pull_request",),
    "poteto-mode/playbooks/autopilot-full.md": ("watch_pull_request",),
    "poteto-mode/playbooks/autopilot-stack.md": ("watch_pull_request",),
    "setup-pstack/SKILL.md": ("watch_pull_request", "0.0.46-nightly.20261005.2702"),
}
LINK = re.compile(r"\]\(((?!https?:|mailto:|#)[^)\s]+)\)")
# A T3 thread link is `t3-thread://v1/<threadId>`. It names no file.
THREAD_LINK = re.compile(r"t3-thread://v1/[^)\s]")
# Bash ends a <<'JSON' body only on a line that is JSON and nothing else.
CATALOG_HEREDOC_OPEN = "<<'JSON'"
CATALOG_HEREDOC_CLOSER = "JSON"

ROLES_PY = Path(__file__).resolve().parents[1] / "t3/scripts/roles.py"
TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"
README_PATH = Path(__file__).resolve().parents[1] / "README.md"
UPSTREAM_PATH = Path(__file__).resolve().parents[1] / "upstream.json"
GUIDE_PATH = Path(__file__).resolve().parents[1] / "docs/guide.md"
# Every tests/test_*.py must carry fence_git_environment as it is in the reference module, and a setUpModule that contains a call to it.
FENCE_REFERENCE = "test_landing.py"
FENCE_FUNCTION = "fence_git_environment"
FENCE_SETUP = "setUpModule"
FENCE_BRIGADE_DIR = "brigade"
FENCE_BRIGADE_SUPPORT = "support.py"
FENCE_BRIGADE_MODULE = "tests.brigade.support"
LIGHT_HEADER_CELLS = frozenset({"light", "light mode", "light behavior", "in light mode"})
LIGHT_MODE_NAME = re.compile(r"(?i:\blight mode\b)|Mode: light")
TABLE_SEPARATOR = re.compile(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?")
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


def guide_count_findings(guide, root):
    """Report a doctor count quoted in the guide that disagrees with the tree's skill count.

    The shipped count is the set skill_names in scripts/install.py reads,
    every directory in the tree that holds a SKILL.md. A quote is a pair of
    numbers with a slash between them and pstack-t3 after them, the shape
    link_health in scripts/install.py prints. A quote whose two numbers
    differ, or whose first number differs from the shipped count, is a
    finding that names the guide, the line, the quoted count, and the
    shipped count. A guide that quotes no doctor count is one finding. A
    guide that cannot be read is one finding and no line is read. An empty
    list means every quote agrees with the shipped count.
    """
    try:
        text = guide.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"docs/guide.md: could not be read, so its doctor count was not checked: {type(error).__name__}"]
    shipped = sum(1 for _skill in root.glob("*/SKILL.md"))
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


_catalog_module = None


def _catalog():
    global _catalog_module
    if _catalog_module is None:
        spec = importlib.util.spec_from_file_location("pstack_catalog", Path(__file__).resolve().parent / "catalog.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _catalog_module = module
    return _catalog_module


def light_waiver_cells():
    spec = importlib.util.spec_from_file_location("pstack_roles", ROLES_PY)
    roles = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(roles)
    return tuple(sorted({", ".join(steps) for steps in roles.LIGHT_WAIVERS.values() if len(steps) >= 2}))


def _header_cells(line):
    return [cell.replace("`", "").replace("*", "").strip().casefold() for cell in line.strip().strip("|").split("|")]


def light_table_findings(rel_s, text, cells):
    findings = []
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        restated = any(cell in line for cell in cells)
        if line.lstrip().startswith("|") and number < len(lines) and TABLE_SEPARATOR.fullmatch(lines[number].strip()):
            header = _header_cells(line)
            restated = restated or bool(LIGHT_HEADER_CELLS.intersection(header)) or (
                "playbook" in header and any(cell == "first" or cell.startswith("fix") for cell in header)
            )
        if restated:
            findings.append(f"{rel_s}:{number}: light table restated. Link pstack-runtime/SKILL.md#modes instead of pasting it: {line.strip()[:120]}")
        elif LIGHT_MODE_NAME.search(line) and "pstack-runtime/SKILL.md#" not in line:
            findings.append(f"{rel_s}:{number}: names light mode without a link to the runtime's Modes section. Link pstack-runtime/SKILL.md#modes: {line.strip()[:120]}")
    return findings


def catalog_heredoc_findings(rel, text):
    findings = []
    in_body = False
    for number, line in enumerate(text.splitlines(), 1):
        if not in_body:
            if CATALOG_HEREDOC_OPEN in line:
                in_body = True
            continue
        if line == CATALOG_HEREDOC_CLOSER:
            in_body = False
            continue
        if line.strip() != CATALOG_HEREDOC_CLOSER:
            continue
        if line.lstrip() != line:
            findings.append(f"{rel}:{number}: catalog heredoc closer JSON is indented. Put JSON at column 0")
        else:
            findings.append(f"{rel}:{number}: catalog heredoc closer JSON has trailing whitespace. The closer is JSON with nothing after it")
    return findings


def _witness_skills_are_files(root):
    return (root / "pstack-runtime/SKILL.md").is_file() and (root / "setup-pstack/SKILL.md").is_file()


def check_tree(root):
    root = Path(root)
    findings = []
    cells = light_waiver_cells()
    witness = _witness_skills_are_files(root)
    for skill_md in sorted(root.glob("*/SKILL.md")):
        skill = skill_md.parent.name
        text = skill_md.read_text()
        if witness and len(text.split("---")) >= 3 and "disable-model-invocation" in text.split("---")[1]:
            findings.append(f"{skill}/SKILL.md: frontmatter contains disable-model-invocation")
        match = re.match(r"---\n(.*?)\n---\n", text, re.S)
        if not match:
            findings.append(f"{skill}/SKILL.md: missing frontmatter")
            continue
        try:
            keys = yaml.safe_load(match.group(1)) if yaml else dict(
                (k.strip(), v.strip().strip('"')) for k, v in (line.split(":", 1) for line in match.group(1).splitlines() if ":" in line and not line.startswith(" ")))
        except Exception as error:
            findings.append(f"{skill}/SKILL.md: frontmatter is not valid YAML: {str(error).splitlines()[0]}")
            continue
        if not isinstance(keys, dict):
            findings.append(f"{skill}/SKILL.md: frontmatter is not a mapping")
            continue
        for key in FRONTMATTER_REQUIRED:
            if not isinstance(keys.get(key), str) or not keys[key].strip():
                findings.append(f"{skill}/SKILL.md: frontmatter lacks a string {key}")
        if keys.get("name") != skill:
            findings.append(f"{skill}/SKILL.md: name must equal the directory name")
        if len(str(keys.get("description", ""))) > 1024:
            findings.append(f"{skill}/SKILL.md: description over 1024 characters")
    for file in sorted(root.rglob("*")):
        if not file.is_file() or file.suffix not in (".md", ".ts", ".mjs", ".sh", ".py", ".json", ".yaml", ".tsv"):
            continue
        rel = file.relative_to(root)
        text = file.read_text()
        rel_s = str(rel)
        for tool in REQUIRED_TOOLS.get(rel_s, ()):
            if tool not in text:
                findings.append(f"{rel_s}: missing {tool}")
        # The runtime's vocabulary table names each Cursor term it replaces.
        patterns = [] if rel_s == "pstack-runtime/SKILL.md" else FORBIDDEN
        for number, line in enumerate(text.splitlines(), 1):
            for pattern, why in patterns:
                if re.search(pattern, line):
                    findings.append(f"{rel}:{number}: {why}: {line.strip()[:120]}")
        findings.extend(catalog_heredoc_findings(rel, text))
        if file.suffix == ".md":
            if rel_s != "pstack-runtime/SKILL.md":
                findings.extend(light_table_findings(rel_s, text, cells))
            for target in LINK.findall(text):
                if THREAD_LINK.match(target):
                    continue
                path = target.split("#", 1)[0]
                # Template placeholders such as (url) are not paths.
                if "/" not in path and "." not in path:
                    continue
                if path and not (file.parent / path).resolve().exists():
                    findings.append(f"{rel}: broken link {target}")
    if (root / "poteto-mode/SKILL.md").is_file():
        findings.extend(_catalog().missing_playbooks(root))
    if witness and not (root / RENDERED_MARKER).is_file():
        findings.append(f"{RENDERED_MARKER.as_posix()}: is missing")
    if (root / RENDERED_MARKER).is_file():
        repo = Path(__file__).resolve().parents[1]
        findings.extend(library_wording_findings(repo / "tests"))
        findings.extend(machine_value_findings(repo / "ops", repo / "docs/operations.md", _machine_identity()))
        findings.extend(shared_source_findings(repo / "t3/scripts/roles.py", repo / "scripts/install.py"))
        findings.extend(guide_count_findings(GUIDE_PATH, root))
    return findings


def _module_function(module, name):
    """The last `def` of `name` directly in the module body. A later rebinding by assignment, import, or a block is not seen."""
    found = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name]
    return found[-1] if found else None


def _calls_name(function, name):
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name for node in ast.walk(function))


def _read_module(path):
    try:
        return ast.parse(path.read_text(), str(path)), None
    except (OSError, ValueError, SyntaxError) as error:
        return None, error


def _unreadable_finding(rel, error):
    return f"{rel}: could not be read or parsed, so its git environment fence was not checked: {type(error).__name__}"


def _module_fence_findings(module, rel, reference, sentence, label):
    findings = []
    fence = _module_function(module, FENCE_FUNCTION)
    setup = _module_function(module, FENCE_SETUP)
    if fence is None:
        findings.append(f"{rel}: has no def {FENCE_FUNCTION} directly in the module body. {sentence}")
    elif ast.dump(fence) != reference:
        findings.append(f"{rel}: {FENCE_FUNCTION} differs from the one in {label}/{FENCE_REFERENCE}. {sentence}")
    if setup is None:
        findings.append(f"{rel}: has no def {FENCE_SETUP} directly in the module body. {sentence}")
    elif not _calls_name(setup, FENCE_FUNCTION):
        findings.append(f"{rel}: {FENCE_SETUP} contains no call to the bare name {FENCE_FUNCTION}. {sentence}")
    return findings


def _is_support_import(node):
    relative = node.level == 1 and node.module == "support"
    absolute = node.level == 0 and node.module == FENCE_BRIGADE_MODULE
    return relative or absolute


def _target_binds(target, name):
    if isinstance(target, ast.Name):
        return target.id == name
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_target_binds(element, name) for element in target.elts)
    if isinstance(target, ast.Starred):
        return _target_binds(target.value, name)
    return False


def _setup_name_binding(statement):
    if isinstance(statement, ast.FunctionDef) and statement.name == FENCE_SETUP:
        return "def"
    if isinstance(statement, (ast.AsyncFunctionDef, ast.ClassDef)) and statement.name == FENCE_SETUP:
        return "assign"
    if isinstance(statement, ast.Assign) and any(_target_binds(target, FENCE_SETUP) for target in statement.targets):
        return "assign"
    if isinstance(statement, ast.AnnAssign) and statement.value is not None and _target_binds(statement.target, FENCE_SETUP):
        return "assign"
    if isinstance(statement, ast.Import):
        for alias in statement.names:
            if (alias.asname or alias.name.split(".", 1)[0]) == FENCE_SETUP:
                return "wrong"
        return None
    if not isinstance(statement, ast.ImportFrom):
        return None
    found = None
    for alias in statement.names:
        if (alias.asname or alias.name) != FENCE_SETUP:
            continue
        if alias.name != FENCE_SETUP:
            found = "alias"
        elif _is_support_import(statement):
            found = "import"
        else:
            found = "wrong"
    return found


def _renames_setup_away(statement):
    if not isinstance(statement, ast.ImportFrom):
        return False
    return any(alias.name == FENCE_SETUP and alias.asname not in (None, FENCE_SETUP) for alias in statement.names)


def _last_setup_binding(module):
    binding = None
    renamed = False
    for statement in module.body:
        found = _setup_name_binding(statement)
        if found is not None:
            binding = found
        elif _renames_setup_away(statement):
            renamed = True
    if binding is not None:
        return binding
    if renamed:
        return "alias"
    return None


def _imports_only_support_classes(module, class_names):
    if not module.body:
        return False
    for statement in module.body:
        if not isinstance(statement, ast.ImportFrom) or not _is_support_import(statement):
            return False
        if not statement.names or any(alias.name not in class_names for alias in statement.names):
            return False
    return True


def _area_findings(module, rel, reference, sentence, label, class_names):
    binding = _last_setup_binding(module)
    if binding == "import":
        return []
    if binding == "alias":
        return [f"{rel}: imports {FENCE_SETUP} under another name. {sentence}"]
    if binding == "wrong":
        return [f"{rel}: binds {FENCE_SETUP} by an import that is not from {FENCE_BRIGADE_MODULE}. {sentence}"]
    if binding == "assign":
        return [f"{rel}: binds {FENCE_SETUP} to something other than an import from {FENCE_BRIGADE_MODULE}. {sentence}"]
    if binding == "def":
        return _module_fence_findings(module, rel, reference, sentence, label)
    if _imports_only_support_classes(module, class_names):
        return []
    return [f"{rel}: has no {FENCE_SETUP} bound directly in the module body. {sentence}"]


def _brigade_findings(tests_dir, label, reference, copy):
    brigade = tests_dir / FENCE_BRIGADE_DIR
    if not brigade.is_dir():
        return []
    sentence = f"Import {FENCE_SETUP} from {label}/{FENCE_BRIGADE_DIR}/{FENCE_BRIGADE_SUPPORT}"
    support_rel = f"{label}/{FENCE_BRIGADE_DIR}/{FENCE_BRIGADE_SUPPORT}"
    findings = []
    support, error = _read_module(brigade / FENCE_BRIGADE_SUPPORT)
    if error is not None:
        findings.append(_unreadable_finding(support_rel, error))
        class_names = frozenset()
    else:
        class_names = frozenset(node.name for node in support.body if isinstance(node, ast.ClassDef))
        findings.extend(_module_fence_findings(support, support_rel, reference, copy, label))
    for file in sorted(brigade.glob("test_*.py")):
        if not file.is_file():
            continue
        rel = f"{label}/{FENCE_BRIGADE_DIR}/{file.name}"
        module, error = _read_module(file)
        if error is not None:
            findings.append(_unreadable_finding(rel, error))
            continue
        findings.extend(_area_findings(module, rel, reference, sentence, label, class_names))
    return findings


def fence_findings(tests_dir):
    """Name test modules whose git environment fence does not match tests/test_landing.py.

    The comparison value is ast.dump of the last def fence_git_environment
    directly in the module body. That dump ignores comments, whitespace, and
    line numbers and includes the docstring. Each top-level test_*.py module
    also needs a def setUpModule directly in the module body that calls the
    bare name fence_git_environment.

    A missing or unreadable test_landing.py, or one with no such function,
    returns that one finding and checks nothing else.

    An absent brigade directory adds no finding. When that directory is
    present, support.py uses the same rules and the same copy sentence as a
    top-level module. A missing or unreadable support.py reports that it could
    not be read or parsed and includes the exception type name. A missing file
    is FileNotFoundError. Each test_*.py file directly in that directory is an
    area module. Files in subdirectories are not checked.

    The last binding of the name setUpModule in an area module body decides.
    A from-import of that name from tests.brigade.support at level 0, or from
    .support at level 1, passes even when the import lists other names and the
    module defines a class with another name. A later statement that binds the
    name replaces that import. A from-import that binds the name setUpModule
    to a different imported name is an alias binding. A from-import that
    renames setUpModule away does not bind the name. That from-import after a
    binding does not replace that binding. The alias finding applies when no
    actual binding of the name was found. A from-import that binds the name
    from any other module is a finding. A plain import that binds the name
    setUpModule is that same other-module finding. A plain import that only
    renames setUpModule away does not bind the name. An assignment of the name
    is a finding. An annotated assignment with a value is a finding. An
    annotation with no value does not bind the name. A def setUpModule uses
    the top-level fence rules. An async def setUpModule or a class statement
    named setUpModule is a finding, including when a valid import came
    earlier. Those findings end with Import setUpModule from the brigade
    support path.

    Only a statement in the module body can bind the name. An area module that
    does not bind the name still passes when every body statement imports
    classes defined directly in support.py and there is at least one such
    import. Anything else that leaves the name unbound is a finding.

    Path labels and both sentences use the tests directory name.
    """
    tests_dir = Path(tests_dir)
    label = tests_dir.name
    reference_path = tests_dir / FENCE_REFERENCE
    copy = f"Copy {FENCE_FUNCTION} and {FENCE_SETUP} from {label}/{FENCE_REFERENCE}"
    reference = None
    if reference_path.is_file():
        try:
            reference_function = _module_function(ast.parse(reference_path.read_text(), str(reference_path)), FENCE_FUNCTION)
        except (OSError, ValueError, SyntaxError):
            reference_function = None
        reference = ast.dump(reference_function) if reference_function else None
    if reference is None:
        return [f"{label}/{FENCE_REFERENCE}: is missing, cannot be read or parsed, or has no def {FENCE_FUNCTION} directly in the module body, so no test module can be compared with it"]
    findings = []
    for file in sorted(tests_dir.glob("test_*.py")):
        if not file.is_file():
            continue
        rel = f"{label}/{file.name}"
        module, error = _read_module(file)
        if error is not None:
            findings.append(_unreadable_finding(rel, error))
            continue
        findings.extend(_module_fence_findings(module, rel, reference, copy, label))
    findings.extend(_brigade_findings(tests_dir, label, reference, copy))
    return findings


@dataclasses.dataclass(frozen=True)
class Identity:
    """home, user, and host compared as a path or an address in ops and the operations doc."""

    home: str
    user: str
    host: str


LIBRARY_SENTENCES = (
    "invalid choice",
    "the following arguments are required",
    "unrecognized arguments",
    "expected one argument",
)
EXEMPT_FUNCTION = "assert_refused_choice"
EXEMPT_FILE = "test_check.py"
EXEMPT_METHOD_PREFIX = "test_library_wording"
REQUIRED_SCAN = (
    "ops/t3-nightly-update.sh",
    "ops/t3-archive-threads.py",
    "ops/config.example",
    "docs/operations.md",
)
SHARED = (
    ("IGNORED", "assign"),
    ("config_home", "def"),
    ("new_file", "def"),
)
RENDERED_MARKER = Path("pstack-runtime/scripts/roles.py")


def _docstring_statement(node):
    body = getattr(node, "body", None)
    if not body:
        return None
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return first
    return None


def _ast_list(value):
    if isinstance(value, ast.AST):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, ast.AST):
                yield item


def _library_strings(node, stack):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for field, value in ast.iter_fields(node):
            if field == "body":
                continue
            for child in _ast_list(value):
                yield from _library_strings(child, stack)
        inner = (*stack, node.name)
        skipped = _docstring_statement(node)
        for statement in node.body:
            if statement is not skipped:
                yield from _library_strings(statement, inner)
        return
    if isinstance(node, (ast.Module, ast.ClassDef)):
        for field, value in ast.iter_fields(node):
            if field == "body":
                continue
            for child in _ast_list(value):
                yield from _library_strings(child, stack)
        skipped = _docstring_statement(node)
        for statement in node.body:
            if statement is not skipped:
                yield from _library_strings(statement, stack)
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            yield node, stack
        return
    for child in ast.iter_child_nodes(node):
        yield from _library_strings(child, stack)


def _library_allowed(path, stack):
    if EXEMPT_FUNCTION in stack:
        return True
    nearest = stack[-1] if stack else ""
    return path.name == EXEMPT_FILE and nearest.startswith(EXEMPT_METHOD_PREFIX)


def library_wording_findings(tests_dir):
    """Report a string literal under tests_dir that contains one of argparse's own sentences.

    The sentences are invalid choice, the following arguments are required,
    unrecognized arguments, and expected one argument. The formatting of argparse's choice list differs
    between Python 3.10 and 3.12. A comment is not a finding, because it is
    not in the syntax tree. The docstring, meaning the first string statement
    of a module, class, function, or async function, is not a finding. A later
    bare string statement is a finding. A string is allowed when an enclosing
    function is named assert_refused_choice. It is also allowed when the file
    name is test_check.py and the nearest enclosing function name starts with
    test_library_wording. A module-level constant in test_check.py is still
    checked. A helper in that file whose name does not start with
    test_library_wording is still checked. The allowance is the function name,
    not a line number. An unreadable or unparseable Python file is a finding
    that names the exception type. An empty list means nothing matched.
    """
    tests_dir = Path(tests_dir)
    findings = []
    files = sorted(
        (path for path in tests_dir.rglob("*.py") if path.is_file()),
        key=lambda path: path.relative_to(tests_dir).as_posix(),
    )
    for path in files:
        rel = path.relative_to(tests_dir.parent).as_posix()
        try:
            tree = ast.parse(path.read_text(), str(path))
        except (OSError, ValueError, SyntaxError, UnicodeDecodeError) as error:
            findings.append(
                f"{rel}: could not be read or parsed, so argparse wording was not checked: {type(error).__name__}"
            )
            continue
        for node, stack in _library_strings(tree, ()):
            if _library_allowed(path, stack):
                continue
            for sentence in LIBRARY_SENTENCES:
                if sentence in node.value:
                    findings.append(
                        f"{rel}:{node.lineno}: contains the argparse phrase {sentence!r}. "
                        "Assert the exit status and the words this project wrote, or call assert_refused_choice"
                    )
    return findings


def _machine_value_hits(text, identity):
    name, word_body = "A-Za-z0-9._-", "A-Za-z0-9_"
    word = word_body + "-"
    home, user, first_host_label = (re.escape(value) for value in (identity.home, identity.user, identity.host.split(".")[0]))
    patterns = []
    if len(identity.home) > 1:
        as_a_whole_path_prefix = rf"(?<![{name}]){home}(?![{name}])"
        patterns.append(("home directory", as_a_whole_path_prefix, 0))
    if len(identity.user) > 1:
        after_a_tilde = rf"(?<=~){user}(?![{name}])"
        before_an_at_sign = rf"(?<![{name}]){user}(?=@)"
        after_a_name_and_a_slash = rf"(?<=[{name}]/){user}(?![{name}])"
        as_the_only_absolute_segment = rf"(?<![{name}])/{user}(?![{name}])(?!/)"
        patterns.append(("user name", "|".join((
            after_a_tilde, before_an_at_sign, after_a_name_and_a_slash, as_the_only_absolute_segment,
        )), 0))
    if len(identity.host.split(".")[0]) > 1:
        after_an_at_sign_or_two_slashes = rf"(?:(?<=@)|(?<=//)){first_host_label}(?![{word}])"
        before_a_port = rf"(?<![{word}]){first_host_label}(?=:[0-9])"
        before_a_domain = rf'(?<![{word_body}/$"-]){first_host_label}(?=\.[A-Za-z0-9])'
        host_forms = [after_an_at_sign_or_two_slashes, before_a_port, before_a_domain]
        if len(identity.host.split(".")) > 1:
            as_the_whole_host = rf'(?<![{word_body}$-]){re.escape(identity.host)}(?![{word}])'
            host_forms.append(as_the_whole_host)
        patterns.append(("host name", "|".join(host_forms), re.IGNORECASE))
    return [(kind, number, line) for number, line in enumerate(text.splitlines(), 1)
            for kind, pattern, flags in patterns if re.search(pattern, line, flags)]


def machine_value_findings(ops_dir, operations_doc, identity):
    """Report the given identity's home directory, user name, or host name at the positions below.

    Scan every file under ops_dir, sorted, then operations_doc. Read text with
    errors='replace'. Skip a pattern when the home, the user name, or the
    first host label has length 1 or less.
    The home matches as a whole path prefix when each side is absent or outside
    the name class A-Za-z0-9._- (letters, digits, dot, underscore, and hyphen).
    A user name matches after a tilde, or after a name-class character and a
    slash, when the next character is outside that class. It matches as the
    only segment of an absolute path when the slash has no name-class character
    before it and the next character is neither a name-class character nor a
    slash. It matches before an at sign when the previous character is outside
    that class. It does not match as the first segment of a longer absolute
    path, so </dev/null is quiet for the user dev and cd /root/foo reports the
    home /root without the user root.
    A host label matches after an at sign or two slashes when the next
    character is outside the word class A-Za-z0-9_- (letters, digits, underscore,
    and hyphen). It matches before a colon and a digit when the previous character
    is outside that class. It matches before a dot and an alphanumeric when
    the previous character is outside that class and is not a slash, a dollar
    sign, or a double quote. These three matches use the first label. Every
    host match is case insensitive. When the host name has more than one label,
    the whole host name also matches when the previous character is absent or
    outside the word class and is not a dollar sign, and the next character is
    absent or outside the word class. For the label ops, "ops.conf" does not
    match and ping ops.lan does. For the label app, $APP.prev does not match
    and curl app:3773/x does. For the label config, ops/config.example does not
    match, and //config/share and see config.json match. For the host box7.lan,
    ssh "box7.lan", HOST="box7.lan", and /mnt/box7.lan/archives match, and
    $box7.lan does not.
    Each hit becomes
    f"{rel}:{number}: holds this machine's {kind}"
    with kind one of 'home directory', 'user name', or 'host name', in that
    pattern order for a line. rel is the path relative to ops_dir.parent.
    When one of REQUIRED_SCAN is missing, append
    f"{rel}: is missing, so this machine's home directory, user name, and host name were not scanned"
    and do not invent a scan of that path. An empty list means the required
    files exist and none of the scanned text matched.
    """
    ops_dir = Path(ops_dir)
    operations_doc = Path(operations_doc)
    repo = ops_dir.parent
    findings = []
    scanned = []
    if ops_dir.is_dir():
        scanned.extend(sorted(path for path in ops_dir.rglob("*") if path.is_file()))
    if operations_doc.is_file() and all(path.resolve() != operations_doc.resolve() for path in scanned):
        scanned.append(operations_doc)
    for path in scanned:
        rel = path.relative_to(repo).as_posix()
        text = path.read_text(errors="replace")
        for kind, number, _line in _machine_value_hits(text, identity):
            findings.append(f"{rel}:{number}: holds this machine's {kind}")
    for label in REQUIRED_SCAN:
        path = operations_doc if label == "docs/operations.md" else ops_dir / Path(label).name
        if not path.is_file():
            findings.append(
                f"{label}: is missing, so this machine's home directory, user name, and host name were not scanned"
            )
    return findings


def _module_assignment(module, name):
    found = None
    for statement in module.body:
        if isinstance(statement, ast.Assign) and any(_target_binds(target, name) for target in statement.targets):
            found = statement
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None and _target_binds(statement.target, name):
            found = statement
    return found


def _shared_node(module, name, kind):
    if kind == "def":
        return _module_function(module, name)
    return _module_assignment(module, name)


def _load_shared(path):
    if not path.is_file():
        return None
    source = path.read_text()
    return source, ast.parse(source, str(path))


def _function_source(source, node):
    lines = source.splitlines(keepends=True)
    start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
    return "".join(inspect.getblock(lines[start - 1:]))


def _binds_shared(statement, name):
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return statement.name == name
    if isinstance(statement, ast.Assign):
        return any(_target_binds(target, name) for target in statement.targets)
    if isinstance(statement, ast.AnnAssign):
        return statement.value is not None and _target_binds(statement.target, name)
    if isinstance(statement, ast.AugAssign):
        return _target_binds(statement.target, name)
    return False


def _unresolved_bindings(module, name):
    found = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if _binds_shared(child, name):
                    found.append(child)
                continue
            if _binds_shared(child, name):
                found.append(child)
            walk(child)

    for statement in module.body:
        if isinstance(statement, (ast.If, ast.Try, ast.With, ast.For, ast.While, ast.Match)):
            walk(statement)
        elif name == "IGNORED" and isinstance(statement, ast.AugAssign) and _target_binds(statement.target, name):
            found.append(statement)
    return found


def shared_source_findings(roles_path, install_path, roles_label="t3/scripts/roles.py", install_label="scripts/install.py"):
    """Report source text of IGNORED, config_home, or new_file that differs between the two files.

    Parse both files. Do not import them. IGNORED is the last assignment to
    that name directly in the module body. config_home and new_file are the
    last def of that name directly in the module body. A function's compared
    text is the lines from its first decorator, or from the def line when it
    has no decorator, through the last line inspect.getblock keeps. A
    decorator line, the def, the body, an inline comment on the last
    statement, and an indented comment after the last statement are kept.
    A blank line inside the function is kept. A blank line after the last
    kept line is dropped. A comment at column 0 after the function is
    dropped. An assignment is compared with ast.get_source_segment. A def,
    an async def, an assignment, an annotated assignment with a value, or an
    augmented assignment to one of these three names inside if, try, with,
    for, while, or match at module level is a binding the static comparison
    cannot resolve. An augmented assignment to IGNORED directly in the module
    body is the same kind of binding. A binding inside a function or class
    body is not a module binding. The last direct definition still wins among
    direct ones. A missing file, a missing name, or an unreadable file is a
    finding. None on both sides is not a match. An empty list means all three
    names exist as direct definitions in both files, none of them has a
    binding the static comparison cannot resolve, and their source text is
    the same.
    """
    roles_path = Path(roles_path)
    install_path = Path(install_path)
    loaded = {}
    findings = []
    for label, path in ((roles_label, roles_path), (install_label, install_path)):
        try:
            loaded[label] = _load_shared(path)
        except (OSError, ValueError, SyntaxError, UnicodeDecodeError) as error:
            loaded[label] = error
        if loaded[label] is None:
            findings.append(f"{label}: is missing, so IGNORED, config_home, and new_file were not compared")
        elif isinstance(loaded[label], Exception):
            findings.append(
                f"{label}: could not be read or parsed, so shared source was not compared: {type(loaded[label]).__name__}"
            )
    if any(not isinstance(loaded[label], tuple) for label in (roles_label, install_label)):
        return findings
    roles_source, roles_module = loaded[roles_label]
    install_source, install_module = loaded[install_label]
    for name, kind in SHARED:
        for label, module in ((roles_label, roles_module), (install_label, install_module)):
            for node in _unresolved_bindings(module, name):
                findings.append(f"{label}:{node.lineno}: {name} is bound where the static comparison cannot resolve it")
        roles_node = _shared_node(roles_module, name, kind)
        install_node = _shared_node(install_module, name, kind)
        if roles_node is None:
            findings.append(f"{roles_label}: has no {name}. Define it with the same source text as {install_label}")
        if install_node is None:
            findings.append(f"{install_label}: has no {name}. Define it with the same source text as {roles_label}")
        if roles_node is None or install_node is None:
            continue
        roles_text = _function_source(roles_source, roles_node) if kind == "def" else ast.get_source_segment(roles_source, roles_node)
        install_text = _function_source(install_source, install_node) if kind == "def" else ast.get_source_segment(install_source, install_node)
        if roles_text is None or install_text is None or roles_text != install_text:
            findings.append(
                f"{roles_label}: {name} differs from the one in {install_label}. Make the source text the same"
            )
    return findings


def _machine_identity():
    """Identity(pwd.getpwuid(os.getuid()).pw_dir, pw_name, socket.gethostname())."""
    entry = pwd.getpwuid(os.getuid())
    return Identity(entry.pw_dir, entry.pw_name, socket.gethostname())


def main(argv):
    tree = Path(argv[0]) if argv else Path(__file__).resolve().parents[1] / "skills"
    problems = check_tree(tree) + fence_findings(TESTS_DIR)
    if not argv:
        problems += badge_findings(README_PATH, UPSTREAM_PATH)
    print("\n".join(problems) or "ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
