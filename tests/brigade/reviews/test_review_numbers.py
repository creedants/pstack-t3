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


class ReviewNumbersTest(BrigadeTest):
    def appended(self, table, values):
        path = self.at / table
        header = path.read_text().splitlines()[0].split("\t")
        row = dict.fromkeys(header, "")
        row.update(values)
        with path.open("a") as handle:
            handle.write("\t".join(row[key] for key in header) + "\n")

    def ago(self, delta):
        return (datetime.now(timezone.utc) - delta).isoformat(timespec="microseconds")

    def append_pass(self, at, verdict="send-back", sha="abc", verifier=CODEX, member=""):
        self.appended("pass.tsv", dict(at=at, dish="D1", sha=sha, verdict=verdict, author=CLAUDE,
                                       verifier=verifier, member=member))

    def seat(self, text, verifier):
        lines = [line.split() for line in text.splitlines()]
        return next((cells for cells in lines if cells and cells[0] == verifier), None)

    def test_a_later_commit_alone_does_not_uphold_a_send_back(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=2)), verdict="send-back", sha="abc")
        self.append_pass(self.ago(timedelta(hours=1)), verdict="send-back", sha="def")
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "2", "0", "0", "2", "0", "0", "n/a"])

    def test_a_pass_at_the_same_sha_still_overrules_and_the_rate_falls_below_100(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=4)), verdict="send-back", sha="abc")
        self.append_pass(self.ago(timedelta(hours=3)), verdict="send-back", sha="def")
        self.append_pass(self.ago(timedelta(hours=2)), verdict="send-back", sha="ghi")
        self.append_pass(self.ago(timedelta(hours=1)), verdict="pass", sha="ghi", verifier=CLAUDE)
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "3", "0", "0", "3", "0", "1", "0.0%"])

    def test_a_pass_at_another_sha_upholds_the_send_back(self):
        self.fired_bug_fix()
        self.append_pass(self.ago(timedelta(hours=2)), verdict="send-back", sha="abc")
        self.append_pass(self.ago(timedelta(hours=1)), verdict="pass", sha="def", verifier=CLAUDE)
        self.assertEqual(self.seat(self.brigade("pass", "hits"), CODEX),
                         [CODEX, "1", "0", "0", "1", "1", "0", "100.0%"])

    def test_a_young_store_prints_the_span_its_records_cover(self):
        self.fired_bug_fix()
        self.refile("T1", hours=39)
        self.append_pass(self.ago(timedelta(hours=39)), verdict="send-back", sha="abc")
        out = self.brigade("close", "--dry-run")
        self.assertIn("## Send-back reasons (over the 39 hours this store has records for)", out)
        self.assertIn("- 0 of 1 tickets created over the 39 hours this store has records for came from a report (0%).", out)

    def test_a_store_older_than_both_windows_prints_todays_wording(self):
        self.fired_bug_fix()
        self.refile("T1", days=20)
        self.append_pass(self.ago(timedelta(days=20)), verdict="send-back", sha="abc")
        out = self.brigade("close", "--dry-run")
        self.assertIn("## Send-back reasons (last 7 days)", out)
        self.assertIn("No send-backs in the last 7 days.", out)
        self.assertIn("- no ticket was created in the last 14 days.", out)
