#!/usr/bin/env python3
"""Run every test under the start directory in worker processes and print one merged report.

A listing child discovers the tests. A test the loader built in place of a module gets its outcome there.
The runner cuts the other tests into shards of at most SHARD_SIZE tests of one module and runs each shard
in a fresh process. A worker appends one JSON line per event to its own
result file. The runner settles exactly one verdict per discovered test, so `Ran N tests` prints the
number discovery returned.

A test whose worker reported no result for it is LOST. The exit status is 0 when the report ends in OK,
1 when it ends in FAILED, 2 when the runner did not start the tests, and 130 on SIGINT or SIGTERM.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARD_SIZE = 10
# land.py's governor defaults to one slot per four cores, with at least 1 slot and at most 4. A run inside one slot starts at most four workers by default.
MAX_DEFAULT_JOBS = 4
DEFAULT_TIMEOUT = 300.0
# A child holds the start directory of the runner that started it. The runner refuses that directory.
ACTIVE = "PSTACK_RUN_TESTS"
POLL_SECONDS = 0.05
PROGRESS_SECONDS = 30.0
SEP1 = "=" * 70
SEP2 = "-" * 70


@dataclass(frozen=True)
class Outcome:
    word: str    # heads the notice and the block. "" prints neither.
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
    output: str = "" # what the shard wrote to stdout and stderr, on the first "lost" test of that shard


@dataclass(frozen=True)
class Test:
    seq: int         # index in discovery order, the key for every result
    id: str
    module: str
    preset: Verdict | None   # the outcome of a test the loader built in place of a module


@dataclass(frozen=True)
class Shard:
    number: int
    module: str
    seqs: tuple[int, ...]


@dataclass(frozen=True)
class Ended:
    status: int
    over: float | None = None   # the limit, when the runner killed the worker for exceeding it


@dataclass(frozen=True)
class Accounting:
    verdicts: dict[int, Verdict]
    fixtures: tuple[Problem, ...]
    violations: tuple[tuple[int, str], ...]   # a seq and what the worker did with it


@dataclass
class Running:
    shard: Shard
    proc: subprocess.Popen
    home: Path
    size: int
    progress_at: float


class Refusal(Exception):
    pass


class LedgerError(Exception):
    pass


class Ledger:
    """Holds at most one verdict per seq of the plan. settle is the only writer of verdicts."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.verdicts: dict[int, Verdict] = {}
        self.run_errors: list[Problem] = []

    def settle(self, seq: int, verdict: Verdict) -> None:
        if not 0 <= seq < self.size:
            raise LedgerError(f"seq {seq} is outside the plan of {self.size} tests")
        if seq in self.verdicts:
            raise LedgerError(f"seq {seq} is already settled")
        self.verdicts[seq] = verdict

    def close(self) -> None:
        for seq in range(self.size):
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
    return 1 if any(OUTCOMES[kind].fails for kind in tallies(ledger)) else 0


def count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def block(kind: str, label: str, body: str) -> str:
    return f"{SEP1}\n{OUTCOMES[kind].word}: {label}\n{SEP2}\n{body}\n" if body else f"{SEP1}\n{OUTCOMES[kind].word}: {label}\n"


def lost_body(verdict: Verdict) -> str:
    if not verdict.output:
        return verdict.cause + "\n"
    lines = "".join(f"  {line}\n" for line in verdict.output.splitlines())
    return f"{verdict.cause}\nOutput of its shard:\n{lines}"


def render(plan: tuple[Test, ...], ledger: Ledger, seconds: float) -> str:
    out = []
    for seq in sorted(ledger.verdicts):
        verdict = ledger.verdicts[seq]
        out.extend(block(problem.kind, problem.label, problem.traceback) for problem in verdict.problems)
        if verdict.kind == "xpass":
            out.append(block("xpass", plan[seq].id, ""))
        if verdict.kind == "lost":
            out.append(block("lost", plan[seq].id, lost_body(verdict)))
    out.extend(block(problem.kind, problem.label, problem.traceback) for problem in ledger.run_errors)
    ran = len(ledger.verdicts)
    counted = tallies(ledger)
    listed = ", ".join(f"{outcome.tally}={counted[kind]}" for kind, outcome in OUTCOMES.items() if outcome.tally and counted[kind])
    word = "FAILED" if exit_status(ledger) else "OK"
    out.append(f"{SEP2}\nRan {count(ran, 'test')} in {seconds:.1f}s\n\n")
    out.append(f"{word} ({listed})\n" if listed else f"{word}\n")
    return "".join(out)


