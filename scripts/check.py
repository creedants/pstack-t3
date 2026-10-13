#!/usr/bin/env python3
"""Lint a generated skills tree for Cursor-only leftovers and broken structure, and the test modules for the git environment fence.

check_tree judges only the tree it is given. badge_findings reports a README upstream badge that is missing or that disagrees with the version in upstream.json. The default CLI run and the build add that repository check. An explicit tree argument gets no badge finding.
"""

import ast
import importlib.util
import json
import re
import sys

try:
    import yaml
except ImportError:  # the build still runs; frontmatter gets the shallow check only
    yaml = None
from pathlib import Path

# Each pattern names a Cursor-only mechanism and its T3 replacement.
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


def check_tree(root):
    root = Path(root)
    findings = []
    cells = light_waiver_cells()
    for skill_md in sorted(root.glob("*/SKILL.md")):
        skill = skill_md.parent.name
        text = skill_md.read_text()
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
    if isinstance(statement, ast.Assign) and any(_target_binds(target, FENCE_SETUP) for target in statement.targets):
        return "assign"
    if isinstance(statement, ast.AnnAssign) and _target_binds(statement.target, FENCE_SETUP):
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
    module defines its own class. A from-import that renames setUpModule away,
    or that binds the name setUpModule to a different imported name, is an
    alias finding when that is the last binding. A from-import that binds the
    name from any other module is a finding. A plain import that binds the
    name setUpModule is that same other-module finding. A plain import that
    only renames setUpModule away does not bind the name. An assignment or
    annotated assignment of the name is a finding. A def setUpModule uses the
    top-level fence rules. Those findings end with Import setUpModule from
    the brigade support path.

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


def main(argv):
    tree = Path(argv[0]) if argv else Path(__file__).resolve().parents[1] / "skills"
    problems = check_tree(tree) + fence_findings(TESTS_DIR)
    if not argv:
        problems += badge_findings(README_PATH, UPSTREAM_PATH)
    print("\n".join(problems) or "ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
