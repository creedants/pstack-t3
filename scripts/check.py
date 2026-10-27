#!/usr/bin/env python3
"""Lint a generated skills tree for Cursor-only leftovers and broken structure, and the test modules for the git environment fence.

check_tree judges the tree it is given, and when that tree contains `pstack-runtime/scripts/roles.py` it also reports library wording, machine values, and shared source for the checkout that contains this file. With `guide` true, which is the default, that same case reports a doctor count quoted in that checkout's `docs/guide.md` and a skill count stated in that checkout's `README.md` that disagree with the number of skills in the tree. The same case reports a T3 Code minimum version, stated on a line between `T3 Code` and `or later` in a plain sentence or a Markdown link with optional backticks, that disagrees with the version in the setup gate string. The build keeps the default and main passes `guide=not argv`, so the build and a bare `python3 scripts/check.py` report the guide while an explicit tree argument does not. A tree without that file gets none of those findings. badge_findings reports a README upstream badge that is missing or that disagrees with the version in upstream.json. The default CLI run and the build add that repository check. An explicit tree argument gets no badge finding. home_path_findings reports each line outside `vendor/`, `.git`, `__pycache__`, and the allow list that holds a `/home/<name>/` path, and the bare CLI run adds it.
"""

from pathlib import Path

import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import yaml
except ImportError:  # the build still runs; frontmatter gets the shallow check only
    yaml = None

from _check.changes import _at_file_line, change_findings
from _check.fence import (
    FENCE_BRIGADE_DIR,
    FENCE_BRIGADE_MODULE,
    FENCE_BRIGADE_SUPPORT,
    FENCE_FUNCTION,
    FENCE_LANDING_DIR,
    FENCE_LANDING_MODULE,
    FENCE_LANDING_SUPPORT,
    FENCE_REFERENCE,
    FENCE_SETUP,
    _area_findings,
    _brigade_findings,
    _is_support_import,
    _landing_findings,
    _last_setup_binding,
    _module_fence_findings,
    _package_findings,
    _renames_setup_away,
    _setup_name_binding,
    fence_findings,
)
from _check.fragments import (
    FRAGMENT_DIRS,
    FRAGMENT_LINK,
    _fragment_link_targets,
    _fence_open,
    _heading_slugs,
    _is_fence_close,
    _list_content,
    _strip_code_spans,
    _visible_lines,
    heading_slug,
)
from _check.identity import (
    REQUIRED_SCAN as _IDENTITY_REQUIRED_SCAN,
    Identity,
    _machine_identity,
    _machine_value_hits,
    machine_value_findings,
)
from _check.pyast import (
    _calls_name,
    _module_function,
    _read_module,
    _target_binds,
    _unreadable_finding,
)
from _check.readme import (
    _GATE_MINIMUM,
    _NAMED_MINIMUM,
    _QUOTED_COUNT,
    _STATED_COUNT,
    _BADGE_LINE,
    _VERSION_TOKEN,
    _hits,
    _pin_version,
    _readme_text,
    _setup_gate_version,
    _shipped_skill_count,
    _split_lines,
    badge_findings,
    guide_count_findings,
    minimum_version_findings,
    readme_count_findings,
    rewritten_readme,
)
from _check.shared_source import (
    SHARED,
    _binds_shared,
    _function_source,
    _load_shared,
    _module_assignment,
    _shared_node,
    _unresolved_bindings,
    shared_source_findings,
)
from _check.siblings import _catalog, _release, _size_limit
from _check.tree import (
    LIGHT_HEADER_CELLS,
    LIGHT_MODE_NAME,
    TABLE_SEPARATOR,
    _header_cells,
    _witness_skills_are_files,
    catalog_heredoc_findings,
    light_table_findings,
)
from _check import tree as _tree_mod
from _check import fragments as _fragments_mod
from _check.vocabulary import (
    CATALOG_HEREDOC_CLOSER,
    CATALOG_HEREDOC_OPEN,
    FORBIDDEN,
    FRONTMATTER_REQUIRED,
    LINK,
    RENDERED_MARKER,
    REQUIRED_TOOLS,
    SETUP_GATE,
    THREAD_LINK,
    Layout,
)
from _check.wording import (
    EXEMPT_FILE,
    EXEMPT_FUNCTION,
    EXEMPT_METHOD_PREFIX,
    LIBRARY_SENTENCES,
    _ast_list,
    _docstring_statement,
    _library_allowed,
    _library_strings,
    library_wording_findings,
)

ROLES_PY = Path(__file__).resolve().parents[1] / "t3/scripts/roles.py"
TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"
README_PATH = Path(__file__).resolve().parents[1] / "README.md"
UPSTREAM_PATH = Path(__file__).resolve().parents[1] / "upstream.json"
GUIDE_PATH = Path(__file__).resolve().parents[1] / "docs/guide.md"
VENDOR_SKILLS_PATH = Path(__file__).resolve().parents[1] / "vendor/pstack/skills"
REQUIRED_SCAN = _IDENTITY_REQUIRED_SCAN

HOME_PATH = re.compile(r"/home/[A-Za-z0-9._-]+/")

HOME_PATH_EXEMPT = frozenset({
    "tests/activity_tests/test_filter.py",
    "tests/activity_tests/test_filter_paths.py",
    "tests/activity_tests/test_goal_strip.py",
    "tests/activity_tests/test_privacy.py",
    "tests/activity_tests/test_words.py",
    "tests/ops_tests/test_scan.py",
    "tests/check_tests/test_home_paths.py",
})


def _layout():
    return Layout(
        repo=Path(__file__).resolve().parents[1],
        roles_py=ROLES_PY,
        tests_dir=TESTS_DIR,
        readme_path=README_PATH,
        upstream_path=UPSTREAM_PATH,
        guide_path=GUIDE_PATH,
        vendor_skills_path=VENDOR_SKILLS_PATH,
        yaml=yaml,
    )


def light_waiver_cells():
    return _tree_mod.light_waiver_cells(_layout())


def _vendor_skill_names():
    vendor_path = VENDOR_SKILLS_PATH
    if not vendor_path.is_dir():
        return frozenset()
    return frozenset(path.name for path in vendor_path.iterdir() if path.is_dir())


def fragment_findings(root):
    return _fragments_mod.fragment_findings(root, _layout())


def check_tree(root, guide=True):
    return _tree_mod.check_tree(root, _layout(), guide=guide)


def home_path_findings(repo):
    """One finding per line under repo that holds a home directory path, outside vendor and the allow list.

    A path with a .git or __pycache__ part and a path whose first part is vendor are skipped, and so is a
    path in HOME_PATH_EXEMPT. Each file is read as UTF-8 with errors replaced. Findings come in sorted path
    order and line order, with the path relative to repo in POSIX form.
    """
    root = Path(repo)
    findings = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if ".git" in parts or "__pycache__" in parts or parts[0] == "vendor":
            continue
        rel = "/".join(parts)
        if rel in HOME_PATH_EXEMPT:
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if HOME_PATH.search(line):
                findings.append(f"{rel}:{number}: holds a home directory path")
    return findings


def main(argv):
    layout = _layout()
    if argv:
        return _tree_mod.main(argv, layout)
    problems = (check_tree(layout.repo / "skills")
                + fence_findings(layout.tests_dir)
                + badge_findings(layout.readme_path, layout.upstream_path)
                + _size_limit().findings(layout.repo)
                + home_path_findings(layout.repo))
    print("\n".join(problems) or "ok")
    return 1 if problems else 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
