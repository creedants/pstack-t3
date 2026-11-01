#!/usr/bin/env python3
"""Run the tests under the start directory in separate processes and print one merged report.

A listing child discovers the tests. The runner cuts them into shards of at most SHARD_SIZE tests whose
classes are defined in one module and runs each shard in a fresh process. A worker makes the same discovery
call as the listing child. It runs nothing unless its test ids equal the listing's in number and order. It
then runs the test objects its own discovery put at the positions of its shard. It appends one JSON line per
event to its own result file. Before it prints the report, the runner settles exactly one verdict per test
this run selected. The `Ran` line counts the tests this run settled. A run with no `--part` settles every
discovered test.

The runner opens each result file before it starts the worker and reads the file through that descriptor. It
stops reading a result file when it finds that the path of the file no longer leads to the file it opened, or
that the file is shorter than what the runner has read of it. That is an error for the shard. A test of that
shard with no verdict by then is LOST.

A shard finished cleanly when its worker reported that it had finished, then exited with status 0 before the
runner found it past the `--timeout` limit, and no result file error or event violation was recorded. Every
other shard adds one error to the report, whatever results its worker wrote. A test with no result is LOST,
with one exception. A worker reports a test as behind a fixture when the suite passes over it, the setUpClass
of its class or the setUpModule of its module failed or raised SkipTest, and no test started between those
two events. It reports that when the next test starts or the suite returns. A test reported behind a fixture
that raised SkipTest is skipped when an earlier event of the result file says that the setUpClass of the
test's class or the setUpModule of its module raised SkipTest. Without that earlier event the report is a
violation and gives the test no verdict. The runner cannot tell an event its worker wrote from the same event
written by a test that runs in that worker.

The exit status is 0 when the last line of the report starts with OK, 1 when it starts with FAILED, 2 when
the runner did not start the tests, 5 when it is NO TESTS RAN, and 130 when SIGINT or SIGTERM stops the run.
When discovery returns no test, the report ends as `python3 -m unittest discover` ends on the same Python.
That is NO TESTS RAN from Python 3.12 on and OK before it.

To regenerate the durations file, run `python3 scripts/run_tests.py --record-durations <path>`. The recording
run is one ordinary run of the suite, so the workers `-j` names and their contention are part of every number
it writes. The measurement for a test is the gap the runner reads between that worker's start and result
events. Polling and scheduling affect the error. Each shard's overhead is its worker's span less the gaps of
its tests. The file holds one record per module, as that module's measured seconds per test and the typical
overhead of one shard of it. A test whose start and result events are read together has
no measurement. A module the file does not name takes `default_seconds` per test and `default_overhead`
per shard. A stale or missing durations file costs balance and never coverage.

What this runner guarantees. The tests are trusted code. They can be wrong, slow, flaky, or broken, and they
are not written to defeat the runner. A run exits 0 only when each test the run selected has exactly one
verdict, that verdict is a pass, a skip, or an expected failure read from the result file of the test's
shard, and each shard of the run finished cleanly. A run with no `--part` selects
every test discovery returned. A shard holds the tests of one module, and the modules with the most tests are
built first. The part split keeps a module's tests in one part, except that a module estimated above one
part's share of the default worker count is placed one test at a time, so its tests can land in different
parts, and a plan with fewer modules than parts places every test on its own. Each item goes to the part
with the smallest estimated total by the rule above, ties to the lowest part number. A module the durations
file does not name takes a stated default per test and per shard.
The assignment depends only on the plan discovery returned and the durations file, so each part job of a
run computes the same split. A part run starts its most expensive shards first while a full run keeps build order.
Without `--record-durations`, a run with `--part i/n` selects part i of n by the rule above and no other test. So each of these makes
the run exit with another status than 0. A failing assertion. An error in a test or in a fixture. A module
that fails to import. A worker that exits or is killed before it reported that it had finished, as when a
test calls `os._exit`. A worker past
the `--timeout` limit. A result file that the runner finds removed, replaced, or cut shorter than what it
read. A line the runner reads from a result file that is not an event. SIGINT or SIGTERM that stops the run.
A worker runs the test objects its own discovery builds, and the run fails when that discovery differs from
the listing's in ids, count, or order. Test code that writes or rewrites result events, or that stops,
signals, or inspects the runner process, is outside this guarantee. `python3 -m unittest` gives no such
guarantee either.

The runner sends SIGKILL to the process group of each child it starts when that child ends, when that child
is past the `--timeout` limit, when the runner stops reading that child's result file, and when the run stops
before that child ends. On Linux with /proc the runner looks for children of its process as the run begins.
When it finds none, it tries to become the child subreaper, so that a process orphaned below it becomes its
child. Where that succeeds, after its last child ends, it sends SIGKILL to every process below it in /proc's
parent links and repeats until none is left or SWEEP_SECONDS pass. That reaches a process that left its group
for a new session. The runner names each process still there after SWEEP_SECONDS. Such a process does not
change the exit status. When it finds a child, the runner prints one line that says so and does not try to
become the child subreaper. There, and wherever it did not become the child subreaper, the runner signals
those process groups only, so it does not end a process that left the group of the child that started it.
Separately, where the platform offers prctl, each child asks the kernel, in the fork before it runs the
runner, to send it SIGKILL when the runner process ends. A platform without prctl makes no such request. There
the runner holds the write end of one pipe for its whole life and gives each worker the read end. As it
starts, each worker forks a watcher into that worker's process group. The watcher sends that worker SIGKILL once the
pipe reports that the runner process has ended, and it signals no other process. The runner ends the watcher with SIGKILL to the
worker process group. There the listing child and the processes a test starts run on after a
runner that never reached its own cleanup. On every platform a worker exits as it starts when its parent is not
the runner that started it. The runner names its scratch directory
with its own pid, so a run removes each scratch directory under the temp directory named after a pid that no
process has, and a removal that fails leaves it.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _run_tests import ACTIVE, DEFAULT_TIMEOUT, ENTRY, MAX_DEFAULT_JOBS, NO_TESTS, POLL_SECONDS, PR_SET_CHILD_SUBREAPER, PROGRESS_SECONDS, QUOTED_BYTES, QUOTED_LINES, ROOT as _PKG_ROOT, SEP1, SEP2, SHARD_SIZE, SWEEP_SECONDS
from _run_tests.events import EVENTS, STATUSES, PROBLEM, SETUP, Accounting, Channel, Ended, Ledger, LedgerError, Outcome, OUTCOMES, Problem, Refusal, Interrupted, Running, Shard, Test, Verdict, exit_status, fails, kinds, parse_event, plural, quoted, shaped, tallies, verdict_of
from _run_tests.plan import DEFAULT_OVERHEAD, DEFAULT_SECONDS, Costs, ModuleCost, build_shards, launch_order, part_of, read_durations, tests_in_part
from _run_tests.pool import kill_group, launch, list_tests, read_output, run_pool
from _run_tests.pool import run as _pool_run
from _run_tests.processes import Watch, adopt_orphans, children_by_parent, start_child
from _run_tests.report import account, block, render
from _run_tests.worker import Recorder, class_name, discover, enter, list_main, worker_main
from _run_tests import parse_part, positive, processes

ROOT = Path(__file__).resolve().parents[1]

# Loaded by path, not as tests.isolation. A `tests` package on sys.path or in sys.modules shadows a
# fixture tree that imports its own top-level tests package, which is a shape the runner must support.
# Registered before it runs, so a later import of tests.isolation gets this copy and the process holds one guard.
isolation = sys.modules.get("pstack_test_isolation")
if isolation is None:
    _loader = spec_from_file_location("pstack_test_isolation", ROOT / "tests" / "isolation.py")
    isolation = module_from_spec(_loader)
    sys.modules["pstack_test_isolation"] = isolation
    _loader.loader.exec_module(isolation)

# Record the visible protected roots, and the roots the pointer's marker carries, before rewriting any child environment.
isolation.remember_originals()

_original_child_env = processes.child_env


def child_env(environ: dict[str, str], home: Path, start: Path) -> dict[str, str]:
    """Return a worker's environment with a home inside its own run root, whatever start directory it uses."""
    return isolation.worker_env(_original_child_env(environ, home, start), home)


