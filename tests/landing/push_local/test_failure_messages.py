if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    land,
    os,
    sh,
    unittest,
    mock,
)


class LandingTest(LandingTestBase):

    def log_git_calls(self):
        """Put a git in front of PATH that appends each call's arguments to git-calls, then runs the real git."""
        wrapper = self.base / "log-bin"
        wrapper.mkdir()
        real = sh("sh", "-c", "command -v git", cwd=self.base)
        log = self.base / "git-calls"
        (wrapper / "git").write_text(f'#!/bin/sh\necho "$*" >> {log}\nexec {real} "$@"\n')
        (wrapper / "git").chmod(0o755)
        patch = mock.patch.dict(os.environ, {"PATH": f"{wrapper}:{os.environ['PATH']}"})
        patch.start()
        self.addCleanup(patch.stop)
        return log

    def calls(self, log, word):
        return [line for line in (log.read_text().splitlines() if log.exists() else []) if word in line.split()]

    def reject_pushes(self, text):
        hook = self.base / "origin.git/hooks/pre-receive"
        hook.write_text(f"#!/bin/sh\necho '{text}' >&2\nexit 1\n")
        hook.chmod(0o755)

    def test_a_rejected_push_pause_names_both_ways_out_without_doubled_prefix_or_period(self):
        self.init()
        self.reject_pushes("remote: error: GH006: Protected branch update failed for refs/heads/main.")
        self.queue_one()
        paused = self.land("land")
        self.assertIn("queue paused: push rejected", paused)
        self.assertIn("GH006: Protected branch update failed for refs/heads/main", paused)
        self.assertNotIn("remote: remote:", paused)
        self.assertNotIn("..", paused)
        self.assertIn("land.py resume", paused)
        self.assertIn("land.py mode human", paused)

    def test_git_reason_drops_userinfo_from_a_url(self):
        reason = land.git_reason("fatal: unable to access 'https://user:secret@example.com/o/r.git/': The requested URL returned error: 401")
        self.assertNotIn("secret", reason)
        self.assertNotIn("user:", reason)

    def test_a_local_mode_bounce_prints_no_remote_delete_warning_and_runs_no_remote_delete(self):
        self.init(mode="local", base=sh("git", "rev-parse", "HEAD", cwd=self.work))
        self.queue_one(text="BROKEN\n")
        sh("git", "remote", "remove", "origin", cwd=self.work)
        log = self.log_git_calls()
        bounced = self.land("land")
        self.assertIn("bounced E1", bounced)
        self.assertNotIn("could not delete", bounced)
        self.assertEqual(self.calls(log, "--delete"), [])

    def test_a_push_mode_bounce_of_an_entry_that_never_pushed_a_landing_branch_runs_no_remote_delete(self):
        self.init()
        self.queue_one(text="BROKEN\n")
        log = self.log_git_calls()
        self.assertIn("bounced E1", self.land("land"))
        self.assertEqual(self.calls(log, "--delete"), [])

    def test_prune_in_local_mode_exits_zero_and_lists_no_remote_branches(self):
        self.init(mode="local", base=sh("git", "rev-parse", "HEAD", cwd=self.work))
        sh("git", "remote", "remove", "origin", cwd=self.work)
        log = self.log_git_calls()
        self.assertEqual(self.land("prune"), "")
        self.assertEqual(self.calls(log, "ls-remote"), [])

    def test_a_push_init_against_an_empty_remote_says_to_push_a_first_commit(self):
        sh("git", "init", "-q", "--bare", "-b", "main", "empty.git", cwd=self.base)
        sh("git", "remote", "set-url", "origin", str(self.base / "empty.git"), cwd=self.work)
        refused = self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", ok=False)
        self.assertEqual(refused, "land: origin has no main branch; push a first commit to origin main and run land.py init again")
        self.assertNotIn("couldn't find remote ref", refused)


if __name__ == "__main__":
    unittest.main()