def verdict_of(event: dict) -> Verdict:
    return Verdict(event["status"], tuple(Problem(**problem) for problem in event["problems"]))


def account(shard: Shard, events: list[dict], ended: Ended) -> Accounting:
    """Return one verdict for every seq of the shard, from the events its worker wrote and how the worker ended."""
    given = set(shard.seqs)
    started: set[int] = set()
    results: dict[int, Verdict] = {}
    fixtures: list[Problem] = []
    violations: list[tuple[int, str]] = []
    done = False
    for event in events:
        if event["ev"] == "fixture":
            fixtures.append(Problem("error", event["label"], event["traceback"]))
        elif event["ev"] == "done":
            done = True
        elif event["seq"] not in given:
            violation = (event["seq"], "reported a test it was not given")
            if violation not in violations:
                violations.append(violation)
        elif event["ev"] == "start":
            started.add(event["seq"])
        elif event["seq"] in results:
            violations.append((event["seq"], "reported a second result for one test"))
        else:
            results[event["seq"]] = verdict_of(event)

    worker = f"The worker for shard {shard.number}"
    if ended.over is not None:
        how = f"exceeded the limit of {ended.over:g} s"
    elif ended.status < 0:
        how = f"was killed by signal {-ended.status}"
    else:
        how = f"exited with status {ended.status}"

    verdicts = {}
    for seq in shard.seqs:
        if seq in results:
            verdicts[seq] = results[seq]
        elif not done and seq in started:
            verdicts[seq] = Verdict("lost", cause=f"{worker} {how} while this test was running.")
        elif not done:
            verdicts[seq] = Verdict("lost", cause=f"{worker} {how} before it started this test.")
        elif fixtures and seq not in started:
            verdicts[seq] = Verdict("lost", cause=f"A class or module fixture failed in the worker for shard {shard.number}, so this test did not start.")
        else:
            verdicts[seq] = Verdict("lost", cause=f"{worker} finished and reported fewer tests than it was given.")
    return Accounting(verdicts, tuple(fixtures), tuple(violations))


def build_shards(plan: tuple[Test, ...], size: int = SHARD_SIZE) -> list[Shard]:
    modules: dict[str, list[int]] = {}
    for test in plan:
        if test.preset is None:
            modules.setdefault(test.module, []).append(test.seq)
    shards = []
    for module, seqs in sorted(modules.items(), key=lambda item: -len(item[1])):
        count = math.ceil(len(seqs) / size)
        # When a module needs more than one shard, a stride puts neighbours in discovery order in different shards.
        for offset in range(count):
            shards.append(Shard(len(shards) + 1, module, tuple(seqs[offset::count])))
    return shards


def child_env(environ: dict[str, str], home: Path, start: Path) -> dict[str, str]:
    # argparse reads the terminal size from COLUMNS and LINES, and tests compare help text.
    env = {name: value for name, value in environ.items() if name not in ("COLUMNS", "LINES")}
    env.update(TMPDIR=str(home / "tmp"), XDG_STATE_HOME=str(home / "state"), XDG_CONFIG_HOME=str(home / "config"))
    env[ACTIVE] = str(start)
    return env


def start_child(args: list[str], home: Path, start: Path) -> subprocess.Popen:
    for name in ("tmp", "state", "config"):
        (home / name).mkdir(parents=True)
    # A file, not a pipe. A process that outlives its test cannot hold the runner's read open.
    with open(home / "output", "wb") as output:
        return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *args], cwd=start.parent,
                                env=child_env(dict(os.environ), home, start), stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT, start_new_session=True)


def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def read_output(home: Path) -> str:
    return (home / "output").read_text(encoding="utf-8", errors="replace")


def list_tests(start: Path, scratch: Path) -> tuple[Test, ...]:
    home = scratch / "list"
    proc = start_child(["-s", str(start), "--list", str(home / "plan.json")], home, start)
    try:
        status = proc.wait()
    finally:
        kill_group(proc)
    if status != 0:
        raise Refusal(f"the listing of {start} ended with status {status}\n{read_output(home)}".rstrip())
    entries = json.loads((home / "plan.json").read_text())
    return tuple(Test(seq, entry["id"], entry["module"], verdict_of(entry["preset"]) if entry["preset"] else None)
                 for seq, entry in enumerate(entries))


