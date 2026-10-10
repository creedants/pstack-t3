#!/usr/bin/env python3
"""Run every test under the start directory in separate processes and print one merged report.

A listing child discovers the tests. The runner cuts them into shards of at most SHARD_SIZE tests whose
classes are defined in one module and runs each shard in a fresh process. A worker makes the same discovery
call as the listing child. It runs nothing unless its test ids equal the listing's in number and order. It
then runs the test objects its own discovery put at the positions of its shard. It appends one JSON line per
event to its own result file. Before it prints the report, the runner settles exactly one verdict per
discovered test, so the `Ran` line counts the tests discovery returned.

A shard finished cleanly when its worker reported that it had finished, then exited with status 0 before the
runner found it past the `--timeout` limit, and its result file holds only events the shard may write. Every
other shard adds one error to the report, whatever results its worker wrote. A test with no result is LOST,
with one exception. A worker reports a test as behind a fixture when the suite passes over it, the setUpClass
of its class or the setUpModule of its module failed or raised SkipTest, and no test started between those
two events. It reports that when the next test starts or the suite returns. A test reported behind a fixture
that raised SkipTest is skipped. The runner cannot tell an event its worker wrote from the same event written
by a test that runs in that worker.

The exit status is 0 when the last line of the report starts with OK, 1 when it starts with FAILED, 2 when
the runner did not start the tests, 5 when it is NO TESTS RAN, and 130 when SIGINT or SIGTERM stops the run.
When discovery returns no test, the report ends as `python3 -m unittest discover` ends on the same Python.
That is NO TESTS RAN from Python 3.12 on and OK before it.

The runner sends SIGKILL to the process group of each child it starts when that child ends, when that child
is past the `--timeout` limit, and when the run stops before that child ends. On Linux with /proc the runner
tries to become the child subreaper, so that a process orphaned below it becomes its child. Where that
succeeds, after its last child ends, it sends SIGKILL to every process below it in /proc's parent links and
repeats until none is left or SWEEP_SECONDS pass. That reaches a process that left its group for a new
session. The runner names each process still there after SWEEP_SECONDS. Such a process does not change the
exit status. The runner sends no signal to a child its process had before the run and does not look below
one. Everywhere else the runner does not end a process that left the process group of the child that started
it.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARD_SIZE = 10
# land.py's governor defaults to one slot per four cores, with at least 1 slot and at most 4.
MAX_DEFAULT_JOBS = 4
DEFAULT_TIMEOUT = 300.0
# A child holds the start directory of the runner that started it. The runner refuses that directory.
ACTIVE = "PSTACK_RUN_TESTS"
POLL_SECONDS = 0.05
PROGRESS_SECONDS = 30.0
SWEEP_SECONDS = 10.0
# The exit status and the last line of `python3 -m unittest discover` when it finds no test.
NO_TESTS = (5, "NO TESTS RAN") if sys.version_info >= (3, 12) else (0, "OK")
# From linux/prctl.h.
PR_SET_CHILD_SUBREAPER = 36
# The report quotes at most this many complete lines that are not events from one result file, and at most this
# many bytes of each line it quotes. It also quotes a last line that has no newline.
QUOTED_LINES = 5
QUOTED_BYTES = 120
SEP1 = "=" * 70
SEP2 = "-" * 70

# The fields each event holds besides "ev".
EVENTS = {
    "start": {"seq": int},                                    # the test began
    "result": {"seq": int, "status": str, "problems": list},  # the test ended
    "behind": {"seq": int, "skip": bool},                     # the test did not start, behind a setUpClass or setUpModule that skipped or failed
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
    if any(OUTCOMES[kind].fails for kind in tallies(ledger)):
        return 1
    return 0 if ledger.size else NO_TESTS[0]


def plural(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def block(kind: str, label: str, body: str) -> str:
    return f"{SEP1}\n{OUTCOMES[kind].word}: {label}\n{SEP2}\n{body}\n" if body else f"{SEP1}\n{OUTCOMES[kind].word}: {label}\n"


def render(plan: tuple[Test, ...], ledger: Ledger, seconds: float) -> str:
    out = []
    for seq in sorted(ledger.verdicts):
        verdict = ledger.verdicts[seq]
        out.extend(block(problem.kind, problem.label, problem.traceback) for problem in verdict.problems)
        if verdict.kind == "xpass":
            out.append(block("xpass", plan[seq].id, ""))
        if verdict.kind == "lost":
            out.append(block("lost", plan[seq].id, verdict.cause + "\n"))
    out.extend(block(problem.kind, problem.label, problem.traceback) for problem in ledger.run_errors)
    ran = len(ledger.verdicts)
    counted = tallies(ledger)
    listed = ", ".join(f"{outcome.tally}={counted[kind]}" for kind, outcome in OUTCOMES.items() if outcome.tally and counted[kind])
    word = "FAILED" if exit_status(ledger) == 1 else "OK" if ran else NO_TESTS[1]
    out.append(f"{SEP2}\nRan {plural(ran, 'test')} in {seconds:.3f}s\n\n")
    out.append(f"{word} ({listed})\n" if listed else f"{word}\n")
    return "".join(out)


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
    """Reads the complete lines a worker appends to its result file and keeps those that are events."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.lines = 0
        self.events: list[dict] = []
        self.errors: list[str] = []   # the report's text for what the file holds that is not an event, and for a file that cannot be read
        self.unquoted = 0             # lines that are not events, past the QUOTED_LINES that errors quotes

    def read(self, last: bool = False) -> bool:
        """Take the complete lines the file gained since the last read. Return whether they held an event."""
        try:
            with open(self.path, "rb") as file:
                file.seek(self.offset)
                data = file.read()
        except OSError as error:
            if last:
                self.errors.append(f"Its result file cannot be read. {error}.")
            return False
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
        return len(self.events) > known


