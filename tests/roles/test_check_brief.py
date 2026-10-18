import os
import tempfile
import unittest
from pathlib import Path
from tests import in_process
from .support import PERSONA_PATH, PLAYBOOK_NAMES, ROOT, agent_body, check_text, mode_output, run_check_brief, setUpModule


D25_BRIEF = """\
Act as the implementation sub-agent for this task.

Implement the pstack-t3 change described in /tmp/d25-architect/IMPL.md. Read that file, /tmp/d25-architect/SYNTHESIS.md, and /tmp/d25-architect/c3-sol/SKETCH.md before you edit. The synthesis wins where the sketch differs.

Work only in /tmp/pstack-work/pstack-t3-d25 on branch pstack-t3/d25. Do not create a worktree. Do not merge, rebase, push, or open a pull request. Do not edit README.md or CHANGELOG.md. Do not hand-edit skills/ or vendor/pstack. Do not spawn agents.

Two green commits, in order. First the roles defaults and the setup watch gate, with tests. Then scripts/sync_upstream.py and the override ports. Run tests and builds only through python3 /tmp/pstack-work/skills/landing/scripts/land.py slot -- <command>.

Commit messages have no Co-Authored-By trailer, no Generated-with footer, and no mention of an agent or a tool.

When both commits are green, write /tmp/d25-impl-notes.md as IMPL.md describes, including the changelog sentences you did not add."""


MISSING_PERSONA = (
    f"missing persona: open the brief with the body of {PERSONA_PATH}, without its frontmatter\n"
)


MISSING_PLAYBOOK = (
    "missing playbook: add one line 'Playbook: playbooks/<name>.md', "
    f"where <name> is one of: {PLAYBOOK_NAMES}\n"
)


MISSING_MODE = (
    "missing Mode: paste the lines 'roles.py mode --playbook <name> --attempt <kind>' prints, "
    "which include one line 'Mode: full' or 'Mode: light'\n"
)


