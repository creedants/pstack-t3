from __future__ import annotations

import collections
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import NO_TESTS, QUOTED_BYTES, QUOTED_LINES

# The fields each event holds besides "ev".
EVENTS = {
    "start": {"seq": int},                                    # the test began
    "result": {"seq": int, "status": str, "problems": list},  # the test ended
    "behind": {"seq": int, "skip": bool},                     # the test did not start, behind a setUpClass or setUpModule that skipped or failed
    "skipped": {"method": str, "name": str},                  # the setUpClass of the class with that name or the setUpModule of the module with that name raised SkipTest
    "fixture": {"label": str, "traceback": str},              # an error or a failure outside a running test
    "done": {},                                               # the worker's suite returned
}
STATUSES = ("ok", "skip", "xfail", "xpass", "bad")
PROBLEM = {"kind": str, "label": str, "traceback": str}
# The name unittest reports a setUpClass or setUpModule under when it failed or raised SkipTest.
SETUP = re.compile(r"(setUpClass|setUpModule) \((.+)\)")


@dataclass(frozen=True)
class Outcome:
    word: str    # heads the notice and the block. "" for a kind that prints neither.
    fails: bool
    tally: str   # its name in the last line. "" is not listed.


# The order is the order of the last line.
OUTCOMES = {
    "fail": Outcome("FAIL", True, "failures"),
    "error": Outcome("ERROR", True, "errors"),
    "lost": Outcome("LOST", True, "lost"),
    "skip": Outcome("", False, "skipped"),
    "xfail": Outcome("", False, "expected failures"),
    "xpass": Outcome("UNEXPECTED SUCCESS", True, "unexpected successes"),
    "ok": Outcome("", False, ""),
}


@dataclass(frozen=True)
class Problem:
    kind: str        # "fail" or "error"
    label: str       # the text after the word in the block heading
    traceback: str


@dataclass(frozen=True)
class Verdict:
    kind: str        # "ok", "skip", "xfail", "xpass", "lost", or "bad"
    problems: tuple[Problem, ...] = ()   # non-empty exactly when kind is "bad"
    cause: str = ""  # why a "lost" test has no result


@dataclass(frozen=True)
class Test:
    seq: int         # index in discovery order, the key for every result
    id: str
    module: str
    cls: str         # its class, as class_name returns it


@dataclass(frozen=True)
class Shard:
    number: int
    module: str
    seqs: tuple[int, ...]
    classes: tuple[str, ...]   # the cls of the test at each seq, in the order of seqs


@dataclass(frozen=True)
class Ended:
    status: int
    over: float | None = None   # the limit, when the runner killed the worker for exceeding it


@dataclass(frozen=True)
class Accounting:
    verdicts: dict[int, Verdict]
    fixtures: tuple[Problem, ...]
    violations: tuple[tuple[int | None, str], ...]   # what the worker did that the shard may not, and the seq of the event when it holds one
    ending: str   # how the worker ended when a clean finish excludes that ending, else ""


class Refusal(Exception):
    pass


class Interrupted(Exception):
    pass


class LedgerError(Exception):
    pass


class Ledger:
    """Holds at most one verdict per seq of the plan. settle is the only writer of verdicts."""

    def __init__(self, size: int, required: tuple[int, ...] | None = None) -> None:
        self.size = size
        self.verdicts: dict[int, Verdict] = {}
        self.run_errors: list[Problem] = []
        self.required = range(size) if required is None else required

    def settle(self, seq: int, verdict: Verdict) -> None:
        if not 0 <= seq < self.size:
            raise LedgerError(f"seq {seq} is outside the plan of {self.size} tests")
        if seq in self.verdicts:
            raise LedgerError(f"seq {seq} is already settled")
        self.verdicts[seq] = verdict

    def close(self) -> None:
        for seq in self.required:
            if seq not in self.verdicts:
                self.settle(seq, Verdict("lost", cause="No worker reported it."))


def kinds(verdict: Verdict) -> list[str]:
    return [problem.kind for problem in verdict.problems] if verdict.kind == "bad" else [verdict.kind]


def fails(verdict: Verdict) -> bool:
    return any(OUTCOMES[kind].fails for kind in kinds(verdict))


