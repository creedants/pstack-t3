if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import ProcessReason, SCRIPT, call, json, os, real_process

class RailBMixin:
    def test_a_store_without_a_generation_works_until_set_thread_records_one(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["thread"] = "t1"
        (self.at / "restaurant.json").write_text(json.dumps(meta))
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "legacy"), "T1")
        self.brigade("set", "--thread", "t1")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "unowned", ok=False, owner=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.brigade("--owner", "t1@1", "ticket", "add", "--kind", "fix", "--summary", "owned"), "T2")

    def test_blocked_counts_follow_the_block_that_holds_now(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate",
                         "--paths", "src", ok=False)
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate blocked: lease")
            self.assertIn("Perf (reports milestones): waiting tickets: 1 (1 blocked)", self.brigade("walk"))
            self.land("lease", "release", "L1")
            self.assertTrue(self.brigade("watch").splitlines()[1].startswith("T1: unblocked; run fire"))
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate")
            self.assertIn("Perf (reports milestones): waiting tickets: 1\n", self.brigade("walk") + "\n")
            self.land("lease", "claim", "--holder", "engine/D2", "--paths", "src")
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate blocked: lease")

    def test_fire_and_watch_name_the_worker_cap_first_when_both_caps_hold(self):
        from unittest import mock
        self.init_landing("--cap", "1")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one", "--paths", "src")
            refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "two",
                                   "--paths", "docs", ok=False)
            self.assertEqual(refused, "brigade: nothing fired: 1 of 1 workers running")
            self.assertEqual(self.brigade("watch").splitlines()[-1], "T2: waiting for a worker (1 of 1 running)")
            self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] two blocked: workers")
            self.brigade("set", "--workers", "2")
            self.assertEqual(self.brigade("watch").splitlines()[-1],
                             "T2: waiting for room in the repository (1 of 1 changes in flight)")
            self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"),
                             "T2 waiting normal fix [user] two blocked: repository")

    def test_item_mode_flags_refuse_light_and_a_missing_reason_with_exit_1(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        before = (self.at / "log.tsv").read_bytes()
        refusals = [
            (("dish", "D1", "--mode", "light", "--reason", "r"),
             "brigade: an item's mode only moves to full; change the coordinator with set --mode light"),
            (("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "t", "--mode", "light", "--reason", "r"),
             "brigade: an item's mode only moves to full; change the coordinator with set --mode light"),
            (("dish", "D1", "--mode", "full"), 'brigade: --mode full needs --reason "<one line>"'),
            (("dish", "D1", "--mode", "full", "--reason", " "), 'brigade: --mode full needs --reason "<one line>"'),
            (("dish", "D1", "--reason", "r"), "brigade: --reason goes with --mode full"),
            (("dish", "D1", "--mode", "fast", "--reason", "r"), "brigade: --mode takes full, got 'fast'"),
            (("dish", "D1", "--mode", "full", "--reason", "r", "--state", "dropped"),
             "brigade: --mode full needs open work; run --state dropped without it"),
            (("dish", "D1", "--mode", "full", "--reason", "r", "--state", "queued"),
             "brigade: --mode full needs open work; run --state queued without it"),
        ]
        for args, message in refusals:
            outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at), *args])
            self.assertEqual((outcome.code, outcome.stderr_text().strip()), (1, message), args)
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] t")

    def test_the_worker_cap_defaults_to_two_and_a_later_fire_clears_the_block(self):
        self.open()
        self.assertNotIn("workers", json.loads((self.at / "restaurant.json").read_text()))
        for summary in ("one", "two", "three"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a"), "D1")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b"), "D2")
        refused = self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "c", ok=False)
        self.assertIn("nothing fired: 2 of 2 workers running", refused)
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"),
                         "T3 waiting normal fix [user] three blocked: workers")
        self.assertIn("T3: waiting for a worker (2 of 2 running)", self.brigade("watch"))
        self.brigade("set", "--workers", "3")
        self.assertEqual(self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "c"), "D3")
        self.assertNotIn("blocked:", self.brigade("ticket", "list"))

    def test_ticket_help_names_paths(self):
        # -h output
        result = real_process(ProcessReason.HELP, SCRIPT, ["--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("add, list, update, move, take, or record paths on tickets on the rail", " ".join(result.stdout.split()))

    def test_ticket_list_prints_the_kind_then_the_goal_after_the_priority_and_neither_for_a_ticket_with_no_kind_and_no_goal(self):
        self.worked_store()
        self.hand_edit("rail.tsv", "T9", kind="")
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), [
            "T4 waiting normal fix G1 [user] Activity page drops a row",
            "T5 waiting low polish [user] Tidy the lease docs",
            "T8 waiting normal [user] Installer keeps a relative config home",
            "T9 waiting normal G1 [user] Activity page filters by coordinator",
        ])
        self.assertEqual(self.brigade("ticket", "list", "--state", "assigned").splitlines()[:2], [
            "T2 assigned normal fix G1 [user] Lease check arms a reservation",
            "T3 assigned low polish G2 [user] Tidy the lease help",
        ])

    def test_ticket_list_reads_rail_rows_written_before_the_priority_paths_and_decision_cells(self):
        self.open()
        self.old_rail()
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.brigade("ticket", "list").splitlines(), [
            "T1 waiting normal [user] Fix login",
            "T2 waiting low [report] Tidy the guide perf/reports/D1.md#1",
            "T3 waiting urgent [upstream] Sync upstream abc123",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "New", "--priority", "urgent", "--paths", "a.txt"), "T4")
        self.assertEqual((self.at / "rail.tsv").read_bytes()[:len(before)], before)
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1], "T4 waiting urgent fix [user] New")
        self.assertEqual((self.at / "rail.tsv").read_text().splitlines()[-1].split("\t")[5:],
                         ["", "New", "urgent", "a.txt", "", "fix", "", ""])

    def test_ticket_set_kind_goal_and_no_goal_are_refused_on_a_waiting_ticket_that_an_item_in_flight_names_and_change_no_file_and_priority_alone_is_accepted(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2", "--priority", "low")
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "feature", "--summary", "Build a")
        for ticket in ("T1", "T2"):
            self.assertEqual(self.brigade("ticket", "set", ticket, "--state", "waiting"), f"{ticket} waiting")
        for state in ("in-progress", "in-review", "passed", "sent-back", "blocked", "queued"):
            self.hand_edit("dishes.tsv", "D1", state=state)
            before = self.store_files()
            for ticket, flags in (("T1", ("--kind", "polish")), ("T1", ("--goal", "G2")), ("T1", ("--no-goal",)),
                                  ("T1", ("--priority", "urgent", "--kind", "polish")), ("T2", ("--goal", "G1"))):
                with self.subTest(state=state, ticket=ticket, flags=flags):
                    self.assertEqual(self.brigade("ticket", "set", ticket, *flags, ok=False),
                                     f"brigade: {ticket} is a ticket of D1, which is {state}; --kind, --goal, and --no-goal change "
                                     "a waiting ticket that no item in flight names; D1 keeps the kind and goal it took when it "
                                     f"started; drop D1, and every other item in flight that names {ticket}, first, or leave {ticket} as it is")
            self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")

    def test_ticket_set_with_empty_paths_clears_the_recorded_paths(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "a.txt")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--paths", ""), "T1 no paths")
        self.assertEqual(self.cells("T1"), ("", "", ""))
