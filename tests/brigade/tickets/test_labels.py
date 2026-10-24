if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import CLAUDE, CODEX, SCRIPT, _wait_for_path, json, runpy

class LabelsMixin:
    def test_a_dropped_ticket_and_a_done_ticket_of_a_goal_do_not_keep_the_goal_open(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "set", "T1", "--state", "dropped")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "dropped", "--evidence", "why"), "G1 dropped")

    def test_a_goal_change_while_fire_claims_its_lease_is_caught_before_the_write_and_the_lease_is_released(self):
        case = self.fake_land()
        self.two_goals()
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        proc = self.fire_with_fake_land(case, "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", "--paths", "src")
        _wait_for_path(case / "claiming")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--goal", "G2"), "T2 goal G2")
        before = self.store_files()
        (case / "claim-proceed").touch()
        _, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()),
                         (1, "brigade: nothing fired: T2 is under G2 and the lead ticket T1 is under G1; "
                             "name T2 in --ride-along, which takes a low ticket that is not a feature, or start it on its own"))
        self.assertEqual((case / "land-calls").read_text().splitlines(),
                         ["status --json", "lease claim --holder perf/D1 --paths src,changes/perf%2Fd1.md", "lease release L7"])
        self.assertEqual(self.store_files(), before)

    def test_a_state_is_logged_only_when_it_changes(self):
        self.open()
        self.fire_one()
        self.brigade("dish", "D1", "--state", "in-progress", "--task", "t-1")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", "grok/grok-4.7", "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("ticket", "set", "T1", "--state", "done")
        log = (self.at / "log.tsv").read_text()
        self.assertEqual(log.count("\tdish\tD1\tin-progress\t"), 1)
        self.assertEqual(log.count("\tticket\tT1\tdone\t"), 1)

    def test_a_waiting_ticket_that_an_item_in_flight_names_is_warned_of_for_a_bad_kind_and_a_bad_goal_with_that_items_id_and_state(self):
        self.fired_bug_fix()
        self.brigade("ticket", "set", "T1", "--state", "waiting")
        self.hand_edit("rail.tsv", "T1", kind="chore", goal="G9")
        held = f"{self.NOT_WHILE} T1 is a ticket of D1, which is in-progress; correct it in rail.tsv by hand"
        self.assert_each_printing_command_warns(f"T1 has kind 'chore', {self.NOT_A_KIND}; {held}",
                                                f"T1 names goal 'G9', {self.NO_GOAL_ROW}; {held}")

    def test_a_waiting_ticket_whose_kind_cell_holds_no_kind_is_warned_of_by_each_printing_command_and_counts_as_untagged(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="bugfix")
        out = self.assert_each_printing_command_warns(f"T1 has kind 'bugfix', {self.NOT_A_KIND}; run ticket set T1 --kind <kind>")
        self.assertEqual(out["status"].splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "untagged: 1"])
        self.assertEqual(out["ticket list"], "T1 waiting normal [user] a")
        self.assertEqual(out["startable"].splitlines()[2], "T1 normal, 0m: unknown, no paths recorded")
        self.assertEqual(out["close --dry-run"].splitlines()[4:], ["## New tickets, not started", "", "- T1: a",
                                                                  "", "## Send-back reasons (last 7 days)", "", "No send-backs in the last 7 days.",
                                                                  "", "## Spend", "", "none",
                                                                  "", "## Follow-up share", "",
                                                                  "- 0 of 1 tickets created in the last 14 days came from a report (0%)."])
        self.assertNotIn("bugfix", "".join(out.values()))

    def test_fire_ride_along_of_a_low_ticket_whose_kind_cell_is_a_padded_feature_warns_and_leaves_the_cell_as_it_was(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--priority", "low")
        self.hand_edit("rail.tsv", "T2", kind=" feature ")
        has = f"brigade: warning: T2 has kind ' feature ', {self.NOT_A_KIND}; "
        self.assertEqual(self.run_raw("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", "--ride-along", "T2"),
                         (0, "D1\n", has + "run ticket set T2 --kind <kind>\n"))
        self.assertEqual(self.raw_cells("rail.tsv", "T2", "state", "kind", "goal"), (b"assigned", b" feature ", b""))
        code, out, err = self.run_raw("ticket", "list")
        self.assertEqual((code, err), (0, has + f"{self.NOT_WHILE} T2 is assigned; correct it in rail.tsv by hand\n"))
        self.assertEqual(out, "T1 assigned normal fix [user] a\nT2 assigned low [user] b\n")

    def test_follow_up_kind_is_empty_for_an_item_with_no_kind_and_else_fix_at_urgent_or_normal_and_polish_at_low(self):
        follow_up_kind = runpy.run_path(str(SCRIPT))["follow_up_kind"]
        self.assertEqual([follow_up_kind("", priority) for priority in ("urgent", "normal", "low")], ["", "", ""])
        self.assertEqual([follow_up_kind("feature", priority) for priority in ("urgent", "normal", "low")], ["fix", "fix", "polish"])

    def test_next_asks_about_a_blocked_ticket_only_while_it_waits(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "One")
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Two", ok=False)
        self.assertEqual(refused, "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("set", "--reports-to", "admin-thread")
        young = self.next_sheet("liveness")
        self.assertNotIn("watch still shows T2 blocked", young)
        stamp = self.backdate_blocked("T2", 2)
        waiting = self.next_sheet("liveness")
        self.assertIn(
            "if watch still shows T2 blocked: t3_thread_send admin-thread mode queue "
            f"clientRequestId blocked:bridge-kit/perf/T2: blocked perf T2: waiting since {stamp}. "
            "Lease ids are not in the store.",
            waiting)
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Two")
        self.assertIn("T2 assigned", self.brigade("ticket", "list"))
        assigned = self.next_sheet("liveness")
        self.assertNotIn("watch still shows T2 blocked", assigned)
        self.assertNotIn("clientRequestId blocked:", assigned)

    def test_the_worked_store_holds_the_kind_and_goal_cells_its_tests_count(self):
        self.worked_store()
        self.assertEqual([(row[0], row[2], row[5], *row[10:]) for row in self.rows_of("rail.tsv")], [
            ("T1", "done", "D1", "feature", "G1"), ("T2", "assigned", "D2", "fix", "G1"), ("T3", "assigned", "D2", "polish", "G2"),
            ("T4", "waiting", "", "fix", "G1"), ("T5", "waiting", "", "polish", ""), ("T6", "assigned", "D3", "upkeep", "G2"),
            ("T7", "assigned", "D4", "study", ""), ("T8", "waiting", "", "", ""), ("T9", "waiting", "", "feature", "G1"),
            ("T10", "done", "D5", "polish", "")])
        self.assertEqual([(row[0], row[2], row[4], *row[15:]) for row in self.rows_of("dishes.tsv")], [
            ("D1", "merged", "T1", "feature", "G1"), ("D2", "in-review", "T2,T3", "fix", "G1"), ("D3", "sent-back", "T6", "upkeep", "G2"),
            ("D4", "in-progress", "T7", "study", ""), ("D5", "merged", "T10", "polish", "")])

    def test_ticket_set_kind_and_goal_change_a_ticket_set_back_to_waiting_whose_item_is_merged_or_dropped_and_leave_the_items_cells(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.brigade("ticket", "set", "T1", "--state", "waiting")
        self.hand_edit("dishes.tsv", "D1", state="merged")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--kind", "polish", "--goal", "G2"), "T1 kind polish; goal G2")
        self.assertEqual((self.label("rail.tsv", "T1"), self.label("dishes.tsv", "D1")), (("polish", "G2"), ("fix", "G1")))
        self.hand_edit("dishes.tsv", "D1", state="dropped")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--kind", "upkeep", "--no-goal"), "T1 kind upkeep; unassigned")
        self.assertEqual((self.label("rail.tsv", "T1"), self.label("dishes.tsv", "D1")), (("upkeep", ""), ("fix", "G1")))

    def test_ticket_set_kind_goal_and_no_goal_are_refused_on_a_done_ticket_and_on_a_dropped_ticket_and_change_no_file(self):
        self.open()
        self.goal()
        for number, state in enumerate(("done", "dropped"), 1):
            self.brigade("ticket", "add", "--summary", f"work {number}", "--kind", "fix", "--goal", "G1")
            self.brigade("ticket", "set", f"T{number}", "--state", state)
        before = self.store_files()
        for number, state in enumerate(("done", "dropped"), 1):
            for flags in (("--kind", "polish"), ("--goal", "G1"), ("--no-goal",), ("--priority", "low", "--kind", "polish"),
                          ("--state", "waiting", "--no-goal")):
                with self.subTest(state=state, flags=flags):
                    self.assertEqual(self.brigade("ticket", "set", f"T{number}", *flags, ok=False),
                                     f"brigade: T{number} is {state}; --kind, --goal, and --no-goal change a waiting ticket only")
        self.assertEqual(self.store_files(), before)

    def test_ticket_set_kind_goal_and_no_goal_are_refused_on_an_assigned_ticket_with_its_items_id_and_change_no_file(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        before = self.store_files()
        for flags in (("--kind", "polish"), ("--goal", "G1"), ("--no-goal",), ("--priority", "low", "--kind", "polish")):
            with self.subTest(flags=flags):
                self.assertEqual(self.brigade("ticket", "set", "T1", *flags, ok=False),
                                 "brigade: T1 is assigned; --kind, --goal, and --no-goal change a waiting ticket only; "
                                 "D1 keeps the kind and goal it took when it started")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")

    def test_ticket_set_kind_goal_and_no_goal_change_the_two_cells_of_a_waiting_ticket_and_add_no_log_row(self):
        self.open()
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        before = ((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes())
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "G1", "--kind", "polish"), "T1 kind polish; goal G1")
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", "G1"))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "G2"), "T1 goal G2")
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", "G2"))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--no-goal"), "T1 unassigned")
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", ""))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--kind", "study", "--priority", "low", "--no-goal", "--state", "dropped"),
                         "T1 dropped; priority low; kind study; unassigned")
        self.assertEqual(self.label("rail.tsv", "T1"), ("study", ""))
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "dropped")
        self.assertEqual((self.at / "log.tsv").read_bytes().count(b"\n"), before[0].count(b"\n") + 1)

    def test_ticket_set_with_goal_and_no_goal_together_exits_2_and_changes_no_file(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        before = self.store_files()
        code, out, _ = self.run_raw("ticket", "set", "T1", "--goal", "G1", "--no-goal")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(self.store_files(), before)
