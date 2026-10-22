from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    REVIEWER,
    land,
    os,
    sh,
    shutil,
    sys,
    unittest,
)
from .base import QueueBase

REASON = "required checks failed on https://github.com/o/r/pull/9: test (3.12)"


class LandingTest(QueueBase, LandingTestBase):

    def gh_calls(self):
        path = self.base / "gh-calls"
        return path.read_text().splitlines() if path.exists() else []

    def record_actions(self):
        """Write close to an actions file when the queue closes the PR and delete when it deletes the branch."""
        actions = self.base / "actions"
        real_gh = os.environ["LAND_GH"]
        gh = self.base / "gh-order"
        gh.write_text(
            f"""#!{sys.executable}
import os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] == ["pr", "close"]:
    with Path({str(actions)!r}).open("a") as out:
        out.write("close\\n")
os.execv({real_gh!r}, [{real_gh!r}, *args])
""")
        gh.chmod(0o755)
        os.environ["LAND_GH"] = str(gh)
        real_git = shutil.which("git")
        (self.base / "order").mkdir()
        git = self.base / "order" / "git"
        git.write_text(
            f"""#!{sys.executable}
import os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:1] == ["push"] and "--delete" in args:
    with Path({str(actions)!r}).open("a") as out:
        out.write("delete\\n")
os.execv({real_git!r}, [{real_git!r}, *args])
""")
        git.chmod(0o755)
        os.environ["PATH"] = str(self.base / "order") + os.pathsep + os.environ["PATH"]
        return actions

    def test_a_failed_check_bounce_closes_the_pr_then_deletes_the_branch(self):
        with self.fake_gh():
            actions = self.record_actions()
            (self.base / "checks").write_text("failed")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), f"bounced E1 (r/D1): {REASON}")
        self.assertEqual(actions.read_text().splitlines(), ["close", "delete"])
        self.assertIn(f"pr close https://github.com/o/r/pull/9 --comment {REASON}", self.gh_calls())
        self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
        self.assertIn("E1 bounced", self.land("status", "E1"))

    def test_a_closed_pr_bounce_deletes_the_branch_and_issues_no_close(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "pr-state").write_text(f"CLOSED {candidate} \n")
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): PR closed without merging")
        self.assertEqual([line for line in self.gh_calls() if line.startswith("pr close ")], [])
        self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_a_failed_cleanup_warns_and_leaves_the_entry_bounced(self):
        with self.fake_gh():
            sh("git", "config", "receive.denyDeletes", "true", cwd=self.base / "origin.git")
            (self.base / "checks").write_text("failed")
            self.init(mode="merge")
            self.queue_one()
            reported = self.land("land")
        self.assertIn(f"bounced E1 (r/D1): {REASON}", reported)
        self.assertIn("warning: could not delete landing/e1", reported)
        self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
        self.assertIn("E1 bounced", self.land("status", "E1"))


if __name__ == "__main__":
    unittest.main()
