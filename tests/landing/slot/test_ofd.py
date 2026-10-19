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

    def test_an_ofd_caller_and_a_flock_caller_reorder_without_sharing_a_slot(self):
        if not self.ofd_locks_exist():
            self.skipTest("this platform does not provide OFD locks")
        ofd = self.base / "ofd/land.py"
        ofd.parent.mkdir()
        ofd.write_text(OFD_SLOT_SCRIPT)
        slots = SlotCallers(self, 1)

        def parked(name, *flags, script=SCRIPT):
            caller = slots.start(name, *flags, script=script, stepped=True)
            self.assertEqual(slots.settle(caller), "waiting", name)
            return caller

        def later_waiter_first(holder, earlier, later):
            slots.release(holder)
            self.assertEqual(slots.step(later), "started", later.name)
            self.assertEqual(slots.step(earlier), "waiting", earlier.name)
            slots.release(later)
            self.assertEqual(slots.step(earlier), "started", earlier.name)
            slots.release(earlier)

        def worker_behind_exclusive(exclusive, worker):
            self.assertEqual(slots.step(worker), "waiting", worker.name)
            slots.release(exclusive)
            self.assertEqual(slots.step(worker), "started", worker.name)
            slots.release(worker)

        later_waiter_first(slots.enter(slots.start("ofd-holder", script=ofd)),
                           parked("ofd-earlier", script=ofd), parked("flock-later"))
        later_waiter_first(slots.enter(slots.start("flock-holder")),
                           parked("flock-earlier"), parked("ofd-later", script=ofd))
        worker_behind_exclusive(slots.enter(slots.start("flock-exclusive", "--exclusive")),
                                parked("ofd-worker", script=ofd))
        worker_behind_exclusive(slots.enter(slots.start("ofd-exclusive", "--exclusive", script=ofd)),
                                parked("flock-worker"))
        self.assertEqual(slots.entries(), [
            "ofd-holder", "flock-later", "ofd-earlier", "flock-holder", "ofd-later", "flock-earlier",
            "flock-exclusive", "ofd-worker", "ofd-exclusive", "flock-worker"])
        self.assertEqual(slots.peak(), 1)

        slots = SlotCallers(self, 2)
        holders = [slots.enter(slots.start("ofd-holder", script=ofd)), slots.enter(slots.start("flock-holder"))]
        workers = []
        for index in range(3):
            workers.append(slots.queued(slots.start(f"ofd-{index}", script=ofd, marked=True, hold=0.2)))
            workers.append(slots.queued(slots.start(f"flock-{index}", marked=True, hold=0.2)))
        for holder in holders:
            slots.release(holder)
        for worker in workers:
            self.assertEqual(worker.process.wait(timeout=120), 0, worker.name)
        self.assertEqual(len(slots.entries()), 8)
        self.assertEqual(slots.peak(), 2)

    def test_an_ofd_caller_sees_every_file_of_a_two_slot_hold(self):
        if not self.ofd_locks_exist():
            self.skipTest("this platform does not provide OFD locks")
        ofd = self.base / "ofd/land.py"
        ofd.parent.mkdir()
        ofd.write_text(OFD_SLOT_SCRIPT)
        slots = SlotCallers(self, 2)
        wide = slots.enter(slots.start("wide", "--slots", "2"))
        narrow = slots.start("ofd-narrow", script=ofd, stepped=True)
        self.assertEqual(slots.settle(narrow), "waiting")
        self.assertEqual(slots.step(narrow), "waiting")
        slots.release(wide)
        self.assertEqual(slots.step(narrow), "started")
        slots.release(narrow)
        self.assertEqual(slots.entries(), ["wide", "ofd-narrow"])
        self.assertEqual(slots.peak(), 1)

    def test_an_old_slot_holder_blocks_a_new_caller_and_the_reverse(self):
        slots = SlotCallers(self, 1)
        old = self.base / "old/land.py"
        old.parent.mkdir()
        old.write_text(PREVIOUS_SLOT_SCRIPT)

        holder = slots.enter(slots.start("old-holder", script=old))
        ordinary = slots.start("new-ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        exclusive = slots.start("new-exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        self.assertEqual(slots.step(ordinary), "waiting")
        self.assertEqual(slots.step(exclusive), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(ordinary), "started")
        slots.release(ordinary)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)

        holder = slots.enter(slots.start("new-holder"))
        waiter = slots.start("old-waiter", script=old, stepped=True)
        self.assertEqual(slots.settle(waiter), "waiting")
        self.assertEqual(slots.step(waiter), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(waiter), "started")
        slots.release(waiter)
        self.assertEqual(slots.entries(), ["old-holder", "new-ordinary", "new-exclusive", "new-holder", "old-waiter"])

    def test_a_running_holder_is_released_before_the_fixture_directory_goes(self):
        self.governor(1)
        holders = []

        def check():
            self.assertEqual(holders[0].returncode, 0)
            self.assertTrue((self.base / "holder-worker" / "release").exists())

        self.addCleanup(check)
        holders.append(self.start_holder())
        self.assertIsNone(holders[0].poll())
