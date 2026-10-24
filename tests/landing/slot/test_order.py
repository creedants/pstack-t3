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

    def test_an_earlier_ordinary_waiter_runs_before_a_later_exclusive(self):
        slots = SlotCallers(self, 2)
        holders = [slots.enter(slots.start(f"holder-{index}")) for index in range(2)]
        ordinary = slots.start("ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        for holder in holders:
            slots.release(holder)
        self.assertEqual(slots.step(exclusive), "waiting")
        self.assertEqual(slots.step(ordinary), "started")
        self.assertEqual(slots.step(exclusive), "waiting")
        slots.release(ordinary)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)
        self.assertEqual(slots.entries(), ["holder-0", "holder-1", "ordinary", "exclusive"])

    def test_an_earlier_exclusive_waiter_runs_before_a_later_ordinary_once_every_slot_is_free(self):
        slots = SlotCallers(self, 2)
        holders = [slots.enter(slots.start(f"holder-{index}")) for index in range(2)]
        holders.append(slots.enter(slots.start("landing", pool="landing")))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        ordinary = slots.start("ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        for holder in holders:
            slots.release(holder)
        self.assertEqual(slots.step(ordinary), "waiting")
        self.assertEqual(slots.step(exclusive), "started")
        self.assertEqual(slots.step(ordinary), "waiting")
        slots.release(exclusive)
        self.assertEqual(slots.step(ordinary), "started")
        slots.release(ordinary)
        self.assertEqual(slots.entries(), ["holder-0", "holder-1", "landing", "exclusive", "ordinary"])

    def test_a_worker_queued_after_a_waiting_exclusive_waits_for_the_drain(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        ordinary = slots.start("ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        self.assertFalse(exclusive.started)
        self.assertEqual(slots.step(exclusive), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(exclusive), "started")
        self.assertEqual(slots.step(ordinary), "waiting")
        slots.release(exclusive)
        self.assertEqual(slots.step(ordinary), "started")
        slots.release(ordinary)
        self.assertEqual(slots.entries(), ["holder", "exclusive", "ordinary"])

    def test_an_exclusive_waiter_holds_no_slot_lock_while_it_waits(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        free = []
        for name in ("landing-0", "worker-0", "worker-1"):
            with open(slots.governor / name, "a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                free.append(name)
        self.assertEqual(free, ["landing-0", "worker-1"])
        slots.release(holder)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)

    def test_a_two_slot_caller_behind_one_busy_slot_holds_nothing(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        wide = slots.start("wide", "--slots", "2", stepped=True)
        self.assertEqual(slots.settle(wide), "waiting")
        free = []
        for name in ("landing-0", "worker-0", "worker-1"):
            with open(slots.governor / name, "a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                free.append(name)
        self.assertEqual(free, ["landing-0", "worker-1"])
        self.assertEqual(os.listdir(slots.governor / "tickets"), ["1-worker"])
        slots.release(holder)
        self.assertEqual(slots.step(wide), "started")
        slots.release(wide)

    def test_a_later_one_slot_caller_does_not_pass_an_earlier_two_slot_ticket(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        wide = slots.start("wide", "--slots", "2", stepped=True)
        self.assertEqual(slots.settle(wide), "waiting")
        narrow = slots.start("narrow", stepped=True)
        self.assertEqual(slots.settle(narrow), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(wide), "started")
        self.assertIn(("enter", "wide", "worker-0", "worker-1"), slots.events())
        self.assertEqual(slots.step(narrow), "waiting")
        slots.release(wide)
        self.assertEqual(slots.step(narrow), "started")
        slots.release(narrow)
        self.assertEqual(slots.entries(), ["holder", "wide", "narrow"])

    def test_slots_over_the_governor_are_refused_and_the_command_does_not_run(self):
        directory = self.governor(2)
        marker = self.base / "ran"

        def slot_status(*flags):
            stderr = io.StringIO()
            with mock.patch.dict(os.environ), contextlib.redirect_stderr(stderr):
                os.environ.pop("LAND_SLOT", None)
                return land.main(["slot", *flags, "--", "touch", str(marker)]), stderr.getvalue()

        integer = "land: --slots must be a positive integer\n"
        self.assertEqual(slot_status("--slots", "3"),
                         (1, "land: --slots 3 is more than the 2 worker slots this governor has\n"))
        self.assertEqual(slot_status("--slots", "0"), (1, integer))
        self.assertEqual(slot_status("--slots", "-1"), (1, integer))
        self.assertEqual(slot_status("--slots", "nope"), (1, integer))
        self.assertEqual(slot_status("--slots", "2", "--exclusive"),
                         (1, "land: --slots cannot be combined with --exclusive\n"))
        self.assertFalse(marker.exists())
        self.assertFalse((directory / "worker-0").exists())
        with mock.patch.dict(os.environ, {"LAND_SLOT": "worker", "LAND_SLOT_FILES": "worker-0"}):
            with land.slot("worker", count=3):
                self.assertEqual(os.environ["LAND_SLOT_FILES"], "worker-0")
        self.assertFalse((directory / "worker-0").exists())

    def test_a_waiter_killed_with_sigkill_does_not_block_a_later_caller(self):
        slots = SlotCallers(self, 1)

        def kill(caller):
            self.assertFalse(caller.started)
            os.kill(caller.process.pid, signal.SIGKILL)
            self.assertEqual(caller.process.wait(timeout=20), -signal.SIGKILL)

        holder = slots.enter(slots.start("holder"))
        kill(slots.queued(slots.start("ticketed", marked=True)))
        slots.release(holder)
        slots.release(slots.enter(slots.start("after-ticketed")))
        self.assertEqual(list((slots.governor / "tickets").iterdir()), [])

        with open(slots.governor / "tickets.lock", "a") as mutex:
            fcntl.flock(mutex, fcntl.LOCK_EX)
            blocked = slots.start("blocked", marked=True)
            slots.wait(lambda: (blocked.directory / "blocking").exists(), "the waiter blocks on the turn mutex")
            kill(blocked)
        slots.release(slots.enter(slots.start("after-blocked")))
        self.assertEqual(slots.entries(), ["holder", "after-ticketed", "after-blocked"])

    def test_many_callers_never_exceed_the_slot_count(self):
        slots = SlotCallers(self, 2)
        waiting = [slots.start(f"worker-{index}", marked=True) for index in range(5)]
        while waiting:
            expected = min(2, len(waiting))
            slots.wait(lambda: sum(caller.started for caller in waiting) >= expected
                       and all(caller.started or (caller.directory / "waiting-1").exists() for caller in waiting),
                       "the slots are full and the rest wait")
            running = [caller for caller in waiting if caller.started]
            self.assertEqual(len(running), expected)
            slots.release(running[0])
            waiting.remove(running[0])

        exclusive = slots.enter(slots.start("exclusive", "--exclusive"))
        waiting = [slots.queued(slots.start(f"later-{index}", marked=True)) for index in range(3)]
        waiting.append(slots.queued(slots.start("landing", pool="landing", marked=True)))
        self.assertEqual([caller.name for caller in waiting if caller.started], [])
        slots.release(exclusive)
        while waiting:
            slots.wait(lambda: any(caller.started for caller in waiting), "a waiting caller started")
            running = next(caller for caller in waiting if caller.started)
            slots.release(running)
            waiting.remove(running)

        running, peak = set(), 0
        for word, name, *_ in slots.events():
            if word == "exit":
                running.remove(name)
                continue
            running.add(name)
            peak = max(peak, len(running - {"landing"}))
            if "exclusive" in running:
                self.assertEqual(running, {"exclusive"})
        self.assertEqual(peak, 2)
        self.assertEqual(len(slots.entries()), 10)

    def test_contending_callers_never_share_a_slot_and_never_pass_an_earlier_ticket(self):
        kinds = ["worker", "exclusive", "landing", "worker", "landing", "exclusive", "worker", "worker", "landing", "exclusive"]
        self.assertEqual(self.contend(1, kinds, hold=0.1), 1)
        self.admit_latest_first(1, kinds)

    def test_contending_callers_fill_two_worker_slots_and_never_pass_an_earlier_ticket(self):
        kinds = ["worker", "worker", "landing", "exclusive", "worker", "landing", "worker", "worker", "exclusive", "worker"]
        self.assertEqual(self.contend(2, kinds, hold=1.5), 2)
        self.admit_latest_first(2, kinds)
