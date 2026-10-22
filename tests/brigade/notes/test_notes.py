from ..support import setUpModule
from ..support import BrigadeTest


class NotesTest(BrigadeTest):
    def test_marked_correctness_follow_up_becomes_waiting_ticket_with_item_goal_and_kind(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Correctness: Tidy the guide.\n"),
                         "T2 added from perf/reports/D1.md#1: Correctness: Tidy the guide.")
        self.assertEqual(self.label("rail.tsv", "T2"), ("polish", "G1"))

    def test_second_marked_follow_up_files_no_ticket_and_prints_one_ticket_per_item(self):
        self.fired_bug_fix()
        out = self.follow_ups_in("## Follow-ups\n\n- Correctness: First.\n- Safety: Second.\n")
        self.assertEqual(out.splitlines(), [
            "T2 added from perf/reports/D1.md#1: Correctness: First.",
            "not filed, D1.md takes one ticket per item: Safety: Second.",
            "noted perf/reports/D1.md#2, no tracked path: Safety: Second.",
        ])
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), [
            "T2 waiting low polish [report] Correctness: First. perf/reports/D1.md#1",
        ])

    def test_unmarked_follow_up_writes_notes_row_and_files_no_ticket(self):
        self.tracked_project("a.py")
        self.fired_bug_fix()
        out = self.follow_ups_in("## Follow-ups\n\n- Fix `a.py`.\n")
        self.assertEqual(out, "noted a.py from perf/reports/D1.md#1: Fix `a.py`.")
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "")
        rows = (self.at / "notes.tsv").read_text().splitlines()
        self.assertEqual(rows[0], "path\titem\tat\ttext")
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1].startswith("a.py\tD1\t"))

    def test_second_run_on_same_report_adds_no_ledger_row_and_prints_already_noted(self):
        self.tracked_project("a.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Fix `a.py`.\n")
        before = (self.at / "notes.tsv").read_bytes()
        out = self.follow_ups_in("## Follow-ups\n\n- Fix `a.py`.\n")
        self.assertEqual(out, "already noted a.py from perf/reports/D1.md#1: Fix `a.py`.")
        self.assertEqual((self.at / "notes.tsv").read_bytes(), before)

    def test_dry_run_prints_would_add_and_would_note_and_changes_no_store_file(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "## Follow-ups\n\n- Correctness: Fix it.\n- Tidy docs.\n")
        before = self.store_files()
        out = self.brigade("ticket", "add", "--from-report", "reports/D1.md", "--dry-run")
        self.assertEqual(out.splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: Fix it.",
            "would note perf/reports/D1.md#2, no tracked path: Tidy docs.",
        ])
        self.assertEqual(self.store_files(), before)

    def test_dry_run_prints_already_noted_for_a_row_the_ledger_holds_and_changes_no_store_file(self):
        self.tracked_project("a.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Fix `a.py`.\n")
        before = self.store_files()
        out = self.brigade("ticket", "add", "--from-report", "reports/D1.md", "--dry-run")
        self.assertEqual(out, "already noted a.py from perf/reports/D1.md#1: Fix `a.py`.")
        self.assertEqual(self.store_files(), before)

    def test_ticket_notes_paths_prints_matching_notes_and_missing_ledger_prints_no_notes(self):
        self.tracked_project("a.py", "b.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Fix `a.py`.\n- Fix `b.py`.\n")
        out = self.brigade("ticket", "notes", "--paths", "a.py")
        self.assertIn("a.py: D1 ", out)
        self.assertNotIn("b.py: D1 ", out)
        self.assertEqual(self.brigade("ticket", "notes", "--paths", "missing.py"), "no notes")
        (self.at / "notes.tsv").unlink()
        self.assertEqual(self.brigade("ticket", "notes", "--all"), "no notes")

    def test_close_prints_follow_up_share_with_number_for_window(self):
        self.open()
        self.brigade("set", "--intake", "report")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "old user")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "old report", "--source", "report")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "new user")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "new report", "--source", "report")
        self.refile("T1", days=15)
        self.refile("T2", days=15)
        code, out, _ = self.close_run("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("- 1 of 2 tickets created in the last 14 days came from a report (50%).", out)