processes.child_env = child_env


def run(start: Path, jobs: int, timeout: float, part: tuple[int, int] | None = None, *, list_tests_fn=None) -> int:
    return _pool_run(start, jobs, timeout, part, _list_tests=list_tests_fn or list_tests, _run_pool=run_pool, _Watch=Watch)


def summarize_durations(plan: tuple[Test, ...], measured: dict[str, float], spans: list[tuple[str, tuple[int, ...], float]]) -> dict:
    """Return the durations document `record_durations` writes: one record per module, as its measured seconds per test and the median overhead of one shard of it."""
    by_seq = {test.seq: test for test in plan}
    counts: dict[str, int] = {}
    for test in plan:
        counts[test.module] = counts.get(test.module, 0) + 1
    per_module: dict[str, list[float]] = {}
    all_overheads: list[float] = []
    for module, seqs, span in spans:
        gap = 0.0
        for seq in seqs:
            test = by_seq.get(seq)
            if test is not None:
                gap += measured.get(test.id, 0.0)
        overhead = max(0.0, span - gap)
        per_module.setdefault(module, []).append(overhead)
        all_overheads.append(overhead)
    if measured:
        default_seconds = round(statistics.fmean(measured.values()), 3)
    else:
        default_seconds = DEFAULT_SECONDS
    if all_overheads:
        default_overhead = round(statistics.median(all_overheads), 3)
    else:
        default_overhead = DEFAULT_OVERHEAD
    modules: dict[str, list] = {}
    for module in sorted(set(counts) | set(per_module)):
        count = counts.get(module, 0)
        seconds = sum(measured.get(test.id, 0.0) for test in plan if test.module == module)
        if module in per_module and per_module[module]:
            overhead = round(statistics.median(per_module[module]), 3)
        else:
            overhead = default_overhead
        modules[module] = [round(seconds / count, 3) if count else 0.0, overhead]
    return {"default_seconds": default_seconds, "default_overhead": default_overhead, "modules": modules}