@dataclass
class Running:
    shard: Shard
    proc: subprocess.Popen
    home: Path
    channel: Channel
    progress_at: float


def verdict_of(event: dict) -> Verdict:
    return Verdict(event["status"], tuple(Problem(**problem) for problem in event["problems"]))


def account(shard: Shard, events: list[dict], ended: Ended) -> Accounting:
    """Return one verdict for every seq of the shard, from the events its worker wrote and how the worker ended."""
    given = set(shard.seqs)
    last: dict[int, str] = {}   # the kind of the last event accepted for a seq
    results: dict[int, Verdict] = {}
    skips: dict[int, bool] = {}
    fixtures: list[Problem] = []
    violations: list[tuple[int | None, str]] = []
    done = False

    def violate(seq: int | None, what: str) -> None:
        if (seq, what) not in violations:
            violations.append((seq, what))

    for event in events:
        kind, seq = event["ev"], event.get("seq")
        if done:
            violate(seq, "wrote an event after it reported that it had finished")
        elif kind == "done":
            done = True
        elif kind == "fixture":
            fixtures.append(Problem("error", event["label"], event["traceback"]))
        elif seq not in given:
            violate(seq, "reported a test it was not given")
        elif kind == "result" and last.get(seq) == "start":
            last[seq], results[seq] = kind, verdict_of(event)
        elif kind == "result" and last.get(seq) == "result":
            violate(seq, "reported a second result for one test")
        elif kind == "result":
            violate(seq, "reported a result for a test it had not started")
        elif seq in last:
            violate(seq, f"{'started' if kind == 'start' else 'passed over'} a test it had already reported")
        else:
            last[seq] = kind
            if kind == "behind":
                skips[seq] = event["skip"]

    worker = f"The worker for shard {shard.number}"
    if ended.over is not None:
        how = f"exceeded the limit of {ended.over:g} s"
    elif ended.status < 0:
        how = f"was killed by signal {-ended.status}"
    else:
        how = f"exited with status {ended.status}"
    clean = done and ended.status == 0 and ended.over is None
    ending = "" if clean else f"{worker} {how} {'after' if done else 'before'} it reported that it had finished."

    verdicts = {}
    for seq in shard.seqs:
        if seq in results:
            verdicts[seq] = results[seq]
        elif skips.get(seq) is True:
            verdicts[seq] = Verdict("skip")
        elif seq in skips:
            verdicts[seq] = Verdict("lost", cause=f"A class or module fixture failed in the worker for shard {shard.number}, and this test did not start.")
        elif done:
            verdicts[seq] = Verdict("lost", cause=f"{worker} finished without an accepted result for this test.")
        elif last.get(seq) == "start":
            verdicts[seq] = Verdict("lost", cause=f"{worker} {how} while this test was running.")
        else:
            verdicts[seq] = Verdict("lost", cause=f"{worker} {how} before it started this test.")
    return Accounting(verdicts, tuple(fixtures), tuple(violations), ending)


