import importlib.util
import re
from pathlib import Path

from .changes import change_findings
from .fence import fence_findings
from .fragments import fragment_findings
from .identity import _machine_identity, machine_value_findings
from .readme import badge_findings, guide_count_findings, minimum_version_findings, readme_count_findings
from .shared_source import shared_source_findings
from .siblings import _catalog, _size_limit
from .vocabulary import (
    CATALOG_HEREDOC_CLOSER,
    CATALOG_HEREDOC_OPEN,
    FORBIDDEN,
    FRONTMATTER_REQUIRED,
    LINK,
    RENDERED_MARKER,
    REQUIRED_TOOLS,
    RETIRED_SKIPPED,
    RETIRED_SOURCE_TOPS,
    RETIRED_TOOL,
    RETIRED_TOOLS,
    THREAD_LINK,
    Layout,
)
from .wording import library_wording_findings

LIGHT_HEADER_CELLS = frozenset({"light", "light mode", "light behavior", "in light mode"})
LIGHT_MODE_NAME = re.compile(r"(?i:\blight mode\b)|Mode: light")
TABLE_SEPARATOR = re.compile(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?")


def light_waiver_cells(layout):
    spec = importlib.util.spec_from_file_location("pstack_roles", layout.roles_py)
    roles = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(roles)
    return tuple(sorted({", ".join(steps) for steps in roles.LIGHT_WAIVERS.values() if len(steps) >= 2}))


def _header_cells(line):
    return [cell.replace("`", "").replace("*", "").strip().casefold() for cell in line.strip().strip("|").split("|")]


def light_table_findings(rel_s, text, cells):
    if rel_s in ("pstack-runtime/SKILL.md", "pstack-runtime/light.md"):
        return []
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


def _retired_files(base, tops, skipped):
    """Every file below one of tops under base, minus the skipped two-part prefixes and __pycache__."""
    paths = []
    for top in tops:
        for path in sorted((base / top).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            parts = path.relative_to(base).parts
            if any(parts[:2] == prefix for prefix in skipped):
                continue
            paths.append(path)
    return paths


def retired_tool_findings(tree, repo=None):
    """One finding per retired T3 tool name per line in the given skills tree, and in the checkout.

    The tree scan covers every file it holds. When repo is given, the checkout's t3 and docs trees
    are read too, minus docs/changelog and docs/cli. A tree finding names the path relative to
    tree, a checkout finding the path relative to repo, which is how the other check_tree findings
    read. Each file is read as UTF-8 with errors replaced. Each scan comes in sorted path order,
    then line order, then column order.
    """
    scans = [(Path(tree), (".",), ())]
    if repo is not None:
        scans.append((Path(repo), RETIRED_SOURCE_TOPS, RETIRED_SKIPPED))
    findings = []
    for base, tops, skipped in scans:
        for path in _retired_files(base, tops, skipped):
            rel = path.relative_to(base).as_posix()
            text = path.read_bytes().decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                for match in RETIRED_TOOL.finditer(line):
                    name = match.group(0)
                    findings.append(
                        f"{rel}:{number}: retired T3 tool {name}; use {RETIRED_TOOLS[name]}: {line.strip()[:120]}"
                    )
    return findings


def check_tree(root, layout, guide=True):
    root = Path(root)
    yaml = layout.yaml
    findings = []
    cells = light_waiver_cells(layout)
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
        patterns = [] if rel_s == "pstack-runtime/SKILL.md" else FORBIDDEN
        for number, line in enumerate(text.splitlines(), 1):
            for pattern, why in patterns:
                if re.search(pattern, line):
                    findings.append(f"{rel}:{number}: {why}: {line.strip()[:120]}")
            if "threadIds" in line:
                findings.append(
                    f"{rel}:{number}: shipped text uses a plural threadIds key. "
                    f"Pass one explicit threadId per t3_thread_organize call: {line.strip()[:120]}"
                )
            if "t3_thread_organize" in line:
                lowered = line.lower()
                if any(verb in lowered for verb in ("archive", "settle", "snooze")) and "threadId" not in line:
                    findings.append(
                        f"{rel}:{number}: t3_thread_organize archive/settle/snooze step must name threadId. "
                        f"Add the one explicit threadId: {line.strip()[:120]}"
                    )
        findings.extend(catalog_heredoc_findings(rel, text))
        if file.suffix == ".md":
            findings.extend(light_table_findings(rel_s, text, cells))
            for target in LINK.findall(text):
                if THREAD_LINK.match(target):
                    continue
                path = target.split("#", 1)[0].split("?", 1)[0]
                if "/" not in path and "." not in path:
                    continue
                if path and not (file.parent / path).resolve().exists():
                    findings.append(f"{rel}: broken link {target}")
    findings.extend(retired_tool_findings(root, layout.repo if witness else None))
    findings.extend(fragment_findings(root, layout))
    if (root / "poteto-mode/SKILL.md").is_file():
        findings.extend(_catalog().missing_playbooks(root))
    if witness and not (root / RENDERED_MARKER).is_file():
        findings.append(f"{RENDERED_MARKER.as_posix()}: is missing")
    if (root / RENDERED_MARKER).is_file():
        repo = layout.repo
        findings.extend(library_wording_findings(repo / "tests"))
        findings.extend(change_findings(repo / "changes"))
        findings.extend(machine_value_findings(repo / "ops", repo / "docs/operations.md", _machine_identity()))
        findings.extend(shared_source_findings(repo / "t3/scripts/roles.py", repo / "scripts/install.py"))
        if guide:
            findings.extend(guide_count_findings(layout.guide_path, root))
            findings.extend(readme_count_findings(layout.readme_path, root))
            findings.extend(minimum_version_findings(layout.readme_path, layout.guide_path))
    return findings


def main(argv, layout):
    tree = Path(argv[0]) if argv else layout.repo / "skills"
    problems = check_tree(tree, layout, guide=not argv) + fence_findings(layout.tests_dir)
    if not argv:
        problems += badge_findings(layout.readme_path, layout.upstream_path)
        problems += _size_limit().findings(layout.repo)
    print("\n".join(problems) or "ok")
    return 1 if problems else 0
