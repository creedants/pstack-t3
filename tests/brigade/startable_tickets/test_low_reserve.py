from ..support import json
from .fixtures import HELD_BACK


class LowReserveMixin:
    def test_startable_holds_back_a_free_low_ticket_from_the_last_idle_worker_while_a_normal_ticket_waits(self):
        self.queue()
        self.brigade("set", "--autofire", "low")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, held back: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            f"T2 low fix, 0m: {HELD_BACK}; b.txt",
        ]))

    def test_a_zero_low_reserve_holds_no_low_ticket_back(self):
        self.queue()
        self.brigade("set", "--autofire", "low")
        self.brigade("set", "--low-reserve", "0")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: low",
            "waiting tickets: 2, startable: 2",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 low fix, 0m: startable; b.txt",
            "  Tidy b",
        ]))

    def test_the_held_back_line_states_the_configured_low_reserve(self):
        self.queue("--workers", "3")
        self.brigade("set", "--autofire", "low")
        self.brigade("set", "--low-reserve", "2")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 3 running, 3 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, held back: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 low fix, 0m: held back, low tickets wait while an urgent or normal ticket can start now and the "
            "workers still idle are at most 2; b.txt",
        ]))

    def test_set_replacing_thread_refuses_a_malformed_low_reserve_before_landing_writes(self):
        self.queue()
        self.brigade("set", "--thread", "t1")
        path = self.at / "restaurant.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "lowReserve": "two"}))
        before = self.store_files(), self.landing_rows()
        error = self.brigade("set", "--thread", "t2", "--replace", code=1)
        self.assertIn("restaurant.json lowReserve 'two' is invalid", error)
        self.assertEqual((self.store_files(), self.landing_rows()), before)

    def test_set_records_and_clears_the_low_reserve(self):
        self.queue()
        self.brigade("set", "--low-reserve", "0")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["lowReserve"], 0)
        self.brigade("set", "--low-reserve", "")
        self.assertNotIn("lowReserve", json.loads((self.at / "restaurant.json").read_text()))

    def test_set_refuses_a_low_reserve_that_is_not_a_whole_number(self):
        self.queue()
        before = (self.at / "restaurant.json").read_bytes()
        self.assertEqual(self.brigade("set", "--low-reserve", "two", code=1),
                         "brigade: --low-reserve takes a whole number of 0 or more, got 'two'")
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_a_malformed_stored_low_reserve_is_refused_by_startable_and_set_can_repair_it(self):
        self.queue()
        path = self.at / "restaurant.json"
        meta = json.loads(path.read_text())
        meta["lowReserve"] = "two"
        path.write_text(json.dumps(meta, indent=2) + "\n")
        refused = "brigade: warning: restaurant.json lowReserve 'two' is invalid: it is not a whole number of 0 or more"
        self.assertIn(refused, self.brigade("startable", code=1))
        before = self.store_files()
        self.assertIn(refused, self.brigade("set", "--reporting", "digest", code=1))
        self.assertEqual(self.store_files(), before)
        self.brigade("set", "--low-reserve", "2")
        self.assertEqual(json.loads(path.read_text())["lowReserve"], 2)
        meta = json.loads(path.read_text())
        meta["lowReserve"] = "two"
        path.write_text(json.dumps(meta, indent=2) + "\n")
        self.brigade("set", "--low-reserve", "")
        self.assertNotIn("lowReserve", json.loads(path.read_text()))

    def test_startable_lists_a_free_low_ticket_as_startable_at_a_cap_of_one_when_no_urgent_or_normal_ticket_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 1, startable: 1",
            "T1 low fix, 0m: startable; a.txt",
            "  Tidy a",
        ]))

    def test_startable_lists_two_free_low_tickets_as_startable_at_a_cap_of_two_when_no_urgent_or_normal_ticket_waits(self):
        self.queue()
        self.brigade("set", "--autofire", "low")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: low",
            "waiting tickets: 2, startable: 2",
            "T1 low fix, 0m: startable; a.txt",
            "  Tidy a",
            "T2 low fix, 0m: startable; b.txt",
            "  Tidy b",
        ]))

    def test_startable_blocks_a_free_low_ticket_for_no_idle_worker_when_a_normal_ticket_takes_the_one_worker(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 low fix, 0m: blocked, waiting for an idle worker; b.txt",
        ]))

    def test_a_lease_blocked_normal_ticket_does_not_hold_the_low_reserve(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
            "T2 low fix, 0m: startable; a.txt",
            "  Tidy a",
        ]))

    def test_an_urgent_ticket_with_no_paths_does_not_hold_the_low_reserve(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Fix something", "--priority", "urgent")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, unknown: 1",
            "T1 urgent fix, 0m: unknown, no paths recorded",
            "T2 low fix, 0m: startable; a.txt",
            "  Tidy a",
        ]))

    def test_a_decision_ticket_does_not_hold_the_low_reserve(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Pick a name", "--decision")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, decisions: 1",
            "T1 normal fix, 0m: decision for the owner",
            "T2 low fix, 0m: startable; a.txt",
            "  Tidy a",
        ]))

    def test_startable_flags_a_low_ticket_filed_more_than_seven_days_ago_and_no_other(self):
        self.queue()
        self.filed("Old and low", "--paths", "a.txt", "--priority", "low")
        self.filed("Low", "--paths", "b.txt", "--priority", "low")
        self.filed("Old", "--paths", "c.txt")
        self.refile("T1", days=7, minutes=1)
        self.refile("T2", days=6, hours=23)
        self.refile("T3", days=9)
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, below auto-start: 2",
            "T3 normal fix, 9d: startable; c.txt",
            "  Old",
            "T1 low fix, 7d (over a week): below auto-start; a.txt",
            "T2 low fix, 6d: below auto-start; b.txt",
        ]))