def build_shards(plan: tuple[Test, ...], size: int = SHARD_SIZE) -> list[Shard]:
    modules: dict[str, list[int]] = {}
    for test in plan:
        modules.setdefault(test.module, []).append(test.seq)
    shards = []
    for module, seqs in sorted(modules.items(), key=lambda item: -len(item[1])):
        count = math.ceil(len(seqs) / size)
        # When a module needs more than one shard, a stride puts neighbours in discovery order in different shards.
        for offset in range(count):
            shards.append(Shard(len(shards) + 1, module, tuple(seqs[offset::count])))
    return shards


def adopt_orphans() -> bool:
    """Try to make this process the child subreaper. Return whether Linux did and /proc lists this process."""
    if not sys.platform.startswith("linux") or not Path("/proc/self/stat").exists():
        return False
    try:
        import ctypes
        prctl = ctypes.CDLL(None, use_errno=True).prctl
        prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        return prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except (ImportError, OSError, AttributeError):
        return False


def children_by_parent() -> dict[int, list[int]]:
    """Map each process that has a child listed in /proc to those children. A process that ended and is not yet reaped is listed."""
    children: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                stat = Path("/proc", entry, "stat").read_bytes()
                # The command name sits in parentheses and may itself hold spaces and parentheses.
                parent = int(stat[stat.rindex(b")") + 2:].split()[1])
            except (OSError, ValueError, IndexError):
                continue   # /proc gives no parent for it. It ended during the walk, or /proc hides it from this user.
            children.setdefault(parent, []).append(int(entry))
    return children


class Watch:
    """Notes SIGINT and SIGTERM, and holds the orphan handling the module docstring describes."""

    def __init__(self) -> None:
        self.signals: list[int] = []
        signal.signal(signal.SIGINT, self.note)
        signal.signal(signal.SIGTERM, self.note)
        self.adopts = adopt_orphans()
        # The children this process had before the run. Empty where adopt_orphans did not work.
        self.spared = frozenset(children_by_parent().get(os.getpid(), ())) if self.adopts else frozenset()

    def note(self, signum, frame) -> None:
        self.signals.append(signum)

    def pause(self, owned: set[int]) -> None:
        """Sleep one poll. Raise Interrupted after a signal. Where adopt_orphans worked, reap each exited child up to the first one in owned, so nothing else in this process may wait for a child."""
        time.sleep(POLL_SECONDS)
        if self.signals:
            raise Interrupted
        while self.adopts:
            try:
                exited = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                return
            # The caller takes the status of a process in owned through its Popen, and the next pause goes on from there.
            if exited is None or exited.si_pid in owned:
                return
            os.waitpid(exited.si_pid, 0)

    def sweep(self, seconds: float = SWEEP_SECONDS) -> list[int]:
        """Do what the module docstring says the runner does after its last child ends, for at most `seconds`. Return the pids still there."""
        if not self.adopts:
            return []
        deadline = time.monotonic() + seconds
        while True:
            children = children_by_parent()
            left, todo = [], [pid for pid in children.get(os.getpid(), ()) if pid not in self.spared]
            while todo:
                left.append(todo.pop())
                todo.extend(children.get(left[-1], ()))
            if not left or time.monotonic() >= deadline:
                return left
            for pid in left:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, os.WNOHANG)
                except (ProcessLookupError, PermissionError, ChildProcessError):
                    pass   # it is gone, it belongs to another user, or it is not a child of this process
            time.sleep(POLL_SECONDS / 5)


