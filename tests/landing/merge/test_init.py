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
    def test_init_in_merge_mode_picks_an_allowed_method(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "squash")
            self.assertIn(
                "repo view --json mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed",
                (self.base / "gh-calls").read_text(),
            )
            self.allow_methods(rebase=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.allow_methods(squash=True, rebase=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.allow_methods(merge=True, squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.allow_methods(merge=True, squash=True, rebase=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.allow_methods(merge=True, rebase=True)
            self.init(mode="merge", merge_method="rebase")
            self.assertEqual(self.stored_merge_method(), "rebase")

    def test_init_outside_merge_mode_does_not_ask_which_methods_are_allowed(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="human")
            calls = (self.base / "gh-calls").read_text() if (self.base / "gh-calls").exists() else ""
            self.assertNotIn("mergeCommitAllowed", calls)
            self.assertEqual(self.stored_merge_method(), "merge")

    def test_init_refuses_a_merge_method_the_repository_disallows(self):
        with self.fake_gh():
            self.allow_methods(squash=True, rebase=True)
            self.assertEqual(
                self.land("init", "--trunk", "main", "--mode", "merge", "--check", "./check.sh",
                          "--merge-method", "merge", ok=False),
                "land: repository does not allow merge; allowed: squash, rebase",
            )
            self.assertIn("has no landing contract", self.land("status", ok=False))
            self.allow_methods()
            self.assertEqual(
                self.land("init", "--trunk", "main", "--mode", "merge", "--check", "./check.sh", ok=False),
                "land: repository allows no merge method; allowed: none",
            )

    def test_init_keeps_merge_when_gh_cannot_read_allowed_methods(self):
        with self.fake_gh():
            (self.base / "repo-view-fails").write_text("x")
            self.allow_methods(squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.init(mode="merge", merge_method="rebase")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.land("init", "--trunk", "main", "--mode", "merge")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.land("mode", "human")
            before = self.contract()
            self.land("init", "--trunk", "main", "--mode", "human")
            self.assertEqual(self.contract(), before)

    def test_a_rerun_keeps_an_omitted_check_setup_and_timeout_and_writes_a_passed_batch(self):
        with self.fake_gh():
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh",
                      "--setup", "make deps", "--batch", "2", "--timeout", "90")
            self.land("init", "--trunk", "main", "--mode", "human", "--batch", "4")
            stored = self.contract()
            self.assertEqual(stored["checks"], ["./check.sh"])
            self.assertEqual(stored["setup"], "make deps")
            self.assertEqual(stored["batch"], 4)
            self.assertEqual(stored["timeout"], 90)
            self.land("init", "--trunk", "main", "--mode", "human", "--remote", "origin")
            self.assertEqual(self.contract(), stored)
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "make verify",
                      "--check", "make lint", "--setup", "", "--timeout", "45")
            self.assertEqual(self.contract(), {**stored, "checks": ["make verify", "make lint"],
                                              "setup": "", "timeout": 45})

    def test_a_rerun_without_merge_method_keeps_rebase_while_it_stays_allowed(self):
        with self.fake_gh():
            self.allow_methods(squash=True, rebase=True)
            self.init(mode="merge", merge_method="rebase")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.allow_methods(squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "squash")

    def test_a_rerun_fills_only_missing_contract_keys(self):
        with self.fake_gh():
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "make verify",
                      "--setup", "make deps", "--batch", "7", "--timeout", "95")
            before = self.contract()
            with land.Store.for_repo(self.work) as store:
                with store.tx() as db:
                    db.execute("DELETE FROM contract WHERE key IN ('setup', 'batch')")
            self.land("init", "--trunk", "main", "--mode", "human")
            self.assertEqual(self.contract(), {**before, "setup": "", "batch": 1})

    def test_a_rerun_with_no_allowed_merge_method_leaves_the_contract_unchanged(self):
        with self.fake_gh():
            self.allow_methods(rebase=True)
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "make verify",
                      "--setup", "make deps", "--batch", "7", "--timeout", "95", "--merge-method", "rebase")
            before = self.contract()
            self.allow_methods()
            self.assertEqual(
                self.land("init", "--trunk", "main", "--mode", "human", "--batch", "4", ok=False),
                "land: repository allows no merge method; allowed: none")
            self.assertEqual(self.contract(), before)

    def test_a_refused_merge_method_names_the_command_that_changes_it(self):
        refusal = "GraphQL: Merge commits are not allowed on this repository. (mergePullRequest)"
        with self.fake_gh():
            self.allow_methods(merge=True, squash=True, rebase=True)
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
            (self.base / "plain-merge-fails").write_text(refusal)
            self.allow_methods(squash=True)
            self.age_absence(1)
            self.assertEqual(
                self.land("land"),
                "queue paused: GitHub refused to merge https://github.com/o/r/pull/9: "
                f"{refusal}. Run land.py mode merge --merge-method squash, then land.py resume",
            )
            self.assertEqual(
                self.land("mode", "human", ok=False),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )
            self.assertEqual(
                self.land("mode", "merge", "--merge-method", "merge", ok=False),
                "land: repository does not allow merge; allowed: squash",
            )
            self.assertEqual(
                self.land("mode", "merge", "--merge-method", "squash"),
                "landing mode is now merge, merging with --squash; leases held by r/D1",
            )
            (self.base / "plain-merge-fails").unlink()
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_the_merge_method_changes_while_an_entry_is_queued(self):
        with self.fake_gh():
            self.allow_methods(merge=True, squash=True)
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(
                self.land("mode", "merge", "--merge-method", "squash"),
                "landing mode is now merge, merging with --squash; leases held by r/D1",
            )
            self.assertEqual(self.stored_merge_method(), "squash")
            self.assertEqual(
                self.land("mode", "push", ok=False),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )

    def test_a_method_change_paused_in_github_keeps_the_mode_a_second_process_set(self):
        with self.fake_gh():
            self.allow_methods(merge=True, squash=True)
            self.init(mode="merge")
            self.pause_repo_view()
            (self.base / "pause-repo-view").write_text("")
            child = real_popen(
                ProcessReason.RACE,
                [sys.executable, str(SCRIPT), "--repo", str(self.work),
                 "mode", "merge", "--merge-method", "squash"],
                env=os.environ.copy())
            try:
                deadline = time.monotonic() + 10
                while not (self.base / "repo-view-waiting").exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        out, err = child.communicate()
                        self.fail(f"repo view did not pause: code {child.returncode}\n{out}\n{err}")
                    time.sleep(0.01)
                self.assertEqual(self.land("mode", "human"), "landing mode is now human")
                self.assertEqual(self.queue_one(), "E1")
                (self.base / "repo-view-release").write_text("")
                out, err = child.communicate(timeout=10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
            self.assertEqual(child.returncode, 1, out + err)
            self.assertEqual(
                err.strip(),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )
            self.assertEqual(self.stored_merge_method(), "merge")
            status = self.land("status")
            self.assertTrue(status.startswith("human mode onto"), status)
            self.assertIn("queued: 1", status)

    def test_mode_help_separates_a_switch_from_a_method_change(self):
        top = real_process(ProcessReason.HELP, SCRIPT, ["--help"], env=None)
        mode = real_process(ProcessReason.HELP, SCRIPT, ["mode", "--help"], env=None)
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertEqual(mode.returncode, 0, mode.stderr)
        for text in (top.stdout, mode.stdout):
            shown = " ".join(text.split())
            self.assertIn("when the queue is empty", shown)
            self.assertIn("--merge-method may change while entries are in flight", shown)
            self.assertNotIn("while nothing is in flight", shown)

    def test_one_busy_entry_is_singular_and_two_are_plural(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            self.assertEqual(
                self.land("mode", "merge", ok=False),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )
            self.queue_one(path="b.txt", name="w2", holder="r/D2")
            self.land("land")
            self.assertEqual(
                self.land("mode", "push", ok=False),
                "land: 2 entries are queued, landing, or awaiting merge; change the mode when the queue is empty",
            )

    def test_the_mode_changes_only_while_nothing_is_in_flight(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            self.assertIn("awaiting merge; change the mode when the queue is empty", self.land("mode", "merge", ok=False))
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.land("land")
            self.assertEqual(self.land("mode", "merge", "--merge-method", "squash"), "landing mode is now merge, merging with --squash")
            self.assertTrue(self.land("status").startswith("merge mode onto"))
            self.assertIn("needs a new contract", self.land("mode", "local", ok=False))

    def test_a_contract_written_with_the_old_auto_mode_reads_as_push(self):
        self.init(mode="auto")
        self.assertTrue(self.land("status").startswith("push mode onto"))
        self.assertEqual(self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh").split()[0], "updated")

    def test_human_mode_adopts_the_pr_a_crashed_run_created(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            (self.base / "crash-on-create").write_text("")
            result = real_process(
                ProcessReason.KILLED_CALLER, SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            self.assertEqual(result.returncode, -9)
            self.assertEqual(self.land("land"), "adopted E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9", self.land("status", "E1"))

    def test_human_mode_bounces_a_pr_merged_with_a_head_the_queue_did_not_check(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            (self.base / "pr-state").write_text("MERGED 0123456789abcdef deadbeef")
            self.assertIn("bounced E1 (r/D1): merged at deadbeef with head 0123456789ab", self.land("land"))

    def test_human_mode_pauses_when_trunk_loses_a_landed_commit(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            sh("git", "fetch", "-q", "origin", cwd=self.work)
            merged = self.commit_on_origin("human merge")
            (self.base / "pr-state").write_text(f"MERGED {sh('git', 'rev-parse', 'landing/e1', cwd=self.base / 'origin.git')} {merged}")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            sh("git", "push", "-q", "--force", "origin", "HEAD:main", cwd=self.work)
            self.assertIn("queue paused: trunk no longer contains the last landed commit", self.land("land"))
