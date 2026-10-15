from .support import (
    setUpModule,
    LandingTest as LandingTestBase,
    ADMIN,
    land,
    os,
    Path,
    ProcessReason,
    real_popen,
    REVIEWER,
    SCRIPT,
    sh,
    sys,
    time,
)


class LandingTest(LandingTestBase):
    def test_an_open_contest_holds_both_holders_out_of_a_push_mode_batch(self):
        self.contest_holds_both_holders("push", batch=4)
        self.assertEqual(self.queue_one(path="lib/x.py", name="w3", holder="ops/D1"), "E3")
        self.assertEqual(self.land("land"), "landed E3 (ops/D1)\nstill queued: E1 (held by C1), E2 (held by C1)")
        self.assertEqual(self.origin_log(), ["w3", "init"])

    def test_an_open_contest_holds_both_holders_in_local_mode(self):
        self.contest_holds_both_holders("local", trunk="lane", base="main")
        self.assertEqual(sh("git", "rev-parse", "refs/landing/lane", cwd=self.work), sh("git", "rev-parse", "main", cwd=self.work))

    def test_an_open_contest_keeps_both_holders_out_of_pr_opening_in_human_mode(self):
        with self.fake_gh():
            self.contest_holds_both_holders("human")
            calls = (self.base / "gh-calls").read_text() if (self.base / "gh-calls").exists() else ""
            self.assertNotIn("pr create", calls)
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_an_open_contest_keeps_both_holders_out_of_pr_opening_in_merge_mode(self):
        with self.fake_gh():
            self.contest_holds_both_holders("merge")
            self.assertNotIn("pr create", (self.base / "gh-calls").read_text())
            self.assertFalse((self.base / "merge-calls").exists())

    def test_a_settled_contest_holds_the_second_holder_until_the_first_lands_after_a_bounce(self):
        self.init()
        self.queue_one(path="a.txt", text="BROKEN\n", name="w1", holder="docs/D7")
        self.contest("--holders", "docs/D7,engine/D3")
        self.queue_one(path="b.txt", name="w2", holder="engine/D3")
        settled = "C1: docs/D7 lands first, engine/D3 waits"
        self.assertEqual(self.contest("--settle", "C1", "--first", "docs/D7"), settled)
        self.assertEqual(self.contest("--settle", "C1", "--first", "docs/D7"), settled)
        out = self.land("land")
        self.assertTrue(out.startswith("bounced E1 (docs/D7): checks failed:"), out)
        self.assertTrue(out.endswith("\nstill queued: E2 (held by C1)"), out)
        (self.base / "w1" / "a.txt").write_text("fixed\n")
        fixed = self.commit("fix", cwd=self.base / "w1")
        self.assertEqual(self.land("submit", "--holder", "docs/D7", "--branch", "w1", "--sha", fixed, "--lease", "L1", "--reviewer", REVIEWER), "E3")
        self.assertEqual(self.land("land"), "landed E3 (docs/D7), E2 (engine/D3)")
        self.assertEqual(self.origin_log(), ["w2", "fix", "w1", "init"])
        self.assertEqual(self.contest("--settle", "C1", "--first", "docs/D7"), settled)
        self.assertEqual(self.contest("--settle", "C1", "--first", "engine/D3", ok=False), "land: C1 is done: docs/D7 landed")

    def test_a_contest_settles_again_in_reverse_and_refuses_an_order_that_closes_a_cycle(self):
        self.init()
        self.assertEqual(self.contest("--holders", "docs/D7,engine/D3"), "C1")
        self.contest("--settle", "C1", "--first", "docs/D7")
        self.assertEqual(self.contest("--settle", "C1", "--first", "engine/D3"), "C1: engine/D3 lands first, docs/D7 waits")
        self.assertEqual(self.contest("--holders", "docs/D7,ops/D1"), "C2")
        self.contest("--settle", "C2", "--first", "docs/D7")
        self.assertEqual(self.contest("--holders", "ops/D1,engine/D3"), "C3")
        self.assertEqual(self.contest("--settle", "C3", "--first", "ops/D1", ok=False),
                         "land: C3 cannot order ops/D1 before engine/D3: it closes a cycle with C1, C2")
        self.assertEqual(self.contest("--settle", "C3", "--first", "engine/D3"), "C3: engine/D3 lands first, ops/D1 waits")
        self.assertEqual(self.contest("--cancel", "C1"), "C1 cancelled")
        self.assertEqual(self.contest("--settle", "C3", "--first", "ops/D1"), "C3: ops/D1 lands first, engine/D3 waits")
        self.assertEqual(self.contest("--settle", "C1", "--first", "docs/D7", ok=False), "land: C1 is cancelled")
        self.assertEqual(self.contest("--settle", "C2", "--first", "docs/D9", ok=False), "land: docs/D9 is not a holder in C2")
        self.assertEqual(self.contest("--holders", "docs/D7,docs/D7", ok=False), "land: a contest needs two different holders")
        self.assertEqual(self.contest("--cancel", "C9", ok=False), "land: no C9")

    def test_a_contest_refuses_work_already_landed_and_ignores_a_bounce(self):
        self.init()
        self.queue_one(path="a.txt", name="w1", holder="docs/D7")
        self.queue_one(path="b.txt", text="BROKEN\n", name="w2", holder="engine/D3")
        self.land("land")
        self.assertEqual(self.contest("--holders", "engine/D3,docs/D7", ok=False), "land: docs/D7 has E1 landed; there is nothing left to order")
        self.assertEqual(self.contest("--holders", "engine/D3,ops/D1"), "C1")

    def test_a_contest_waits_for_a_running_land_to_release_the_queue_lock(self):
        import fcntl
        self.init()
        with land.Store.for_repo(self.work) as store:
            with open(store.dir / ".queue.lock", "a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                proc = real_popen(
                    ProcessReason.LOCK,
                    [sys.executable, str(SCRIPT), "--repo", str(self.work), "contest", "--holders", "docs/D7,engine/D3", *ADMIN],
                    env=os.environ.copy())
                time.sleep(1)
                self.assertIsNone(proc.poll())
                self.assertEqual(self.ruling_rows("contest"), [])
            out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, out.strip()), (0, "C1"), err)

    def test_every_ruling_write_needs_an_owner(self):
        self.init()
        for args in (["lease", "reserve", "--for", "docs/", "--paths", "a.txt", "--ruling", "R1"], ["lease", "unreserve", "S1"],
                     ["share", "--for", "docs/", "1"], ["contest", "--holders", "a/D1,b/D1"], ["contest", "--cancel", "C1"]):
            self.assertIn("--owner", self.land(*args, ok=False))

    def test_paused_ruling_writes_from_a_replaced_admin_change_nothing(self):
        self.init()
        self.land("cap", "4")
        self.share("docs/", 1)
        self.contest("--holders", "docs/D7,engine/D3")
        self.contest("--settle", "C1", "--first", "docs/D7")
        tables = ("share", "reservation", "contest")
        before = {table: self.ruling_rows(table) for table in tables}
        commands = [
            ["share", "--for", "docs/", "2", *ADMIN],
            ["lease", "reserve", "--for", "docs/", "--paths", "a.txt", "--ruling", "R9", *ADMIN],
            ["contest", "--settle", "C1", "--first", "engine/D3", *ADMIN],
        ]
        running = []
        for number, args in enumerate(commands):
            case = self.base / f"paused{number}"
            case.mkdir()
            proc = real_popen(
                ProcessReason.RACE,
                [sys.executable, str(Path(__file__).resolve().parent / "support.py"), "--paused-child", str(case), "--repo", str(self.work), *args],
                env=os.environ.copy())
            running.append((case, proc))
        for case, proc in running:
            deadline = time.monotonic() + 20
            while not (case / "paused").exists():
                if proc.poll() is not None or time.monotonic() > deadline:
                    self.fail(f"{case.name} did not pause: {proc.communicate()}")
                time.sleep(0.01)
        self.assertEqual(self.land("owner", "--prefix", ".admin/", "--generation", "2"), ".admin/ at generation 2")
        for case, proc in running:
            (case / "proceed").touch()
            out, err = proc.communicate(timeout=30)
            self.assertEqual((proc.returncode, err.strip()), (1, "land: owner .admin/@1 is stale; .admin/ is at generation 2"), out)
        self.assertEqual({table: self.ruling_rows(table) for table in tables}, before)
        current = ("--owner", ".admin/@2")
        self.assertEqual(self.land("share", "--for", "docs/", "2", *current), "docs/ share is 2")
        self.assertEqual(self.land("lease", "reserve", "--for", "docs/", "--paths", "a.txt", "--ruling", "R9", *current), "S1")
        self.assertEqual(self.land("contest", "--settle", "C1", "--first", "engine/D3", *current), "C1: engine/D3 lands first, docs/D7 waits")
        self.assertEqual(self.land("contest", "--cancel", "C1", *ADMIN, ok=False), "land: owner .admin/@1 is stale; .admin/ is at generation 2")

    def test_mode_merge_after_init_in_human_mode_swaps_a_disallowed_method(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="human")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.assertEqual(self.land("mode", "merge"), "landing mode is now merge, merging with --squash")
            self.assertEqual(self.stored_merge_method(), "squash")
            self.assertEqual(self.land("mode", "human", "--merge-method", "merge", ok=False),
                             "land: repository does not allow merge; allowed: squash")
            self.assertEqual(self.stored_merge_method(), "squash")
            self.assertTrue(self.land("status").startswith("merge mode onto"))
            self.assertEqual(self.land("mode", "human", "--merge-method", "squash"), "landing mode is now human, merging with --squash")

    def test_init_in_any_mode_refuses_a_merge_method_the_repository_disallows(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.assertEqual(self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh", "--merge-method", "merge", ok=False),
                             "land: repository does not allow merge; allowed: squash")
            self.assertIn("has no landing contract", self.land("status", ok=False))
            self.init(mode="push", merge_method="squash")
            self.assertEqual(self.stored_merge_method(), "squash")


    def test_a_settled_contest_keeps_its_order_once_the_first_holders_pr_is_open(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one(path="a.txt", name="w1", holder="docs/D7")
            self.contest("--holders", "docs/D7,engine/D3")
            self.queue_one(path="b.txt", name="w2", holder="engine/D3")
            self.contest("--settle", "C1", "--first", "docs/D7")
            self.assertEqual(self.land("land"), "opened PRs for E1 (docs/D7) https://github.com/o/r/pull/9\nstill queued: E2 (held by C1)")
            self.assertEqual(self.contest("--settle", "C1", "--first", "engine/D3", ok=False),
                             "land: C1 cannot put engine/D3 first: docs/D7 has E1 awaiting-merge")
            self.assertEqual(self.contest("--settle", "C1", "--first", "docs/D7"), "C1: docs/D7 lands first, engine/D3 waits")

    def test_mode_merge_checks_the_method_stored_when_it_writes(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="human", merge_method="squash")
            self.pause_repo_view()
            (self.base / "pause-repo-view").write_text("")
            child = real_popen(
                ProcessReason.RACE,
                [sys.executable, str(SCRIPT), "--repo", str(self.work), "mode", "merge"],
                env=os.environ.copy())
            try:
                deadline = time.monotonic() + 10
                while not (self.base / "repo-view-waiting").exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        self.fail(f"repo view did not pause: {child.communicate()}")
                    time.sleep(0.01)
                (self.base / "pause-repo-view").unlink()
                self.init(mode="human")
                self.assertEqual(self.stored_merge_method(), "merge")
                (self.base / "repo-view-release").write_text("")
                out, err = child.communicate(timeout=10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
            self.assertEqual((child.returncode, out.strip()), (0, "landing mode is now merge, merging with --squash"), err)
            self.assertEqual(self.stored_merge_method(), "squash")

    def test_mode_local_checks_an_explicit_merge_method(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="local", base="main", merge_method="squash")
            self.assertEqual(self.land("mode", "local", "--merge-method", "merge", ok=False),
                             "land: repository does not allow merge; allowed: squash")
            self.assertEqual(self.stored_merge_method(), "squash")

    def test_a_settled_contest_keeps_its_order_while_a_failed_pr_still_has_auto_merge(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            (self.base / "disable-auto-fails").write_text("API unavailable")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one(path="a.txt", name="w1", holder="docs/D7")
            self.contest("--holders", "docs/D7,engine/D3")
            self.queue_one(path="b.txt", name="w2", holder="engine/D3")
            self.contest("--settle", "C1", "--first", "docs/D7")
            self.land("land")
            self.land("land")
            (self.base / "checks").write_text("failed")
            self.assertIn("Auto-merge is still enabled: API unavailable", self.land("land"))
            sha = self.land("status", "--holder", "docs/D7").split(", ")[1][:12]
            self.assertEqual(self.land("submit", "--holder", "docs/D7", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER),
                             "E1 already awaiting-merge")
            self.assertEqual(self.contest("--settle", "C1", "--first", "engine/D3", ok=False),
                             "land: C1 cannot put engine/D3 first: docs/D7 has E1 awaiting-merge")
