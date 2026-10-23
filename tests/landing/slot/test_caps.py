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

    def test_an_exclusive_command_that_outlives_its_cap_is_stopped(self):
        self.governor(1)
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
            cap=0.4, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "land: stopped the command at the 0.4s exclusive cap. "
            "--for sets a longer cap and needs --reason past 0.4s.\n")
        self.assert_slots_free()

    def test_an_exclusive_cap_stops_a_child_still_in_the_commands_process_group(self):
        self.governor(1)
        parent_path, child_path = self.base / "parent.pid", self.base / "child.pid"
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", GROUP_SLEEP, str(parent_path), str(child_path)],
            cap=1, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 124)
        self.assertIn("1s exclusive cap", result.stderr)
        self.assertIn("--for", result.stderr)
        self.assert_dead(self.wait_for_pid(parent_path))
        self.assert_dead(self.wait_for_pid(child_path))
        self.assert_slots_free()

    def test_an_exclusive_command_that_ignores_sigterm_is_killed_after_the_grace(self):
        self.governor(1)
        log_path = self.base / "term-log"
        command = (
            "import signal, sys, time\n"
            "from pathlib import Path\n"
            "log = Path(sys.argv[1])\n"
            "def note(word):\n"
            "    handle = log.open('a')\n"
            "    handle.write(f'{word} {time.time()}\\n')\n"
            "    handle.close()\n"
            "def on_term(signum, frame):\n"
            "    note('term')\n"
            "signal.signal(signal.SIGTERM, on_term)\n"
            "note('start')\n"
            "time.sleep(3600)\n"
        )
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", command, str(log_path)],
            cap=4, grace=0.3, timeout=20)
        self.assertEqual(result.returncode, 124)
        lines = log_path.read_text().splitlines()
        self.assertEqual([line.split()[0] for line in lines], ["start", "term"])
        start, term = (float(line.split()[1]) for line in lines)
        self.assertGreater(term - start, 0.2)
        self.assertGreater(time.time() - term, 0.12)
        self.assert_slots_free()

    def test_an_exclusive_command_that_finishes_before_the_cap_keeps_its_status_and_output(self):
        self.governor(1)
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c",
             "import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(7)"],
            cap=2, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "out\n")
        self.assertEqual(result.stderr, "err\n")
        self.assert_slots_free()

    def test_an_ordinary_ready_symlink_leaves_its_destination_absent(self):
        self.governor(1)
        link = self.base / "ready-link"
        destination = self.base / "ready-destination"
        link.symlink_to(destination)
        with mock.patch.dict(os.environ, {"LAND_EXCLUSIVE_READY": str(link)}):
            result = self.run_exclusive(
                ["slot", "--exclusive", "--", sys.executable, "-c", "pass"],
                cap=5, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(destination.exists(), "an ordinary ready symlink created its destination")
        self.assertNotIn("LAND_EXCLUSIVE_READY", SCRIPT.read_text())

    def test_an_exclusive_cap_starts_when_the_hold_takes_the_slots(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        release = self.base / "holder-worker" / "release"
        clock = self.base / "cap-clock"
        clock.write_text("1000")
        with mock.patch.dict(os.environ, {"LAND_CAP_CLOCK": str(clock)}):
            process = self.popen_exclusive(
                ["slot", "--exclusive", "--for", "2", "--", sys.executable, "-c",
                 "import time; time.sleep(0.2); print('ok', flush=True)"],
                main=CAP_CLOCK_MAIN)
            try:
                tickets = self.base / "state/pstack-t3/governor/tickets"
                deadline = time.monotonic() + 20
                seen = False
                while time.monotonic() < deadline:
                    try:
                        names = os.listdir(tickets)
                    except OSError:
                        names = []
                    if any(name.endswith("-exclusive") for name in names):
                        seen = True
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(seen, "the exclusive waiter took no ticket")
                self.assertIsNone(process.poll(), "the exclusive caller exited while the slots were still busy")
                clock.write_text("1010")
                with open(release, "w") as handle:
                    handle.write("x")
                stdout, stderr = process.communicate(timeout=20)
            finally:
                if process.poll() is None:
                    os.kill(process.pid, signal.SIGKILL)
                    process.wait(timeout=20)
                process.stdout.close()
                process.stderr.close()
                if holder.poll() is None:
                    holder.wait(timeout=20)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout, "ok\n")
        self.assertNotIn("exclusive cap", stderr)
        self.assert_slots_free()

    def test_an_exclusive_cap_kills_a_same_group_child_that_ignores_sigterm_after_the_leader_exits(self):
        self.governor(1)
        parent_path, child_path = self.base / "cap-parent.pid", self.base / "cap-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP,
             str(parent_path), str(child_path)],
            cap=4, grace=0.15)
        try:
            child_line = self.wait_for_line(child_path)
            code = process.wait(timeout=10)
            parent_pid = self.wait_for_pid(parent_path)
            child_pid, child_group = (int(part) for part in child_line.split())
            self.assertEqual(child_group, parent_pid)
            self.assert_dead(parent_pid)
            self.assert_dead(child_pid)
            stderr = process.stderr.read()
            self.assertEqual(code, 124)
            self.assertEqual(
                stderr,
                "land: stopped the command at the 4s exclusive cap. "
                "--for sets a longer cap and needs --reason past 4s.\n")
            self.assert_slots_free()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
            self.release_recorded_group(parent_path, child_path)

    def test_cap_cases_without_os_waitid(self):
        self.governor(1)

        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
            cap=0.4, grace=0.05, timeout=10, main=EXCLUSIVE_MAIN_WITHOUT_WAITID)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (124, "", cap_line("0.4")))
        self.assert_slots_free()

        parent_path, child_path = self.base / "group-parent.pid", self.base / "group-child.pid"
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", GROUP_SLEEP, str(parent_path), str(child_path)],
            cap=1, grace=0.05, timeout=10, main=EXCLUSIVE_MAIN_WITHOUT_WAITID)
        self.assertEqual((result.returncode, result.stderr), (124, cap_line("1")))
        self.assert_dead(self.wait_for_pid(parent_path))
        self.assert_dead(self.wait_for_pid(child_path))
        self.assert_slots_free()

        parent_path, child_path = self.base / "ignoring-parent.pid", self.base / "ignoring-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP, str(parent_path), str(child_path)],
            cap=1, grace=0.15, main=EXCLUSIVE_MAIN_WITHOUT_WAITID)
        try:
            child_pid, child_group = (int(part) for part in self.wait_for_line(child_path).split())
            code = process.wait(timeout=10)
            parent_pid = self.wait_for_pid(parent_path)
            self.assertEqual(child_group, parent_pid)
            self.assert_dead(parent_pid)
            self.assert_dead(child_pid)
            self.assertEqual((code, process.stderr.read()), (124, cap_line("1")))
            self.assert_slots_free()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
            self.release_recorded_group(parent_path, child_path)

    def test_an_exclusive_command_that_finishes_leaves_a_background_child(self):
        self.governor(1)
        child_path = self.base / "background-child.pid"
        child = None
        try:
            result = self.run_exclusive(
                ["slot", "--exclusive", "--", sys.executable, "-c", BACKGROUND_CHILD, str(child_path)],
                cap=30, timeout=10)
            child = int(child_path.read_text().split()[0])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("exclusive cap", result.stderr)
            time.sleep(0.2)
            os.kill(child, 0)
            self.assert_slots_free()
        finally:
            if child is None and child_path.exists():
                parts = child_path.read_text().split()
                if parts:
                    try:
                        child = int(parts[0])
                    except ValueError:
                        child = None
            if child is not None:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.kill(child, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)

    def test_an_exclusive_deadline_is_when_every_slot_is_locked(self):
        self.governor(1)
        result = _run(
            [sys.executable, "-c", SLOW_PUBLISH_MAIN, str(SCRIPT.parent),
             "slot", "--exclusive", "--for", "0.2", "--",
             sys.executable, "-c", "print('command-finished', flush=True)"],
            env=self.slot_env(), cwd=self.base, timeout=10, reason=ProcessReason.GOVERNOR)
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn("all-slots-taken", result.stdout)
        self.assertNotIn("command-finished", result.stdout)
        self.assertEqual(
            result.stderr,
            "land: stopped the command at the 0.2s exclusive cap. "
            "--for sets a longer cap and needs --reason past 600s.\n")
        lines = result.stdout.splitlines()
        self.assertEqual([line.split()[0] for line in lines], ["all-slots-taken", "exit"])
        taken = float(lines[0].split()[1])
        exited = float(lines[1].split()[1])
        self.assertGreaterEqual(exited - taken, 0.5)
        self.assert_slots_free()
