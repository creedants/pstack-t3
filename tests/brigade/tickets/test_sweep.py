import hashlib

from ..support import BrigadeTest


class SweepMixin:
    def sweep_digest(self, output):
        return output.splitlines()[-1].rsplit(" ", 1)[-1]

    def old_untagged(self, summary="old work", **ago):
        self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        ident = f"T{len(self.rows_of('rail.tsv'))}"
        self.hand_edit("rail.tsv", ident, kind="", goal="")
        self.refile(ident, **ago)
        return ident

    def test_sweep_lists_an_old_ticket_with_no_kind_or_goal_with_count_and_digest(self):
        self.open()
        self.old_untagged(days=40)
        digest = hashlib.sha256(b"T1").hexdigest()[:8]
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30"),
                         f"T1 40d: old work\n1 ticket; digest {digest}")

    def test_sweep_leaves_a_fully_tagged_old_ticket_unlisted(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--kind", "fix", "--goal", "G1", "--summary", "old work")
        self.refile("T1", days=40)
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30"), "nothing to sweep")

    def test_sweep_leaves_a_young_ticket_unlisted(self):
        self.open()
        self.old_untagged(days=40)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "new work")
        digest = hashlib.sha256(b"T1").hexdigest()[:8]
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30"),
                         f"T1 40d: old work\n1 ticket; digest {digest}")

    def test_sweep_leaves_a_ticket_in_another_state_unlisted(self):
        self.open()
        self.old_untagged(summary="one", days=40)
        self.old_untagged(summary="two", days=40)
        self.old_untagged(summary="three", days=40)
        self.old_untagged(summary="four", days=40)
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.brigade("ticket", "set", "T3", "--state", "dropped")
        self.hand_edit("rail.tsv", "T4", state="moved", dish="to:docs")
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30"), "nothing to sweep")

    def test_sweep_leaves_a_waiting_ticket_that_an_item_in_flight_names_unlisted(self):
        self.open()
        self.old_untagged(days=40)
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one")
        self.hand_edit("rail.tsv", "T1", state="waiting")
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30"), "nothing to sweep")

    def test_sweep_drop_drops_each_listed_ticket_with_a_reason_naming_the_sweep(self):
        self.open()
        self.old_untagged(summary="one", days=40)
        self.old_untagged(summary="two", days=40)
        digest = hashlib.sha256(b"T1\nT2").hexdigest()[:8]
        listed = self.brigade("ticket", "sweep", "--older-than", "30")
        self.assertEqual(listed, f"T1 40d: one\nT2 40d: two\n2 tickets; digest {digest}")
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--drop", "--confirm", digest),
                         "T1 dropped\nT2 dropped")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "dropped")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["state"], "dropped")
        dropped = [row for row in self.log_rows() if row["kind"] == "ticket" and row["state"] == "dropped"]
        self.assertEqual([(row["id"], row["note"]) for row in dropped],
                         [("T1", "sweep --older-than 30"), ("T2", "sweep --older-than 30")])
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30"), "nothing to sweep")
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--drop", "--confirm", digest),
                         "nothing to sweep")

    def test_sweep_goal_gives_a_goal_only_to_tickets_with_none(self):
        self.open()
        self.goal("first")
        self.goal("second")
        self.old_untagged(summary="one", days=40)
        self.brigade("ticket", "add", "--kind", "fix", "--goal", "G1", "--summary", "two")
        self.hand_edit("rail.tsv", "T2", kind="")
        self.refile("T2", days=40)
        digest = hashlib.sha256(b"T1\nT2").hexdigest()[:8]
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--goal", "G2", "--confirm", digest),
                         "T1 goal G2")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["goal"], "G2")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["goal"], "G1")
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--goal", "G2", "--confirm", digest),
                         "nothing to sweep")

    def test_sweep_refuses_a_stale_digest_with_nothing_changed(self):
        self.open()
        self.old_untagged(days=40)
        digest = self.sweep_digest(self.brigade("ticket", "sweep", "--older-than", "30"))
        self.old_untagged(summary="newer", days=35)
        before = self.store_files()
        fresh = hashlib.sha256(b"T1\nT2").hexdigest()[:8]
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--drop", "--confirm", digest, ok=False),
                         f"brigade: digest {digest!r} does not match {fresh!r}; nothing changed")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "waiting")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["state"], "waiting")

    def test_sweep_refuses_confirm_without_action_and_action_without_confirm(self):
        self.open()
        self.old_untagged(days=40)
        digest = self.sweep_digest(self.brigade("ticket", "sweep", "--older-than", "30"))
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--confirm", digest, ok=False),
                         "brigade: --confirm needs --drop or --goal")
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--drop", ok=False),
                         "brigade: --drop or --goal needs --confirm")

    def test_sweep_refuses_an_unknown_goal_with_nothing_changed(self):
        self.open()
        self.old_untagged(days=40)
        digest = self.sweep_digest(self.brigade("ticket", "sweep", "--older-than", "30"))
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--goal", "G9", "--confirm", digest,
                                      ok=False),
                         "brigade: no goal G9; run goal list --all to see every goal")
        self.assertEqual(self.store_files(), before)

    def test_sweep_refuses_an_older_than_below_one(self):
        self.open()
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "0", ok=False),
                         "brigade: --older-than takes a whole number of 1 or more")


class SweepTest(BrigadeTest, SweepMixin):
    pass