def child_env(environ: dict[str, str], home: Path, start: Path) -> dict[str, str]:
    # shutil.get_terminal_size reads COLUMNS and LINES, argparse wraps help text at its width, and tests compare help text.
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
    try:
        return (home / "output").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def list_tests(start: Path, scratch: Path, timeout: float, pause) -> tuple[Test, ...]:
    home = scratch / "list"
    proc = start_child(["-s", str(start), "--list", str(home / "plan.json")], home, start)
    deadline = time.monotonic() + timeout
    over = False
    try:
        while proc.poll() is None and not over:
            pause({proc.pid})
            over = time.monotonic() > deadline
    finally:
        kill_group(proc)
        status = proc.wait()
    if over:
        raise Refusal(f"the listing of {start} exceeded the limit of {timeout:g} s\n{read_output(home)}".rstrip())
    if status != 0:
        raise Refusal(f"the listing of {start} ended with status {status}\n{read_output(home)}".rstrip())
    try:
        entries = json.loads((home / "plan.json").read_text())
        return tuple(Test(seq, entry["id"], entry["module"]) for seq, entry in enumerate(entries))
    except (OSError, ValueError, KeyError, TypeError):
        raise Refusal(f"the listing of {start} left no plan the runner can read\n{read_output(home)}".rstrip()) from None


def launch(shard: Shard, scratch: Path, start: Path) -> Running:
    home = scratch / f"s{shard.number:03d}"
    home.mkdir()
    (home / "results.jsonl").touch()
    spec = {"start": str(start), "results": str(home / "results.jsonl"), "plan": str(scratch / "list" / "plan.json"),
            "seqs": shard.seqs}
    (home / "spec.json").write_text(json.dumps(spec))
    proc = start_child(["--worker", str(home / "spec.json")], home, start)
    return Running(shard, proc, home, Channel(home / "results.jsonl"), time.monotonic())


def run_pool(shards: list[Shard], jobs: int, timeout: float, launch_one, finish, tick, pause) -> None:
    queue = collections.deque(shards)
    running: list[Running] = []
    try:
        while queue or running:
            while queue and len(running) < jobs:
                running.append(launch_one(queue.popleft()))
            pause({worker.proc.pid for worker in running})
            now = time.monotonic()
            for worker in list(running):
                # Only a complete line that is an event moves the limit.
                if worker.channel.read():
                    worker.progress_at = now
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


