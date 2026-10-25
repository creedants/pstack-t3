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
from ..support import BrigadeTest, CLAUDE, CODEX, Path, datetime, timedelta, timezone


TITLE = "## Reviewer hit rate (last 7 days)"
EMPTY = "No reviewed rounds in the last 7 days."
CLOSE_LABELS = {"pass-with-edits": "edits", "send-backs": "send-back", "rate": "upheld rate"}


class HitRateTest(BrigadeTest):
    def appended(self, table, values):
        """One row with these cells appended to a table, keeping the table's own header."""
        path = self.at / table
        header = path.read_text().splitlines()[0].split("\t")
        row = dict.fromkeys(header, "")
        row.update(values)
        with path.open("a") as handle:
            handle.write("\t".join(row[key] for key in header) + "\n")

    def ago(self, delta):
        return (datetime.now(timezone.utc) - delta).isoformat(timespec="microseconds")

    def append_pass(self, at, verdict="pass", sha="abc", verifier=CODEX, member="", report=""):
        self.appended("pass.tsv", dict(at=at, dish="D1", sha=sha, verdict=verdict, author=CLAUDE,
                                       verifier=verifier, member=member, report=report))

    def append_edits(self, at, reviewed, edited, report=""):
        self.appended("edits.tsv", {"at": at, "dish": "D1", "from": reviewed, "to": edited, "report": report})

    def append_decision(self, created, answered, answer):
        self.appended("86.tsv", {"id": "Q1", "at": created, "state": "answered", "dish": "D1",
                                 "question": "", "options": "", "default": "", "answer": answer,
                                 "kind": "round-budget", "answered": answered})

    def seat(self, text, verifier):
        """The cells of the line whose first cell is this seat, split on whitespace."""
        lines = [line.split() for line in text.splitlines()]
        return next((cells for cells in lines if cells and cells[0] == verifier), None)

    def close_cells(self, text, verifier):
        """The closeout line's counters for this seat, keyed by the command's column names."""
        line = next(line for line in text.splitlines() if line.startswith(f"{verifier}  "))
        cells = line.split("  ")
        self.assertEqual(cells[0], verifier)
        pairs = dict(cell.split(" ", 1) for cell in cells[1:])
        return {CLOSE_LABELS.get(label, label): value for label, value in pairs.items()}

    def test_a_send_back_followed_by_another_sha_is_upheld(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=1)), verdict="send-back", sha="abc")
        self.append_pass(self.ago(timedelta(minutes=30)), verdict="pass", sha="def", verifier=CLAUDE)
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "1", "0", "0", "1", "1", "0", "100.0%"])

    def test_a_send_back_followed_by_a_pass_at_its_sha_is_overruled(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=1)), verdict="send-back", sha="abc")
        self.append_pass(self.ago(timedelta(minutes=30)), verdict="pass", sha="abc", verifier=CLAUDE)
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "1", "0", "0", "1", "0", "1", "0.0%"])

    def test_a_known_limits_decision_after_a_send_back_overrules_it(self):
        self.fired_bug_fix()
        sent = self.ago(timedelta(hours=2))
        self.append_pass(sent, verdict="send-back", sha="abc")
        self.append_decision(sent, self.ago(timedelta(hours=1)), "known limits")
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "1", "0", "0", "1", "0", "1", "0.0%"])

    def test_a_redesign_decision_after_a_send_back_overrules_nothing(self):
        self.fired_bug_fix()
        sent = self.ago(timedelta(hours=2))
        self.append_pass(sent, verdict="send-back", sha="abc")
        self.append_decision(sent, self.ago(timedelta(hours=1)), "redesign")
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "1", "0", "0", "1", "0", "0", "n/a"])

    def test_a_later_code_change_after_an_accepting_decision_still_counts_as_upheld(self):
        self.fired_bug_fix()
        sent = self.ago(timedelta(hours=3))
        self.append_pass(sent, verdict="send-back", sha="abc")
        self.append_decision(sent, self.ago(timedelta(hours=2)), "known limits")
        self.append_pass(self.ago(timedelta(hours=1)), verdict="pass", sha="def", verifier=CLAUDE)
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "1", "0", "0", "1", "1", "0", "100.0%"])

    def test_a_second_send_back_at_the_same_sha_leaves_the_first_pending(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=2)), verdict="send-back", sha="abc")
        self.append_pass(self.ago(timedelta(hours=1)), verdict="send-back", sha="abc")
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "2", "0", "0", "2", "0", "0", "n/a"])

    def test_a_send_back_with_no_later_round_is_pending(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=1)), verdict="send-back", sha="abc")
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "1", "0", "0", "1", "0", "0", "n/a"])

    def test_a_pass_with_edits_the_table_records_is_upheld(self):
        self.fired_bug_fix()
        reviewed = self.ago(timedelta(hours=2))
        self.append_pass(reviewed, verdict="pass-with-edits", sha="abc")
        self.append_edits(self.ago(timedelta(hours=1)), "abc", "def")
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "1", "0", "1", "0", "1", "0", "100.0%"])

    def test_a_pass_with_edits_the_table_does_not_record_is_neither(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=2)), verdict="pass-with-edits", sha="abc")
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "1", "0", "1", "0", "0", "0", "n/a"])

    def test_edits_verified_for_one_report_do_not_uphold_another_report_at_the_same_sha(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=3)), verdict="pass-with-edits", sha="abc",
                         report="D1-review-1.md")
        self.append_pass(self.ago(timedelta(hours=2)), verdict="pass-with-edits", sha="abc", verifier=CLAUDE,
                         report="D1-review-2.md")
        self.append_edits(self.ago(timedelta(hours=1)), "abc", "def", report="D1-review-1.md")
        out = self.brigade("pass", "hits")
        self.assertEqual(self.seat(out, CODEX), [CODEX, "1", "0", "1", "0", "1", "0", "100.0%"])
        self.assertEqual(self.seat(out, CLAUDE), [CLAUDE, "1", "0", "1", "0", "0", "0", "n/a"])

    def test_a_member_row_counts_for_its_own_seat_only(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=2)), verdict="pass", sha="abc", verifier=CLAUDE)
        self.append_pass(self.ago(timedelta(hours=1)), verdict="send-back", sha="def", member="yes")
        out = self.brigade("pass", "hits")
        self.assertEqual(self.seat(out, CODEX), [CODEX, "1", "0", "0", "1", "0", "0", "n/a"])
        self.assertEqual(self.seat(out, CLAUDE), [CLAUDE, "1", "1", "0", "0", "0", "0", "n/a"])

    def test_a_late_row_counts_for_its_own_seat_only(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=2)), verdict="pass", sha="def", verifier=CLAUDE)
        self.append_pass(self.ago(timedelta(hours=1)), verdict="send-back", sha="abc")
        out = self.brigade("pass", "hits")
        self.assertEqual(self.seat(out, CODEX), [CODEX, "1", "0", "0", "1", "0", "0", "n/a"])
        self.assertEqual(self.seat(out, CLAUDE), [CLAUDE, "1", "1", "0", "0", "0", "0", "n/a"])

    def test_the_window_counts_a_row_one_minute_inside_and_drops_one_minute_outside(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(days=1, minutes=-1)), verdict="pass", sha="abc")
        self.append_pass(self.ago(timedelta(days=1, minutes=1)), verdict="pass", sha="def", verifier=CLAUDE)
        out = self.brigade("pass", "hits", "--last", "1")
        self.assertEqual(self.seat(out, CODEX), [CODEX, "1", "1", "0", "0", "0", "0", "n/a"])
        self.assertIsNone(self.seat(out, CLAUDE))

    def test_an_empty_window_names_itself_in_the_command_and_the_section(self):
        self.fired_bug_fix()
        self.assertEqual(self.brigade("pass", "hits"), "no reviewed rounds in the last 7 days")
        self.assertIn(f"{TITLE}\n\n{EMPTY}\n\n", self.brigade("close", "--dry-run"))

    def test_the_close_section_counts_the_same_as_the_command(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=2)), verdict="send-back", sha="abc")
        self.append_pass(self.ago(timedelta(hours=1)), verdict="pass", sha="def", verifier=CLAUDE)
        printed = self.brigade("pass", "hits")
        dry = self.brigade("close", "--dry-run")
        written = Path(self.brigade("close", "--to-file")).read_text()
        self.assertIn("## Reviewer hit rate (over the 2 hours this store has records for)", dry)
        for verifier in (CODEX, CLAUDE):
            expected = dict(zip(("rounds", "pass", "edits", "send-back", "upheld", "overruled", "upheld rate"),
                                self.seat(printed, verifier)[1:]))
            self.assertEqual(self.close_cells(dry, verifier), expected)
            self.assertEqual(self.close_cells(written, verifier), expected)

    def test_the_command_writes_nothing_and_leaves_no_lock(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=1)), verdict="send-back", sha="abc")
        (self.at / "restaurant.lock").unlink()
        before = self.store_files()
        self.brigade("pass", "hits")
        self.assertEqual(self.store_files(), before)
        self.assertFalse((self.at / "restaurant.lock").exists())
