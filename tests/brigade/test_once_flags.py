from .support import (
    setUpModule,
    BrigadeTest,
    SCRIPT,
    _owner_words,
    call,
)


class OnceFlagsTest(BrigadeTest):
    TICKETS_REFUSAL = "brigade: --tickets given twice. Pass one comma-separated value."
    LEASE_REFUSAL = "brigade: --lease given twice. Pass one lease id."

    def refused(self, *args):
        return call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                   *_owner_words(self.at), *(str(arg) for arg in args)])

    def check_refused(self, refusal, *args):
        lock = self.at / "restaurant.lock"
        if lock.exists():
            lock.unlink()
        before = self.store_files()
        outcome = self.refused(*args)
        self.assertEqual(outcome.code, 1)
        self.assertEqual(outcome.stderr_text().strip(), refusal)
        self.assertEqual(outcome.stdout_text(), "")
        self.assertEqual(self.store_files(), before)
        self.assertFalse(lock.exists())

    def test_fire_refuses_a_second_tickets_flag_and_changes_nothing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.check_refused(self.TICKETS_REFUSAL, "fire", "--tickets", "T1", "--tickets", "T1",
                           "--station", "bug-fix", "--summary", "s")

    def test_brief_refuses_a_second_lease_flag_and_changes_nothing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s")
        self.brigade("set", "--thread", "thread-coord")
        self.check_refused(self.LEASE_REFUSAL, "brief", "D1", "--goal", "g", "--acceptance", "a",
                           "--contract", "c", "--verify", "v", "--base", "origin/main",
                           "--lease", "L1", "--lease", "L2")

    def test_dish_refuses_a_second_lease_flag_and_changes_nothing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s")
        self.check_refused(self.LEASE_REFUSAL, "dish", "D1",
                           "--lease", "L1", "--lease", "L2")
