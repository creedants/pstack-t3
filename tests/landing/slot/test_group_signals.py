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


def _zombie(pid):
    """Return True when pid is a zombie, and False where /proc holds no entry for it, as it does once the kernel reaps it."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


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
                        while not _zombie(parent_pid) and time.monotonic() < deadline:
                            time.sleep(0.01)
                        self.assertTrue(_zombie(parent_pid), "the leader did not exit at the cap")
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
            while not _zombie(parent_pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(_zombie(parent_pid), "the leader did not exit at the cap")
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

    @unittest.skipUnless(os.path.isdir("/proc"), "the test reads a zombie state from /proc")
    def test_a_process_reaped_after_its_stat_entry_opens_counts_as_not_a_zombie(self):
        """A read through an entry opened while its process ran raises ProcessLookupError once the kernel reaps that process, so the zombie helper reports that it is not a zombie instead of raising out of the test."""

        def held(reap):
            """Start a real child and open its stat entry, killing and reaping it first when reap is true. Return its pid and the open entry."""
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            self.addCleanup(child.wait)
            self.addCleanup(child.kill)
            handle = os.open(f"/proc/{child.pid}/stat", os.O_RDONLY)
            self.addCleanup(os.close, handle)
            if reap:
                child.kill()
                child.wait()
            return child.pid, handle

        def reads_through(handle, entry):
            """Return a Path.read_text that reads the open entry for entry, and reads every other path as before."""
            original = Path.read_text

            def read_text(itself, *args, **kwargs):
                if itself != entry:
                    return original(itself, *args, **kwargs)
                os.lseek(handle, 0, os.SEEK_SET)
                return os.read(handle, 65536).decode()

            return read_text

        live, live_handle = held(reap=False)
        with mock.patch.object(Path, "read_text", reads_through(live_handle, Path(f"/proc/{live}/stat"))):
            self.assertFalse(_zombie(live), "a live process read through its own entry is not a zombie")
        reaped, reaped_handle = held(reap=True)
        entry = Path(f"/proc/{reaped}/stat")
        with mock.patch.object(Path, "read_text", reads_through(reaped_handle, entry)):
            self.assertRaises(ProcessLookupError, entry.read_text)
            self.assertFalse(_zombie(reaped), "a process reaped after its entry opened is not a zombie")
