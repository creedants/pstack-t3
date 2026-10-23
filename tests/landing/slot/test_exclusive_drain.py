from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    SlotCallers,
)
from .base import SlotBase


class LandingTest(SlotBase, LandingTestBase):

    def test_a_worker_queued_after_a_waiting_exclusive_waits_until_the_exclusive_runs(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        later = slots.start("later", stepped=True)
        self.assertEqual(slots.settle(later), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(exclusive), "started")
        self.assertEqual(slots.step(later), "waiting")
        slots.release(exclusive)
        self.assertEqual(slots.step(later), "started")
        slots.release(later)
        self.assertEqual(slots.entries(), ["holder", "exclusive", "later"])

    def test_a_worker_queued_before_a_waiting_exclusive_keeps_its_turn(self):
        slots = SlotCallers(self, 2)
        holders = [slots.enter(slots.start(f"holder-{index}")) for index in range(2)]
        early = slots.start("early", stepped=True)
        self.assertEqual(slots.settle(early), "waiting")
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        for holder in holders:
            slots.release(holder)
        self.assertEqual(slots.step(exclusive), "waiting")
        self.assertEqual(slots.step(early), "started")
        self.assertEqual(slots.step(exclusive), "waiting")
        slots.release(early)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)
        self.assertEqual(slots.entries(), ["holder-0", "holder-1", "early", "exclusive"])
