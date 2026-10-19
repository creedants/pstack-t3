import ast
import contextlib
import errno
import fcntl
import gc
import hashlib
import inspect
import io
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import land
from .shell import git_env, sh
from .inprocess import ProcessReason, _run, drive, real_popen, real_process
from . import paths
from .paths import ADMIN, REVIEWER, ROOT, SCRIPT, repository_state, stamp_in
from .slot_callers import SlotCallers
from .slot_scripts import CAP_CLOCK_MAIN, EXCLUSIVE_MAIN, EXCLUSIVE_MAIN_WITHOUT_WAITID, HOLD_SCRIPT, INTERRUPT_AFTER_THE_LEADER_EXITS, READY_MAIN, _darwin_eperm_main
from .group_scripts import BACKGROUND_CHILD, GROUP_SLEEP, IGNORING_GROUP, RECORD_REPO_SCRIPT, REPORT_SCRIPT, SURVIVING_IGNORING_GROUP, WAIT_SCRIPT
from .previous_slot import PREVIOUS_SLOT_SCRIPT
from .ofd_slot import OFD_SLOT_SCRIPT


class SlotFlowsBase:

    def assert_slot_log(self, callers, kind_of):
        """Return the most workers running at once.

        No two intervals on one slot file overlap, and an exclusive interval overlaps no other caller.
        """
        running, spans, slot_of, peak = set(), {}, {}, 0
        for position, fields in enumerate(callers.events()):
            word, name, *files = fields
            self.assertTrue(files, f"{name} recorded no slot file")
            if word == "enter":
                running.add(name)
                spans[name] = [position, None]
                slot_of[name] = tuple(files)
            else:
                running.remove(name)
                spans[name][1] = position
                self.assertEqual(tuple(files), slot_of[name], name)
            peak = max(peak, sum(kind_of[caller] == "worker" for caller in running))
        self.assertEqual(sorted(spans), sorted(kind_of))

        def overlaps(left, right):
            return left[0] < right[1] and right[0] < left[1]

        by_slot = {}
        for name, files in slot_of.items():
            for slot_file in files:
                by_slot.setdefault(slot_file, []).append(name)
        for slot_file, names in by_slot.items():
            for index, name in enumerate(names):
                for other in names[index + 1:]:
                    self.assertFalse(overlaps(spans[name], spans[other]), f"{name} and {other} overlapped on {slot_file}")
        for name, span in spans.items():
            if kind_of[name] != "exclusive":
                continue
            for other, other_span in spans.items():
                if other != name:
                    self.assertFalse(overlaps(span, other_span), f"{name} overlapped {other}")
        return peak


    def full_slots(self, slots):
        """Start callers that hold every worker slot and the landing slot. Returns the callers, the holders, and each kind."""
        callers = SlotCallers(self, slots)
        kind_of = {f"holder-{index}": "worker" for index in range(slots)}
        holders = [callers.enter(callers.start(name)) for name in kind_of]
        kind_of["holder-landing"] = "landing"
        holders.append(callers.enter(callers.start("holder-landing", pool="landing")))
        return callers, holders, kind_of


    def contend(self, slots, kinds, hold):
        """Queue one free-running caller per kind, in order, behind full slots, then free the slots.

        Each caller holds its slot for hold seconds. The slot log names the slot file each caller holds.
        Returns the most worker slots in use at once.
        """
        callers, holders, kind_of = self.full_slots(slots)
        for index, kind in enumerate(kinds):
            name = f"{index}-{kind}"
            kind_of[name] = kind
            flags = ["--exclusive"] if kind == "exclusive" else []
            pool = "landing" if kind == "landing" else "worker"
            callers.queued(callers.start(name, *flags, marked=True, pool=pool, hold=hold))
        for holder in holders:
            callers.release(holder)
        for caller in callers.callers:
            self.assertEqual(caller.process.wait(timeout=120), 0, caller.name)
        return self.assert_slot_log(callers, kind_of)


    def admit_latest_first(self, slots, kinds):
        """Park one stepped caller per kind behind full slots, free the slots, then let the latest parked caller try first.

        A caller may start before an earlier parked caller that conflicts with it only when that earlier caller could not take a slot.
        Waiters sit on their retry fifo while a slot is released, so a release cannot land between the admission
        check and the take.
        """
        def can_take(kind, busy):
            if kind == "exclusive":
                return not busy
            if kind == "landing":
                return "landing" not in busy and "exclusive" not in busy
            return "exclusive" not in busy and busy.count("worker") < slots

        callers, holders, kind_of = self.full_slots(slots)
        queue = []
        for index, kind in enumerate(kinds):
            name = f"{index}-{kind}"
            kind_of[name] = kind
            flags = ["--exclusive"] if kind == "exclusive" else []
            pool = "landing" if kind == "landing" else "worker"
            caller = callers.start(name, *flags, stepped=True, pool=pool)
            self.assertEqual(callers.settle(caller), "waiting", name)
            queue.append(caller)
        for holder in holders:
            callers.release(holder)

        pending = list(queue)
        running = []
        while pending:
            started = None
            for caller in reversed(pending):
                if callers.step(caller) == "started":
                    started = caller
                    break
            if started is None:
                self.assertTrue(running, "nobody holds a slot and nobody could take one")
                callers.release(running.pop(0))
                continue
            busy = [kind_of[caller.name] for caller in running]
            for other in pending:
                if queue.index(other) >= queue.index(started):
                    continue
                if not land._conflicts(kind_of[other.name], kind_of[started.name]):
                    continue
                self.assertFalse(can_take(kind_of[other.name], busy),
                                 f"{started.name} entered before {other.name} while {other.name} could take a slot, running {busy}")
            pending.remove(started)
            running.append(started)
        for caller in running:
            callers.release(caller)
        self.assert_slot_log(callers, kind_of)


    def ofd_locks_exist(self):
        """True when this platform accepts an OFD byte lock."""
        import struct
        path = self.base / "ofd-probe"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.fcntl(descriptor, 37, struct.pack("hhqqi4x", fcntl.F_WRLCK, os.SEEK_SET, 0, 1, 0))
        except OSError:
            return False
        else:
            return True
        finally:
            os.close(descriptor)
            os.unlink(path)


    def governor(self, slots=1):
        directory = self.base / "state/pstack-t3/governor"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "governor.json").write_text(json.dumps({"slots": slots}))
        return directory


    def slot_env(self):
        return {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}


    @contextlib.contextmanager
    def no_slot(self):
        saved = os.environ.pop("LAND_SLOT", None)
        try:
            yield
        finally:
            if saved is not None:
                os.environ["LAND_SLOT"] = saved


    def start_holder(self, kind="worker", repo=None, command="make test", slots=1):
        directory = self.base / f"holder-{kind}"
        directory.mkdir()
        ready, release = directory / "ready", directory / "release"
        os.mkfifo(release)
        process = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", HOLD_SCRIPT, str(paths.SCRIPT.parent), kind, repo or str(self.work), command,
             str(ready), str(release)],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop():
            if process.poll() is None:
                with open(release, "w") as handle:
                    handle.write("x")
            process.wait(timeout=20)
        self.addCleanup(stop)
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), f"{kind} holder did not take its slot")
        return process


    def temporary_hold(self, pid, locked):
        """Create governor/holds/worker-0.<pid>.tmp, and lock it when locked. Returns its path and descriptor."""
        holds = self.base / "state/pstack-t3/governor/holds"
        holds.mkdir(parents=True, exist_ok=True)
        path = holds / f"worker-0.{pid}.tmp"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return path, descriptor


    def dead_pid(self):
        return int(sh(sys.executable, "-c", "import os; print(os.getpid())", cwd=self.base))


    def wait_for_line(self, path, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and "\n" in path.read_text():
                return path.read_text().strip()
            time.sleep(0.02)
        self.fail(f"no report line in {path}")


    def run_waiter(self, script, release, repo=None, command="npm run build", after="0.2", every="60", kind="worker"):
        out, err = self.base / "waiter.out", self.base / "waiter.err"
        handles = [open(out, "w"), open(err, "w")]
        process = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", script, str(paths.SCRIPT.parent), after, every, kind, repo or str(self.work), command],
            env=self.slot_env(), cwd=self.base, start_new_session=True,
            stdout=handles[0], stderr=handles[1], text=False)
        line = self.wait_for_line(err)
        if release is not None:
            os.write(release, b"x")
        code = process.wait(timeout=20)
        for handle in handles:
            handle.close()
        return code, out.read_text(), err.read_text(), line


    def release_recorded_group(self, *paths):
        pids = []
        for path in paths:
            if not path.exists():
                continue
            for part in path.read_text().split():
                try:
                    pids.append(int(part))
                except ValueError:
                    continue
        if not pids:
            return
        try:
            os.killpg(pids[0], signal.SIGKILL)
        except ProcessLookupError:
            pass
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


    def _worker_waiter_leaves_the_landing_slot_unlocked(self):
        directory = self.base / "reporter"
        directory.mkdir()
        marker, release = directory / "probe", directory / "release"
        os.mkfifo(release)
        gate = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, gate)
        err = directory / "err"
        err_handle = open(err, "w")
        reporter = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", REPORT_SCRIPT, str(paths.SCRIPT.parent), str(marker), str(release), "landing-0",
             "0.2", "60", str(self.work), "make test"],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=err_handle, text=False)

        def stop_reporter():
            if reporter.poll() is None:
                os.killpg(reporter.pid, signal.SIGKILL)
            reporter.wait(timeout=20)
            err_handle.close()
        self.addCleanup(stop_reporter)

        deadline = time.monotonic() + 5
        line = ""
        while time.monotonic() < deadline:
            if marker.exists():
                self.fail("the worker waiter locked the landing slot while reporting")
            if err.exists() and "\n" in err.read_text():
                line = err.read_text().strip().splitlines()[0]
                break
            time.sleep(0.02)
        else:
            self.fail("the worker waiter printed no report")
        self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} "
                                rf"for \d+s: worker-command$")
        self.assertFalse(marker.exists())

        old = self.base / "old/land.py"
        old.parent.mkdir()
        old.write_text(PREVIOUS_SLOT_SCRIPT)
        entered, old_release = directory / "entered", directory / "old-release"
        os.mkfifo(old_release)
        old_gate = os.open(old_release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, old_gate)
        driver = (
            "import importlib.util, os, sys\n"
            "from pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('oldland', sys.argv[1])\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "with module.slot('landing'):\n"
            "    Path(sys.argv[2]).touch()\n"
            "    os.read(os.open(sys.argv[3], os.O_RDONLY), 1)\n"
        )
        caller = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", driver, str(old), str(entered), str(old_release)],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_caller():
            if caller.poll() is None:
                try:
                    os.write(old_gate, b"x")
                except OSError:
                    pass
                try:
                    caller.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(caller.pid, signal.SIGKILL)
                    caller.wait(timeout=20)
        self.addCleanup(stop_caller)

        deadline = time.monotonic() + 5
        while not entered.exists() and time.monotonic() < deadline:
            if marker.exists():
                self.fail("the worker waiter locked the landing slot while the old caller waited")
            time.sleep(0.02)
        self.assertTrue(entered.exists(), "the pre-turn-file landing caller did not enter")
        self.assertFalse(marker.exists())


    def assert_slots_free(self):
        directory = self.base / "state/pstack-t3/governor"
        for name in ("landing-0", "worker-0"):
            descriptor = os.open(directory / name, os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)
        holds = directory / "holds"
        self.assertFalse(holds.exists() and any(holds.iterdir()))


    def assert_dead(self, pid):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        self.fail(f"pid {pid} is still running")


    def assert_live(self, pid):
        # A zombie answers os.kill(pid, 0) too, so the member's state read is the liveness witness.
        state = land._ps_line(pid, "stat=")
        self.assertIsNotNone(state, f"ps could not read pid {pid}")
        self.assertFalse(state.strip().startswith("Z"), f"pid {pid} is a zombie")


    def assert_worker_holder_keeps_the_slot(self, holder):
        self.assertIsNone(holder.poll())
        record = json.loads((land.governor_dir() / "holds" / "worker-0").read_text())
        self.assertEqual(record["pid"], holder.pid)
        self.assertEqual(record["command"], "make test")
        descriptor = os.open(land.governor_dir() / "worker-0", os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
