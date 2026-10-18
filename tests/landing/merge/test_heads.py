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
    def test_merge_mode_pauses_without_merging_a_head_that_replaced_the_candidate_after_a_base_branch_was_modified_refusal_then_lands_the_restored_candidate(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-base-moved").write_text("1")
            (self.base / "pr-9-head-replaced-on-refusal").write_text(rogue)
            pause = self.head_changed_pause(rogue, candidate)
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), rogue)
            self.assertEqual((self.base / "pr-9-checks").read_text(), f"passed {rogue}")
            self.assertEqual(self.origin_log(), ["init"])
            self.assertFalse(self.ref_exists("main:rogue.txt", self.base / "origin.git"))
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash")] + [self.pinned(9, "--squash")] * 2)
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                        f"PR head changed to {rogue[:12]} outside the queue")
            self.assertEqual(self.land("status"),
                             f"merge mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1. Paused: {pause} author policy: any")
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            sh("git", "push", "-q", "--force", "origin", f"{candidate}:refs/heads/landing/e1", cwd=self.work)
            self.post_check(9, "passed")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertFalse(self.ref_exists("main:rogue.txt", self.base / "origin.git"))

    def test_merge_mode_pauses_without_merging_a_head_that_replaced_the_candidate_between_the_check_read_and_the_merge_request(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-head-replaced-after-check-read").write_text(rogue)
            self.assertEqual(self.land("land"), f"queue paused: {self.head_changed_pause(rogue, candidate)}")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), rogue)
            self.assertEqual(self.origin_log(), ["init"])
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash"), self.pinned(9, "--squash")])

    def test_merge_mode_pins_its_auto_merge_request_to_the_candidate_and_pauses_when_another_head_replaced_it_after_the_check_read(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-head-replaced-after-check-read").write_text(rogue)
            self.assertEqual(self.land("land"), f"queue paused: {self.head_changed_pause(rogue, candidate)}")
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash"), self.pinned(9, "--squash")])
            self.assertNotIn("merge requested by the queue", self.land("status", "E1"))
            self.assertEqual(self.origin_log(), ["init"])

    def test_merge_mode_after_a_refused_merge_names_the_refusal_and_not_a_changed_head_when_the_state_read_fails(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-head-replaced-after-check-read").write_text(rogue)
            self.fail_state_reads_after_the_first()
            self.assertEqual(
                self.land("land"),
                "queue paused: GitHub refused to merge https://github.com/o/r/pull/9: "
                "GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest). "
                "Fix it, then run land.py resume")
            self.assertEqual(self.origin_log(), ["init"])

    def test_merge_mode_asks_again_after_one_base_branch_was_modified_refusal_and_lands_both_entries_in_that_run(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            (self.base / "pr-10-base-moved").write_text("1")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
                self.pinned(10, "--auto", "--squash"),
                self.pinned(10, "--squash"),
                self.pinned(10, "--squash"),
            ])
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. landed: 2. author policy: any")

    def test_merge_mode_waits_without_a_pause_after_four_base_branch_was_modified_refusals_in_one_run_and_lands_in_the_next(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            (self.base / "pr-9-base-moved").write_text("4")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash")]
                             + [self.pinned(9, "--squash")] * 4)
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1. author policy: any")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_under_rebase_opens_the_second_entry_in_the_run_that_lands_the_first(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="rebase")
            self.queue_entries(2)
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9\n"
                "still queued: E2")
            self.assertEqual(self.created_heads(), ["landing/e1"])
            self.age_absence(1)
            self.assertEqual(
                self.land("land"),
                "landed E1 (r/D1)\n"
                "opened PRs that merge when their checks pass: E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.created_heads(), ["landing/e1", "landing/e2"])
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.origin_files("landing/e2"), ["one", "two", "lib/x.py"])

    def test_merge_mode_keeps_an_entry_queued_while_an_unmerged_entry_ahead_holds_its_change_then_settles_it_as_landed(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="squash")
            self.assertEqual(self.queue_one(text="same\n", name="w1"), "E1")
            self.assertEqual(self.queue_one(text="same\n", name="w2"), "E2")
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9\n"
                "still queued: E2")
            self.assertEqual(self.land("status", "E2"), "E2 queued (r/D1, w2)")
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt", "L2 submitted r/D1: a.txt"])
            self.post_check(9, "passed")
            self.land("land")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            merged = self.on_origin("rev-parse", "main")
            self.assertEqual(self.land("status", "E2"), f"E2 landed (r/D1, w2). landed as {merged[:12]}. already in trunk")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertEqual(self.created_heads(), ["landing/e1"])

    def test_human_mode_builds_each_candidate_alone_on_trunk_and_lands_the_second_entry_while_the_first_awaits_its_merge(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_entries(2)
            self.assertEqual(
                self.land("land"),
                "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9, E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.origin_files("landing/e1"), ["one", "b.txt", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "main"))
            self.gh("pr", "merge", "https://github.com/o/r/pull/10", "--squash")
            self.assertEqual(self.land("land"), "landed E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "init"])
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9")