def tallies(ledger: Ledger) -> collections.Counter:
    counted = collections.Counter(problem.kind for problem in ledger.run_errors)
    for verdict in ledger.verdicts.values():
        counted.update(kinds(verdict))
    return counted


def exit_status(ledger: Ledger) -> int:
    if any(OUTCOMES[kind].fails for kind in tallies(ledger)):
        return 1
    return 0 if ledger.size else NO_TESTS[0]


def plural(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"

def shaped(value, fields: dict) -> bool:
    return isinstance(value, dict) and value.keys() == fields.keys() and all(type(value[name]) is kind for name, kind in fields.items())


def parse_event(line: bytes) -> dict:
    """Return the event a line of a result file holds. Raise ValueError when the line is not an event."""
    try:
        event = json.loads(line)
    except RecursionError:
        raise ValueError("nested too deep") from None
    if not isinstance(event, dict) or not isinstance(event.get("ev"), str) or event["ev"] not in EVENTS:
        raise ValueError("no known event")
    if not shaped(event, {"ev": str, **EVENTS[event["ev"]]}):
        raise ValueError("wrong fields")
    if event["ev"] == "result":
        problems = event["problems"]
        if event["status"] not in STATUSES or (event["status"] == "bad") != bool(problems):
            raise ValueError("wrong status")
        if not all(shaped(problem, PROBLEM) and problem["kind"] in ("fail", "error") for problem in problems):
            raise ValueError("wrong problems")
    return event


def quoted(raw: bytes) -> str:
    return repr(raw[:QUOTED_BYTES].decode("utf-8", errors="replace")) + (" and more" if len(raw) > QUOTED_BYTES else "")

class Channel:
    """Makes a shard's result file and holds it open. Reads the complete lines the file gains through that descriptor and keeps those that are events."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_EXCL, 0o666)
        self.offset = 0
        self.lines = 0
        self.events: list[dict] = []
        self.errors: list[str] = []   # the report's text for what the file holds that is not an event, and for why the runner stopped reading it
        self.unquoted = 0             # lines that are not events, past the QUOTED_LINES that errors quotes
        self.stopped = False          # whether read found a change that `changed` names

    def changed(self, held: os.stat_result) -> str:
        """Return what happened to the file, or "" when the path leads to the open file and the file is no shorter than what was read."""
        try:
            named = os.stat(self.path)
        except OSError as error:
            return f"cannot be found at its path. {error}"
        if (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
            return "was replaced by another file at its path"
        if held.st_size < self.offset:
            return f"was shortened to {plural(held.st_size, 'byte')} after the runner had read {self.offset}"
        return ""

    def read(self, last: bool = False) -> bool:
        """Take the complete lines the file gained since the last read, then set `stopped` when `changed` names a change. Return whether the lines held an event. Read nothing once `stopped` is set."""
        if self.stopped:
            return False
        held = os.fstat(self.fd)
        data = os.pread(self.fd, max(held.st_size - self.offset, 0), self.offset)
        end = data.rfind(b"\n") + 1
        self.offset += end
        known = len(self.events)
        for line in data[:end].split(b"\n")[:-1]:
            self.lines += 1
            try:
                self.events.append(parse_event(line))
            except ValueError:
                if len(self.errors) < QUOTED_LINES:
                    self.errors.append(f"Line {self.lines} of its result file is not an event. It reads {quoted(line)}.")
                else:
                    self.unquoted += 1
        if last and self.unquoted:
            self.errors.append(f"The number of further lines of its result file that are not events is {self.unquoted}.")
        if last and end < len(data):
            self.errors.append(f"Its result file ends in a line with no newline. It reads {quoted(data[end:])}.")
        what = self.changed(held)
        if what:
            self.stopped = True
            self.errors.append(f"Its result file {what}. The runner stopped reading it.")
        return len(self.events) > known

    def close(self) -> None:
        os.close(self.fd)


@dataclass
class Running:
    shard: Shard
    proc: subprocess.Popen
    home: Path
    channel: Channel
    progress_at: float


def verdict_of(event: dict) -> Verdict:
    return Verdict(event["status"], tuple(Problem(**problem) for problem in event["problems"]))
