from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from . import ENTRY
from .events import SETUP, STATUSES, plural

def enter(start: Path) -> None:
    """Put the start directory and its parent first on sys.path and drop every other entry for this script's directory."""
    here = ENTRY.parent
    sys.path[:] = [str(start), str(start.parent)] + [entry for entry in sys.path if Path(entry).resolve() != here]


def class_name(test) -> str:
    """Return the name unittest gives the class of a test in the label of its setUpClass."""
    return f"{type(test).__module__}.{type(test).__qualname__}"


def discover(start: Path) -> list:
    """Return the tests under start in discovery order. The listing child and every worker call this."""
    def flatten(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from flatten(item)
            else:
                yield item

    enter(start)
    return list(flatten(unittest.TestLoader().discover(str(start))))


class Recorder(unittest.TestResult):
    """Turns what unittest reports into the start, result, behind, skipped, and fixture events of EVENTS. A subtest counts as its test."""

    def __init__(self, tests: list[tuple[unittest.TestCase, int]], emit) -> None:
        super().__init__()
        self.tests = tests
        self.emit = emit
        self.reached = 0     # the index in tests of the first test the suite has not reached
        self.current = None
        # The setUpClass and setUpModule fixtures that failed or skipped since the last test started. Each is the method, the name of its class or module, and whether it skipped.
        self.setups: list[tuple[str, str, bool]] = []

    def pass_over(self, index: int) -> None:
        """Emit a behind event for each test from `reached` up to index whose class or module a fixture in `setups` names."""
        for test, seq in self.tests[self.reached:index]:
            names = {"setUpClass": class_name(test), "setUpModule": type(test).__module__}
            skips = [skip for method, name, skip in self.setups if names[method] == name]
            if skips:
                self.emit({"ev": "behind", "seq": seq, "skip": skips[0]})
        self.reached, self.setups = index, []

    def startTest(self, test):
        # The suite runs its tests in order, and one test object may sit at two positions.
        index = next(index for index in range(self.reached, len(self.tests)) if self.tests[index][0] is test)
        self.pass_over(index)
        self.reached = index + 1
        self.current, self.seq, self.status, self.problems = test, self.tests[index][1], "ok", []
        self.emit({"ev": "start", "seq": self.seq})

    def stopTest(self, test):
        self.emit({"ev": "result", "seq": self.seq, "status": "bad" if self.problems else self.status,
                   "problems": self.problems})
        self.current = None

    def setup(self, holder, skip: bool) -> None:
        named = SETUP.fullmatch(holder.id())
        if named:
            self.setups.append((named[1], named[2], skip))
            if skip:
                self.emit({"ev": "skipped", "method": named[1], "name": named[2]})

    def problem(self, kind, test, label, err):
        text = self._exc_info_to_string(err, test)
        if test is self.current:
            self.problems.append({"kind": kind, "label": label, "traceback": text})
        else:
            self.emit({"ev": "fixture", "label": label, "traceback": text})
            self.setup(test, False)

    def addError(self, test, err):
        self.problem("error", test, str(test), err)

    def addFailure(self, test, err):
        self.problem("fail", test, str(test), err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.problem("fail" if issubclass(err[0], test.failureException) else "error", test, str(subtest), err)

    def addSuccess(self, test):
        pass

    def addSkip(self, test, reason):
        if getattr(test, "test_case", test) is self.current:
            self.status = "skip"
        else:
            self.setup(test, True)

    def addExpectedFailure(self, test, err):
        self.status = "xfail"

    def addUnexpectedSuccess(self, test):
        self.status = "xpass"


def list_main(start: Path, out: Path) -> int:
    out.write_text(json.dumps([{"id": test.id(), "module": type(test).__module__, "cls": class_name(test)}
                               for test in discover(start)]))
    return 0


def worker_main(spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text())
    found = discover(Path(spec["start"]))
    mine, listed = [test.id() for test in found], [entry["id"] for entry in json.loads(Path(spec["plan"]).read_text())]
    if mine != listed:
        differs = next((at for at, pair in enumerate(zip(mine, listed)) if pair[0] != pair[1]), min(len(mine), len(listed)))
        print(f"run_tests: this worker discovered {plural(len(mine), 'test')} and the listing discovered {len(listed)}. "
              f"The ids first differ at position {differs}.", file=sys.stderr)
        return 1
    tests = [(found[seq], seq) for seq in spec["seqs"]]
    with open(spec["results"], "a") as results:
        def emit(event: dict) -> None:
            results.write(json.dumps(event) + "\n")
            results.flush()

        recorder = Recorder(tests, emit)
        unittest.TestSuite(test for test, _ in tests).run(recorder)
        recorder.pass_over(len(tests))
        emit({"ev": "done"})
    return 0
