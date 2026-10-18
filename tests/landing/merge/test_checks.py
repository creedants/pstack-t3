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
    def test_merge_mode_reports_a_pr_opened_for_an_entry_left_without_one(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            self.init(mode="merge")
            self.queue_one()
            self.leave_awaiting_merge_without_a_pr()
            reported = self.land("land")
            self.assertEqual(
                reported,
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertEqual(self.land("land"), "nothing to land")

    def test_merge_mode_merges_its_own_pr_when_there_are_no_checks_to_wait_for(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(),
                             [self.pinned(9, "--auto", "--merge"), self.pinned(9, "--merge")])
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_lets_github_merge_after_required_checks_and_asks_once(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            self.on_origin("update-ref", "refs/heads/main", candidate)
            (self.base / "pr-state").write_text(f"MERGED {candidate} {candidate}")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(len((self.base / "merge-calls").read_text().splitlines()), 1)

    def test_merge_mode_waits_for_running_checks_then_merges(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("pr merge https://github.com/o/r/pull/9 --squash", calls)
            self.assertEqual(self.land("land"), "nothing to land")
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))

    def test_merge_mode_bounces_a_pr_whose_required_checks_failed(self):
        with self.fake_gh():
            (self.base / "checks").write_text("failed")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))

    def test_merge_mode_does_not_merge_when_auto_would_succeed_with_a_pending_check(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            (self.base / "auto-merges-immediately").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse((self.base / "merge-calls").exists())
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_bounces_when_a_check_fails_after_auto_merge_was_enabled(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_does_not_merge_when_the_check_query_fails(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            (self.base / "checks-query-fails").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertEqual(
                out,
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9\n"
                "queue paused: could not read checks for https://github.com/o/r/pull/9: API unavailable. "
                "Fix it, then run land.py resume")
            self.assertIn("could not read checks", out)
            self.assertIn("queue paused", out)
            self.assertNotIn("landed E1", out)
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse((self.base / "merge-calls").exists())

    def test_merge_mode_does_not_merge_a_just_opened_pr_with_zero_posted_checks(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertNotIn("landed", opened)
            self.assertNotIn("queue paused", opened)
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertNotIn("Paused", self.land("status"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse((self.base / "merge-calls").exists())
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(),
                             [self.pinned(9, "--auto", "--merge"),
                              self.pinned(9, "--merge")])
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_does_not_merge_absent_checks_on_a_later_drain(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertNotIn("landed", opened)
            for _ in range(2):
                self.assertEqual(self.land("land"), "nothing to land")
                self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertFalse((self.base / "merge-calls").exists())

    def test_merge_mode_merges_absent_checks_after_the_grace(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_lands_a_merged_pr_when_a_later_check_fails(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            self.on_origin("update-ref", "refs/heads/main", candidate)
            (self.base / "pr-state").write_text(f"MERGED {candidate} {candidate}")
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertIn("E1 landed", self.land("status", "E1"))

    def test_merge_mode_lands_a_green_rollup_whose_check_name_contains_a_quote(self):
        with self.fake_gh():
            posted = [
                {"name": "b\"q\\\t\n", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": posted}))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertNotIn("queue paused", first)
            self.assertIn("landed E1 (r/D1)", first)
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(store.contract.get("paused"), "")
            self.assertEqual(self.land("land"), "nothing to land")

    def test_merge_mode_does_not_merge_when_a_pending_check_appears_between_reads(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"APPROVED","statusCheckRollup":[]}')
            (self.base / "checks-flip").write_text("pending")
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertNotIn("landed", opened)
            self.assertNotIn("queue paused", opened)
            self.assertFalse((self.base / "merge-calls").exists())
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))

    def test_merge_mode_does_not_merge_when_a_failed_check_appears_between_reads(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"APPROVED","statusCheckRollup":[]}')
            (self.base / "checks-flip").write_text("failed")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertNotIn("landed", opened)
            self.assertEqual((self.base / "merge-calls").read_text().strip(),
                             "pr merge https://github.com/o/r/pull/9 --disable-auto")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", opened)
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_drops_the_absent_marker_when_the_entry_lands(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assert_absent(1)
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertNotIn("1", self.absent_marker())

    def test_merge_mode_drops_the_absent_marker_when_the_entry_bounces(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assert_absent(1)
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("failed")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", self.land("land"))
            self.assertNotIn("1", self.absent_marker())
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))
