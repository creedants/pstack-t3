if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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


MISSING_WORKER = (
    "missing worker line: open the brief with 'Read <absolute path>/worker.md before anything else.'\n"
)


MISSING_PERSONA = (
    f"missing persona: open the brief with the body of {PERSONA_PATH}, without its frontmatter\n"
)


TEXT_BEFORE_MODE = (
    "text before the mode lines: the pasted roles.py mode lines go directly below "
    "the persona body, and the task text follows them\n"
)


WORKER = "Read /tmp/skills/poteto-mode/worker.md before anything else.\n\n"


TASK = "Implement the change described in the plan.\n"


DUP_M = "more than one Mode line: keep one\n"


DUP_P = "more than one Playbook line: keep one\n"


GATE_P = "Gate: pass at abc1234 by x/y\n\n"


BAD_M = "bad Mode value 'light (from restaurant.json)': expected full or light\n"


W_FULL = "unexpected Waived by mode line: Mode: full waives nothing, remove it\n"


def _gate_before(lines, prefix):
    parts = lines.splitlines()
    at = next(index for index, line in enumerate(parts) if line.startswith(prefix))
    parts.insert(at, "Gate: brigade")
    return "\n".join(parts) + "\n"


def _q(p,m,t="\nMode:\n"):
    return WORKER+f"{agent_body()}\n\n{mode_output('--playbook',p,'--attempt','first','--brief-mode',m)}\n"+TASK+t


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
        self.assert_check(completed, 1, MISSING_WORKER + MISSING_PERSONA + MISSING_PLAYBOOK + MISSING_MODE)

    def test_passing_brief(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 0, "ok playbooks/refactoring.md\n")

    def test_worker_line_before_the_persona_passes(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 0, "ok playbooks/refactoring.md\n")

    def test_a_second_worker_line_is_not_stripped(self):
        brief = (
            "Read /tmp/skills/poteto-mode/worker.md before anything else.\n"
            + WORKER +
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_a_worker_path_with_spaces_passes(self):
        brief = (
            "Read /tmp/my skills/poteto-mode/worker.md before anything else.\n\n"
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 0, "ok playbooks/refactoring.md\n")

    def test_persona_first_brief_is_refused_without_the_worker_line(self):
        brief = (
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_WORKER)

    def test_task_prose_above_the_mode_lines_is_refused(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nDo the thing now.\n\n"
            "Playbook: playbooks/refactoring.md\nMode: full\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, TEXT_BEFORE_MODE)

    def test_prose_before_the_attempt_line_is_refused(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n"
            "Mode source: brief\n\nDo the thing now.\n\nAttempt: first\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, TEXT_BEFORE_MODE)

    def test_prose_before_the_waived_by_mode_line_is_refused(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nPlaybook: playbooks/feature.md\nMode: light\n"
            "Mode source: brief\nAttempt: first\n\nDo the thing now.\n\n"
            "Waived by mode: Arena, Interrogate, Comment Sicko\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, TEXT_BEFORE_MODE)

    def test_gate_line_before_the_mode_block_is_refused(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nGate: brigade\n\n"
            "Playbook: playbooks/refactoring.md\nMode: full\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, TEXT_BEFORE_MODE)

    def test_malformed_playbook_heading_is_not_part_of_the_mode_block(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nPlaybook: fake heading\n\n"
            "Playbook: playbooks/refactoring.md\nMode: full\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, TEXT_BEFORE_MODE)

    def test_indented_playbook_heading_is_not_part_of_the_mode_block(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\n  Playbook: playbooks/refactoring.md\n\n"
            "Playbook: playbooks/refactoring.md\nMode: full\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, TEXT_BEFORE_MODE)

    def test_gate_line_above_task_is_refused(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nGate: fake\n\nDo the thing now.\n\n"
            "Playbook: playbooks/refactoring.md\nMode: full\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, TEXT_BEFORE_MODE)

    def test_mode_line_before_the_playbook_line_with_prose_between_is_refused(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nMode: full\n\nDo the thing now.\n\n"
            "Playbook: playbooks/refactoring.md\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, TEXT_BEFORE_MODE)

    def test_blank_line_between_the_playbook_and_mode_lines_passes(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\n\nMode: full\n\n"
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 0, "ok playbooks/refactoring.md\n")

    def test_task_gate_line_after_the_mode_block_passes(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n\n"
            + GATE_P
            + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 0, "ok playbooks/refactoring.md\n")

    def test_gate_line_inside_the_printed_full_block_is_refused(self):
        block = _gate_before(
            mode_output("--playbook", "refactoring", "--attempt", "first", "--brief-mode", "full"),
            "Attempt:",
        )
        brief = WORKER + f"{agent_body()}\n\n{block}\n" + TASK
        completed = check_text(brief)
        self.assert_check(completed, 1, TEXT_BEFORE_MODE)

    def test_gate_line_inside_the_printed_light_block_is_refused(self):
        block = _gate_before(
            mode_output("--playbook", "feature", "--attempt", "first", "--brief-mode", "light"),
            "Waived by mode:",
        )
        brief = WORKER + f"{agent_body()}\n\n{block}\n" + TASK
        completed = check_text(brief)
        self.assert_check(completed, 1, TEXT_BEFORE_MODE)

    def test_gate_line_after_the_complete_printed_block_passes(self):
        lines = mode_output("--playbook", "refactoring", "--attempt", "first", "--brief-mode", "full")
        brief = (
            WORKER +
            f"{agent_body()}\n\n{lines}\n"
            + GATE_P + TASK
        )
        completed = check_text(brief)
        self.assert_check(completed, 0, "ok playbooks/refactoring.md\n")

    def test_quoted_full_no_placement(self):
        self.assert_check(check_text(_q("refactoring","full")),1,DUP_M)

    def test_quoted_light_no_placement(self):
        self.assert_check(check_text(_q("bug-fix","light")),1,DUP_M)

    def test_quoted_full_waiver_no_placement(self):
        self.assert_check(check_text(_q("refactoring","full","\nWaived by mode:\n")),1,W_FULL)

    def test_reordered_light_waiver_gate_attempt_refused(self):
        self.assert_check(check_text(WORKER+f"{agent_body()}\n\nPlaybook: playbooks/bug-fix.md\nMode: light\nMode source: brief\nWaived by mode: Comment Sicko\nGate: brigade\nAttempt: first\n"+TASK),1,TEXT_BEFORE_MODE)

    def test_persona_truncated_by_one_sentence(self):
        body = agent_body()
        split = body.rfind(". ")
        self.assertGreater(split, 0)
        sentence = body[split + 2:]
        self.assertTrue(sentence.endswith("."))
        self.assertLess(len(sentence), len(body))
        brief = (
            WORKER +
            f"{body[:-len(sentence)].rstrip()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_persona_after_other_text(self):
        brief = (
            WORKER +
            f"Read this note first.\n\n{agent_body()}\n\n"
            "Playbook: playbooks/refactoring.md\nMode: full\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_frontmatter_paste(self):
        brief = (
            WORKER
            + PERSONA_PATH.read_text(encoding="utf-8")
            + "\nPlaybook: playbooks/refactoring.md\nMode: full\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_missing_playbook_line(self):
        brief = WORKER + f"{agent_body()}\n\nMode: full\n\n" + TASK
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PLAYBOOK)

    def test_unknown_playbook_name(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nPlaybook: playbooks/not-a-playbook.md\nMode: full\n"
        )
        completed = check_text(brief)
        self.assert_check(
            completed,
            1,
            f"unknown playbook 'playbooks/not-a-playbook.md': expected one of: {PLAYBOOK_NAMES}\n",
        )

    def test_two_playbook_lines(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\n"
            "Playbook: playbooks/refactoring.md\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: full\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, DUP_P)

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
            WORKER +
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
            "Attempt: first\n"
            "Waived by mode: Arena, Interrogate, Comment Sicko\n"
        )
        self.assert_check(check_text(brief), 0, "ok playbooks/feature.md\n")

    def test_two_mode_lines(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
            "Mode: light\n"
            "Attempt: retry\n"
        )
        self.assert_check(check_text(brief), 1, DUP_M)

    def test_mode_with_trailing_words(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light (from restaurant.json)\n"
            "Attempt: retry\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            BAD_M,
        )

    def test_waiver_under_full(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: full\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            W_FULL,
        )

    def test_wrong_waivers_feature_first(self):
        brief = (
            WORKER +
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
            WORKER +
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
            WORKER +
            f"{agent_body()}\n\n"
            "Playbook: playbooks/refactoring.md\n"
            "Attempt: nonsense\n"
            "Waived by mode: How\n"
            "Waived by mode: Arena, Interrogate, Comment Sicko\n"
            "\n" + TASK
        )
        self.assert_check(check_text(brief), 1, MISSING_MODE)

    def test_persona_and_playbook_only_fails(self):
        brief = (
            WORKER +
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\n"
        )
        self.assert_check(check_text(brief), 1, MISSING_MODE)

    def test_pasted_light_session_output_passes(self):
        lines = mode_output("--playbook", "feature", "--attempt", "first", "--session-mode", "light")
        self.assert_check(check_text(f"{WORKER}{agent_body()}\n\n{lines}"), 0, "ok playbooks/feature.md\n")

    def test_pasted_default_output_passes(self):
        lines = mode_output("--playbook", "refactoring", "--attempt", "first")
        self.assert_check(check_text(f"{WORKER}{agent_body()}\n\n{lines}"), 0, "ok playbooks/refactoring.md\n")

    def test_own_playbook_line_beside_pasted_output_fails(self):
        lines = mode_output("--playbook", "feature", "--attempt", "first", "--session-mode", "light")
        brief = (
            WORKER +
            f"{agent_body()}\n\nPlaybook: playbooks/feature.md\n{lines}"
        )
        self.assert_check(check_text(brief), 1, DUP_P)

    def test_bad_attempt_no_traceback(self):
        brief = (
            WORKER +
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
            WORKER +
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
        rows = [(s,a) for s in ("feature","bug-fix","refactoring","perf-issue","hillclimb","authoring-a-skill") for a in ("first","fix","bounce")]+[("investigation","first")]
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
                checked = check_text(f"{WORKER}{body}\n\n{completed.stdout}")
                self.assert_check(checked, 0, f"ok playbooks/{stem}.md\n")

    def test_problem_order_persona_playbook_mode(self):
        brief = "Playbook: playbooks/not-a-playbook.md\nMode: light (from restaurant.json)\n"
        self.assert_check(
            check_text(brief),
            1,
            MISSING_WORKER
            + MISSING_PERSONA
            + f"unknown playbook 'playbooks/not-a-playbook.md': expected one of: {PLAYBOOK_NAMES}\n"
            + BAD_M,
        )
