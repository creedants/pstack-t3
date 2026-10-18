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
    def test_merge_mode_lands_in_the_run_whose_plain_merge_succeeds(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "merge-state-lags").write_text("")
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertNotIn("landed", opened)
            self.age_absence(1)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())

    def test_merge_mode_lands_in_the_run_whose_auto_merge_completes(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            self.land("land")
            (self.base / "auto-merges-immediately").write_text("")
            (self.base / "merge-state-lags").write_text("")
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            calls = (self.base / "merge-calls").read_text().splitlines()
            self.assertEqual(calls[-1], self.pinned(9, "--auto", "--squash"))

    def test_merge_mode_reports_a_merge_that_lands_after_the_opening_state_read(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"OPEN {candidate} ")
            self.on_origin("update-ref", "refs/heads/main", candidate)
            (self.base / "pr-state-after").write_text(f"MERGED {candidate} {candidate}")
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_pauses_for_review_without_calling_auto_merge(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text(
                '{"reviewDecision":"REVIEW_REQUIRED","statusCheckRollup":'
                '[{"name":"test (3.12)","status":"COMPLETED","conclusion":"SUCCESS"}]}')
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused", out)
            self.assertIn("https://github.com/o/r/pull/9", out)
            self.assertIn("approving review", out)
            self.assertNotIn("landed", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))

    def test_merge_mode_pauses_when_changes_are_requested_without_calling_auto_merge(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text(
                '{"reviewDecision":"CHANGES_REQUESTED","statusCheckRollup":[]}')
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused", out)
            self.assertIn("https://github.com/o/r/pull/9", out)
            self.assertIn("changes requested", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())

    def test_merge_mode_disables_auto_merge_when_it_bounces_an_open_pr(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            (self.base / "checks").write_text("failed")
            out = self.land("land")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", out)
            self.assertIn("pr merge https://github.com/o/r/pull/9 --disable-auto", (self.base / "merge-calls").read_text())
            self.assertNotIn("pr comment", (self.base / "gh-calls").read_text())
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_pauses_when_it_cannot_disable_auto_merge_on_a_failed_pr(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            (self.base / "disable-auto-fails").write_text("API unavailable")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.land("land")
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "queue paused: required checks failed on https://github.com/o/r/pull/9: test (3.12). "
                                                "Auto-merge is still enabled: API unavailable. Turn auto-merge off on https://github.com/o/r/pull/9, "
                                                "then run land.py resume and land.py land")
            self.assertTrue(self.land("status", "E1").startswith("E1 awaiting-merge"))
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            (self.base / "auto-merge-request.json").unlink()
            self.land("resume")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", self.land("land"))
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_skips_disable_auto_when_auto_merge_request_is_null(self):
        with self.fake_gh():
            (self.base / "checks").write_text("failed")
            (self.base / "auto-merge-request.json").write_text("null")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--disable-auto", calls)
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertNotIn("auto-merge still enabled", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_pauses_on_a_disable_auto_failure_when_auto_merge_request_is_set(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.arm_auto_merge()
            (self.base / "disable-auto-fails").write_text("GraphQL: Auto merge is not enabled for this pull request")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.age_absence(1)
            self.land("land")
            (self.base / "checks").write_text("failed")
            out = self.land("land")
            self.assertIn("queue paused: required checks failed on https://github.com/o/r/pull/9: test (3.12). "
                          "Auto-merge is still enabled: GraphQL: Auto merge is not enabled for this pull request", out)
            self.assertTrue(self.land("status", "E1").startswith("E1 awaiting-merge"))
            self.assertIn("pr merge https://github.com/o/r/pull/9 --disable-auto", (self.base / "merge-calls").read_text())
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())

    def test_merge_mode_pauses_when_github_requires_a_human_approval(self):
        with self.fake_gh():
            (self.base / "merge-refused").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused: https://github.com/o/r/pull/9 needs an approving review", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())
