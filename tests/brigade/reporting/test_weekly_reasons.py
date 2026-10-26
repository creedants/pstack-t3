if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import sys

from ..support import setUpModule
from ..support import BrigadeTest, CLAUDE, CODEX, Path, datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "t3/added/brigade/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))
from brigade_lib._reviews.rounds import send_back_section


TITLE = "## Send-back reasons (last 7 days)"


class Store:
    """The one attribute send_back_section reads from a store: its directory."""

    def __init__(self, directory):
        self.dir = directory


class WeeklyReasonsTest(BrigadeTest):
    def report_store(self):
        return Store(self.at)

    def pass_rows(self):
        """pass.tsv's rows as dicts, padded to the header's length."""
        header, *lines = (self.at / "pass.tsv").read_text().splitlines()
        keys = header.split("\t")
        return [dict(zip(keys, line.split("\t") + [""] * len(keys))) for line in lines]

    def append_pass(self, at, verdict="send-back", reason="", member="", report=""):
        """One pass.tsv row with these cells, keeping the table's own header."""
        path = self.at / "pass.tsv"
        header = path.read_text().splitlines()[0].split("\t")
        row = dict.fromkeys(header, "")
        row.update(at=at, dish="D1", sha="abc", verdict=verdict, author=CLAUDE, verifier=CODEX,
                   member=member, reason=reason, report=report)
        with path.open("a") as handle:
            handle.write("\t".join(row[key] for key in header) + "\n")

    def days_ago(self, days):
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="microseconds")

    def section_body(self, text):
        lines = text.splitlines()
        return lines[lines.index(TITLE) + 2]

    def test_the_section_counts_a_week_of_non_member_send_backs_by_reason(self):
        self.fired_bug_fix()
        self.append_pass(self.days_ago(1), reason="missing-test")
        self.append_pass(self.days_ago(2), reason="scope")
        self.append_pass(self.days_ago(3))
        self.append_pass(self.days_ago(8), reason="other")
        self.append_pass(self.days_ago(1), reason="other", member="yes")
        self.assertIn(f"{TITLE}\n\nmissing-test 1, scope 1, unrecorded 1\n\n", self.brigade("close", "--dry-run"))

    def test_a_week_with_no_send_backs_says_so(self):
        self.fired_bug_fix()
        self.assertIn(f"{TITLE}\n\nNo send-backs in the last 7 days.\n\n", self.brigade("close", "--dry-run"))

    def test_close_to_file_writes_the_same_section_as_dry_run(self):
        self.fired_bug_fix()
        self.append_pass(self.days_ago(1), reason="missing-test")
        dry = self.brigade("close", "--dry-run")
        written = self.brigade("close", "--to-file")
        text = Path(written).read_text()
        self.assertIn("## Send-back reasons (over the 24 hours this store has records for)\n\nmissing-test 1\n\n", text)
        self.assertEqual(text.strip(), dry)

    def test_the_section_and_pass_stats_count_reasons_the_same_way(self):
        self.fired_bug_fix()
        self.append_pass(self.days_ago(1), reason="missing-test")
        self.append_pass(self.days_ago(2), reason="scope")
        self.append_pass(self.days_ago(3), reason="mystery")
        self.append_pass(self.days_ago(4))
        self.append_pass(self.days_ago(5), reason="other", member="yes")
        dry = self.brigade("close", "--dry-run")
        lines = dry.splitlines()
        heading = next(line for line in lines if line.startswith("## Send-back reasons ("))
        self.assertEqual(heading, "## Send-back reasons (over the 5 days this store has records for)")
        summary = lines[lines.index(heading) + 2]
        self.assertEqual(summary, "missing-test 1, scope 1, unrecorded 2")
        self.assertIn(f"send-backs: {summary}", self.brigade("pass", "stats"))

    def test_a_report_class_prints_beside_the_recorded_reason(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\nReason: missing-test\n")
        self.review_file("D1-review-2.md", "Findings.\n\nReason: other\n")
        self.append_pass(self.days_ago(1), reason="missing-test", report="D1-review-1.md")
        self.append_pass(self.days_ago(2), reason="scope", report="D1-review-2.md")
        self.append_pass(self.days_ago(3), reason="other")
        report_store = self.report_store()
        rows = self.pass_rows()
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        self.assertEqual(send_back_section(rows, now, report_store),
                         ["## Send-back reasons (over the 3 days this store has records for)",
                          "",
                          "missing-test 1, scope 1, other 1",
                          "",
                          "report says: missing-test 1, other 1, not in report 1; "
                          "D1 recorded scope, report says other",
                          ""])

    def test_a_section_with_no_classed_report_matches_the_base_shape(self):
        self.fired_bug_fix()
        self.append_pass(self.days_ago(1), reason="scope")
        report_store = self.report_store()
        rows = self.pass_rows()
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        section = ["## Send-back reasons (over the 24 hours this store has records for)", "", "scope 1", ""]
        self.assertEqual(send_back_section(rows, now, report_store), section)
        self.assertIn("\n".join(section), self.brigade("close", "--dry-run"))

    def test_the_closeout_prints_the_report_class_beside_the_recorded_reason(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\nReason: wrong-behavior\n")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--reason", "scope",
                     "--author", CLAUDE, "--verifier", CODEX, "--report", "D1-review-1.md")
        self.assertIn("report says: wrong-behavior 1; D1 recorded scope, report says wrong-behavior",
                      self.brigade("close", "--dry-run"))

    def test_excluded_rows_count_no_report_class(self):
        self.fired_bug_fix()
        self.review_file("D1-review-7.md", "Findings.\n\nReason: scope\n")
        self.review_file("D1-review-8.md", "Findings.\n\nReason: scope\n")
        self.review_file("D1-review-9.md", "Findings.\n\nReason: scope\n")
        self.append_pass(self.days_ago(1), reason="scope")
        self.append_pass(self.days_ago(1), reason="scope", member="yes", report="D1-review-9.md")
        self.append_pass(self.days_ago(8), reason="scope", report="D1-review-8.md")
        self.append_pass(self.days_ago(1), verdict="pass", report="D1-review-7.md")
        report_store = self.report_store()
        rows = self.pass_rows()
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        section = ["## Send-back reasons (last 7 days)", "", "scope 1", ""]
        self.assertEqual(send_back_section(rows, now, report_store), section)

    def test_an_empty_recorded_reason_disagrees_by_name(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\nReason: scope\n")
        self.append_pass(self.days_ago(1), report="D1-review-1.md")
        report_store = self.report_store()
        rows = self.pass_rows()
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        self.assertEqual(send_back_section(rows, now, report_store),
                         ["## Send-back reasons (over the 24 hours this store has records for)",
                          "",
                          "unrecorded 1",
                          "",
                          "report says: scope 1; D1 recorded unrecorded, report says scope",
                          ""])

    def test_pass_stats_prints_the_section_derived_line(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\nReason: scope\n")
        self.review_file("D1-review-2.md", "Findings.\n\nReason: other\n")
        self.append_pass(self.days_ago(1), reason="scope", report="D1-review-1.md")
        self.append_pass(self.days_ago(2), reason="scope", report="D1-review-2.md")
        report_store = self.report_store()
        rows = self.pass_rows()
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        derived = next(line for line in send_back_section(rows, now, report_store)
                       if line.startswith("report says:"))
        self.assertIn(derived, self.brigade("pass", "stats"))
