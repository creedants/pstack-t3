"""Each `$B ticket add` and `$B fire` command line in the brigade skill that contains `--`.

The two live tests read the skill files. The three regression tests read only
the fixed fixture in this module. A valid edit to the live skill leaves them passing.

Expansion. The filler recognizes three marks when a line has them. `a|b|c` is one token of
alternatives, split on `|`. `[...]` wraps tokens the line includes. The brackets are not
arguments. `<words>` is a placeholder, and a placeholder may contain spaces. `<words>` glued to
other characters stays one token, as in `G<n>`, `A<n>`, and `reports/<dish>.md`. Fill every
placeholder. Keep every token inside a bracket group. Do not drop an optional to make a line pass.

A choice whose alternatives are exactly feature, fix, polish, upkeep, and study becomes one argv
per alternative, in the order written. Every other choice keeps its first alternative. `--source`
keeps `user`. `--priority` keeps `urgent`. The first kind choice on a line is the one that
expands. A later choice on that line keeps its first alternative. `<feed>` is not run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests.brigade.support as test_brigade
from tests.skill_commands_tests.support import COMMAND_LINE_FLOOR, FIXTURE_SKILL, FOLLOW_UP, PROJECT_DIR, PROJECT_SLUG, RESTAURANT, SLUG, Span, Step, Tape, skill_files
from tests.skill_commands_tests.tapes import _filled_argvs, _one_tape, _option, _report_tape, command_spans, skill_spans, tapes

def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def setUpModule():
    fence_git_environment()


class SkillCommandTest(test_brigade.BrigadeTest):
    def test_the_skill_keeps_at_least_seven_command_lines(self):
        spans = skill_spans()
        self.assertGreaterEqual(
            len(spans),
            COMMAND_LINE_FLOOR,
            f"skill yielded {len(spans)} command lines; the floor is {COMMAND_LINE_FLOOR}",
        )

    def test_each_filled_command_prints_its_recorded_line(self):
        for path in skill_files():
            for tape in tapes(command_spans(path.read_text(), path.name)):
                self.run_tape(tape)

    def test_kind_expansion_keeps_broken_ref_and_request_flags(self):
        """The fixture kind line keeps a broken ref or request flag for the parser."""
        spans = command_spans(FIXTURE_SKILL)
        kind_spans = [span for span in spans if len(_filled_argvs(span)) > 1]
        self.assertGreaterEqual(len(kind_spans), 1)
        span = kind_spans[0]
        for original, broken in (("<ref>", "--ref-BROKEN"), ("A<n>", "--request-BROKEN")):
            mutated = Span(span.line, span.text.replace(original, broken, 1), span.source)
            argvs = _filled_argvs(mutated)
            for argv in argvs:
                self.assertIn(broken, argv)
            tape = _one_tape(mutated, argvs)
            with self.assertRaises(AssertionError) as caught:
                self.run_tape(tape)
            self.assertIn(f"{mutated.source} line {mutated.line}", str(caught.exception))

    def test_fire_without_tickets_fails_with_its_line(self):
        """Each fixture fire line without its tickets flag fails with its line."""
        spans = command_spans(FIXTURE_SKILL)
        fire_spans = [span for span in spans if span.text.startswith("$B fire")]
        self.assertGreaterEqual(len(fire_spans), 2)
        for span in fire_spans:
            mutated = Span(span.line, span.text.replace("--tickets", "--tickets-BROKEN", 1), span.source)
            argvs = _filled_argvs(mutated)
            with self.assertRaises(AssertionError) as caught:
                _one_tape(mutated, argvs)
            self.assertIn(f"{mutated.source} line {span.line}", str(caught.exception))

    def test_renamed_from_report_fails_with_its_line(self):
        """The lone fixture report passes and the renamed fixture report fails with its line."""
        spans = command_spans(FIXTURE_SKILL)
        report_spans = [span for span in spans if "--from-report" in span.text]
        self.assertGreaterEqual(len(report_spans), 2)
        target = max(report_spans, key=lambda item: item.line)
        others = [span for span in spans if span is not target and "--from-report" in span.text]
        self.assertGreaterEqual(len(others), 1)
        lone = others[0]
        lone_argvs = _filled_argvs(lone)
        lone_tape = _report_tape(((lone, lone_argvs),))
        self.run_tape(lone_tape)
        mutated = Span(target.line, target.text.replace("--from-report", "--from-report-BROKEN", 1), target.source)
        mutated_argvs = _filled_argvs(mutated)
        mutated_tape = _one_tape(mutated, mutated_argvs)
        with self.assertRaises(AssertionError) as caught:
            self.run_tape(mutated_tape)
        self.assertIn(f"{mutated.source} line {mutated.line}", str(caught.exception))

    def run_tape(self, tape: Tape) -> None:
        saved = (self.temporary, self.store, self.project, self.at)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temporary = temporary
        self.store = Path(temporary.name) / "store"
        self.project = Path(temporary.name) / PROJECT_DIR
        self.project.mkdir()
        self.at = self.store / PROJECT_SLUG / SLUG
        try:
            state = str(Path(temporary.name) / "state")
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state}):
                if tape.landing:
                    self.init_landing()
                for step in tape.steps:
                    self._run_step(step)
        finally:
            self.temporary, self.store, self.project, self.at = saved

    def _run_step(self, step: Step) -> None:
        argv = tuple(str(self.project) if word == "<project>" else word for word in step.argv)
        if "--from-report" in argv:
            self._write_report(argv, step.line, step.source)
        owner = test_brigade._owner_words(self.at)
        result = subprocess.run(
            [sys.executable, str(test_brigade.SCRIPT), "--store", str(self.store), "--at", str(self.at), *owner, *argv],
            capture_output=True,
            text=True,
        )
        shown = " ".join(argv)
        if result.returncode != 0:
            self.fail(f"{step.source} line {step.line}: {shown}\n{result.stderr}")
        if argv[0] == "open":
            self.assertEqual(result.stdout, f"opened {self.at}\n", f"{step.source} line {step.line}: {shown}")
            return
        if step.expect is not None:
            self.assertEqual(result.stdout, step.expect, f"{step.source} line {step.line}: {shown}\n{result.stderr}")

    def _write_report(self, argv: tuple[str, ...], line: int, source: str = "SKILL.md") -> None:
        name = Path(_option(argv, "--from-report", line, source)).name
        directory = self.at / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(FOLLOW_UP, encoding="utf-8")


def load_tests(loader, tests, pattern):
    """Return a suite of this module's test methods.

    SkillCommandTest subclasses BrigadeTest for init_landing and the temp project.
    The default loader would also collect BrigadeTest's test_* methods.
    """
    suite = loader.suiteClass()
    for name in (
        "test_the_skill_keeps_at_least_seven_command_lines",
        "test_each_filled_command_prints_its_recorded_line",
        "test_kind_expansion_keeps_broken_ref_and_request_flags",
        "test_fire_without_tickets_fails_with_its_line",
        "test_renamed_from_report_fails_with_its_line",
    ):
        suite.addTest(SkillCommandTest(name))
    return suite
