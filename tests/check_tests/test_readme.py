import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from .support import (
    setUpModule,
    check,
    tests_tree,
    ROOT,
)


README_AT_1_0_0 = (
    "Preamble keeps 1.0.0 and upstream pstack.\n"
    '  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="license MIT"></a>\n'
    '  <a href="upstream.json"><img src="https://img.shields.io/badge/upstream%20pstack-1.0.0-555.svg" alt="upstream pstack 1.0.0"></a>\n'
    "Postscript keeps 1.0.0.\n"
)
DISAGREE_1_0_0 = "README.md badge url 1.0.0 alt 1.0.0 disagrees with upstream.json version 1.1.0"
ABSENT_1_1_0 = "README.md has no upstream badge and upstream.json version is 1.1.0"


def badge_line(url, alt):
    return (
        f'  <a href="upstream.json"><img src="https://img.shields.io/badge/upstream%20pstack-{url}-555.svg" '
        f'alt="upstream pstack {alt}"></a>\n'
    )


def checker_checkout(root, readme):
    s=root/"scripts"
    s.mkdir(parents=True)
    shutil.copy(ROOT/"scripts/check.py",s/"check.py")
    shutil.copytree(ROOT/"scripts/_check",s/"_check")
    r=root/"t3/scripts"
    r.mkdir(parents=True)
    shutil.copy(ROOT/"t3/scripts/roles.py",r/"roles.py")
    shutil.copytree(ROOT/"t3/scripts/_roles",r/"_roles")
    k=root/"skills/demo"
    k.mkdir(parents=True)
    (k/"SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nbody\n")
    tests_tree(root, {})
    (root/"upstream.json").write_text(json.dumps({"version": "1.1.0"})+"\n")
    if readme is not None:
        (root/"README.md").write_text(readme)


def run_check(root):
    return subprocess.run([sys.executable, "scripts/check.py"], cwd=root, capture_output=True, text=True)