def launch(shard: Shard, plan: tuple[Test, ...], scratch: Path, start: Path) -> Running:
    home = scratch / f"s{shard.number:03d}"
    home.mkdir()
    (home / "results.jsonl").touch()
    spec = {"start": str(start), "results": str(home / "results.jsonl"), "tests": [[seq, plan[seq].id] for seq in shard.seqs]}
    (home / "spec.json").write_text(json.dumps(spec))
    return Running(shard, start_child(["--worker", str(home / "spec.json")], home, start), home, 0, time.monotonic())


def read_events(path: Path) -> list[dict]:
    # A kill can cut the last line short. A line with no newline is not an event.
    return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith("\n")]


def run_pool(shards: list[Shard], jobs: int, timeout: float, launch_one, finish, tick) -> None:
    queue = collections.deque(shards)
    running: list[Running] = []
    try:
        while queue or running:
            while queue and len(running) < jobs:
                running.append(launch_one(queue.popleft()))
            time.sleep(POLL_SECONDS)
            now = time.monotonic()
            for worker in list(running):
                size = (worker.home / "results.jsonl").stat().st_size
                if size != worker.size:
                    worker.size, worker.progress_at = size, now
                status = worker.proc.poll()
                over = None
                if status is None and now - worker.progress_at > timeout:
                    kill_group(worker.proc)
                    status, over = worker.proc.wait(), timeout
                if status is None:
                    continue
                # Ends the processes left in the worker's process group.
                kill_group(worker.proc)
                running.remove(worker)
                finish(worker, Ended(status, over))
            tick(now)
    finally:
        for worker in running:
            kill_group(worker.proc)
            worker.proc.wait()


def interrupt(signum, frame):
    raise KeyboardInterrupt


def run(start: Path, jobs: int, timeout: float) -> int:
    if os.environ.get(ACTIVE) == str(start):
        raise Refusal(f"already running on {start}. A test must not start the runner on its own suite.")
    if not start.is_dir():
        raise Refusal(f"no test found under {start}")
    began = time.monotonic()
    signal.signal(signal.SIGTERM, interrupt)
    scratch = Path(tempfile.mkdtemp(prefix="run-tests-"))
    try:
        plan = list_tests(start, scratch)
        if not plan:
            raise Refusal(f"no test found under {start}")
        shards = build_shards(plan)
        ledger = Ledger(len(plan))

        def say(line: str) -> None:
            print(line, file=sys.stderr, flush=True)

        def settle(seq: int, verdict: Verdict) -> None:
            ledger.settle(seq, verdict)
            if fails(verdict):
                say(f"{OUTCOMES[kinds(verdict)[0]].word:<5} {plan[seq].id}")

        def run_error(problem: Problem) -> None:
            ledger.run_errors.append(problem)
            say(f"{OUTCOMES[problem.kind].word:<5} {problem.label}")

        def finish(worker: Running, ended: Ended) -> None:
            shard = worker.shard
            accounting = account(shard, read_events(worker.home / "results.jsonl"), ended)
            output = read_output(worker.home)
            for seq, verdict in accounting.verdicts.items():
                if verdict.kind == "lost":
                    verdict, output = replace(verdict, output=output), ""
                settle(seq, verdict)
            for problem in accounting.fixtures:
                run_error(problem)
            for seq, what in accounting.violations:
                name = plan[seq].id if 0 <= seq < len(plan) else f"seq {seq}"
                run_error(Problem("error", f"shard {shard.number} of {shard.module}", f"The worker {what}. The test is {name}.\n"))
            shutil.rmtree(worker.home, ignore_errors=True)

        progress_at = began

        def tick(now: float) -> None:
            nonlocal progress_at
            if now - progress_at >= PROGRESS_SECONDS:
                progress_at = now
                not_ok = sum(fails(verdict) for verdict in ledger.verdicts.values()) + len(ledger.run_errors)
                say(f"run_tests: {len(ledger.verdicts)}/{len(plan)} done, {not_ok} not ok, {now - began:.0f}s")

        say(f"run_tests: {count(len(plan), 'test')}, {count(len(shards), 'shard')}, {min(jobs, len(shards))} at a time")
        for test in plan:
            if test.preset is not None:
                settle(test.seq, test.preset)
        run_pool(shards, jobs, timeout, lambda shard: launch(shard, plan, scratch, start), finish, tick)
        ledger.close()
        sys.stderr.write(render(plan, ledger, time.monotonic() - began))
        return exit_status(ledger)
    except KeyboardInterrupt:
        print("run_tests: interrupted", file=sys.stderr)
        return 130
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def enter(start: Path) -> None:
    """Put the start directory and its parent first on sys.path and drop this script's directory."""
    here = Path(__file__).resolve().parent
    sys.path[:] = [str(start), str(start.parent)] + [entry for entry in sys.path if Path(entry).resolve() != here]


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


