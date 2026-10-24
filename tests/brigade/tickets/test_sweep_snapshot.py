import hashlib
from datetime import datetime, timedelta

from ..support import BrigadeTest, SCRIPT, _owner_words, call_in_process, run_globals


class SweepSnapshotMixin:
    """The tests here pin the sweep digest over each listed ticket's id, kind, goal, summary, and filed time."""

    NOW = "2026-02-01T00:00:00+00:00"

    def fixed_clock(self):
        """Patch the loaded entry's clock to NOW. A separate process does not see the patch."""
        run_globals(self, SCRIPT)["now"] = lambda: self.NOW

    def sweep_digest(self, output):
        return output.splitlines()[-1].rsplit(" ", 1)[-1]

    def old_untagged(self):
        """File a waiting ticket filed 40 days ago with no kind and no goal."""
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "old work")
        self.hand_edit("rail.tsv", "T1", kind="", goal="")
        self.refile("T1", days=40)

    def sweep_in_process(self, *args, ok=True):
        """Run ticket sweep in this process, where the fixed clock holds."""
        outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                 *_owner_words(self.at), "ticket", "sweep", *args])
        self.assertEqual(outcome.code == 0, ok, outcome.stdout_text() + outcome.stderr_text())
        return (outcome.stdout_text() if ok else outcome.stderr_text()).strip()

    def refused_after(self, change):
        """List, change one listed cell, then assert the earlier digest refuses the drop and the store is unchanged.

        Returns the digest the first list printed and the digest the second list printed.
        """
        self.open()
        self.goal()
        self.old_untagged()
        approved = self.sweep_digest(self.brigade("ticket", "sweep", "--older-than", "30"))
        change()
        listed = self.brigade("ticket", "sweep", "--older-than", "30")
        self.assertTrue(listed.splitlines()[0].startswith("T1 40d"), listed)
        fresh = self.sweep_digest(listed)
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--drop", "--confirm", approved, ok=False),
                         f"brigade: digest {approved!r} does not match {fresh!r}; nothing changed")
        self.assertEqual(self.store_files(), before)
        return approved, fresh

    def test_sweep_excludes_a_ticket_filed_exactly_n_days_ago(self):
        self.fixed_clock()
        self.open()
        self.old_untagged()
        self.hand_edit("rail.tsv", "T1", at="2026-01-02T00:00:00+00:00")
        self.assertEqual(self.sweep_in_process("--older-than", "30"), "nothing to sweep")

    def test_sweep_lists_a_ticket_filed_one_microsecond_more_than_n_days_ago(self):
        self.fixed_clock()
        self.open()
        self.old_untagged()
        filed = "2026-01-01T23:59:59.999999+00:00"
        self.hand_edit("rail.tsv", "T1", at=filed)
        digest = hashlib.sha256("\t".join(("T1", "", "", "old work", filed)).encode()).hexdigest()[:32]
        self.assertEqual(self.sweep_in_process("--older-than", "30"),
                         f"T1 30d: old work\n1 ticket; digest {digest}")

    def test_sweep_refuses_a_digest_after_a_listed_tickets_goal_changes(self):
        approved, fresh = self.refused_after(lambda: self.brigade("ticket", "set", "T1", "--goal", "G1"))
        self.assertNotEqual(approved, fresh)

    def test_sweep_refuses_a_digest_after_a_listed_tickets_kind_changes(self):
        approved, fresh = self.refused_after(lambda: self.brigade("ticket", "set", "T1", "--kind", "fix"))
        self.assertNotEqual(approved, fresh)

    def test_sweep_refuses_a_digest_after_a_listed_tickets_summary_changes(self):
        approved, fresh = self.refused_after(lambda: self.hand_edit("rail.tsv", "T1", summary="changed"))
        self.assertNotEqual(approved, fresh)

    def test_sweep_refuses_a_digest_after_a_listed_tickets_filed_time_changes(self):
        def an_hour_earlier():
            stored = self.table_row(self.at, "rail.tsv", "T1")["at"]
            self.hand_edit("rail.tsv", "T1", at=(datetime.fromisoformat(stored) - timedelta(hours=1)).isoformat())

        approved, fresh = self.refused_after(an_hour_earlier)
        self.assertNotEqual(approved, fresh)


class SweepSnapshotTest(BrigadeTest, SweepSnapshotMixin):
    pass
