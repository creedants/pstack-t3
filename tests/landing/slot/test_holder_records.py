if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    _darwin_eperm_main,
    _run,
    ast,
    BACKGROUND_CHILD,
    CAP_CLOCK_MAIN,
    cap_line,
    contextlib,
    errno,
    EXCLUSIVE_MAIN,
    EXCLUSIVE_MAIN_WITHOUT_WAITID,
    fcntl,
    GROUP_SLEEP,
    IGNORING_GROUP,
    INTERRUPT_AFTER_THE_LEADER_EXITS,
    io,
    json,
    land,
    mock,
    OFD_SLOT_SCRIPT,
    os,
    Path,
    PREVIOUS_SLOT_SCRIPT,
    ProcessReason,
    re,
    real_popen,
    real_process,
    RECORD_REPO_SCRIPT,
    REPORT_SCRIPT,
    REVIEWER,
    SCRIPT,
    sh,
    signal,
    SlotCallers,
    SLOW_PUBLISH_MAIN,
    subprocess,
    SURVIVING_IGNORING_GROUP,
    sys,
    threading,
    time,
    unittest,
    WAIT_SCRIPT,
    _darwin_eperm_main,
)
from .base import SlotBase


class LandingTest(SlotBase, LandingTestBase):

    def test_sigterm_to_an_exclusive_land_py_stops_the_group_and_clears_the_record(self):
        self.governor(1)
        parent_path, child_path = self.base / "sig-parent.pid", self.base / "sig-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", GROUP_SLEEP, str(parent_path), str(child_path)],
            cap=30, grace=0.2)
        try:
            parent_pid = self.wait_for_pid(parent_path)
            child_pid = self.wait_for_pid(child_path)
            os.kill(process.pid, signal.SIGTERM)
            code = process.wait(timeout=10)
            stderr = process.stderr.read()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(code, 143)
        self.assertNotIn("exclusive cap", stderr)
        self.assert_dead(parent_pid)
        self.assert_dead(child_pid)
        self.assert_slots_free()

    def test_a_record_whose_holder_is_gone_is_not_shown_as_holding(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        (holds / "worker-0").write_text(json.dumps(
            {"kind": "worker", "pid": 2 ** 31 - 1, "repo": str(self.work), "started": time.time(), "command": "make"}))
        self.assertEqual(land._live_holders(["worker-0"]), [land._UNKNOWN])

    def test_an_unlocked_stale_record_reads_as_unknown_when_the_lock_fails_with_enolck(self):
        self.governor(1)
        directory = land.governor_dir()
        slot = os.open(directory / "worker-0", os.O_RDWR | os.O_CREAT, 0o666)
        fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(os.close, slot)
        holds = directory / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        record = holds / "worker-0"
        record.write_text(json.dumps(
            {"kind": "worker", "pid": 2147483647, "repo": "/dead/repo", "started": 1, "command": "dead-command"}))
        self.assertEqual(land._record_holder(record), land._UNKNOWN)
        with mock.patch.object(land.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")):
            holders = land._live_holders(["worker-0"])
            line = land._format_wait(15, holders)
        self.assertEqual(holders, [land._UNKNOWN])
        self.assertEqual(line, "land: waited 15s for a slot; unknown")
        self.assertNotIn("dead-command", line)
        self.assertNotIn("/dead/repo", line)

    def test_a_malformed_record_prints_unknown_without_failing(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        record = holds / "worker-0"
        # The reader names a record only while its writer holds the record's own lock, so lock it.
        writer = os.open(record, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for content in ("{ not json", ""):
                record.write_text(content)
                holders = land._live_holders(["worker-0"])
                self.assertEqual(holders, [land._UNKNOWN])
                self.assertEqual(land._format_wait(15, holders), "land: waited 15s for a slot; unknown")
            record.unlink()
            self.assertEqual(land._live_holders(["worker-0"]), [land._UNKNOWN])
        finally:
            os.close(writer)

    def test_a_record_start_no_float_can_hold_reads_as_unknown(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        record = holds / "worker-0"
        writer = os.open(record, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for started in ("-1e309", "1e400", "1" + "0" * 399):
                text = ('{"kind": "worker", "pid": %d, "repo": %s, "started": %s, "command": "make"}'
                        % (os.getpid(), json.dumps(str(self.work)), started)).encode()
                os.pwrite(writer, text, 0)
                os.ftruncate(writer, len(text))
                holders = land._live_holders(["worker-0"])
                self.assertEqual([holder.started for holder in holders], [None])
                self.assertIn("for unknown", land._format_wait(15, holders))
        finally:
            os.close(writer)

    def test_a_record_start_no_float_can_hold_leaves_the_waiter_running(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        record = land._hold_path("worker-0")
        saved = json.loads(record.read_text())
        damaged = re.sub(r'"started": [^,}]+', '"started": -1e309', json.dumps(saved))
        self.assertNotEqual(damaged, json.dumps(saved))
        descriptor = os.open(record, os.O_WRONLY)
        try:
            os.pwrite(descriptor, damaged.encode(), 0)
            os.ftruncate(descriptor, len(damaged))
        finally:
            os.close(descriptor)
        release = os.open(self.base / "holder-worker" / "release", os.O_WRONLY)
        try:
            code, stdout, _, line = self.run_waiter(WAIT_SCRIPT, release, after="0.2")
        finally:
            os.close(release)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "ran\n")
        self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} "
                                rf"for unknown: make test$")

    def test_an_old_holder_with_no_record_names_an_unknown_holder(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="dead-command")
        self.assertEqual(land._read_holder(land._hold_path("worker-0")).command, "dead-command")
        os.kill(holder.pid, signal.SIGKILL)
        self.assertEqual(holder.wait(timeout=20), -signal.SIGKILL)
        old = self.base / "old/land.py"
        old.parent.mkdir()
        old.write_text(PREVIOUS_SLOT_SCRIPT)
        directory = self.base / "old-holder"
        directory.mkdir()
        release, took = directory / "release", directory / "took"
        os.mkfifo(release)
        read_byte = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "Path(sys.argv[2]).touch()\n"
            "os.read(os.open(sys.argv[1], os.O_RDONLY), 1)\n"
        )
        previous = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, str(old), "slot", "--", sys.executable, "-c", read_byte, str(release), str(took)],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_previous():
            if previous.poll() is None:
                os.killpg(previous.pid, signal.SIGKILL)
            previous.wait(timeout=20)
        self.addCleanup(stop_previous)
        deadline = time.monotonic() + 20
        while not took.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(took.exists(), "the previous holder did not take the worker slot")

        gate = os.open(release, os.O_WRONLY)
        try:
            code, stdout, stderr, line = self.run_waiter(WAIT_SCRIPT, gate, after="0.2")
        finally:
            os.close(gate)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "ran\n")
        self.assertEqual(stderr, line + "\n")
        self.assertRegex(line, r"^land: waited \d+s for a slot; unknown$")

    def test_the_wrapped_command_exit_status_and_output_are_unchanged(self):
        self.governor(1)
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT,
            ["slot", "--repo", str(self.work), "--", "sh", "-c", "echo out; echo err >&2; exit 7"],
            env=self.slot_env(), cwd=self.base, timeout=20)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "out\n")
        self.assertEqual(result.stderr, "err\n")

    def test_the_record_holds_only_the_first_80_characters_of_the_command(self):
        self.governor(1)

        def stored(command):
            handles = {name: open(land.governor_dir() / name, "a") for name in land._slot_names("exclusive", 1)}
            try:
                records = land._publish("worker", handles, [handles["worker-0"]], str(self.work), command)
                return json.loads(land._hold_path("worker-0").read_text())
            finally:
                land._release_records(records)
                for handle in handles.values():
                    handle.close()

        record = stored("x" * 120)
        crlf = stored("x" * 60 + "\r\n" * 10 + "SECRET")
        self.assertEqual(record["command"], "x" * 80)
        self.assertEqual(len(record["command"]), 80)
        self.assertEqual(crlf["command"], "x" * 60 + " " * 10)
        self.assertNotIn("SECRET", crlf["command"])
        self.assertEqual(set(record), set(land._Holder._fields))
        self.assertIsNone(record["cap"])
        self.assertIsNone(record["reason"])
        self.assertIsNone(crlf["cap"])
        self.assertIsNone(crlf["reason"])

    def test_a_reporting_probe_cannot_let_a_later_landing_caller_pass_an_earlier_exclusive(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        directory = self.base / "caller-reporter"
        directory.mkdir()
        marker, release = directory / "probe", directory / "release"
        os.mkfifo(release)
        # Keep both ends open so the write that resumes the probe never waits for a reader.
        gate = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, gate)
        reporter = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", REPORT_SCRIPT, str(SCRIPT.parent), str(marker), str(release), "worker-0",
             "0.2", "0.2", str(self.work), "make test"],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_reporter():
            if reporter.poll() is None:
                os.killpg(reporter.pid, signal.SIGKILL)
            reporter.wait(timeout=20)
        self.addCleanup(stop_reporter)

        slots.release(holder)
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "the reporting probe did not pause holding the free slot")

        landing = slots.start("landing", pool="landing", stepped=True)
        slots.wait(lambda: (landing.directory / "blocking").exists() or landing.started,
                   "the landing caller blocks on the turn mutex the paused probe holds")
        self.assertTrue((landing.directory / "blocking").exists())
        self.assertFalse(landing.started)

        os.write(gate, b"x")
        # The probe unlocks the free slot inside its slow_busy probe and returns, and only after that
        # does _admit unlock the turn mutex the landing caller is blocked on. Settling the landing
        # caller before the probe is stopped keeps that order, so its first attempt wakes with the
        # free slot already released and yields to the earlier exclusive instead of starting.
        self.assertEqual(slots.settle(landing), "waiting")
        # Stopping the probe before that attempt leaves when each lock is released up to the
        # kernel, so the attempt can land on either side of the free slot's release. On the
        # early side it takes landing-0 instead of yielding to the earlier exclusive.
        stop_reporter()
        self.assertEqual(slots.step(exclusive), "started")
        self.assertFalse(landing.started)
        slots.release(exclusive)
        self.assertEqual(slots.step(landing), "started")
        slots.release(landing)
        self.assertEqual(slots.entries(), ["holder", "exclusive", "landing"])

    def test_a_worker_waiter_names_the_worker_holder_and_not_the_landing_holder(self):
        self.governor(1)
        self.start_holder(kind="worker", command="worker-command")
        landing = self.start_holder(kind="landing", command="landing-command")
        release = os.open(self.base / "holder-worker" / "release", os.O_WRONLY)
        try:
            _, stdout, stderr, line = self.run_waiter(WAIT_SCRIPT, release, after="0.2")
            self.assertEqual(stdout, "ran\n")
            self.assertEqual(stderr, line + "\n")
            self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} "
                                    rf"for \d+s: worker-command$")
            self.assertNotIn("landing-command", stderr)
            self.assertNotIn("landing ", line)
        finally:
            os.close(release)
            if landing.poll() is None:
                with open(self.base / "holder-landing" / "release", "w") as handle:
                    handle.write("x")
            landing.wait(timeout=20)

    def test_a_worker_waiter_never_locks_the_landing_slot_while_it_reports(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="worker-command")
        try:
            self._worker_waiter_leaves_the_landing_slot_unlocked()
        finally:
            if holder.poll() is None:
                with open(self.base / "holder-worker" / "release", "w") as handle:
                    handle.write("x")
            holder.wait(timeout=20)

    def test_a_holder_publishes_its_record_and_clears_it_on_release(self):
        self.governor(1)
        with self.no_slot():
            with land.slot("worker", repo=str(self.work), command="make test"):
                record = land._read_holder(land._hold_path("worker-0"))
                self.assertEqual((record.kind, record.repo, record.command), ("worker", str(self.work), "make test"))
                self.assertIsNone(record.cap)
                self.assertIsNone(record.reason)
                self.assertTrue(land._hold_path("worker-0").exists())
            self.assertFalse(land._hold_path("worker-0").exists())

    def test_a_failing_wrapped_command_clears_the_holders_record(self):
        self.governor(1)
        with self.no_slot():
            result = real_process(
                ProcessReason.GOVERNOR, SCRIPT,
                ["slot", "--", "sh", "-c", "exit 3"],
                env=self.slot_env(), cwd=self.base, timeout=20)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse(land._hold_path("worker-0").exists())