class CheckBriefCliTest(unittest.TestCase):
    def assert_check(self, completed, code, stdout, stderr=""):
        self.assertEqual(completed.returncode, code, completed.stderr)
        self.assertEqual(completed.stdout, stdout)
        self.assertEqual(completed.stderr, stderr)

    def test_d25_brief(self):
        completed = check_text(D25_BRIEF)
        self.assert_check(completed, 1, MISSING_PERSONA + MISSING_PLAYBOOK + MISSING_MODE)

    def test_passing_brief(self):
        brief = (
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n\n"
            "Implement the change described in the plan.\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 0, "ok playbooks/refactoring.md\n")

    def test_persona_truncated_by_one_sentence(self):
        body = agent_body()
        split = body.rfind(". ")
        self.assertGreater(split, 0)
        sentence = body[split + 2:]
        self.assertTrue(sentence.endswith("."))
        self.assertLess(len(sentence), len(body))
        brief = f"{body[:-len(sentence)].rstrip()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_persona_after_other_text(self):
        brief = f"Read this note first.\n\n{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_frontmatter_paste(self):
        brief = PERSONA_PATH.read_text(encoding="utf-8") + "\nPlaybook: playbooks/refactoring.md\nMode: full\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_missing_playbook_line(self):
        brief = f"{agent_body()}\n\nMode: full\n\nImplement the change described in the plan.\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PLAYBOOK)

    def test_unknown_playbook_name(self):
        brief = f"{agent_body()}\n\nPlaybook: playbooks/not-a-playbook.md\nMode: full\n"
        completed = check_text(brief)
        self.assert_check(
            completed,
            1,
            f"unknown playbook 'playbooks/not-a-playbook.md': expected one of: {PLAYBOOK_NAMES}\n",
        )

    def test_two_playbook_lines(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/refactoring.md\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: full\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, "more than one Playbook line: keep one\n")

    def test_missing_brief_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.md"
            completed = run_check_brief(path)
        self.assert_check(completed, 2, "", f"error: {path}: brief not found\n")


class CheckBriefModeCliTest(unittest.TestCase):
    def assert_check(self, completed, code, stdout, stderr=""):
        self.assertEqual(completed.returncode, code, completed.stderr)
        self.assertEqual(completed.stdout, stdout)
        self.assertEqual(completed.stderr, stderr)

    def test_light_feature_first_passes(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
            "Attempt: first\n"
            "Waived by mode: Arena, Interrogate, Comment Sicko\n"
        )
        self.assert_check(check_text(brief), 0, "ok playbooks/feature.md\n")

    def test_two_mode_lines(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
            "Mode: light\n"
            "Attempt: retry\n"
        )
        self.assert_check(check_text(brief), 1, "more than one Mode line: keep one\n")

    def test_mode_with_trailing_words(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light (from restaurant.json)\n"
            "Attempt: retry\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            "bad Mode value 'light (from restaurant.json)': expected full or light\n",
        )

    def test_waiver_under_full(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: full\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            "unexpected Waived by mode line: Mode: full waives nothing, remove it\n",
        )

    def test_wrong_waivers_feature_first(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
            "Attempt: first\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            "wrong Waived by mode line: expected 'Waived by mode: Arena, Interrogate, Comment Sicko'\n",
        )

    def test_light_without_attempt(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            "missing Attempt: Mode: light needs one line 'Attempt: <kind>', "
            "where <kind> is one of: first, fix, bounce\n",
        )

    def test_code_delegate_without_mode_fails(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/refactoring.md\n"
            "Attempt: nonsense\n"
            "Waived by mode: How\n"
            "Waived by mode: Arena, Interrogate, Comment Sicko\n"
            "\nImplement the change described in the plan.\n"
        )
        self.assert_check(check_text(brief), 1, MISSING_MODE)

    def test_persona_and_playbook_only_fails(self):
        brief = f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\n"
        self.assert_check(check_text(brief), 1, MISSING_MODE)

    def test_pasted_light_session_output_passes(self):
        lines = mode_output("--playbook", "feature", "--attempt", "first", "--session-mode", "light")
        self.assert_check(check_text(f"{agent_body()}\n\n{lines}"), 0, "ok playbooks/feature.md\n")

    def test_pasted_default_output_passes(self):
        lines = mode_output("--playbook", "refactoring", "--attempt", "first")
        self.assert_check(check_text(f"{agent_body()}\n\n{lines}"), 0, "ok playbooks/refactoring.md\n")

    def test_own_playbook_line_beside_pasted_output_fails(self):
        lines = mode_output("--playbook", "feature", "--attempt", "first", "--session-mode", "light")
        brief = f"{agent_body()}\n\nPlaybook: playbooks/feature.md\n{lines}"
        self.assert_check(check_text(brief), 1, "more than one Playbook line: keep one\n")

    def test_bad_attempt_no_traceback(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
            "Attempt: retry\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            "bad Attempt value 'retry': expected one of: first, fix, bounce\n",
        )

    def test_unknown_playbook_skips_waivers(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/nope.md\n"
            "Mode: light\n"
            "Attempt: first\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            f"unknown playbook 'playbooks/nope.md': expected one of: {PLAYBOOK_NAMES}\n",
        )

    def test_mode_output_round_trips(self):
        rows = (
            ("feature", "first"),
            ("feature", "fix"),
            ("feature", "bounce"),
            ("bug-fix", "first"),
            ("bug-fix", "fix"),
            ("bug-fix", "bounce"),
            ("refactoring", "first"),
            ("refactoring", "fix"),
            ("refactoring", "bounce"),
            ("perf-issue", "first"),
            ("perf-issue", "fix"),
            ("perf-issue", "bounce"),
            ("hillclimb", "first"),
            ("hillclimb", "fix"),
            ("hillclimb", "bounce"),
            ("authoring-a-skill", "first"),
            ("authoring-a-skill", "fix"),
            ("authoring-a-skill", "bounce"),
            ("investigation", "first"),
        )
        body = agent_body()
        for stem, attempt in rows:
            with self.subTest(stem=stem, attempt=attempt):
                with tempfile.TemporaryDirectory() as directory:
                    completed = in_process.invoke(in_process.Invocation(
                        program=ROOT / "t3/scripts/roles.py",
                        argv=(
                            "mode",
                            "--brief-mode",
                            "light",
                            "--playbook",
                            stem,
                            "--attempt",
                            attempt,
                            "--cwd",
                            directory,
                        ),
                        env={**os.environ, "XDG_CONFIG_HOME": directory},
                    ))
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                checked = check_text(f"{body}\n\n{completed.stdout}")
                self.assert_check(checked, 0, f"ok playbooks/{stem}.md\n")

    def test_problem_order_persona_playbook_mode(self):
        brief = "Playbook: playbooks/not-a-playbook.md\nMode: light (from restaurant.json)\n"
        self.assert_check(
            check_text(brief),
            1,
            MISSING_PERSONA
            + f"unknown playbook 'playbooks/not-a-playbook.md': expected one of: {PLAYBOOK_NAMES}\n"
            + "bad Mode value 'light (from restaurant.json)': expected full or light\n",
        )
