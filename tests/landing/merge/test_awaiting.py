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
    def test_merge_mode_lands_an_entry_and_pauses_when_its_merge_commit_holds_another_tree_than_its_candidate_and_resume_clears_it(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "outside-commit-before-merge").write_text("")
            out = self.land("land")
            merged = self.on_origin("rev-parse", "main")
            pause = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                     "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(out, f"landed E1 (r/D1)\nqueue paused: {pause}")
            self.assertEqual(self.origin_log(), ["w1", "outside", "init"])
            self.assertEqual(self.land("status", "E1"), f"E1 landed (r/D1, w1). landed as {merged[:12]}. https://github.com/o/r/pull/9")
            self.assertEqual(self.land("status"), f"merge mode onto refs/remotes/origin/main. landed: 1. Paused: {pause} author policy: any")
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. landed: 1. author policy: any")

    def test_merge_mode_pauses_and_leaves_the_entry_awaiting_merge_when_git_cannot_read_its_merge_commit_then_reports_the_other_tree_after_resume(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "outside-commit-before-merge").write_text("")
            with self.git_fails_for_the_merge_commit_of_pr_9("arg.startswith(merged)"):
                out = self.land("land")
                merged = self.on_origin("rev-parse", "main")
                pause = self.unread_commit_pause(merged)
                self.assertEqual(out, f"queue paused: {pause}")
                self.assertEqual(self.origin_log(), ["w1", "outside", "init"])
                self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                            "merge requested by the queue")
                self.assertEqual(self.land("status"),
                                 f"merge mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1. Paused: {pause} author policy: any")
                self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)
                self.assertEqual(self.land("resume"), "queue resumed")
                self.assertEqual(self.land("land"), f"queue paused: {pause}")
                self.assertEqual(self.land("resume"), "queue resumed")
            other_tree = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                          "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(self.land("land"), f"landed E1 (r/D1)\nqueue paused: {other_tree}")

    def test_merge_mode_does_not_report_another_tree_when_the_tree_of_an_equal_merge_commit_cannot_be_read_and_lands_it_after_resume(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            checked_tree = self.on_origin("rev-parse", "landing/e1^{tree}")
            with self.git_fails_for_the_merge_commit_of_pr_9("arg == merged + '^{tree}'"):
                out = self.land("land")
                merged = self.on_origin("rev-parse", "main")
                self.assertEqual(self.on_origin("rev-parse", "main^{tree}"), checked_tree)
                self.assertEqual(out, f"queue paused: {self.unread_commit_pause(merged)}")
                self.assertNotIn("whose tree is not the checked candidate", out)
                self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
                self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt"])
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. landed: 1. author policy: any")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_pauses_when_the_remote_does_not_serve_the_reported_merge_commit(self):
        with self.fake_gh():
            self.open_line(1)
            candidate = self.on_origin("rev-parse", "landing/e1")
            unknown = "1" * 40
            (self.base / "pr-state").write_text(f"MERGED {candidate} {unknown}")
            self.assertEqual(self.land("land"), f"queue paused: {self.unread_commit_pause(unknown)}")
            self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)

    def test_merge_mode_pauses_when_the_state_read_of_a_merged_pr_names_no_merge_commit(self):
        self.pauses_on_a_merged_pr_whose_state_read_reports_the_merge_commit_as("")

    def test_merge_mode_pauses_when_the_state_read_of_a_merged_pr_names_an_abbreviated_merge_commit(self):
        self.pauses_on_a_merged_pr_whose_state_read_reports_the_merge_commit_as("abc123")

    def test_merge_mode_does_not_say_trunk_holds_an_unchecked_change_when_a_merge_with_another_tree_is_not_on_trunk_then_says_it_once_trunk_holds_it(self):
        with self.fake_gh():
            candidate, merged = self.pauses_on_a_merge_of_the_first_of_two_entries_that_trunk_was_rewound_past(outside=True)
            other_tree = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                          "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(self.land("land"), f"landed E1 (r/D1)\nqueue paused: {other_tree}")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            self.post_check(10, "passed")
            self.assertEqual(self.land("land"), "landed E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "outside", "init"])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_leaves_an_entry_awaiting_merge_when_its_merge_with_the_candidate_tree_is_not_on_trunk_then_lands_the_line_once_trunk_holds_it(self):
        with self.fake_gh():
            self.pauses_on_a_merge_of_the_first_of_two_entries_that_trunk_was_rewound_past(outside=False)
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_fetches_a_merge_commit_that_trunk_lost_during_the_run_and_pauses_with_the_entry_awaiting_merge(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            initial, candidate = self.on_origin("rev-parse", "main"), self.on_origin("rev-parse", "landing/e1")
            (self.base / "trunk-rewound-after-merge").write_text("")
            out = self.land("land")
            self.assertEqual(self.origin_log(), ["init"])
            merged = json.loads((self.base / "prs.json").read_text())["9"]["merge"]
            self.assertEqual(out, f"queue paused: {self.off_trunk_pause(merged, initial)}")
            self.assertEqual(sh("git", "log", "-1", "--format=%s", merged, cwd=self.work), "w1")
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                        "merge requested by the queue")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)

    def test_merge_mode_says_the_merge_could_not_be_checked_when_git_cannot_tell_whether_trunk_holds_the_merge_commit(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "outside-commit-before-merge").write_text("")
            with self.git_fails_for_the_merge_commit_of_pr_9("arg == '--is-ancestor'", code=128):
                out = self.land("land")
                merged = self.on_origin("rev-parse", "main")
                self.assertEqual(
                    out,
                    "queue paused: The merge of E1 could not be checked. "
                    f"Git could not tell whether trunk at {merged} holds merge commit {merged} "
                    "(git merge-base --is-ancestor exited 128), "
                    "so the tree of the merge commit was not compared with the tree of the candidate. "
                    "E1 stays awaiting merge. Check that git can read both commits here, "
                    "then run land.py resume and land.py land")
                self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                            "merge requested by the queue")
                self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)
            self.assertEqual(self.land("resume"), "queue resumed")
            other_tree = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                          "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(self.land("land"), f"landed E1 (r/D1)\nqueue paused: {other_tree}")

    def test_merge_mode_says_it_could_not_check_trunk_when_git_cannot_read_the_last_landed_commit(self):
        with self.fake_gh():
            self.open_line(1)
            candidate = self.on_origin("rev-parse", "landing/e1")
            other, unknown = "0123456789abcdef0123456789abcdef01234567", "2" * 40
            (self.base / "pr-state").write_text(f"MERGED {other} {unknown}")
            self.assertEqual(self.land("land"), f"bounced E1 (r/D1): merged at {unknown[:12]} with head {other[:12]}, "
                                                f"not the checked {candidate[:12]}; review what reached trunk")
            (self.base / "pr-state").unlink()
            self.assertEqual(
                self.land("land"),
                f"queue paused: could not check that trunk contains the last landed commit {unknown[:12]} "
                "(git merge-base --is-ancestor exited 128); check that git can read that commit here, then run land.py resume")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "nothing to land")

    def test_merge_mode_pauses_when_the_pr_state_read_fails(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            (self.base / "state-query-fails").write_text("")
            self.assertEqual(
                self.land("land"),
                "queue paused: could not read the state of https://github.com/o/r/pull/9. gh said API unavailable. "
                "Fix it, then run land.py resume")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["init"])
            (self.base / "state-query-fails").unlink()
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_does_not_bounce_a_merge_requested_entry_with_a_failed_check_when_the_second_state_read_fails(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            (self.base / "checks").write_text("failed")
            self.fail_state_reads_after_the_first()
            self.assertEqual(
                self.land("land"),
                "queue paused: could not read the state of https://github.com/o/r/pull/9. gh said API unavailable. "
                "Fix it, then run land.py resume")
            self.assertIn("E1 awaiting-merge", self.land("status", "E1"))
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt"])

    def test_human_mode_leaves_the_entry_and_does_not_pause_when_the_pr_state_read_fails(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            (self.base / "state-query-fails").write_text("")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.land("status"), "human mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1. author policy: any")
