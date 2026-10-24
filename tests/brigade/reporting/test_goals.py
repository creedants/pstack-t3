if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import setUpModule
from ..support import BrigadeTest, CLAUDE, CODEX, Path, ProcessReason, SCRIPT, call, call_in_process, real_process, run_globals, _ReportingRace, _owner_words, _shown_root, datetime, io, json, mock, os, runpy, subprocess, sys, timezone
import sqlite3

class ReportingTest(BrigadeTest):
    def test_close_prints_no_unrecorded_review_warning_for_the_report_a_pass_with_edits_row_names(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_row("D1-review-1.md")))
        self.record_edits("abc", "--edits", "D1-review-1.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
    def test_from_report_files_a_ticket_under_the_goal_of_its_item_when_that_goal_is_done_and_goal_list_prints_that_goal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "merged")
        self.follow_ups_in("## Follow-ups\n\n- Correctness: Tidy the guide.\n")
        self.assertEqual(self.label("rail.tsv", "T2"), ("polish", "G1"))
        self.assertEqual(self.brigade("goal", "list").splitlines()[0],
                         "G1 done activity view: 1 waiting, 0 in flight, 1 merged, 0 send-backs")
    def test_goal_list_in_a_store_with_no_goal_prints_that_and_the_unassigned_line(self):
        self.open()
        before = self.store_files()
        self.assertEqual(self.run_raw("goal", "list"), (0, f"no open or paused goals\n{self.NO_UNASSIGNED_WORK}\n", ""))
        self.assertEqual(self.run_raw("goal", "list", "--all"), (0, f"no goals\n{self.NO_UNASSIGNED_WORK}\n", ""))
        self.assertEqual(self.store_files(), before)
    def test_goal_list_prints_each_open_and_paused_goal_in_file_order_with_its_share_and_all_adds_each_done_and_dropped_goal_with_its_closed_cell(self):
        self.open()
        self.goal("activity view", "--share", "2")
        self.goal("old lint")
        self.goal("current with upstream")
        self.goal("windows port")
        self.brigade("goal", "set", "G2", "--state", "done", "--evidence", "shipped in #12")
        self.brigade("goal", "set", "G3", "--state", "paused")
        self.brigade("goal", "set", "G4", "--state", "dropped", "--evidence", "no users")
        closed = {ident: self.table_row(self.at, "goals.tsv", ident)["closed"] for ident in ("G2", "G4")}
        self.assertEqual(closed["G2"].split(" ", 1)[1], "shipped in #12")
        counts = "0 waiting, 0 in flight, 0 merged, 0 send-backs"
        detail = ["  purpose: One page of activity", "  done when: The page ships"]
        self.assertEqual(self.brigade("goal", "list").splitlines(),
                         [f"G1 open activity view: {counts}, share 2", *detail,
                          f"G3 paused current with upstream: {counts}", *detail,
                          self.NO_UNASSIGNED_WORK])
        self.assertEqual(self.brigade("goal", "list", "--all").splitlines(),
                         [f"G1 open activity view: {counts}, share 2", *detail,
                          f"G2 done old lint: {counts}", *detail, f"  closed: {closed['G2']}",
                          f"G3 paused current with upstream: {counts}", *detail,
                          f"G4 dropped windows port: {counts}", *detail, f"  closed: {closed['G4']}",
                          self.NO_UNASSIGNED_WORK])
    def test_goal_list_with_only_done_and_dropped_goals_prints_no_open_or_paused_goals(self):
        self.open()
        self.goal()
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.assertEqual(self.brigade("goal", "list"), f"no open or paused goals\n{self.NO_UNASSIGNED_WORK}")
    def test_a_goal_share_that_is_not_a_whole_number_of_1_or_more_reads_as_no_limit_with_a_warning_and_goal_set_share_rewrites_the_cell(self):
        self.open()
        self.goal()
        for share in ("lots", "0", "-3"):
            with self.subTest(share=share):
                self.hand_edit("goals.tsv", "G1", share=share)
                code, out, err = self.run_raw("goal", "list")
                self.assertEqual((code, err), (0, f"brigade: warning: G1 has share {share!r}, which is not a whole number of 1 or more; "
                                                  "it reads as no limit; run goal set G1 --share <n>\n"))
                self.assertEqual(out.splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")
        self.assertEqual(self.brigade("goal", "set", "G1", "--share", "2"), "G1 share 2")
        code, out, err = self.run_raw("goal", "list")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs, share 2")
    def test_a_goal_share_of_five_thousand_digits_is_read_and_printed(self):
        self.open()
        self.goal()
        self.hand_edit("goals.tsv", "G1", share="7" * 5000)
        code, out, err = self.run_raw("goal", "list")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.splitlines()[0].endswith(", share " + "7" * 5000))
    def test_goal_add_and_goal_set_in_a_store_with_a_generation_need_the_owner_and_goal_list_does_not(self):
        self.open()
        self.brigade("set", "--thread", "t1")
        refusal = "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status"
        self.assertEqual(self.goal(owner=False, ok=False), refusal)
        self.assertEqual(self.goal(), "G1")
        before = self.store_files()
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "paused", ok=False, owner=False), refusal)
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("goal", "list", owner=False).splitlines()[0],
                         "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")
    def test_goal_list_prints_for_each_goal_the_counts_a_hand_join_of_the_three_tables_gives(self):
        self.worked_store()
        self.assertEqual(self.brigade("goal", "list").splitlines(), [
            # 2 waiting. T4 and T9 wait under G1. 1 in flight. D2 is in review under G1. 1 merged. D1.
            # 3 send-backs. Two on D1 and one on D2. The D2 row with member yes does not count.
            "G1 open activity view: 2 waiting, 1 in flight, 1 merged, 3 send-backs, share 2",
            "  purpose: One page of activity",
            "  done when: The page ships",
            # 0 waiting. T3 and T6 are under G2 and assigned. 1 in flight. D3 is sent back under G2. 0 merged.
            # 1 send-back. One on D3. Its pass-with-edits row does not count. 1 riding along. T3, assigned to D2, a G1 item.
            "G2 paused current with upstream: 0 waiting, 1 in flight, 0 merged, 1 send-back, 1 riding along",
            "  purpose: One page of activity",
            "  done when: The page ships",
            # 2 waiting. T5 and T8. 1 in flight. D4. 1 merged. D5. 1 send-back. One on D5.
            "unassigned: 2 waiting, 1 in flight, 1 merged, 1 send-back",
        ])
    def test_status_prints_the_waiting_and_in_flight_counts_by_kind_and_by_goal_a_hand_join_gives_after_its_workers_line(self):
        self.worked_store()
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread not recorded",
            "reporting: milestones, no landing contract, waiting tickets: 4, in progress: 1, in review: 1, sent back: 1, merged: 2",
            "workers: 2 of 9 running, 7 idle, auto-start: normal",
            "liveness: every 600000 ms (default for milestones)",
            # feature. T9 waits. fix. T4 waits and D2 is in flight. polish. T5 waits, and T3 is assigned so it is in no entry.
            # upkeep. D3 is in flight. study. D4 is in flight.
            "kinds: feature 1 waiting; fix 1 waiting, 1 in flight; polish 1 waiting; upkeep 1 in flight; study 1 in flight",
            # G1. T4 and T9 wait and D2 is in flight. G2. D3 is in flight. unassigned. T5 and T8 wait and D4 is in flight.
            "goals: G1 2 waiting, 1 in flight; G2 paused, 1 in flight; unassigned 2 waiting, 1 in flight",
            # T8 waits with no kind. The waiting counts by kind and this one sum to the 4 waiting tickets.
            "untagged: 1",
        ])
    def test_status_prints_no_kinds_line_no_goals_line_and_no_untagged_line_when_no_ticket_waits_and_no_item_is_in_flight(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "set", "T1", "--state", "dropped")
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread not recorded", "reporting: milestones, no landing contract",
            "workers: 0 of 9 running, 9 idle, auto-start: normal",
            "liveness: every 600000 ms (default for milestones)"])
    def test_status_prints_the_state_of_a_done_goal_that_has_a_waiting_ticket(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.hand_edit("dishes.tsv", "D1", state="merged")
        self.hand_edit("rail.tsv", "T1", state="done")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "merged")
        self.follow_ups_in("## Follow-ups\n\n- Correctness: Tidy the guide.\n")
        self.assertEqual(self.brigade("status").splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: polish 1 waiting", "goals: G1 done, 1 waiting"])
    def test_status_prints_the_unassigned_entry_alone_when_the_store_has_a_goal_and_no_work_under_it(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.assertEqual(self.brigade("status").splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: fix 1 waiting", "goals: unassigned 1 waiting"])
    def test_fire_of_a_ticket_whose_goal_cell_is_a_padded_id_warns_starts_an_unassigned_item_and_leaves_the_cell_as_it_was(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("rail.tsv", "T1", goal=" G1 ")
        names = f"brigade: warning: T1 names goal ' G1 ', {self.NO_GOAL_ROW}; "
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"),
                         (0, "D1\n", names + "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.raw_cells("dishes.tsv", "D1", "kind", "goal"), (b"fix", b""))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "state", "kind", "goal"), (b"assigned", b"fix", b" G1 "))
        code, out, err = self.run_raw("status")
        self.assertEqual((code, err), (0, names + f"{self.NOT_WHILE} T1 is assigned; correct it in rail.tsv by hand\n"))
        self.assertEqual(out.splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: fix 1 in flight", "goals: unassigned 1 in flight"])
    def test_a_rewrite_of_its_table_leaves_each_kept_cell_byte_for_byte_and_the_warning_for_it_still_prints(self):
        self.two_goals()
        for summary in ("a", "b", "c", "d"):
            self.brigade("ticket", "add", "--summary", summary, "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "Do c")
        self.brigade("fire", "--tickets", "T4", "--station", "bug-fix", "--summary", "Do d")
        # Per table, the row whose cell is padded, and a command that rewrites the table to change another row.
        rewrites = {"rail.tsv": ("T1", lambda cell: ("ticket", "set", "T2", "--paths", f"docs/{cell}")),
                    "dishes.tsv": ("D1", lambda cell: ("dish", "D2", "--thread", f"thread-{cell}")),
                    "goals.tsv": ("G1", lambda cell: ("goal", "set", "G2", "--name", f"renamed {cell}"))}
        kept = runpy.run_path(str(SCRIPT))["KEPT_CELLS"]
        self.assertEqual(sorted(kept), sorted(rewrites))
        for table, cells in kept.items():
            ident, rewrite = rewrites[table]
            for cell in cells:
                with self.subTest(table=table, cell=cell):
                    legal, = self.raw_cells(table, ident, cell)
                    self.assertTrue(legal)
                    padded = b" " + legal + b" "
                    self.hand_edit(table, ident, **{cell: padded.decode()})
                    held = padded.decode() if cell == "id" else ident
                    warned = self.run_raw("status")
                    self.assertEqual(warned[0], 0)
                    self.assertIn(repr(padded.decode()), warned[2])
                    before = (self.at / table).read_bytes()
                    self.assertEqual(self.run_raw(*rewrite(cell))[0], 0)
                    self.assertNotEqual((self.at / table).read_bytes(), before)
                    self.assertEqual(self.raw_cells(table, held, cell), (padded,))
                    self.assertEqual(self.run_raw("status"), warned)
                    self.hand_edit(table, held, **{cell: legal.decode()})
                    self.assertEqual(self.run_raw("status")[2], "")
    def test_a_goal_cell_that_ends_in_a_carriage_return_still_reads_as_unassigned_after_another_ticket_is_rewritten(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix")
        rail = self.at / "rail.tsv"
        self.assertEqual(rail.read_bytes().count(b"\tG1\n"), 1)
        rail.write_bytes(rail.read_bytes().replace(b"\tG1\n", b"\tG1\r\n"))
        warning = (f"brigade: warning: T1 names goal 'G1\\r', {self.NO_GOAL_ROW}; "
                   "run ticket set T1 --goal G<n> or --no-goal\n")
        unassigned = ["liveness: every 600000 ms (default for milestones)", "kinds: fix 2 waiting", "goals: unassigned 2 waiting"]
        code, out, err = self.run_raw("status")
        self.assertEqual((code, err, out.splitlines()[3:]), (0, warning, unassigned))
        self.assertEqual(self.run_raw("ticket", "set", "T2", "--priority", "low"), (0, "T2 priority low\n", ""))
        self.assertEqual(self.raw_cells("rail.tsv", "T2", "priority"), (b"low",))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "kind", "goal"), (b"fix", b"G1\r"))
        code, out, err = self.run_raw("status")
        self.assertEqual((code, err, out.splitlines()[3:]), (0, warning, unassigned))
    def test_status_on_a_store_from_before_kinds_prints_its_three_lines_and_then_the_count_of_waiting_tickets_as_untagged(self):
        self.store_from_before_kinds()
        before = self.store_files()
        self.assertEqual(self.run_raw("status"), (0, "\n".join([
            "thread not recorded", f"reporting: milestones, lands by local, {self.OLD_COUNTS}", self.OLD_WORKERS,
            "liveness: every 600000 ms (default for milestones)", "untagged: 5"]) + "\n", ""))
        self.assertEqual(self.store_files(), before)
    def test_goal_list_on_a_store_from_before_kinds_counts_each_ticket_and_item_as_unassigned_and_writes_nothing(self):
        self.store_from_before_kinds()
        before = self.store_files()
        # Waiting. T8 to T12. In flight. D1 to D6. Merged. D7. Send-backs. The one row on D4.
        self.assertEqual(self.brigade("goal", "list"),
                         "no open or paused goals\nunassigned: 5 waiting, 6 in flight, 1 merged, 1 send-back")
        self.assertEqual(self.store_files(), before)
    def test_status_ticket_list_startable_watch_86_list_close_and_walk_print_for_an_empty_store_from_before_kinds_what_the_tool_printed_before_kinds_and_change_no_table(self):
        self.store_from_before_kinds(rows=False)
        before = self.tables()
        idle = "workers: 0 of 2 running, 2 idle, auto-start: normal"
        self.assertEqual(self.run_raw("status"), (0, f"thread not recorded\nreporting: milestones, lands by local\n{idle}\nliveness: every 600000 ms (default for milestones)\n", ""))
        self.assertEqual(self.brigade("ticket", "list"), "")
        self.assertEqual(self.brigade("startable"), f"{idle}\nno waiting tickets")
        self.assertEqual(self.brigade("watch"), f"{idle}\nno work in progress")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")
        self.assertEqual(self.close_run("--dry-run"),
                         (0, "# Perf report\n\nSince opening. nothing on record.\n\nNothing new.\n\n## Spend\n\nnone\n\n## Follow-up share\n\n- no ticket was created in the last 14 days.", ""))
        self.assertEqual(self.brigade("walk").splitlines(), [
            f"{_shown_root(self.project)}: local mode onto refs/landing/lane. empty. author policy: any",
            "  Perf (reports milestones): nothing on record",
            "    thread not recorded"])
        self.assertEqual(self.tables(), before)
    def test_next_old_store(self):
        self.store_from_before_kinds()
        self.assertFalse((self.at / "goals.tsv").exists())
        code, out, err = self.run_raw("next")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("wake: unspecified\n"))
        lines = out.splitlines()
        self.assertEqual([line for line in lines if line.startswith("reply:")], [lines[-1]])
        self.assertFalse((self.at / "goals.tsv").exists())
        path = self.at / "dishes.tsv"
        rows = path.read_text().splitlines()
        fields = rows[1].split("\t")
        self.assertEqual((fields[0], len(fields)), ("D1", 15))
        rows[1] = "\t".join(fields + ["nope"])
        path.write_text("\n".join(rows) + "\n")
        code, out, err = self.run_raw("next")
        status = self.run_raw("status")
        self.assertEqual(code, 0)
        self.assertEqual(err, status[2])
        self.assertIn("D1 has kind 'nope'", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual([line for line in out.splitlines() if line.startswith("reply:")], [out.splitlines()[-1]])
        self.assertFalse((self.at / "goals.tsv").exists())
