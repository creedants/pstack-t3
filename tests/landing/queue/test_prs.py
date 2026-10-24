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
    ast,
    json,
    land,
    mock,
    os,
    ProcessReason,
    real_process,
    REVIEWER,
    SCRIPT,
    sh,
    sys,
)
from .base import QueueBase

class LandingTest(QueueBase, LandingTestBase):
    def test_a_legacy_push_without_a_pr_opens_the_pr_on_landing_e(self):
        with self.fake_gh():
            self.require_pr_head_on_origin()
            previous = self.use_land_script(self.base_land_script())
            try:
                self.init(mode="human")
                self.queue_one()
                (self.base / "pr-create-fails").write_text("gh: not authenticated\n")
                paused = self.land("land")
                remote = self.base / "origin.git"
                candidate = sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote)
                self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))
                (self.base / "pr-create-fails").unlink()
                self.restore_land_script(previous)
                previous = None
                held = self.land("status", "E1")
                self.assertEqual(self.land("resume"), "queue resumed")
                opened = self.land("land")
            finally:
                if previous is not None:
                    self.restore_land_script(previous)
            self.assertIn("queue paused", paused)
            self.assertIn("awaiting-merge", held)
            self.assertNotIn("pull/9", held)
            self.assertNotIn("queue paused", opened)
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote), opened)
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote), candidate)
            status = self.land("status", "E1")
            self.assertIn("awaiting-merge", status)
            self.assertIn("https://github.com/o/r/pull/9", status)
            self.assertNotIn("Paused", self.land("status"))

    def test_a_closed_pr_on_landing_q_is_ignored(self):
        self.ignore_non_open_pr_on_landing_q("CLOSED")

    def test_a_merged_pr_on_landing_q_is_ignored(self):
        self.ignore_non_open_pr_on_landing_q("MERGED")

    def test_an_older_queues_open_pr_on_landing_q_stays_open_by_design_and_a_new_pr_on_landing_e_lands(self):
        """Intended. The queue never reads, adopts, or deletes landing/q<n>. The old branch and PR are closed by hand."""
        with self.fake_gh():
            (self.base / "pr-seq").write_text("9\n")
            candidate = self.crashed_landing_q_pr()
            remote = self.base / "origin.git"
            opened = self.land("land")
            self.assertEqual(opened, "opened PRs for E1 (r/D1) https://github.com/o/r/pull/10")
            self.assertEqual(self.created_heads(), ["landing/q1", "landing/e1"])
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote), candidate)
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote), candidate)
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.pr_views_of_landing_q(), [])
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote), candidate)
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertIn("landed as abc123", self.land("status", "E1"))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_a_merged_pr_stored_on_landing_q_lands_when_the_remote_is_not_github(self):
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=False)
            remote = self.base / "origin.git"
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote), candidate)
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertIn("landed as abc123", self.land("status", "E1"))
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertEqual(self.created_heads(), ["landing/q1"])

    def test_a_merged_pr_stored_on_landing_q_lands_when_the_remote_is_github(self):
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=True)
            remote = self.base / "origin.git"
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote), candidate)
            self.assertIn("landed as abc123", self.land("status", "E1"))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_a_merged_pr_stored_on_landing_q_waits_when_the_forge_says_a_hidden_landing_e_exists(self):
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=True)
            remote = self.hide_a_landing_e1_the_older_queue_never_made(candidate)
            deletion = self.git_raw("push", "--no-follow-tags", "origin", "--delete", "landing/e1", cwd=self.work)
            self.assertIn("remote ref does not exist", deletion.stderr)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_accepted_limit_a_hidden_landing_e_is_left_in_place_and_the_entry_settles_when_the_remote_is_not_github(self):
        """Intended. With no forge to ask, a hidden branch looks missing, and waiting would never end."""
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=False)
            remote = self.hide_a_landing_e1_the_older_queue_never_made(candidate)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertNotIn("branch-status", (self.base / "gh-calls").read_text())

    def test_a_pr_stored_on_landing_q_that_merged_at_another_head_bounces_when_the_remote_is_not_github(self):
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=False)
            other = "0123456789abcdef0123456789abcdef01234567"
            (self.base / "pr-state").write_text(f"MERGED {other} abc123")
            settled = self.land("land")
            self.assertTrue(settled.startswith("bounced E1 (r/D1): merged at abc123 with head 0123456789ab"), settled)
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=self.base / "origin.git"), candidate)
            self.assertIn("review what reached trunk", self.land("status", "E1"))

    def test_a_pr_stored_on_landing_q_that_closed_bounces_when_the_remote_is_not_github(self):
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=False)
            (self.base / "pr-state").write_text(f"CLOSED {candidate}")
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): PR closed without merging")
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=self.base / "origin.git"), candidate)
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_a_merged_pr_on_landing_e_waits_when_git_says_it_is_absent_and_the_remote_is_not_github(self):
        """The PR head is landing/e1, so git's absent line alone does not settle the entry."""
        with self.fake_gh():
            self.init(mode="human")
            sh("git", "remote", "set-url", "origin", str(self.base / "origin.git"), cwd=self.work)
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9")
            remote = self.base / "origin.git"
            candidate = sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote)
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            sh("git", "update-ref", "-d", "refs/heads/landing/e1", cwd=remote)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))

    def test_an_older_queues_open_pr_on_landing_q_stays_open_by_design_and_a_closed_pr_on_landing_e_bounces(self):
        """Intended. The old branch and its PR stay when the new PR on landing/e<n> bounces."""
        with self.fake_gh():
            (self.base / "pr-seq").write_text("9\n")
            candidate = self.crashed_landing_q_pr()
            remote = self.base / "origin.git"
            self.assertEqual(self.land("land"), "opened PRs for E1 (r/D1) https://github.com/o/r/pull/10")
            (self.base / "pr-state").write_text(f"CLOSED {candidate}")
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): PR closed without merging")
            self.assertEqual(self.created_heads(), ["landing/q1", "landing/e1"])
            self.assertEqual(self.pr_views_of_landing_q(), [])
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote), candidate)
            self.assertIn("PR closed without merging", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_a_merged_pr_on_landing_e_at_another_head_is_not_adopted(self):
        """A fresh contract opens its own PR when landing/e1 is an earlier contract's merged PR."""
        with self.fake_gh():
            self.record_created_pr_branch()
            self.require_pr_head_on_origin()
            self.init(mode="human")
            other = "0123456789abcdef0123456789abcdef01234567"
            (self.base / "pr-url").write_text("https://github.com/o/r/pull/9\n")
            (self.base / "pr-head").write_text("landing/e1")
            (self.base / "pr-seq").write_text("10\n")
            (self.base / "entry-pr-state").write_text(f"MERGED {other}\n")
            self.queue_one()
            reported = self.land("land")
        self.assertEqual(self.created_heads(), ["landing/e1"], reported)
        self.assertNotIn("pull/9", reported)
        status = self.land("status", "E1")
        self.assertIn("awaiting-merge", status)
        self.assertIn("https://github.com/o/r/pull/10", status)
        self.assertNotIn("https://github.com/o/r/pull/9", status)
        remote = self.base / "origin.git"
        self.assertNotEqual(sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote), other)

    def test_human_mode_opens_a_pr_and_marks_landed_when_it_merges(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9")
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            self.assertTrue(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            (self.base / "pr-state").write_text(f"OPEN {candidate} ")
            self.assertEqual(self.land("land"), "nothing to land")
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_human_mode_uses_the_submitted_pr_title_and_body(self):
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.init(mode="human")
            sha = self.worker("w1", {"a.txt": "agent\n"})
            self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            body = self.base / "body.md"
            body.write_text("Cuts load time by 300 ms.\n")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER,
                      "--title", "Faster load", "--body-file", str(body))
            self.land("land")
        create = [line for line in log.read_text().splitlines() if "'create'" in line][0]
        self.assertIn("'--title', 'Faster load'", create)
        self.assertIn("'--body', 'Cuts load time by 300 ms.\\n'", create)
        self.assertNotIn("Queued by", create)
        self.assertNotIn("Reviewed by", create)

    def test_human_mode_without_a_submitted_body_uses_the_commit_body(self):
        create = self.create_args_without_a_submitted_body("-m", "Faster load", "-m", "Cuts load time by 300 ms.\n\nMeasured on a cold start.")
        self.assertEqual(create[create.index("--title") + 1], "Faster load")
        self.assertEqual(create[create.index("--body") + 1], "Cuts load time by 300 ms.\n\nMeasured on a cold start.")
        self.assertNotIn("Queued by", " ".join(create))
        self.assertNotIn("Reviewed by", " ".join(create))

    def test_human_mode_without_any_body_opens_the_pr_with_an_empty_body(self):
        create = self.create_args_without_a_submitted_body("-m", "Faster load")
        self.assertEqual(create[create.index("--body") + 1], "")
        self.assertNotIn("Queued by", " ".join(create))
        self.assertNotIn("Reviewed by", " ".join(create))

    def test_the_pr_body_carries_the_kind_and_the_goal_of_the_items_row(self):
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh", "--batch", "1",
                      "--pr-item-line", "on")
            cases = (
                ("polish", "G2", "Kind: polish. Goal: rules in the tool"),
                ("polish", "", "Kind: polish"),
                ("", "G2", "Goal: rules in the tool"),
                ("", "", ""),
            )
            for index, (kind, goal, expected) in enumerate(cases, 1):
                with self.subTest(kind=kind, goal=goal):
                    branch = f"w{index}"
                    self.store_item(branch, kind, goal)
                    sh("git", "checkout", "-q", "-b", branch, "origin/main", cwd=self.work)
                    (self.work / "a.txt").write_text(f"agent {index}\n")
                    sha = self.commit(branch)
                    lease = self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
                    self.land("submit", "--holder", "r/D1", "--branch", branch, "--sha", sha, "--lease",
                              lease, "--reviewer", REVIEWER)
                    self.land("land")
                    self.assertTrue(log.exists(), "the queue opened no pull request")
                    create = ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][-1])
                    self.assertEqual(create[create.index("--body") + 1], expected)
                    log.unlink()

    def test_a_sole_store_without_a_matching_record_leaves_the_submitted_body_alone(self):
        """Pin review finding D255-review-1 R1. A sole foreign store must not lend its kind and goal."""
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh", "--batch", "1",
                      "--pr-item-line", "on")
            self.assertEqual(self.stored_pr_item_line(), "true")
            body_file = self.base / "submitted.md"
            body_file.write_text("Reviewed body")
            cases = ("missing", "foreign")
            for index, case in enumerate(cases, 1):
                with self.subTest(record=case):
                    branch = f"v{index}"
                    self.store_item(branch, "polish", "G2")
                    store = self.base / "state/pstack-t3/brigade/proj/store"
                    if case == "missing":
                        (store / "restaurant.json").unlink()
                    else:
                        (store / "restaurant.json").write_text(json.dumps({"projectRoot": "/foreign/root"}))
                    sh("git", "checkout", "-q", "-b", branch, "origin/main", cwd=self.work)
                    (self.work / "a.txt").write_text(f"agent {index}\n")
                    sha = self.commit(branch)
                    lease = self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
                    self.land("submit", "--holder", "r/D1", "--branch", branch, "--sha", sha, "--lease",
                              lease, "--reviewer", REVIEWER, "--body-file", str(body_file))
                    self.land("land")
                    self.assertTrue(log.exists(), "the queue opened no pull request")
                    create = ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][-1])
                    self.assertEqual(create[create.index("--body") + 1], "Reviewed body")
                    log.unlink()
