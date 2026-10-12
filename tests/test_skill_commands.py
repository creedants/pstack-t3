"""Each `$B ticket add` and `$B fire` command line in the brigade skill that contains `--`.

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
import re
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from tests import test_brigade

COMMAND_LINE_FLOOR = 7

FOLLOW_UP = "## Follow-ups\n\n- File the skill line.\n"

# Quoted "..." fills from "...quoted". A bare "..." fills from "...bare".
FILL = {
    "<request>": "Skill line request",
    "<outcome>": "Skill line outcome",
    "<one line>": "skill line",
    "<kind>": "fix",
    "<playbook>": "bug-fix",
    "<minutes>": "60",
    "<files and directories the dish will change>": "src/a",
    "<name>": "skill-line",
    "<dish>": "D1",
    "<ref>": "app-7f3",
    "<n>": "1",
    "...quoted": "File the follow-up",
    "...bare": "T1",
}
KIND_CHOICE = frozenset({"feature", "fix", "polish", "upkeep", "study"})
RESTAURANT = "Perf"
# open slugs --name. Report refs and the lease holder use that slug.
SLUG = "perf"
# open ignores --at and joins the store, the project directory's slug, and the restaurant slug.
# "Bridge Kit" is the directory BrigadeTest uses, so the opened path is {store}/bridge-kit/perf.
PROJECT_DIR = "Bridge Kit"
PROJECT_SLUG = "bridge-kit"
_PLACEHOLDER = re.compile(r"<[^>]*>")
_COMMAND = re.compile(r"^\$B (?:ticket add|fire)\b")


def skill_text() -> str:
    """The skill markdown. BRIGADE_SKILL_MD overrides the path. This is the only reader of that variable."""
    override = os.environ.get("BRIGADE_SKILL_MD")
    path = Path(override) if override else Path(__file__).resolve().parents[1] / "t3/added/brigade/SKILL.md"
    return path.read_text()


@dataclass(frozen=True)
class Span:
    """One command line as the scanner left it.

    `line` is the 1-based line in the skill file. `text` includes the `$B` prefix, excludes a
    trailing fence comment, and excludes prose outside the code span.
    """

    line: int
    text: str


@dataclass(frozen=True)
class Step:
    """One subprocess on a tape.

    `argv` follows `brigade.py --store` and `--at`. `expect` is the stdout a step must print,
    including its trailing newline. `open` passes None and must print `opened` plus the restaurant path.
    A non-zero status fails the tape and names `line`.
    """

    line: int
    argv: tuple[str, ...]
    expect: str | None


@dataclass(frozen=True)
class Tape:
    """Ordered argv that one fresh store runs, from `open` through the skill commands.

    `landing` is true when the skill step is `fire` with `--paths`. The runner calls `init_landing`
    only then. It always points `XDG_STATE_HOME` at the tape's temp state directory.
    """

    landing: bool
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Token:
    """One word after the scan, before placeholder fill.

    `alternatives` is left to right. A word with no bar is a one-tuple. `quoted` is true when
    double quotes grouped the word, which is how `...` chooses its fill.
    """

    alternatives: tuple[str, ...]
    quoted: bool = False


def command_spans(text: str) -> tuple[Span, ...]:
    """Code spans that are `$B ticket add` or `$B fire` command lines, in file order.

    A candidate is one inline code span, or one physical line of a fenced code block after
    `strip_shell_comment`. It is a command line when the stripped text matches
    `^$B (ticket add|fire)\\b` and contains `--`. A span that is only `$B ticket add` or
    `$B fire` is a bare mention. Prose after the closing backtick is not appended.
    """
    found: list[Span] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        mark = _fence_mark(line)
        if fence is None:
            if mark is not None:
                fence = mark
                continue
            for raw in _inline_codes(line):
                candidate = raw.strip()
                if _is_command(candidate):
                    found.append(Span(number, candidate))
            continue
        if mark is not None and len(mark) >= len(fence):
            fence = None
            continue
        candidate = strip_shell_comment(line).strip()
        if _is_command(candidate):
            found.append(Span(number, candidate))
    return tuple(found)


def strip_shell_comment(line: str) -> str:
    """Cut from the first space-hash that is outside single and double quotes."""
    quote = ""
    for index, character in enumerate(line):
        if quote:
            if character == quote:
                quote = ""
            continue
        if character in ("'", '"'):
            quote = character
            continue
        if character == "#" and index > 0 and line[index - 1] == " ":
            return line[: index - 1]
    return line


def tapes(spans: tuple[Span, ...]) -> tuple[Tape, ...]:
    """Each span is its own store, except from-report spans, which share one. A kind choice is several skill steps on that store.

    Tokenize each span, fill placeholders from FILL, expand the kind choice, then wrap the argv
    in the setup steps that make that argv succeed. When a kind choice has `--request` or `--ref`
    with the placeholder fill after the flag, those become `A1` upward and `app-7f3-<kind>`.
    A token after the flag that is not that fill stays as written, so a broken flag reaches the parser.
    Any other line keeps FILL, so `<n>` is `1` and `<ref>` is `app-7f3`.
    """
    prepared = tuple((span, _filled_argvs(span)) for span in spans)
    reports = tuple((span, argvs) for span, argvs in prepared if _is_from_report(argvs[0]))
    built: list[Tape] = []
    placed = False
    for span, argvs in prepared:
        if _is_from_report(argvs[0]):
            if not placed:
                built.append(_report_tape(reports))
                placed = True
            continue
        built.append(_one_tape(span, argvs))
    return tuple(built)


def tokenize(text: str) -> tuple[Token, ...]:
    """Drop the leading `$B`. Then scan left to right.

    Whitespace separates tokens. A double quote groups one token and is not part of the argv.
    `[` opens an optional group that is always kept. `]` closes it and is not part of a token.
    Groups do not nest. `<` ... `>` is part of the current token and may contain spaces, so
    `<files and directories the dish will change>` is one token and `G<n>` stays one token.
    A `|` splits a choice after the token is whole. `user|github|<feed>` is three alternatives.
    """
    body = text.strip()
    if body.startswith("$B"):
        body = body[2:].lstrip()
    tokens: list[Token] = []
    index = 0
    length = len(body)
    while index < length:
        while index < length and body[index] in " \t":
            index += 1
        if index >= length:
            break
        if body[index] in "[]":
            index += 1
            continue
        quoted = False
        if body[index] == '"':
            quoted = True
            index += 1
        buf: list[str] = []
        while index < length:
            character = body[index]
            if quoted:
                if character == '"':
                    index += 1
                    break
                if character == "<":
                    index = _take_placeholder(body, index, buf)
                    continue
                buf.append(character)
                index += 1
                continue
            if character in " \t[]":
                break
            if character == '"':
                break
            if character == "<":
                index = _take_placeholder(body, index, buf)
                continue
            buf.append(character)
            index += 1
        raw = "".join(buf)
        if raw:
            tokens.append(Token(_split_bar(raw), quoted))
    return tuple(tokens)


def _fence_mark(line: str) -> str | None:
    stripped = line.lstrip(" \t")
    if not stripped.startswith("`"):
        return None
    size = 0
    while size < len(stripped) and stripped[size] == "`":
        size += 1
    if size < 3 or "`" in stripped[size:]:
        return None
    return stripped[:size]


def _inline_codes(line: str) -> tuple[str, ...]:
    found: list[str] = []
    index = 0
    length = len(line)
    while index < length:
        if line[index] != "`":
            index += 1
            continue
        start = index
        while index < length and line[index] == "`":
            index += 1
        width = index - start
        cursor = index
        while cursor < length:
            if line[cursor] != "`":
                cursor += 1
                continue
            run = cursor
            while cursor < length and line[cursor] == "`":
                cursor += 1
            if cursor - run == width:
                found.append(line[index:run])
                index = cursor
                break
        else:
            continue
    return tuple(found)


def _is_command(text: str) -> bool:
    return bool(_COMMAND.match(text)) and "--" in text


def _take_placeholder(text: str, index: int, buf: list[str]) -> int:
    end = text.find(">", index)
    if end == -1:
        buf.append(text[index])
        return index + 1
    buf.append(text[index : end + 1])
    return end + 1


def _split_bar(token: str) -> tuple[str, ...]:
    parts: list[str] = []
    buf: list[str] = []
    index = 0
    while index < len(token):
        if token[index] == "<":
            end = token.find(">", index)
            if end == -1:
                buf.append(token[index:])
                break
            buf.append(token[index : end + 1])
            index = end + 1
            continue
        if token[index] == "|":
            parts.append("".join(buf))
            buf = []
            index += 1
            continue
        buf.append(token[index])
        index += 1
    parts.append("".join(buf))
    return tuple(parts)


def _fill(token: str, line: int, quoted: bool) -> str:
    if token == "...":
        return FILL["...quoted" if quoted else "...bare"]
    def replace(match: re.Match[str]) -> str:
        key = match.group(0)
        if key not in FILL:
            raise AssertionError(f"SKILL.md line {line}: unknown placeholder {key}")
        return FILL[key]
    return _PLACEHOLDER.sub(replace, token)


def _filled_argvs(span: Span) -> tuple[tuple[str, ...], ...]:
    tokens = tokenize(span.text)
    kind_at = next((index for index, token in enumerate(tokens) if frozenset(token.alternatives) == KIND_CHOICE), None)
    if kind_at is None:
        return (_fill_tokens(tokens, span.line, None),)
    argvs = []
    for number, kind in enumerate(tokens[kind_at].alternatives, 1):
        argv = list(_fill_tokens(tokens, span.line, (kind_at, kind)))
        _replace(argv, "--ref", f"{FILL['<ref>']}-{kind}", FILL["<ref>"])
        _replace(argv, "--request", f"A{number}", f"A{FILL['<n>']}")
        argvs.append(tuple(argv))
    return tuple(argvs)


def _fill_tokens(tokens: tuple[Token, ...], line: int, kind: tuple[int, str] | None) -> tuple[str, ...]:
    argv = []
    for index, token in enumerate(tokens):
        if kind is not None and index == kind[0]:
            argv.append(kind[1])
            continue
        argv.append(_fill(token.alternatives[0], line, token.quoted))
    return tuple(argv)


def _replace(argv: list[str], flag: str, value: str, expected: str) -> None:
    if flag not in argv:
        return
    at = argv.index(flag)
    if at + 1 >= len(argv):
        return
    if argv[at + 1] != expected:
        return
    argv[at + 1] = value


def _option(argv: tuple[str, ...], flag: str, line: int) -> str:
    if flag not in argv:
        raise AssertionError(f"SKILL.md line {line}: missing {flag} in {' '.join(argv)}")
    at = argv.index(flag)
    if at + 1 >= len(argv):
        raise AssertionError(f"SKILL.md line {line}: missing value after {flag} in {' '.join(argv)}")
    return argv[at + 1]


def _is_from_report(argv: tuple[str, ...]) -> bool:
    return argv[:2] == ("ticket", "add") and "--from-report" in argv


def _open_step(line: int, workers: int | None = None) -> Step:
    argv: tuple[str, ...] = ("open", "--project-root", "<project>", "--name", RESTAURANT)
    if workers is not None:
        argv = (*argv, "--workers", str(workers))
    return Step(line, argv, None)


def _goal_step(line: int) -> Step:
    return Step(
        line,
        ("goal", "add", "--name", "Skill line", "--purpose", "Hold the ticket", "--done", "The ticket is filed"),
        "G1\n",
    )


def _one_tape(span: Span, argvs: tuple[tuple[str, ...], ...]) -> Tape:
    if len(argvs) > 1:
        return _kind_tape(span.line, argvs)
    argv = argvs[0]
    if argv[0] == "fire":
        return _fire_tape(span.line, argv)
    if argv[:2] == ("ticket", "add"):
        return _plain_add_tape(span.line, argv)
    raise AssertionError(f"SKILL.md line {span.line}: no tape for {' '.join(argv)}")


def _kind_tape(line: int, argvs: tuple[tuple[str, ...], ...]) -> Tape:
    """The setup ticket prints T1. Each expanded command prints the next id."""
    steps = [_open_step(line)]
    if any("--goal" in argv for argv in argvs):
        steps.append(_goal_step(line))
    steps.append(Step(line, ("ticket", "add", "--summary", "Skill line request", "--kind", "fix"), "T1\n"))
    for index, argv in enumerate(argvs, 1):
        steps.append(Step(line, argv, f"T{index + 1}\n"))
    return Tape(False, tuple(steps))


def _fire_tape(line: int, argv: tuple[str, ...]) -> Tape:
    """Create every ticket id the line names, including a gap id next_id would otherwise skip.

    A missing or malformed `--tickets` fails with the line number.
    """
    raw = _option(argv, "--tickets", line)
    named = raw.split(",")
    for ident in named:
        if not ident.startswith("T") or not ident[1:].isdigit():
            raise AssertionError(f"SKILL.md line {line}: bad ticket id {ident} in {' '.join(argv)}")
    if "--ride-along" in argv:
        ride_raw = _option(argv, "--ride-along", line)
        ride = set(ride_raw.split(",")) if ride_raw else set()
        for ident in ride:
            if not ident.startswith("T") or not ident[1:].isdigit():
                raise AssertionError(f"SKILL.md line {line}: bad ticket id {ident} in {' '.join(argv)}")
    else:
        ride = set()
    if "--kind" in argv:
        kind = _option(argv, "--kind", line)
    else:
        kind = "fix"
    lead = named[0]
    highest = max(int(ident[1:]) for ident in named)
    steps = [_open_step(line)]
    unused = 0
    for number in range(1, highest + 1):
        ident = f"T{number}"
        if ident == lead:
            ticket = ("ticket", "add", "--kind", kind, "--summary", "Lead ticket")
        elif ident in ride:
            ticket = ("ticket", "add", "--kind", "fix", "--priority", "low", "--summary", "Ride along")
        else:
            unused += 1
            summary = "Unused id" if unused == 1 else f"Unused {ident}"
            ticket = ("ticket", "add", "--kind", "fix", "--summary", summary)
        steps.append(Step(line, ticket, f"T{number}\n"))
    if "--paths" in argv:
        expect = f"D1 (lease L1 held by {SLUG}/D1)\n"
    else:
        expect = "D1\n"
    steps.append(Step(line, argv, expect))
    return Tape("--paths" in argv, tuple(steps))


def _plain_add_tape(line: int, argv: tuple[str, ...]) -> Tape:
    steps = [_open_step(line)]
    if "--goal" in argv:
        steps.append(_goal_step(line))
    steps.append(Step(line, argv, "T1\n"))
    return Tape(False, tuple(steps))


def _report_tape(items: tuple[tuple[Span, tuple[tuple[str, ...], ...]], ...]) -> Tape:
    """From-report spans share one store.

    Each report stem is fired once, in numeric dish order, with no paths. Dishes from 1 through
    the highest stem exist, so a lone D2 still has D1. The store opens with workers for the highest
    dish. A dry-run step does not take a ticket id. The first real add is the next id after those lead tickets.
    """
    parsed: list[tuple[Span, tuple[tuple[str, ...], ...], str, int]] = []
    for span, argvs in items:
        raw = _option(argvs[0], "--from-report", span.line)
        stem = Path(raw).stem
        if not stem.startswith("D") or not stem[1:].isdigit():
            raise AssertionError(f"SKILL.md line {span.line}: bad report {raw} in {' '.join(argvs[0])}")
        parsed.append((span, argvs, stem, int(stem[1:])))
    highest = max(number for _, _, _, number in parsed)
    line = items[0][0].line
    steps = [_open_step(line, highest)]
    for number in range(1, highest + 1):
        steps.append(Step(line, ("ticket", "add", "--kind", "fix", "--summary", f"Lead {number}"), f"T{number}\n"))
        steps.append(Step(
            line,
            ("fire", "--tickets", f"T{number}", "--station", "bug-fix", "--summary", f"Dish {number}"),
            f"D{number}\n",
        ))
    next_ticket = highest + 1
    item = _follow_up_item()
    for span, argvs, _, _ in parsed:
        argv = argvs[0]
        name = Path(_option(argv, "--from-report", span.line)).name
        ref = f"{SLUG}/reports/{name}#1"
        if "--dry-run" in argv:
            expect = f"would add from {ref}: {item}\n"
        else:
            expect = f"T{next_ticket} added from {ref}: {item}\n"
            next_ticket += 1
        steps.append(Step(span.line, argv, expect))
    return Tape(False, tuple(steps))


def _follow_up_item() -> str:
    for line in FOLLOW_UP.splitlines():
        if line.startswith("- "):
            return line[2:]
    raise AssertionError("FOLLOW_UP has no bullet")


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
        spans = command_spans(skill_text())
        self.assertGreaterEqual(
            len(spans),
            COMMAND_LINE_FLOOR,
            f"SKILL.md yielded {len(spans)} command lines; the floor is {COMMAND_LINE_FLOOR}",
        )

    def test_each_filled_command_prints_its_recorded_line(self):
        for tape in tapes(command_spans(skill_text())):
            self.run_tape(tape)

    def test_kind_expansion_keeps_broken_ref_and_request_flags(self):
        spans = command_spans(skill_text())
        kind_spans = [span for span in spans if len(_filled_argvs(span)) > 1]
        self.assertGreaterEqual(len(kind_spans), 1)
        span = kind_spans[0]
        for original, broken in (("<ref>", "--ref-BROKEN"), ("A<n>", "--request-BROKEN")):
            mutated = Span(span.line, span.text.replace(original, broken, 1))
            argvs = _filled_argvs(mutated)
            for argv in argvs:
                self.assertIn(broken, argv)
            tape = _one_tape(mutated, argvs)
            with self.assertRaises(AssertionError) as caught:
                self.run_tape(tape)
            self.assertIn(f"SKILL.md line {mutated.line}", str(caught.exception))

    def test_fire_without_tickets_fails_with_its_line(self):
        spans = command_spans(skill_text())
        fire_spans = [span for span in spans if span.text.startswith("$B fire")]
        self.assertGreaterEqual(len(fire_spans), 2)
        for span in fire_spans:
            mutated = Span(span.line, span.text.replace("--tickets", "--tickets-BROKEN", 1))
            argvs = _filled_argvs(mutated)
            with self.assertRaises(AssertionError) as caught:
                _one_tape(mutated, argvs)
            self.assertIn(f"SKILL.md line {span.line}", str(caught.exception))

    def test_renamed_from_report_fails_with_its_line(self):
        spans = command_spans(skill_text())
        report_spans = [span for span in spans if "--from-report" in span.text]
        self.assertGreaterEqual(len(report_spans), 2)
        target = max(report_spans, key=lambda item: item.line)
        others = [span for span in spans if span is not target and "--from-report" in span.text]
        self.assertGreaterEqual(len(others), 1)
        lone = others[0]
        lone_argvs = _filled_argvs(lone)
        lone_tape = _report_tape(((lone, lone_argvs),))
        self.run_tape(lone_tape)
        mutated = Span(target.line, target.text.replace("--from-report", "--from-report-BROKEN", 1))
        mutated_argvs = _filled_argvs(mutated)
        mutated_tape = _one_tape(mutated, mutated_argvs)
        with self.assertRaises(AssertionError) as caught:
            self.run_tape(mutated_tape)
        self.assertIn(f"SKILL.md line {mutated.line}", str(caught.exception))

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
            self._write_report(argv, step.line)
        owner = test_brigade._owner_words(self.at)
        result = subprocess.run(
            [sys.executable, str(test_brigade.SCRIPT), "--store", str(self.store), "--at", str(self.at), *owner, *argv],
            capture_output=True,
            text=True,
        )
        shown = " ".join(argv)
        if result.returncode != 0:
            self.fail(f"SKILL.md line {step.line}: {shown}\n{result.stderr}")
        if argv[0] == "open":
            self.assertEqual(result.stdout, f"opened {self.at}\n", f"SKILL.md line {step.line}: {shown}")
            return
        if step.expect is not None:
            self.assertEqual(result.stdout, step.expect, f"SKILL.md line {step.line}: {shown}\n{result.stderr}")

    def _write_report(self, argv: tuple[str, ...], line: int) -> None:
        name = Path(_option(argv, "--from-report", line)).name
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
