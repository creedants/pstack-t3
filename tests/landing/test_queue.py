from .support import (
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


class LandingTest(LandingTestBase):
    def test_status_refuses_a_q_number(self):
        self.init()
        self.assertEqual(self.queue_one(), "E1")
        self.assertIn("'Q1' is not an entry id such as E3", self.land("status", "Q1", ok=False))
        named = self.land("status", "E1")
        self.assertEqual(self.land("status", "1"), named)
        self.assertTrue(named.startswith("E1 queued"), named)

    def test_a_batch_lands_in_one_push_and_resubmitting_is_idempotent(self):
        self.init(batch=4)
        shas = [self.worker(f"w{n}", {f: f"{f} v{n}\n"}) for n, f in enumerate(["a.txt", "b.txt", "lib/x.py"], 1)]
        for n, (sha, path) in enumerate(zip(shas, ["a.txt", "b.txt", "lib"]), 1):
            self.land("lease", "claim", "--holder", f"r/D{n}", "--paths", path)
            self.assertEqual(self.land("submit", "--holder", f"r/D{n}", "--branch", f"w{n}", "--sha", sha, "--lease", f"L{n}", "--reviewer", REVIEWER), f"E{n}")
        self.assertEqual(self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", shas[0], "--lease", "L1", "--reviewer", REVIEWER), "E1 already queued")
        self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2), E3 (r/D3)")
        self.assertEqual(self.origin_log(), ["w3", "w2", "w1", "init"])
        self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_a_failing_batch_lands_the_good_entries_alone_and_bounces_the_bad_one(self):
        self.init(batch=3)
        good1 = self.worker("w1", {"a.txt": "fine\n"})
        bad = self.worker("w2", {"b.txt": "BROKEN\n"})
        good2 = self.worker("w3", {"lib/x.py": "fine\n"})
        for n, (sha, path) in enumerate([(good1, "a.txt"), (bad, "b.txt"), (good2, "lib")], 1):
            self.land("lease", "claim", "--holder", f"r/D{n}", "--paths", path)
            self.land("submit", "--holder", f"r/D{n}", "--branch", f"w{n}", "--sha", sha, "--lease", f"L{n}", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1 (r/D1), E3 (r/D3)", out)
        self.assertIn("bounced E2 (r/D2): checks failed: `./check.sh` exited 1", out)
        self.assertEqual(self.origin_log(), ["w3", "w1", "init"])
        self.assertEqual(self.land("lease", "list").split(" until ")[0], "L2 active r/D2")

    def test_a_conflict_bounces_and_the_fixed_resubmission_lands(self):
        self.init()
        first = self.worker("w1", {"a.txt": "one\n"})
        second = self.worker("w2", {"a.txt": "two\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", first, "--lease", "L1", "--reviewer", REVIEWER)
        self.land("land")
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", second, "--lease", "L2", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "bounced E2 (r/D2): conflict with trunk")
        path = self.base / "w2"
        sh("git", "fetch", "-q", "origin", cwd=path)
        sh("git", "reset", "-q", "--hard", "origin/main", cwd=path)
        (path / "a.txt").write_text("one\ntwo\n")
        fixed = self.commit("w2 fixed", cwd=path)
        self.assertEqual(self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", fixed, "--lease", "L2", "--reviewer", REVIEWER), "E3")
        self.assertEqual(self.land("land"), "landed E3 (r/D2)")

    def test_a_bounced_entry_resubmitted_gets_a_new_id(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "BROKEN\n"})
        self.claim("r/D1", "a.txt")
        submit = ["submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER]
        self.assertEqual(self.land(*submit), "E1")
        self.assertIn("bounced E1 (r/D1): checks failed: `./check.sh` exited 1", self.land("land"))
        self.rebuild_entry_without_autoincrement()
        self.assertIn("E1 bounced", self.land("status", "E1"))
        self.assertEqual(self.land(*submit), "E2")
        self.assertEqual(self.land("status", "E1", ok=False), "land: no E1")
        self.assertIn("E2 queued", self.land("status", "E2"))

    def test_a_conflict_under_a_non_english_locale_bounces(self):
        translated = self.use_non_english_locale()
        self.init()
        first = self.worker("w1", {"a.txt": "one\n"})
        second = self.worker("w2", {"a.txt": "two\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", first, "--lease", "L1", "--reviewer", REVIEWER)
        self.land("land")
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", second, "--lease", "L2", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "bounced E2 (r/D2): conflict with trunk")
        if not translated:
            recorded = self.git_locale_log.read_text().splitlines()
            self.assertTrue(recorded)
            for line in recorded:
                self.assertEqual(line, "cherry-pick LC_ALL=C")

    def test_a_conflict_rerere_resolved_to_trunk_bounces(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "worker\n"})
        (self.work / "a.txt").write_text("human\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        sh("git", "config", "rerere.enabled", "true", cwd=self.work)
        sh("git", "config", "rerere.autoupdate", "true", cwd=self.work)
        record = self.base / "record"
        sh("git", "worktree", "add", "-q", "--detach", str(record), "origin/main", cwd=self.work)
        conflict = self.git_raw("cherry-pick", sha, cwd=record)
        self.assertNotEqual(conflict.returncode, 0, conflict.stdout + conflict.stderr)
        (record / "a.txt").write_text("human\n")
        sh("git", "rerere", cwd=record)
        sh("git", "add", "a.txt", cwd=record)
        sh("git", "cherry-pick", "--abort", cwd=record)
        replay = self.git_raw("cherry-pick", sha, cwd=record)
        detail = replay.stdout + replay.stderr
        self.assertNotEqual(replay.returncode, 0, detail)
        self.assertIn("CONFLICT", detail)
        self.assertIn("could not apply", detail)
        self.assertEqual(self.git_raw("status", "--porcelain", cwd=record).stdout, "")
        self.assertEqual(self.git_raw("ls-files", "-u", cwd=record).stdout, "")
        sh("git", "cherry-pick", "--abort", cwd=record)
        self.assertEqual(self.land("land"), "bounced E1 (r/D1): conflict with trunk")
        status = self.land("status", "E1")
        self.assertIn("E1 bounced", status)
        self.assertIn("conflict with trunk", status)
        self.assertNotIn("already in trunk", status)
        self.assertEqual(self.land("lease", "list").split(" until ")[0], "L1 active r/D1")
        self.assertEqual(self.origin_log(), ["human", "init"])
        self.assertEqual(sh("git", "show", "main:a.txt", cwd=self.base / "origin.git"), "human")

    def test_landing_uses_the_pinned_sha_even_when_the_branch_moves_on(self):
        self.init()
        reviewed = self.worker("w1", {"a.txt": "reviewed\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", reviewed, "--lease", "L1", "--reviewer", REVIEWER)
        (self.base / "w1/a.txt").write_text("unreviewed\n")
        self.commit("sneaky", cwd=self.base / "w1")
        self.land("land")
        self.assertEqual(self.origin_log(), ["w1", "init"])
        self.assertEqual(sh("git", "show", "main:a.txt", cwd=self.base / "origin.git"), "reviewed")

    def test_trunk_moved_by_a_human_is_rebased_onto_and_a_rewind_pauses_the_queue(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        (self.work / "b.txt").write_text("human\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertEqual(self.origin_log(), ["w1", "human", "init"])
        sh("git", "push", "-q", "--force", "origin", "HEAD~1:main", cwd=self.work)
        other = self.worker("w2", {"b.txt": "agent2\n"})
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "b.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", other, "--lease", "L2", "--reviewer", REVIEWER)
        self.assertIn("queue paused: trunk no longer contains the last landed commit", self.land("land"))
        self.assertIn("queue paused", self.land("land"))
        self.land("resume")
        self.assertEqual(self.land("land"), "landed E2 (r/D2)")

    def test_a_patch_already_on_trunk_lands_without_replaying_it(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        (self.work / "a.txt").write_text("agent\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertEqual(self.origin_log(), ["human", "init"])
        self.assertIn("already in trunk", self.land("status", "E1"))

    def test_a_commit_that_started_empty_pauses_instead_of_landing(self):
        self.init()
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "a.txt").write_text("agent\n")
        self.commit("w1", cwd=path)
        sh("git", "commit", "-q", "--allow-empty", "-m", "empty", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        paused = self.land("land")
        self.assertIn("queue paused", paused)
        self.assertIn("cherry-pick failed", paused)
        self.assertNotIn("landed", paused)
        self.assertEqual(self.origin_log(), ["init"])

    def test_a_crash_after_the_push_is_recovered_as_landed_without_pushing_again(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        real = land.publish

        def publish_then_crash(*args):
            real(*args)
            raise KeyboardInterrupt

        with land.Store.for_repo(self.work) as store:
            with mock.patch.object(land, "publish", publish_then_crash), self.assertRaises(KeyboardInterrupt):
                land.land(store)
        self.assertEqual(self.land_in_another_process("status", "E1").split(" (")[0], "E1 landing")
        self.assertEqual(self.land_in_another_process("land"), "nothing to land")
        self.assertIn("E1 landed", self.land_in_another_process("status", "E1"))
        self.assertIn("recovered after an interrupted run", self.land_in_another_process("status", "E1"))
        self.assertEqual(self.origin_log(), ["w1", "init"])

    def test_a_held_queue_lock_reports_busy(self):
        self.init()
        with land.Store.for_repo(self.work) as store:
            import fcntl
            with open(store.dir / ".queue.lock", "a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                result = real_process(
                    ProcessReason.LOCK, SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("queue busy", result.stdout)

    def test_local_mode_lands_on_a_queue_owned_ref_and_never_moves_a_branch(self):
        self.init(mode="local", trunk="perf", base="main")
        sha = self.worker("w1", {"a.txt": "fast\n"}, base="main")
        self.land("lease", "claim", "--holder", "perf/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "perf/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        main_before = sh("git", "rev-parse", "main", cwd=self.work)
        self.assertEqual(self.land("land"), "landed E1 (perf/D1)")
        self.assertEqual(sh("git", "show", "refs/landing/perf:a.txt", cwd=self.work), "fast")
        self.assertEqual(sh("git", "rev-parse", "main", cwd=self.work), main_before)
        self.assertEqual(sh("git", "status", "--porcelain", cwd=self.work), "")

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
            sh("git", "remote", "set-url", "origin", str(self.base / "origin.git"), cwd=self.work)
            self.init(mode="human")
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

    def test_a_pr_opened_for_an_entry_left_without_one_is_reported(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.leave_awaiting_merge_without_a_pr()
            reported = self.land("land")
            self.assertEqual(reported, "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9")
            status = self.land("status", "E1")
            self.assertIn("awaiting-merge", status)
            self.assertIn("https://github.com/o/r/pull/9", status)
            self.assertEqual(self.land("land"), "nothing to land")

    def test_a_failed_pin_leaves_nothing_queued(self):
        self.init()
        wrapper = self.base / "bin"
        wrapper.mkdir()
        real = sh("sh", "-c", "command -v git", cwd=self.base)
        (wrapper / "git").write_text(f'#!/bin/sh\ncase "$*" in *refs/landing/pins/*) exit 1;; esac\nexec {real} "$@"\n')
        (wrapper / "git").chmod(0o755)
        sha = self.worker("w1", {"a.txt": "agent\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        with mock.patch.dict(os.environ, {"PATH": f"{wrapper}:{os.environ['PATH']}"}):
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, ok=False)
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1. author policy: any")

    def test_a_policy_rejection_pauses_the_queue_after_one_check_run(self):
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", f"echo run >> {self.base}/runs")
        hook = self.base / "origin.git/hooks/pre-receive"
        hook.write_text("#!/bin/sh\necho rejected by policy >&2\nexit 1\n")
        hook.chmod(0o755)
        self.queue_one()
        self.assertIn("queue paused: push rejected: remote: rejected by policy", self.land("land"))
        self.assertEqual((self.base / "runs").read_text(), "run\n")
        self.assertIn("E1 queued", self.land("status", "E1"))

    def test_a_check_that_escapes_its_group_cannot_hold_the_queue(self):
        script = self.base / "escape.py"
        script.write_text("import os, time\nos.setsid()\ntime.sleep(20)\n")
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", f"{sys.executable} {script}; true", "--timeout", "1")
        self.queue_one()
        started = __import__("time").monotonic()
        self.assertIn("timed out", self.land("land"))
        self.assertLess(__import__("time").monotonic() - started, 12)

    def test_non_utf8_check_output_and_file_contents_land(self):
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", "printf '\\377'")
        self.worker("w1", {"seed.txt": "seed\n"})
        (self.base / "w1/latin.txt").write_bytes(b"caf\xe9\n")
        sha = self.commit("latin", cwd=self.base / "w1")
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "seed.txt,latin.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_landing_works_without_a_git_identity(self):
        self.init()
        self.queue_one()
        empty = self.base / "home"
        empty.mkdir()
        bare_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        bare_env.update({"HOME": str(empty), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})
        result = real_process(
            ProcessReason.INHERITED_ENV, SCRIPT, ["--repo", str(self.work), "land"], env=bare_env)
        self.assertEqual(result.stdout.strip(), "landed E1 (r/D1)", result.stderr)

    def test_fingerprint_sees_whitespace_and_ignores_line_numbers(self):
        repo = self.work
        base = sh("git", "rev-parse", "HEAD", cwd=repo)
        (repo / "lib/x.py").write_text("lib/x.py\n    return 1\n")
        indented = self.commit("indent")
        sh("git", "reset", "-q", "--hard", base, cwd=repo)
        (repo / "lib/x.py").write_text("lib/x.py\n  return 1\n")
        other = self.commit("indent 2")
        self.assertNotEqual(land.fingerprint(repo, base, indented), land.fingerprint(repo, base, other))
        sh("git", "reset", "-q", "--hard", base, cwd=repo)
        (repo / "lib/x.py").write_text("header\nlib/x.py\n")
        moved = self.commit("header")
        (repo / "lib/x.py").write_text("header\nlib/x.py\n    return 1\n")
        indented_after_header = self.commit("indent after header")
        self.assertEqual(land.fingerprint(repo, base, indented), land.fingerprint(repo, moved, indented_after_header))

    def test_a_check_that_hangs_is_killed_at_the_timeout(self):
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", "sleep 30", "--timeout", "1")
        sha = self.worker("w1", {"a.txt": "agent\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertIn("bounced E1 (r/D1): checks failed: `sleep 30` timed out", self.land("land"))

    def test_the_contract_refuses_a_conflicting_init_and_other_clones(self):
        self.init()
        self.assertIn("already sets trunk='main'", self.land("init", "--trunk", "dev", "--mode", "auto", ok=False))
        sh("git", "clone", "-q", "origin.git", "other", cwd=self.base)
        _code, _out, err = self.run_land(self.base / "other", "status")
        self.assertIn("has no landing contract", err)

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
            self.init(mode="human")
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
            self.init(mode="human")
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