class Recorder(unittest.TestResult):
    """Emits a start and a result event for each given test that runs, and a fixture event for an error or failure on anything else."""

    def __init__(self, tests: list[tuple[unittest.TestCase, int]], emit) -> None:
        super().__init__()
        # Holding the tests keeps their ids from passing to an object unittest makes later.
        self.tests = tests
        self.seqs = {id(test): seq for test, seq in tests}
        self.emit = emit
        self.current = None

    def startTest(self, test):
        self.current, self.status, self.problems = test, "ok", []
        self.emit({"ev": "start", "seq": self.seqs[id(test)]})

    def stopTest(self, test):
        self.emit({"ev": "result", "seq": self.seqs[id(test)], "status": "bad" if self.problems else self.status,
                   "problems": self.problems})
        self.current = None

    def problem(self, kind, test, label, err):
        text = self._exc_info_to_string(err, test)
        if test is self.current:
            self.problems.append({"kind": kind, "label": label, "traceback": text})
        else:
            self.emit({"ev": "fixture", "label": label, "traceback": text})

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
        if test is self.current:
            self.status = "skip"

    def addExpectedFailure(self, test, err):
        self.status = "xfail"

    def addUnexpectedSuccess(self, test):
        self.status = "xpass"


def list_main(start: Path, out: Path) -> int:
    enter(start)
    plan = []
    for test in flatten(unittest.defaultTestLoader.discover(str(start))):
        preset = None
        # The loader puts a test of its own in place of a module it could not import or that skipped itself.
        # Nothing loads that test by its id, so its outcome is taken here.
        if type(test).__module__ == "unittest.loader":
            events: list[dict] = []
            test.run(Recorder([(test, 0)], events.append))
            preset = events[-1]
        plan.append({"id": test.id(), "module": type(test).__module__, "preset": preset})
    out.write_text(json.dumps(plan))
    return 0


def worker_main(spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text())
    enter(Path(spec["start"]))
    loader = unittest.TestLoader()
    tests = []
    with open(spec["results"], "a") as results:
        def emit(event: dict) -> None:
            results.write(json.dumps(event) + "\n")
            results.flush()

        for seq, name in spec["tests"]:
            try:
                loaded = list(flatten(loader.loadTestsFromName(name)))
                if len(loaded) != 1:
                    raise LookupError(f"{name} loads as {len(loaded)} tests")
            except Exception:
                emit({"ev": "start", "seq": seq})
                emit({"ev": "result", "seq": seq, "status": "bad",
                      "problems": [{"kind": "error", "label": name, "traceback": traceback.format_exc()}]})
                continue
            tests.append((loaded[0], seq))
        unittest.TestSuite(test for test, _ in tests).run(Recorder(tests, emit))
        emit({"ev": "done"})
    return 0


def positive(kind):
    def parse(text: str):
        value = kind(text)
        if not value > 0:
            raise argparse.ArgumentTypeError(f"{text} is not above 0")
        return value
    return parse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-j", "--jobs", type=positive(int), default=min(MAX_DEFAULT_JOBS, os.cpu_count() or 1),
                        help=f"The largest number of worker processes alive at once. The default is the smaller of {MAX_DEFAULT_JOBS} and os.cpu_count().")
    parser.add_argument("-s", "--start-directory", default=str(ROOT / "tests"),
                        help="Directory to discover tests under. Each worker runs in its parent directory. The default is tests/ in this repository.")
    parser.add_argument("--timeout", type=positive(float), default=DEFAULT_TIMEOUT,
                        help=f"Seconds a worker may go without writing an event before the runner kills it. The default is {DEFAULT_TIMEOUT:g}.")
    parser.add_argument("--list", help=argparse.SUPPRESS)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return worker_main(Path(args.worker))
    start = Path(args.start_directory).resolve()
    if args.list:
        return list_main(start, Path(args.list))
    try:
        return run(start, args.jobs, args.timeout)
    except Refusal as refusal:
        print(f"run_tests: {refusal}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