def format_durations(doc: dict) -> str:
    """Return the durations file text. Each module sits on one line, so the file stays small and its diffs stay readable."""
    lines = ["{",
             f'  "default_seconds": {doc["default_seconds"]},',
             f'  "default_overhead": {doc["default_overhead"]},',
             '  "modules": {']
    names = sorted(doc["modules"])
    for index, name in enumerate(names):
        rate, overhead = doc["modules"][name]
        comma = "," if index < len(names) - 1 else ""
        lines.append(f"    {json.dumps(name)}: [{rate}, {overhead}]{comma}")
    lines.extend(["  }", "}"])
    return "\n".join(lines) + "\n"


def record_durations(start: Path, jobs: int, timeout: float, out: Path) -> int:
    stamps: dict[int, dict[str, float]] = {}
    original_read = Channel.read

    def wrapped(self, last: bool = False) -> bool:
        before = len(self.events)
        try:
            return original_read(self, last=last)
        finally:
            now = time.monotonic()
            for event in self.events[before:]:
                if event.get("ev") in ("start", "result"):
                    stamps.setdefault(event["seq"], {})[event["ev"]] = now

    plan_holder: dict[str, tuple[Test, ...]] = {}

    def listing(start_arg: Path, scratch: Path, timeout_arg: float, pause) -> tuple[Test, ...]:
        plan = list_tests(start_arg, scratch, timeout_arg, pause)
        plan_holder["plan"] = plan
        return plan

    spans: list[tuple[str, tuple[int, ...], float]] = []
    original_pool = run_pool

    def wrapping_pool(shards, pool_jobs: int, limit: float, launch_one, finish, tick, pause) -> None:
        starts: dict[int, tuple[float, str, tuple[int, ...]]] = {}

        def wrapping_launch(shard):
            running = launch_one(shard)
            starts[id(running)] = (time.monotonic(), shard.module, tuple(shard.seqs))
            return running

        def wrapping_finish(worker, ended) -> None:
            try:
                return finish(worker, ended)
            finally:
                end = time.monotonic()
                info = starts.pop(id(worker), None)
                if info is not None:
                    begin, module, seqs = info
                    spans.append((module, seqs, end - begin))

        return original_pool(shards, pool_jobs, limit, wrapping_launch, wrapping_finish, tick, pause)

    Channel.read = wrapped
    globals()["run_pool"] = wrapping_pool
    try:
        status = run(start, jobs, timeout, list_tests_fn=listing)
    finally:
        Channel.read = original_read
        globals()["run_pool"] = original_pool
    plan = plan_holder.get("plan", ())
    by_id = {test.seq: test.id for test in plan}
    measured: dict[str, float] = {}
    for seq, times in stamps.items():
        ident = by_id.get(seq)
        if ident is None or "start" not in times or "result" not in times:
            continue
        gap = times["result"] - times["start"]
        if gap > 0:
            measured[ident] = gap
    doc = summarize_durations(plan, measured, spans)
    data = format_durations(doc)
    out.write_text(data)
    size = len(data.encode("utf-8"))
    print(f"run_tests: measured {len(measured)} tests, {len(spans)} shards, wrote {len(doc['modules'])} modules, "
          f"default_seconds {doc['default_seconds']}, default_overhead {doc['default_overhead']}, "
          f"{size} bytes to {out}", file=sys.stderr, flush=True)
    return status


def _child(argv: list[str]) -> bool:
    """Return whether argv starts the listing child or a worker, counting each abbreviation argparse accepts."""
    for arg in argv:
        name = arg.split("=", 1)[0]
        if len(name) > 2 and ("--list".startswith(name) or "--worker".startswith(name)):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    from _run_tests import main as _pkg_main

    if _child(sys.argv[1:] if argv is None else argv):
        # The listing child and every worker import test modules, so each installs and verifies the guard
        # before discovery. A failed install raises SystemExit, which exits nonzero.
        isolation.install()
    return _pkg_main(argv, __doc__)


if __name__ == "__main__":
    import sys

    sys.exit(main())
