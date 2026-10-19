from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    json,
    land,
    os,
    ProcessReason,
    real_popen,
    real_process,
    SCRIPT,
    sh,
    subprocess,
    sys,
    time,
)
import types
from unittest import mock
from .base import MergeBase

class LandingTest(MergeBase, LandingTestBase):
    def test_merge_mode_builds_each_queued_entry_on_the_candidate_of_the_entry_ahead(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="squash")
            self.queue_entries(3)
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9, "
                "E2 (r/D2) https://github.com/o/r/pull/10, E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.origin_files("landing/e1"), ["one", "b.txt", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e2"), ["one", "two", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e3"), ["one", "two", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "landing/e1"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "landing/e2"))
            self.assertEqual(self.origin_log(), ["init"])
            self.assertFalse((self.base / "merge-calls").exists())

    def test_merge_mode_lands_three_stacked_entries_in_order_in_the_run_after_their_checks_pass(self):
        with self.fake_gh():
            self.open_line(3)
            for number in (9, 10, 11):
                self.post_check(number, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2), E3 (r/D3)")
            self.assertEqual(self.origin_log(), ["w3", "w2", "w1", "init"])
            self.assertEqual([self.on_origin("show", "--format=", "--name-only", commit) for commit in ("main~2", "main~1", "main")],
                             ["a.txt", "b.txt", "lib/x.py"])
            self.assertEqual(self.origin_files("main"), ["one", "two", "three"])
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
                self.pinned(10, "--auto", "--squash"),
                self.pinned(10, "--squash"),
                self.pinned(11, "--auto", "--squash"),
                self.pinned(11, "--squash"),
            ])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_merges_nothing_while_the_first_entry_is_pending_notes_the_entry_ahead_on_the_passed_ones_then_lands_all_three(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "pending")
            self.post_check(10, "passed")
            self.post_check(11, "passed")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["init"])
            self.assertEqual(self.land("status", "E1"),
                             "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. waiting for required checks before merging")
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for E1 to merge first")
            self.assertEqual(self.land("status", "E3"),
                             "E3 awaiting-merge (r/D3, w3). https://github.com/o/r/pull/11. waiting for E2 to merge first")
            self.post_check(9, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2), E3 (r/D3)")
            self.assertEqual(self.origin_log(), ["w3", "w2", "w1", "init"])

    def test_merge_mode_keeps_a_failed_entry_behind_a_pending_first_and_bounces_it_in_the_run_that_lands_the_first(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "pending")
            self.post_check(10, "failed")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for E1 to merge first")
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt", "L2 submitted r/D2: b.txt"])
            self.post_check(9, "passed")
            self.assertEqual(self.land("land"),
                             "landed E1 (r/D1)\n"
                             "bounced E2 (r/D2): required checks failed on https://github.com/o/r/pull/10: test (3.12)")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
            ])

    def test_merge_mode_with_no_posted_checks_opens_two_entries_and_lands_both_in_the_second_run(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="squash")
            self.queue_entries(2)
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9, "
                "E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["init"])
            self.age_absence(1, 2)
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])

    def test_merge_mode_lands_both_entries_in_the_run_after_one_killed_right_after_the_first_merge(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            (self.base / "crash-after-merge").write_text("")
            self.killed_land()
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_bounces_a_failed_first_entry_alone_rebuilds_the_two_behind_it_without_its_change_then_lands_both(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "failed")
            self.post_check(10, "passed")
            self.post_check(11, "passed")
            self.assert_absent(1, 2, 3)
            self.assertEqual(
                self.land("land"),
                "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)\n"
                "rebuilt E2 (r/D2) https://github.com/o/r/pull/10\n"
                "rebuilt E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e3"), ["a.txt", "two", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "landing/e2"))
            self.assertEqual(self.listed(), ["L1 active r/D1: a.txt", "L2 submitted r/D2: b.txt", "L3 submitted r/D3: lib/x.py"])
            self.assert_absent(2, 3)
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for required checks before merging")
            self.post_check(10, "passed")
            self.post_check(11, "passed")
            self.assertEqual(self.land("land"), "landed E2 (r/D2), E3 (r/D3)")
            self.assertEqual(self.origin_log(), ["w3", "w2", "init"])
            self.assertEqual(self.origin_files("main"), ["a.txt", "two", "three"])

    def test_merge_mode_lands_the_first_entry_bounces_a_failed_second_and_rebuilds_the_third_without_the_second(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "passed")
            self.post_check(10, "failed")
            self.post_check(11, "passed")
            self.assertEqual(
                self.land("land"),
                "landed E1 (r/D1)\n"
                "bounced E2 (r/D2): required checks failed on https://github.com/o/r/pull/10: test (3.12)\n"
                "rebuilt E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.origin_files("landing/e3"), ["one", "b.txt", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
            ])

    def test_merge_mode_bounces_an_entry_whose_rebuild_conflicts_with_trunk_and_rebuilds_the_entry_behind_it(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "passed")
            self.post_check(10, "pending")
            self.post_check(11, "pending")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.commit_on_origin("outside", path="b.txt")
            self.assertEqual(
                self.land("land"),
                "bounced E2 (r/D2): conflict with trunk\n"
                "rebuilt E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.origin_files("landing/e3"), ["one", "outside", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.listed(), ["L2 active r/D2: b.txt", "L3 submitted r/D3: lib/x.py"])
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertEqual(self.origin_log(), ["outside", "w1", "init"])

    def test_merge_mode_merges_nothing_and_rebuilds_both_passed_entries_when_trunk_gained_an_outside_commit_then_lands_both(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            self.commit_on_origin("outside")
            self.assertEqual(
                self.land("land"),
                "rebuilt E1 (r/D1) https://github.com/o/r/pull/9\n"
                "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["outside", "init"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "landing/e1"))
            self.assertEqual(self.on_origin("show", "landing/e2:merged.txt"), "outside")
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "outside", "init"])

    def test_merge_mode_rebuilds_an_entry_again_after_a_run_killed_between_the_rebuild_push_and_its_row(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "failed")
            before = self.entry_fields(2, "candidate", "onto")
            with self.land_dies_after_its_push_to("landing/e2"):
                self.killed_land()
            self.assertEqual(self.land("status", "E1"),
                             "E1 bounced (r/D1, w1). https://github.com/o/r/pull/9. "
                             "required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.entry_fields(2, "candidate", "onto"), before)
            self.assertEqual(self.land("land"), "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.entry_fields(2, "candidate", "onto"),
                             (self.on_origin("rev-parse", "landing/e2"), self.on_origin("rev-parse", "main")))
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for required checks before merging")

    def test_a_store_from_before_the_onto_column_keeps_its_first_entry_rebuilds_the_second_behind_it_then_lands_both(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "pending")
            first = self.on_origin("rev-parse", "landing/e1")
            alone = sh("git", "rev-parse", "w2", cwd=self.work)
            sh("git", "push", "-q", "--force", "origin", f"{alone}:refs/heads/landing/e2", cwd=self.work)
            self.entry_table_without_onto({2: alone})
            self.assertNotIn("onto", self.entry_columns())
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            log = self.record_pushes()
            try:
                self.assertEqual(self.land("land"), "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            finally:
                self.stop_recording_pushes()
            self.assertIn("onto", self.entry_columns())
            self.assertEqual([line.rsplit(":", 1)[1] for line in log.read_text().splitlines()], ["refs/heads/landing/e2"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), first)
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), first)
            self.assertEqual(self.origin_files("landing/e2"), ["one", "two", "lib/x.py"])
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])
