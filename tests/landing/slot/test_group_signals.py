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
from .base import STOPPED_AFTER_TERM_MAIN, SlotBase


class LandingTest(SlotBase, LandingTestBase):

    def test_sigint_to_an_exclusive_land_py_kills_a_same_group_child_that_ignores_sigterm(self):
        self.governor(1)
        code, stderr = self.signal_exclusive_ignoring_child(signal.SIGINT)
        self.assertEqual(code, -2)
        self.assertIn("KeyboardInterrupt", stderr)
        self.assertNotIn("exclusive cap", stderr)

    def test_sigterm_to_an_exclusive_land_py_kills_a_same_group_child_that_ignores_sigterm(self):
        self.governor(1)
        code, stderr = self.signal_exclusive_ignoring_child(signal.SIGTERM)
        self.assertEqual(code, 143)
        self.assertNotIn("exclusive cap", stderr)

    def test_sighup_to_an_exclusive_land_py_kills_a_same_group_child_that_ignores_sigterm(self):
        self.governor(1)
        code, stderr = self.signal_exclusive_ignoring_child(signal.SIGHUP)
        self.assertEqual(code, 129)
        self.assertNotIn("exclusive cap", stderr)

    @unittest.skipUnless(os.path.isdir("/proc"), "the test reads a zombie state from /proc")
    def test_a_signal_during_grace_after_the_leader_exits_kills_the_group(self):
        self.governor(1)

        def zombie(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] == "Z"
            except FileNotFoundError:
                return False

        statuses = {signal.SIGTERM: 143, signal.SIGHUP: 129, signal.SIGINT: -2}
        for label, main in (("waitid", EXCLUSIVE_MAIN), ("no-waitid", EXCLUSIVE_MAIN_WITHOUT_WAITID)):
            for signum, status in statuses.items():
                with self.subTest(signal=signum.name, os=label):
                    parent_path = self.base / f"grace-{label}-{signum.name}-parent.pid"
                    child_path = self.base / f"grace-{label}-{signum.name}-child.pid"
                    process = self.popen_exclusive(
                        ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP,
                         str(parent_path), str(child_path)],
                        cap=1, grace=30, main=main)
                    try:
                        child_pid, child_group = (int(part) for part in self.wait_for_line(child_path).split())
                        parent_pid = self.wait_for_pid(parent_path)
                        self.assertEqual(child_group, parent_pid)
                        deadline = time.monotonic() + 10
                        while not zombie(parent_pid) and time.monotonic() < deadline:
                            time.sleep(0.01)
                        self.assertTrue(zombie(parent_pid), "the leader did not exit at the cap")
                        os.kill(process.pid, signum)
                        code = process.wait(timeout=10)
                        self.assert_dead(child_pid)
                        self.assert_slots_free()
                        self.assertEqual(code, status)
                        self.assertNotIn("exclusive cap", process.stderr.read())
                    finally:
                        if process.poll() is None:
                            os.kill(process.pid, signal.SIGKILL)
                            process.wait(timeout=10)
                        process.stdout.close()
                        process.stderr.close()
                        self.release_recorded_group(parent_path, child_path)

    @unittest.skipUnless(os.path.isdir("/proc"), "the test reads a zombie state from /proc")
    def test_a_signal_between_the_group_sigterm_and_the_grace_kills_the_group_at_once(self):
        # The test starts a supervisor process to send it a signal and read its exit status. Its
        # wrapper stops the supervisor right after the supervisor's SIGTERM to the group. The
        # test waits for the leader's zombie, sends SIGHUP, and resumes the supervisor, which
        # must exit with the signal status within 10s, one third of the 30s grace. A supervisor
        # that lets the signal escape the interval runs the grace a second time and exits one
        # grace later.
        self.governor(1)

        def zombie(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] == "Z"
            except FileNotFoundError:
                return False

        parent_path, child_path = self.base / "term-gap-parent.pid", self.base / "term-gap-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP,
             str(parent_path), str(child_path)],
            cap=1, grace=30, main=STOPPED_AFTER_TERM_MAIN)
        try:
            child_pid, child_group = (int(part) for part in self.wait_for_line(child_path).split())
            parent_pid = self.wait_for_pid(parent_path)
            self.assertEqual(child_group, parent_pid)
            deadline = time.monotonic() + 10
            while not zombie(parent_pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(zombie(parent_pid), "the leader did not exit at the cap")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                state = Path(f"/proc/{process.pid}/stat").read_text().rpartition(")")[2].split()[0]
                if state == "T":
                    break
                time.sleep(0.01)
            else:
                self.fail("the supervisor did not stop after its group SIGTERM")
            os.kill(process.pid, signal.SIGHUP)
            os.kill(process.pid, signal.SIGCONT)
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                self.fail("the supervisor did not exit within 10s of the signal, one third of the 30s grace")
            self.assertEqual(code, 129)
            self.assert_dead(child_pid)
            self.assert_slots_free()
            self.assertNotIn("exclusive cap", process.stderr.read())
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
            self.release_recorded_group(parent_path, child_path)