def run(start: Path, jobs: int, timeout: float) -> int:
    if os.environ.get(ACTIVE) == str(start):
        raise Refusal(f"already running on {start}. A test must not start the runner on its own suite.")
    if not start.is_dir():
        raise Refusal(f"no test found under {start}")
    began = time.monotonic()
    watch = Watch()
    scratch = Path(tempfile.mkdtemp(prefix="run-tests-"))

    def say(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    try:
        try:
            plan = list_tests(start, scratch, timeout, watch.pause)
            shards = build_shards(plan)
            ledger = Ledger(len(plan))

            def settle(seq: int, verdict: Verdict) -> None:
                ledger.settle(seq, verdict)
                if fails(verdict):
                    say(f"{OUTCOMES[kinds(verdict)[0]].word:<5} {plan[seq].id}")

            def run_error(problem: Problem) -> None:
                ledger.run_errors.append(problem)
                say(f"{OUTCOMES[problem.kind].word:<5} {problem.label}")

            def finish(worker: Running, ended: Ended) -> None:
                shard = worker.shard
                worker.channel.read(last=True)
                accounting = account(shard, worker.channel.events, ended)
                for seq, verdict in accounting.verdicts.items():
                    settle(seq, verdict)
                for problem in accounting.fixtures:
                    run_error(problem)
                # Each entry is one way this shard did not finish cleanly.
                faults = [accounting.ending] if accounting.ending else []
                faults.extend(worker.channel.errors)
                for seq, what in accounting.violations:
                    name = plan[seq].id if seq in shard.seqs else f"seq {seq}"
                    faults.append(f"The worker {what}." if seq is None else f"The worker {what}. The test is {name}.")
                if faults:
                    output = "".join(f"  {line}\n" for line in read_output(worker.home).splitlines())
                    body = "".join(f"{fault}\n" for fault in faults) + (f"Output of the worker:\n{output}" if output else "")
                    run_error(Problem("error", f"shard {shard.number} of {shard.module}", body))
                shutil.rmtree(worker.home, ignore_errors=True)

            progress_at = began

            def tick(now: float) -> None:
                nonlocal progress_at
                if now - progress_at >= PROGRESS_SECONDS:
                    progress_at = now
                    not_ok = sum(fails(verdict) for verdict in ledger.verdicts.values()) + len(ledger.run_errors)
                    say(f"run_tests: {len(ledger.verdicts)}/{len(plan)} done, {not_ok} not ok, {now - began:.0f}s")

            say(f"run_tests: {plural(len(plan), 'test')}, {plural(len(shards), 'shard')}, {min(jobs, len(shards))} at a time")
            run_pool(shards, jobs, timeout, lambda shard: launch(shard, scratch, start), finish, tick, watch.pause)
        finally:
            left = watch.sweep()
            if left:
                say(f"run_tests: processes {' '.join(map(str, sorted(left)))} outlived the run")
        ledger.close()
        sys.stderr.write(render(plan, ledger, time.monotonic() - began))
        return exit_status(ledger)
    except Interrupted:
        say("run_tests: interrupted")
        return 130
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def enter(start: Path) -> None:
    """Put the start directory and its parent first on sys.path and drop every other entry for this script's directory."""
    here = Path(__file__).resolve().parent
    sys.path[:] = [str(start), str(start.parent)] + [entry for entry in sys.path if Path(entry).resolve() != here]


def discover(start: Path) -> list:
    """Return the tests under start in discovery order. The listing child and every worker call this."""
    def flatten(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from flatten(item)
            else:
                yield item

    enter(start)
    return list(flatten(unittest.defaultTestLoader.discover(str(start))))


class Recorder(unittest.TestResult):
    """Turns what unittest reports into the start, result, behind, and fixture events of EVENTS. A subtest counts as its test."""

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
            names = {"setUpClass": f"{type(test).__module__}.{type(test).__qualname__}", "setUpModule": type(test).__module__}
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
    out.write_text(json.dumps([{"id": test.id(), "module": type(test).__module__} for test in discover(start)]))
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


def positive(kind):
    def parse(text: str):
        value = kind(text)
        if not value > 0:
            raise argparse.ArgumentTypeError(f"{text} is not above 0")
        return value
    return parse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     epilog=__doc__.split("\n\n")[-1].replace("SWEEP_SECONDS", f"{SWEEP_SECONDS:g} seconds"))
    parser.add_argument("-j", "--jobs", type=positive(int), default=min(MAX_DEFAULT_JOBS, os.cpu_count() or 1),
                        help=f"The largest number of worker processes alive at once. The default is the smaller of {MAX_DEFAULT_JOBS} and the CPU count, or 1 when the CPU count is unknown.")
    parser.add_argument("-s", "--start-directory", default=str(ROOT / "tests"),
                        help="Directory to discover tests under. Each worker runs in the parent of this directory. The default is tests/ in this repository.")
    parser.add_argument("--timeout", type=positive(float), default=DEFAULT_TIMEOUT,
                        help=f"Seconds a worker may go without writing an event before the runner kills it. The listing of the tests gets the same number of seconds in all. The default is {DEFAULT_TIMEOUT:g}.")
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
