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

    def test_a_ticket_name_hard_linked_to_an_unreadable_file_does_not_stop_a_slot(self):
        """The slot command is a process because the test asserts its exit status."""
        tickets = self.base / "state/pstack-t3/governor/tickets"
        tickets.mkdir(parents=True)
        outside = self.base / "outside-unreadable"
        outside.write_bytes(b"keep")
        outside.chmod(0)
        os.link(outside, tickets / "1-worker")
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"],
            env=self.slot_env(), timeout=20)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual([path.name for path in tickets.iterdir()], [])
        self.assertEqual(outside.stat().st_nlink, 1)
        outside.chmod(0o600)
        self.assertEqual(outside.read_bytes(), b"keep")

    def test_two_names_of_one_live_ticket_do_not_keep_a_free_landing_caller_waiting(self):
        """The waiter and the landing caller are processes so the test can order their attempts."""
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        waiter = slots.start("waiter", stepped=True)
        self.assertEqual(slots.settle(waiter), "waiting")
        tickets = slots.governor / "tickets"
        os.link(tickets / "1-worker", tickets / "2-exclusive")
        mutex = open(slots.governor / "tickets.lock", "a")
        self.addCleanup(mutex.close)
        fcntl.flock(mutex, fcntl.LOCK_EX)
        try:
            found = land._waiting(tickets, None)
        finally:
            fcntl.flock(mutex, fcntl.LOCK_UN)
        slots.release(holder)
        landing = slots.start("landing", pool="landing", stepped=True)
        landing_state = slots.settle(landing)
        self.assertEqual((found, landing_state), ([], "started"))
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), [])
        self.assertEqual(slots.step(waiter), "started")
        held = {}
        for word, name, *files in slots.events():
            if word == "exit":
                held.pop(name, None)
                continue
            own = set(files)
            for other, theirs in held.items():
                self.assertEqual(own & theirs, set(), f"{name} and {other}")
            held[name] = own
        slots.release(landing)
        slots.release(waiter)

    def test_a_slot_runs_when_its_ticket_is_removed_while_it_waits(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        waiter = slots.start("waiter", stepped=True)
        self.assertEqual(slots.settle(waiter), "waiting")
        names = sorted(path.name for path in (slots.governor / "tickets").iterdir())
        self.assertEqual(names, ["1-worker"])
        (slots.governor / "tickets" / names[0]).unlink()
        slots.release(holder)
        self.assertEqual(slots.step(waiter), "started")
        slots.release(waiter)
        self.assertEqual(slots.entries(), ["holder", "waiter"])

    def test_a_displaced_exclusive_that_retries_while_the_slot_is_busy_keeps_later_callers_waiting(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        tickets = slots.governor / "tickets"
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["1-exclusive"])
        (tickets / "1-exclusive").unlink()
        self.assertEqual(slots.step(exclusive), "waiting")
        self.assertEqual([path.stat().st_nlink for path in tickets.iterdir()], [1])
        ordinary = slots.start("ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(ordinary), "waiting")
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)
        self.assertEqual(slots.step(ordinary), "started")
        slots.release(ordinary)
        self.assertEqual(slots.entries(), ["holder", "exclusive", "ordinary"])
        self.assertEqual(slots.peak(), 1)
        held = {}
        for word, name, *files in slots.events():
            if word == "exit":
                held.pop(name, None)
                continue
            own = set(files)
            for other, theirs in held.items():
                self.assertEqual(own & theirs, set(), f"{name} and {other}")
            held[name] = own

    def test_a_number_2_retry_with_a_number_3_alias_cannot_keep_a_landing_caller_waiting(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        first = slots.start("first", stepped=True)
        self.assertEqual(slots.settle(first), "waiting")
        second = slots.start("second", stepped=True)
        self.assertEqual(slots.settle(second), "waiting")
        tickets = slots.governor / "tickets"
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["1-worker", "2-worker"])
        os.link(tickets / "1-worker", tickets / "3-exclusive")
        self.assertEqual(slots.step(second), "waiting")
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["2-worker"])
        self.assertEqual((tickets / "2-worker").stat().st_nlink, 1)
        slots.release(holder)
        landing = slots.start("landing", pool="landing", stepped=True)
        self.assertEqual(slots.settle(landing), "started")
        self.assertFalse(first.started)
        self.assertFalse(second.started)
        slots.release(landing)
        self.assertEqual(slots.step(second), "started")
        slots.release(second)
        self.assertEqual(slots.step(first), "started")
        slots.release(first)
        self.assertEqual(slots.entries(), ["holder", "landing", "second", "first"])
        self.assertEqual(slots.peak(), 1)
        held = {}
        for word, name, *files in slots.events():
            if word == "exit":
                held.pop(name, None)
                continue
            own = set(files)
            for other, theirs in held.items():
                self.assertEqual(own & theirs, set(), f"{name} and {other}")
            held[name] = own

    def test_a_filesystem_that_refuses_the_turn_mutex_flock_does_not_run_the_command(self):
        self.governor(1)
        marker = self.base / "ran"
        real = fcntl.flock

        def slot_status(refused, code):
            def flock(handle, operation):
                if operation == refused:
                    raise OSError(code, os.strerror(code))
                return real(handle, operation)

            stderr = io.StringIO()
            with mock.patch.dict(os.environ), mock.patch.object(land.fcntl, "flock", flock), \
                    contextlib.redirect_stderr(stderr):
                os.environ.pop("LAND_SLOT", None)
                return land.main(["slot", "--", "touch", str(marker)]), stderr.getvalue()

        for code in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOLCK):
            self.assertEqual(
                slot_status(fcntl.LOCK_EX, code),
                (1, "land: this filesystem does not support the slot governor's ordering lock\n"))
        with self.assertRaises(OSError) as raised:
            slot_status(fcntl.LOCK_EX | fcntl.LOCK_NB, errno.ENOLCK)
        self.assertEqual(raised.exception.errno, errno.ENOLCK)
        self.assertFalse(marker.exists())
        self.assert_slots_free()

    def test_a_hold_names_its_slot_files_and_a_nested_slot_changes_neither_variable(self):
        self.governor(2)

        def seen():
            return os.environ.get("LAND_SLOT"), os.environ.get("LAND_SLOT_FILES")

        with mock.patch.dict(os.environ):
            os.environ.pop("LAND_SLOT", None)
            os.environ.pop("LAND_SLOT_FILES", None)
            with land.slot("worker"):
                self.assertEqual(seen(), ("worker", "worker-0"))
                with land.slot("landing"):
                    self.assertEqual(seen(), ("worker", "worker-0"))
                self.assertEqual(seen(), ("worker", "worker-0"))
            self.assertEqual(seen(), (None, None))
            with land.slot("worker", count=2):
                self.assertEqual(seen(), ("worker", "worker-0 worker-1"))
                with land.slot("worker", count=2):
                    self.assertEqual(seen(), ("worker", "worker-0 worker-1"))
                self.assertEqual(seen(), ("worker", "worker-0 worker-1"))
            self.assertEqual(seen(), (None, None))
            with land.slot("landing"):
                self.assertEqual(seen(), ("landing", "landing-0"))
            with land.exclusive_slot():
                self.assertEqual(seen(), ("exclusive", "landing-0 worker-0 worker-1"))
            self.assertEqual(seen(), (None, None))
