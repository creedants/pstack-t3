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


TITLE = "## Send-back reasons (last 7 days)"


class WeeklyReasonsTest(BrigadeTest):
    def append_pass(self, at, verdict="send-back", reason="", member=""):
        """One pass.tsv row with these cells, keeping the table's own header."""
        path = self.at / "pass.tsv"
        header = path.read_text().splitlines()[0].split("\t")
        row = dict.fromkeys(header, "")
        row.update(at=at, dish="D1", sha="abc", verdict=verdict, author=CLAUDE, verifier=CODEX,
                   member=member, reason=reason)
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
        self.assertIn(f"{TITLE}\n\nmissing-test 1\n\n", text)
        self.assertEqual(text.strip(), dry)

    def test_the_section_and_pass_stats_count_reasons_the_same_way(self):
        self.fired_bug_fix()
        self.append_pass(self.days_ago(1), reason="missing-test")
        self.append_pass(self.days_ago(2), reason="scope")
        self.append_pass(self.days_ago(3), reason="mystery")
        self.append_pass(self.days_ago(4))
        self.append_pass(self.days_ago(5), reason="other", member="yes")
        summary = self.section_body(self.brigade("close", "--dry-run"))
        self.assertEqual(summary, "missing-test 1, scope 1, unrecorded 2")
        self.assertIn(f"send-backs: {summary}", self.brigade("pass", "stats"))
