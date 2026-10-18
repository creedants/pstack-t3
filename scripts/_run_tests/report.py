from __future__ import annotations

import collections
from pathlib import Path

from . import NO_TESTS, SEP1, SEP2
from .events import OUTCOMES, Accounting, Ended, Ledger, Problem, Shard, Test, Verdict, exit_status, fails, kinds, plural, tallies, verdict_of

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


def account(shard: Shard, events: list[dict], ended: Ended, stopped: bool = False) -> Accounting:
    """Return one verdict for every seq of the shard, from the events the runner read, how the worker ended, and whether the runner stopped reading the result file."""
    given = set(shard.seqs)
    classes = dict(zip(shard.seqs, shard.classes))
    skipped: set[tuple[str, str]] = set()   # the method and name of each skipped event so far
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
        elif kind == "skipped":
            skipped.add((event["method"], event["name"]))
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
        elif kind == "behind" and event["skip"] and not skipped & {("setUpClass", classes[seq]), ("setUpModule", shard.module)}:
            violate(seq, "reported a test as behind a skipped fixture with no earlier report that the setUpClass of its class "
                         "or the setUpModule of its module raised SkipTest")
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
    if done and ended.status == 0 and ended.over is None:
        ending = ""
    elif done:
        ending = f"{worker} {how} after it reported that it had finished."
    elif stopped:
        ending = f"{worker} {how} before the runner read that it had finished."
    else:
        ending = f"{worker} {how} before it reported that it had finished."

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
        elif stopped:
            verdicts[seq] = Verdict("lost", cause=f"The runner stopped reading the result file of shard {shard.number} before it read a result for this test.")
        elif last.get(seq) == "start":
            verdicts[seq] = Verdict("lost", cause=f"{worker} {how} while this test was running.")
        else:
            verdicts[seq] = Verdict("lost", cause=f"{worker} {how} before it started this test.")
    return Accounting(verdicts, tuple(fixtures), tuple(violations), ending)
