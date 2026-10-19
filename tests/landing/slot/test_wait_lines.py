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

    def test_a_waiter_behind_a_worker_holder_reports_the_holder_after_the_interval(self):
        self.governor(1)
        self.start_holder(kind="worker", command="pytest -q tests/integration")
        release = os.open(self.base / "holder-worker" / "release", os.O_WRONLY)
        try:
            _, stdout, stderr, line = self.run_waiter(
                WAIT_SCRIPT, release, command="npm run build", after="0.2")
        finally:
            os.close(release)
        self.assertEqual(stdout, "ran\n")
        self.assertEqual(stderr, line + "\n")
        self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} for \d+s: "
                                rf"pytest -q tests/integration$")

    def test_a_waiter_that_gets_a_slot_at_once_prints_nothing(self):
        self.governor(1)
        result = _run(
            [sys.executable, "-c", WAIT_SCRIPT, str(SCRIPT.parent), "0.2", "60", "worker", str(self.work), "make"],
            env=self.slot_env(), cwd=self.base, timeout=20, reason=ProcessReason.GOVERNOR)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "ran\n")
        self.assertEqual(result.stderr, "")
        holds = self.base / "state/pstack-t3/governor/holds"
        self.assertFalse(holds.exists() and list(holds.iterdir()))

    def test_a_waiter_behind_an_exclusive_holder_names_it_exclusive(self):
        self.governor(1)
        self.start_holder(kind="exclusive", command="bench --all")
        release = os.open(self.base / "holder-exclusive" / "release", os.O_WRONLY)
        try:
            _, stdout, _, line = self.run_waiter(WAIT_SCRIPT, release, after="0.2")
        finally:
            os.close(release)
        self.assertEqual(stdout, "ran\n")
        self.assertIn(f"exclusive {self.work} for ", line)
        self.assertIn(": bench --all", line)

    def test_a_stderr_error_on_the_slot_wait_line_does_not_cancel_the_waiter(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        release = self.base / "holder-worker" / "release"
        attempted = threading.Event()
        body = []

        class DeadStderr:
            def write(self, data):
                attempted.set()
                raise OSError(errno.EIO, "Input/output error")

            def flush(self):
                return None

        def release_holder():
            attempted.wait(timeout=5)
            with open(release, "w") as handle:
                handle.write("x")

        releaser = threading.Thread(target=release_holder)
        saved_after = land.REPORT_AFTER
        land.REPORT_AFTER = 0.05
        saved_stderr = sys.stderr
        releaser.start()
        try:
            sys.stderr = DeadStderr()
            with self.no_slot():
                with land.slot("worker", repo=str(self.work), command="npm test"):
                    body.append("ran")
        finally:
            sys.stderr = saved_stderr
            land.REPORT_AFTER = saved_after
            releaser.join(timeout=10)
            if holder.poll() is None:
                holder.wait(timeout=20)
        self.assertFalse(releaser.is_alive())
        self.assertTrue(attempted.is_set(), "the waiter never printed a wait line")
        self.assertEqual(body, ["ran"])
