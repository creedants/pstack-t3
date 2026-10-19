from __future__ import annotations

import collections
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import ACTIVE, POLL_SECONDS, PROGRESS_SECONDS
from .events import OUTCOMES, Accounting, Channel, Ended, Interrupted, Ledger, Problem, Refusal, Running, Shard, Test, Verdict, exit_status, fails, kinds, plural
from .plan import build_shards, launch_order, tests_in_part
from .processes import Watch, child_env, start_child
from .report import account, render

def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
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
        return tuple(Test(seq, entry["id"], entry["module"], entry["cls"]) for seq, entry in enumerate(entries))
    except (OSError, ValueError, KeyError, TypeError):
        raise Refusal(f"the listing of {start} left no plan the runner can read\n{read_output(home)}".rstrip()) from None


def launch(shard: Shard, scratch: Path, start: Path) -> Running:
    home = scratch / f"s{shard.number:03d}"
    home.mkdir()
    channel = Channel(home / "results.jsonl")
    spec = {"start": str(start), "results": str(home / "results.jsonl"), "plan": str(scratch / "list" / "plan.json"),
            "seqs": shard.seqs}
    (home / "spec.json").write_text(json.dumps(spec))
    proc = start_child(["--worker", str(home / "spec.json")], home, start)
    return Running(shard, proc, home, channel, time.monotonic())


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
                if status is None and worker.channel.stopped:
                    kill_group(worker.proc)
                    status = worker.proc.wait()
                elif status is None and now - worker.progress_at > timeout:
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
            worker.channel.close()


def run(start: Path, jobs: int, timeout: float, part: tuple[int, int] | None = None, _list_tests=None, _run_pool=None, _Watch=None) -> int:
    if os.environ.get(ACTIVE) == str(start):
        raise Refusal(f"already running on {start}. A test must not start the runner on its own suite.")
    if not start.is_dir():
        raise Refusal(f"no test found under {start}")
    began = time.monotonic()
    WatchCls = _Watch if _Watch is not None else Watch
    list_tests_fn = _list_tests if _list_tests is not None else list_tests
    run_pool_fn = _run_pool if _run_pool is not None else run_pool
    watch = WatchCls()
    scratch = Path(tempfile.mkdtemp(prefix="run-tests-"))

    def say(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    if watch.inherited:
        say("run_tests: this process had a child before the run, so the runner ends only the process groups of the children it starts")
    try:
        try:
            plan = list_tests_fn(start, scratch, timeout, watch.pause)
            selected = plan if part is None else tests_in_part(plan, part[0], part[1])
            if part is not None and not selected:
                raise Refusal(f"part {part[0]}/{part[1]} has no test")
            shards = list(launch_order(build_shards(selected), part is not None))
            ledger = Ledger(len(plan), None if part is None else tuple(test.seq for test in selected))

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
                worker.channel.close()
                accounting = account(shard, worker.channel.events, ended, worker.channel.stopped)
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
                    say(f"run_tests: {len(ledger.verdicts)}/{len(selected)} done, {not_ok} not ok, {now - began:.0f}s")

            say(f"run_tests: {plural(len(selected), 'test')}, {plural(len(shards), 'shard')}, {min(jobs, len(shards))} at a time")
            run_pool_fn(shards, jobs, timeout, lambda shard: launch(shard, scratch, start), finish, tick, watch.pause)
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