class BadgeCheckTest(unittest.TestCase):
    def test_check_py_names_an_older_badge_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checker_checkout(root, README_AT_1_0_0)
            result = run_check(root)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, DISAGREE_1_0_0 + "\n", ""))
            self.assertEqual((root / "README.md").read_text(), README_AT_1_0_0)

    def test_check_py_names_a_missing_badge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checker_checkout(root, None)
            result = run_check(root)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, ABSENT_1_1_0 + "\n", ""))
            self.assertFalse((root / "README.md").exists())

    def test_badge_findings_name_each_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream.json"
            upstream.write_text(json.dumps({"version": "1.1.0"}) + "\n")
            readme = root / "README.md"
            cases = (
                ("url", badge_line("1.0.0", "1.1.0"), ["README.md badge url 1.0.0 alt 1.1.0 disagrees with upstream.json version 1.1.0"]),
                ("alt", badge_line("1.1.0", "1.0.0"), ["README.md badge url 1.1.0 alt 1.0.0 disagrees with upstream.json version 1.1.0"]),
                ("both", badge_line("1.0.0", "1.2.0"), ["README.md badge url 1.0.0 alt 1.2.0 disagrees with upstream.json version 1.1.0"]),
                ("agreement", badge_line("1.1.0", "1.1.0"), []),
                ("two lines", badge_line("1.1.0", "1.1.0") + badge_line("1.0.0", "1.0.0"), [ABSENT_1_1_0]),
                ("not a badge", "not a badge\n", [ABSENT_1_1_0]),
            )
            for name, text, expected in cases:
                with self.subTest(slot=name):
                    readme.write_text(text)
                    self.assertEqual(check.badge_findings(readme, upstream), expected)

    def test_badge_findings_read_a_crlf_readme(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream.json"
            upstream.write_text(json.dumps({"version": "1.1.0"}) + "\n")
            readme = root / "README.md"
            readme.write_bytes(README_AT_1_0_0.replace("\n", "\r\n").encode())
            self.assertEqual(check.badge_findings(readme, upstream), [DISAGREE_1_0_0])
            agreed = README_AT_1_0_0.replace("upstream%20pstack-1.0.0", "upstream%20pstack-1.1.0").replace(
                "upstream pstack 1.0.0", "upstream pstack 1.1.0"
            )
            readme.write_bytes(agreed.replace("\n", "\r\n").encode())
            self.assertEqual(check.badge_findings(readme, upstream), [])

    def test_check_tree_judges_only_the_given_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills/demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nbody\n")
            readme = root / "README.md"
            upstream = root / "upstream.json"
            readme.write_text(README_AT_1_0_0)
            upstream.write_text(json.dumps({"version": "1.1.0"}) + "\n")
            with mock.patch.object(check, "README_PATH", readme), mock.patch.object(check, "UPSTREAM_PATH", upstream):
                self.assertEqual(check.check_tree(root / "skills"), [])
                self.assertEqual(check.badge_findings(readme, upstream), [DISAGREE_1_0_0])

    def test_an_explicit_tree_gets_no_repository_badge_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Path(directory) / "skills"
            skills.mkdir()
            tests = tests_tree(directory, {})
            readme = Path(directory) / "README.md"
            upstream = Path(directory) / "upstream.json"
            readme.write_text(README_AT_1_0_0)
            upstream.write_text(json.dumps({"version": "1.1.0"}) + "\n")
            output = io.StringIO()
            with mock.patch.object(check, "TESTS_DIR", tests), mock.patch.object(
                check, "README_PATH", readme
            ), mock.patch.object(check, "UPSTREAM_PATH", upstream), contextlib.redirect_stdout(output):
                status = check.main([str(skills)])
            self.assertEqual((status, output.getvalue()), (0, "ok\n"))



class SkillCountCheckTest(unittest.TestCase):
    def _tree(self, guide, skills=(), readme=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "pstack-runtime/scripts").mkdir(parents=True)
        (root / "pstack-runtime/scripts/roles.py").write_text("")
        for name in skills:
            skill = root / name
            skill.mkdir()
            (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n\nbody\n")
        document = root / "docs/guide.md"
        document.parent.mkdir()
        document.write_text(guide)
        patcher = mock.patch.object(check, "GUIDE_PATH", document)
        patcher.start()
        self.addCleanup(patcher.stop)
        if readme is None:
            readme = f"See all {len(skills)} skills and every playbook.\n"
        self._readme_states(root, readme)
        return root

    def _readme_states(self, root, readme):
        """Point README_PATH at a README holding the given text."""
        document = root / "README.md"
        document.write_text(readme)
        patcher = mock.patch.object(check, "README_PATH", document)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_guide_that_quotes_the_shipped_count_is_quiet(self):
        root = self._tree("`doctor` should show `3/3 pstack-t3` for each provider you use.\n", ("a", "b", "c"))
        self.assertEqual(check.check_tree(root), [])

    def test_a_stale_count_is_named_with_the_line_the_quote_and_the_shipped_count(self):
        root = self._tree("intro\n`doctor` should show `2/2 pstack-t3` for each provider you use.\n", ("a", "b", "c"))
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md:2: quotes the doctor count 2/2, and the shipped skill count is 3",
        ])

    def test_a_quote_whose_two_numbers_differ_is_named(self):
        root = self._tree("`doctor` should show `3/4 pstack-t3` for each provider you use.\n", ("a", "b", "c"))
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md:1: quotes the doctor count 3/4, and the shipped skill count is 3",
        ])

    def test_a_guide_that_quotes_no_count_is_named(self):
        root = self._tree("no count here\n", ("a",))
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md: quotes no doctor count, and the shipped skill count is 1",
        ])

    def test_a_guide_that_cannot_be_read_is_named(self):
        root = self._tree("`doctor` should show `1/1 pstack-t3` for each provider you use.\n", ("a",))
        (root / "docs/guide.md").unlink()
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md: could not be read, so its doctor count was not checked: FileNotFoundError",
        ])

    def test_an_explicit_tree_argument_gets_no_guide_finding(self):
        root = self._tree("intro\n`doctor` should show `2/2 pstack-t3` for each provider you use.\n", skills=("a", "b"))
        (root / "pstack-runtime/SKILL.md").write_text(
            "---\nname: pstack-runtime\ndescription: d\n---\n\n"
            "Call `watch_pull_request`. Call `unwatch_pull_request`.\n"
        )
        tests = tests_tree(root, {})
        output = io.StringIO()
        with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
            status = check.main([str(root)])
        self.assertEqual((status, output.getvalue()), (0, "ok\n"))

    def test_a_readme_that_states_the_shipped_count_is_quiet(self):
        root = self._tree("`doctor` should show `2/2 pstack-t3` for each provider you use.\n", skills=("a", "b"))
        self.assertEqual(check.check_tree(root), [])

    def test_a_stale_readme_count_is_named_with_the_line_the_stated_count_and_the_shipped_count(self):
        root = self._tree(
            "`doctor` should show `3/3 pstack-t3` for each provider you use.\n",
            readme="See all 60 skills and every playbook.\n",
            skills=("a", "b", "c"),
        )
        self.assertEqual(check.check_tree(root), [
            "README.md:1: states all 60 skills, and the shipped skill count is 3",
        ])

    def test_a_readme_that_states_no_skill_count_is_named(self):
        root = self._tree("`doctor` should show `1/1 pstack-t3` for each provider you use.\n", skills=("a",))
        self._readme_states(root, "no count here\n")
        self.assertEqual(check.check_tree(root), [
            "README.md: states no skill count, and the shipped skill count is 1",
        ])

    def test_a_readme_that_cannot_be_read_is_named(self):
        root = self._tree("`doctor` should show `1/1 pstack-t3` for each provider you use.\n", skills=("a",))
        (root / "README.md").unlink()
        self.assertEqual(check.check_tree(root), [
            "README.md: could not be read, so its skill count was not checked: FileNotFoundError",
        ])

    def test_an_explicit_tree_argument_gets_no_readme_count_finding(self):
        root = self._tree(
            "intro\n`doctor` should show `2/2 pstack-t3` for each provider you use.\n",
            readme="See all 60 skills and every playbook.\n",
            skills=("a", "b"),
        )
        (root / "pstack-runtime/SKILL.md").write_text(
            "---\nname: pstack-runtime\ndescription: d\n---\n\n"
            "Call `watch_pull_request`. Call `unwatch_pull_request`.\n"
        )
        tests = tests_tree(root, {})
        output = io.StringIO()
        with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
            status = check.main([str(root)])
        self.assertEqual((status, output.getvalue()), (0, "ok\n"))



