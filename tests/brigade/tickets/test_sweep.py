if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import hashlib
from datetime import datetime, timedelta, timezone

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
        lines = self.brigade("ticket", "sweep", "--older-than", "30").splitlines()
        self.assertEqual(lines[0], "T1 40d: old work")
        self.assertRegex(lines[1], r"^1 ticket; digest [0-9a-f]{32}$")

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
        lines = self.brigade("ticket", "sweep", "--older-than", "30").splitlines()
        self.assertEqual(lines[0], "T1 40d: old work")
        self.assertRegex(lines[1], r"^1 ticket; digest [0-9a-f]{32}$")

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
        listed = self.brigade("ticket", "sweep", "--older-than", "30")
        self.assertEqual(listed.splitlines()[:2], ["T1 40d: one", "T2 40d: two"])
        digest = self.sweep_digest(listed)
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
        listed = self.brigade("ticket", "sweep", "--older-than", "30")
        self.assertEqual(listed.splitlines()[:2], ["T1 40d: one", "T2 40d G1: two"])
        digest = self.sweep_digest(listed)
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
        fresh = self.sweep_digest(self.brigade("ticket", "sweep", "--older-than", "30"))
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--drop", "--confirm", digest, ok=False),
                         f"brigade: digest {digest!r} does not match {fresh!r}; nothing changed")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "waiting")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["state"], "waiting")

    def test_sweep_refuses_a_stale_digest_when_the_id_lists_share_their_first_eight_sha256_hex_digits(self):
        self.open()
        for number in range(1, 25):
            self.old_untagged(summary=f"work {number}", days=40)
        approved = ("T2", "T3", "T4", "T6", "T7", "T8", "T13", "T14", "T16", "T19", "T21", "T22")
        current = ("T2", "T4", "T5", "T7", "T10", "T11", "T15", "T16", "T17", "T19", "T21", "T22")
        approved_ids = hashlib.sha256("\n".join(approved).encode()).hexdigest()
        current_ids = hashlib.sha256("\n".join(current).encode()).hexdigest()
        self.assertEqual(approved_ids[:8], current_ids[:8])
        self.assertNotEqual(approved_ids, current_ids)
        for ident in (f"T{number}" for number in range(1, 25)):
            if ident not in approved:
                self.brigade("ticket", "set", ident, "--state", "assigned")
        listed = self.brigade("ticket", "sweep", "--older-than", "30")
        self.assertEqual([line.split(" ", 1)[0] for line in listed.splitlines()[:-1]], list(approved))
        approved_digest = self.sweep_digest(listed)
        for ident in approved:
            if ident not in current:
                self.brigade("ticket", "set", ident, "--state", "assigned")
        for ident in current:
            if ident not in approved:
                self.brigade("ticket", "set", ident, "--state", "waiting")
        listed = self.brigade("ticket", "sweep", "--older-than", "30")
        self.assertEqual([line.split(" ", 1)[0] for line in listed.splitlines()[:-1]], list(current))
        fresh = self.sweep_digest(listed)
        self.assertNotEqual(approved_digest, fresh)
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--drop", "--confirm", approved_digest,
                                      ok=False),
                         f"brigade: digest {approved_digest!r} does not match {fresh!r}; nothing changed")
        self.assertEqual(self.store_files(), before)

    def test_sweep_refuses_a_duplicate_ticket_id_on_list_with_nothing_changed(self):
        self.open()
        self.old_untagged(summary="one", days=40)
        self.old_untagged(summary="two", days=40)
        self.brigade("ticket", "set", "T1", "--state", "assigned")
        self.hand_edit("rail.tsv", "T2", id="T1")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", ok=False),
                         "brigade: duplicate ticket T1 in rail.tsv; nothing changed")
        self.assertEqual(self.store_files(), before)

    def test_sweep_refuses_a_duplicate_ticket_id_on_apply_with_nothing_changed(self):
        self.open()
        self.old_untagged(summary="one", days=40)
        self.old_untagged(summary="two", days=40)
        self.brigade("ticket", "set", "T1", "--state", "assigned")
        self.hand_edit("rail.tsv", "T2", id="T1")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--drop", "--confirm", "0" * 32,
                                      ok=False),
                         "brigade: duplicate ticket T1 in rail.tsv; nothing changed")
        self.assertEqual(self.store_files(), before)

    def test_sweep_excludes_a_ticket_filed_just_under_n_days_ago(self):
        self.open()
        ident = self.old_untagged(summary="edge", days=40)
        stamp = (datetime.now(timezone.utc) - timedelta(days=30) + timedelta(minutes=1)).isoformat()
        self.hand_edit("rail.tsv", ident, at=stamp)
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30"), "nothing to sweep")

    def test_sweep_lists_a_ticket_filed_just_over_n_days_ago(self):
        self.open()
        ident = self.old_untagged(summary="past", days=30)
        lines = self.brigade("ticket", "sweep", "--older-than", "30").splitlines()
        self.assertEqual(lines[0], f"{ident} 30d: past")
        self.assertRegex(lines[1], r"^1 ticket; digest [0-9a-f]{32}$")

    def test_sweep_refuses_a_malformed_filed_date_with_nothing_changed(self):
        self.open()
        self.old_untagged(days=40)
        self.hand_edit("rail.tsv", "T1", at="not-a-date")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", ok=False),
                         "brigade: bridge-kit/perf/rail.tsv line 2 is malformed; fix or remove it")
        self.assertEqual(self.store_files(), before)

    def test_sweep_refuses_a_missing_filed_date_with_nothing_changed(self):
        self.open()
        self.old_untagged(days=40)
        self.hand_edit("rail.tsv", "T1", at="")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--drop", "--confirm", "0" * 32,
                                      ok=False),
                         "brigade: bridge-kit/perf/rail.tsv line 2 is malformed; fix or remove it")
        self.assertEqual(self.store_files(), before)

    def test_sweep_apply_refuses_a_missing_or_stale_owner_while_list_writes_nothing(self):
        self.open()
        self.old_untagged(days=40)
        digest = self.sweep_digest(self.brigade("ticket", "sweep", "--older-than", "30"))
        self.brigade("set", "--thread", "t1")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", owner=False),
                         f"T1 40d: old work\n1 ticket; digest {digest}")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--drop", "--confirm", digest,
                                      owner=False, ok=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.run_at(self.at, "--owner", "other@1", "ticket", "sweep", "--older-than", "30",
                                     "--drop", "--confirm", digest, owner=False, ok=False),
                         "brigade: owner other@1 is stale; this store is owned by t1@1")
        self.assertEqual(self.store_files(), before)

    def test_sweep_lists_a_ticket_with_a_dangling_goal_and_assigns_over_it(self):
        self.open()
        self.goal("first")
        ident = self.old_untagged(summary="stray", days=40)
        self.hand_edit("rail.tsv", ident, goal="G9")
        listed = self.brigade("ticket", "sweep", "--older-than", "30")
        self.assertEqual(listed.splitlines()[0], f"{ident} 40d: stray")
        digest = self.sweep_digest(listed)
        self.assertEqual(self.brigade("ticket", "sweep", "--older-than", "30", "--goal", "G1", "--confirm", digest),
                         f"{ident} goal G1")
        self.assertEqual(self.table_row(self.at, "rail.tsv", ident)["goal"], "G1")

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
