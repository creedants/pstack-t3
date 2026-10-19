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
)


class LightTableExemptionTest(unittest.TestCase):
    TABLE = (
        "| Spawn | Light behavior |\n"
        "| --- | --- |\n"
        "| `swarm` | Spawn at most 3 workers. |\n"
    )
    SKILL = "---\nname: pstack-runtime\ndescription: d\n---\n\n## Gate review\n\nCall `watch_pull_request`. Call `unwatch_pull_request`.\n"

    def test_a_light_table_in_light_md_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "pstack-runtime"
            runtime.mkdir(parents=True)
            (runtime / "SKILL.md").write_text(self.SKILL)
            (runtime / "light.md").write_text(
                "# Light behavior\n\nRead this file before every spawn and run the row for that spawn.\n\n" + self.TABLE
            )
            self.assertEqual(check.check_tree(root), [])

    def test_the_same_table_in_another_file_still_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "pstack-runtime"
            runtime.mkdir(parents=True)
            (runtime / "SKILL.md").write_text(self.SKILL)
            (runtime / "other.md").write_text(self.TABLE)
            findings = check.check_tree(root)
            self.assertTrue(
                any("light table restated" in finding and "pstack-runtime/other.md" in finding for finding in findings),
                findings,
            )

    def test_forbidden_patterns_still_apply_to_light_md(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "pstack-runtime"
            runtime.mkdir(parents=True)
            (runtime / "SKILL.md").write_text(self.SKILL)
            (runtime / "light.md").write_text("# Light behavior\n\nRun with run_in_background.\n")
            findings = check.check_tree(root)
            self.assertTrue(
                any("pstack-runtime/light.md" in finding and "Cursor Task flag" in finding for finding in findings),
                findings,
            )



class PlaybookBulletCheckTest(unittest.TestCase):
    def test_a_demo_skill_has_no_playbook_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nbody\n")
            self.assertEqual(check.check_tree(directory), [])

    def test_the_command_reports_a_missing_playbook_file(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Path(directory) / "skills"
            skill = skills / "poteto-mode"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: poteto-mode\ndescription: d\n---\n\n## Playbooks\n- **No such playbook.** Nothing.\n")
            tests = tests_tree(directory, {})
            output = io.StringIO()
            with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
                status = check.main([str(skills)])
            self.assertEqual(
                (status, output.getvalue()),
                (1, 'poteto-mode/SKILL.md: playbook "No such playbook" resolves to playbooks/no-such-playbook.md, which does not exist\n'),
            )

    def test_the_command_accepts_an_indented_note_under_a_playbook_bullet(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Path(directory) / "skills"
            skill = skills / "poteto-mode"
            (skill / "playbooks").mkdir(parents=True)
            (skill / "playbooks/bug-fix.md").write_text("ok\n")
            (skill / "SKILL.md").write_text(
                "---\nname: poteto-mode\ndescription: d\n---\n\n"
                "## Playbooks\n"
                "- **Bug fix.** File: `playbooks/bug-fix.md`.\n"
                "    - Choose this when the defect is already reproduced.\n"
            )
            tests = tests_tree(directory, {})
            output = io.StringIO()
            with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
                status = check.main([str(skills)])
            self.assertEqual((status, output.getvalue()), (0, "ok\n"))



class FrontmatterWithoutPyYamlTest(unittest.TestCase):
    def test_without_pyyaml_a_malformed_flow_sequence_is_not_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: [unterminated\n---\n\nbody\n")
            with mock.patch.object(check, "yaml", None):
                self.assertEqual(check.check_tree(directory), [])



class RenderedTreeCheckTest(unittest.TestCase):
    def _root(self, roles=False, demo=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "pstack-runtime").mkdir()
        (root / "pstack-runtime/SKILL.md").write_text(
            "---\nname: pstack-runtime\ndescription: d\n---\n\n"
            "Call `watch_pull_request`. Call `unwatch_pull_request`.\n"
        )
        (root / "setup-pstack").mkdir()
        (root / "setup-pstack/SKILL.md").write_text(
            "---\nname: setup-pstack\ndescription: d\n---\n\n"
            "Call `watch_pull_request`. T3 Code 0.1.0 or later.\n"
        )
        if roles:
            (root / "pstack-runtime/scripts").mkdir()
            (root / "pstack-runtime/scripts/roles.py").write_text("")
        if demo is not None:
            (root / "demo").mkdir()
            (root / "demo/SKILL.md").write_text(demo)
        return root

    def _guide_agrees(self, root):
        """Point GUIDE_PATH at a guide that quotes the count this tree ships."""
        document = root / "docs/guide.md"
        document.parent.mkdir()
        shipped = len(list(root.glob("*/SKILL.md")))
        document.write_text(f"`doctor` should show `{shipped}/{shipped} pstack-t3` for each provider you use.\n")
        patcher = mock.patch.object(check, "GUIDE_PATH", document)
        patcher.start()
        self.addCleanup(patcher.stop)
        readme = root / "README.md"
        readme.write_text(f"See all {shipped} skills and every playbook.\n")
        patcher = mock.patch.object(check, "README_PATH", readme)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_tree_with_both_witness_skills_and_roles_py_has_no_roles_finding(self):
        root = self._root(roles=True)
        self._guide_agrees(root)
        self.assertEqual(check.check_tree(root), [])

    def test_a_tree_with_both_witness_skills_and_no_roles_py_names_that_file(self):
        self.assertEqual(check.check_tree(self._root()), ["pstack-runtime/scripts/roles.py: is missing"])

    def test_disable_model_invocation_in_a_skill_body_is_not_a_frontmatter_finding(self):
        demo = (
            "---\nname: demo\ndescription: d\n---\n\n"
            "The build strips disable-model-invocation.\n"
        )
        root = self._root(roles=True, demo=demo)
        self._guide_agrees(root)
        self.assertEqual(check.check_tree(root), [])

    def test_disable_model_invocation_in_frontmatter_is_named(self):
        demo = (
            "---\nname: demo\ndescription: d\ndisable-model-invocation: true\n---\n\n"
            "body\n"
        )
        root = self._root(roles=True, demo=demo)
        self._guide_agrees(root)
        self.assertEqual(
            check.check_tree(root),
            ["demo/SKILL.md: frontmatter contains disable-model-invocation"],
        )

    def test_disable_model_invocation_after_one_mark_is_only_missing_frontmatter(self):
        demo = "---\nname: demo\ndescription: d\ndisable-model-invocation: true\n"
        root = self._root(roles=True, demo=demo)
        self._guide_agrees(root)
        self.assertEqual(
            check.check_tree(root),
            ["demo/SKILL.md: missing frontmatter"],
        )



class EffortSpeedTest(unittest.TestCase):
    def test_an_instruction_to_choose_a_dial_is_named(self):
        reason = (
            "pick, choose, select, raise, lower, change, use, or set has at most 180 intervening characters before reasoning level, effort level, fast or standard speed, fast mode, or standard speed, "
            "or pick, choose, or select has at most 60 intervening characters before effort; both matches are case-insensitive, stay on one line, and have no '.', '!', or '?' between the verb and dial; "
            "point at the Subagents section through an override"
        )
        body = (
            "---\nname: demo\ndescription: d\n---\n\n"
            "Pick each subagent's reasoning level and fast or standard speed for the job, per the Subagents section.\n"
            "Before each spawn, start from the role's line or default and pick the reasoning level and speed the job needs.\n"
            "Choose the effort level before the spawn.\n"
            "Choose the effort before the spawn.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(body)
            findings = check.check_tree(directory)
        lines = body.splitlines()
        expected = [
            f"demo/SKILL.md:{number}: {reason}: {lines[number - 1]}"
            for number in (6, 7, 8, 9)
        ]
        self.assertEqual(findings, expected)

    def test_a_subagents_pointer_and_a_described_reasoning_level_are_not_named(self):
        body = (
            "---\nname: demo\ndescription: d\n---\n\n"
            "Follow the Subagents section.\n"
            "Pass the seat the role this step names, per the Subagents section of the poteto-mode skill.\n"
            "A seat that names its own reasoning level keeps it when it is at or below the budget level.\n"
            "Before each spawn, start from the role the step names and pass that seat unchanged.\n"
            "When no roles file exists and it matters, ask whether the user wants to pick a model for each role and a reasoning budget now.\n"
            "A large or cross-cutting effort routes to figure-it-out.\n"
            'Use `--set "judgment and prose=claudeAgent/claude-opus-5-5?effort=xhigh"` for that seat.\n'
            "The reasoning level to pick is xhigh.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(body)
            self.assertEqual(check.check_tree(directory), [])


class ThreadOrganizeLintTest(unittest.TestCase):
    def _skill(self, body):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        skill = root / "demo"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\n" + body + "\n")
        return root

    def test_a_threadids_key_in_shipped_text_is_reported(self):
        root = self._skill("Archive with `t3_thread_organize`, `threadIds`: [t1, t2].")
        self.assertEqual(
            check.check_tree(root),
            [
                "demo/SKILL.md:6: shipped text uses a plural threadIds key. "
                "Pass one explicit threadId per t3_thread_organize call: Archive with `t3_thread_organize`, `threadIds`: [t1, t2]."
            ],
        )
    def test_a_thread_organize_archive_step_without_threadid_is_reported(self):
        root = self._skill("t3_thread_organize archive this thread.")
        self.assertEqual(
            check.check_tree(root),
            [
                "demo/SKILL.md:6: t3_thread_organize archive/settle/snooze step must name threadId. "
                "Add the one explicit threadId: t3_thread_organize archive this thread."
            ],
        )

    def test_a_thread_organize_step_that_names_threadid_passes(self):
        root = self._skill("t3_thread_organize archive this thread by threadId.")
        self.assertEqual(check.check_tree(root), [])