class MinimumT3CodeVersionCheckTest(unittest.TestCase):
    """The tests cover agreeing minimum-version sentences, a different version in each document, and a gate string that cannot be read."""

    def _tree(self, readme, guide):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "pstack-runtime/scripts").mkdir(parents=True)
        (root / "pstack-runtime/scripts/roles.py").write_text("")
        skill = root / "demo"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nbody\n")
        readme_path = root / "README.md"
        readme_path.write_text("See all 1 skills and every playbook.\n" + readme)
        guide_path = root / "docs/guide.md"
        guide_path.parent.mkdir()
        guide_path.write_text("`doctor` should show `1/1 pstack-t3` for each provider you use.\n" + guide)
        for attribute, document in (("README_PATH", readme_path), ("GUIDE_PATH", guide_path)):
            patcher = mock.patch.object(check, attribute, document)
            patcher.start()
            self.addCleanup(patcher.stop)
        return root

    def test_a_readme_and_guide_that_name_the_gate_version_are_quiet(self):
        gate = check._setup_gate_version()
        root = self._tree(
            f"Setup needs T3 Code {gate} or later.\n",
            f"You need [T3 Code](https://example.test/releases) `{gate}` or later.\n",
        )
        self.assertEqual(check.check_tree(root), [])

    def test_a_readme_naming_another_version_is_named_with_both_versions(self):
        gate = check._setup_gate_version()
        root = self._tree(
            "Setup needs T3 Code 9.9.9 or later.\n",
            f"You need T3 Code `{gate}` or later.\n",
        )
        self.assertEqual(check.check_tree(root), [
            f"README.md:2: states the T3 Code minimum version 9.9.9, and the setup gate requires {gate}",
        ])

    def test_a_guide_naming_another_version_is_named_with_both_versions(self):
        gate = check._setup_gate_version()
        root = self._tree(
            f"Setup needs T3 Code {gate} or later.\n",
            "You need T3 Code `9.9.9` or later, and Python 3.10 or later.\n",
        )
        self.assertEqual(check.check_tree(root), [
            f"docs/guide.md:2: states the T3 Code minimum version 9.9.9, and the setup gate requires {gate}",
        ])

    def test_a_gate_string_that_cannot_be_read_is_named_instead_of_silently_skipping_the_comparison(self):
        root = self._tree(
            "Setup needs T3 Code 9.9.9 or later.\n",
            "You need T3 Code `9.9.9` or later.\n",
        )
        gate = mock.patch.dict(check.REQUIRED_TOOLS, {"setup-pstack/SKILL.md": ("T3 Code soon or later",)})
        gate.start()
        self.addCleanup(gate.stop)
        self.assertEqual(check.check_tree(root), [
            "setup-pstack/SKILL.md: the setup gate's T3 Code minimum version could not be read",
        ])
