from ..support import Path, ProcessReason, SCRIPT, SUPPORT, _race_args, _wait_for_path, os, real_popen, runpy, sys, time


class ReplacementOrderMixin:
    def test_a_log_write_that_read_restaurant_json_before_a_replacement_keeps_the_new_thread(self):
        self.with_thread()
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        restaurant = glob["Restaurant"](self.dir(".admin"), "t1@1")
        self.assertEqual(restaurant.meta["thread"], "t1")
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        after_claim = self.files(".admin")
        with self.assertRaises(glob["BrigadeError"]) as raised:
            restaurant.log("ticket", "T1", "waiting", "one")
        self.assertEqual(str(raised.exception), "owner t1@1 is stale; this store is owned by recovering:t9@2")
        self.assertEqual(self.files(".admin"), after_claim)
        unfenced = glob["Restaurant"](self.dir(".admin"))
        unfenced.unfenced = True
        unfenced.log("ticket", "T1", "waiting", "one")
        meta = self.meta(".admin")
        self.assertEqual((meta["thread"], meta["generation"], meta["previousThread"]), ("recovering:t9", 2, "t1"))

    def test_claim_first_then_a_paused_rule_add_is_refused(self):
        self.with_thread()
        case = Path(self.temporary.name) / "rule"
        case.mkdir()
        support = SUPPORT
        proc = real_popen(
            ProcessReason.STOPPED,
            [sys.executable, str(support), *_race_args(
                "pause", case,
                ["--store", str(self.store), "--at", str(self.dir(".admin")),
                 "--owner", "t1@1", "rule", "add", "--kind", "ownership",
                 "--parties", "docs,engine", "--question", "Who owns T1?", "--rule", "priority",
                 "--decision", "docs"])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        _wait_for_path(case / "paused", timeout=20)
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        after_claim = self.files(".admin")
        (case / "proceed").touch()
        code, _, err = self.finish(proc)
        self.assertEqual((code, err), (1, "brigade: owner t1@1 is stale; this store is owned by recovering:t9@2"))
        self.assertEqual(self.files(".admin"), after_claim)
        self.assertEqual(self.rows(".admin", "rulings.tsv"), [])

    def test_after_a_terminal_wait_the_old_runs_paused_commands_change_nothing(self):
        self.with_thread()
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "one")
        self.admin("ticket", "add", "--kind", "fix", "--summary", "route me")
        old = ("--owner", "t1@1")
        paused = [self.admin_child("pause", f"stale{number}", *old, *args) for number, args in enumerate((
            ("rule", "add", "--kind", "ownership", "--parties", "docs,engine", "--question", "q", "--rule", "priority", "--decision", "d"),
            ("request", "--to", "docs", "from-user docs: late"),
            ("sync",),
            ("ticket", "move", "T1", "--to", "docs"),
        ))]
        for case, _ in paused:
            _wait_for_path(case / "paused", timeout=20)
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", "--stopped", "r1")
        self.assertEqual(self.admin("status").splitlines()[-1], "owner t2@3")
        self.assertIn("docs", self.admin("sync"))
        admin_after, docs_after = self.files(".admin"), self.files("docs")
        for case, proc in paused:
            (case / "proceed").touch()
            code, _, err = self.finish(proc)
            self.assertEqual((code, err), (1, "brigade: owner t1@1 is stale; this store is owned by t2@3"))
        self.assertEqual(self.files(".admin"), admin_after)
        self.assertEqual(self.files("docs"), docs_after)
        self.assertEqual(self.inbox("docs"), [])

    def test_a_write_that_took_the_lock_before_the_claim_finishes_first(self):
        self.with_thread()
        case, proc = self.admin_child("hold", "hold", "--owner", "t1@1", "rule", "add", "--kind", "ownership",
                                      "--parties", "docs,engine", "--question", "Who owns T1?", "--rule", "priority",
                                      "--decision", "docs")
        _wait_for_path(case / "holding", timeout=20)
        claim = real_popen(
            ProcessReason.OTHER_LOCK,
            [sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.dir(".admin")),
             "set", "--thread", "recovering:t9", "--replace", "--expect", "t1"],
            text=True)
        self.addCleanup(lambda: claim.poll() is None and claim.kill())
        time.sleep(0.5)
        self.assertIsNone(claim.poll(), "the claim waits for the lock")
        (case / "proceed").touch()
        self.assertEqual(self.finish(proc)[:2], (0, "R1"))
        self.assertEqual(self.finish(claim)[0], 0)
        kinds = [(row[1], row[2]) for row in self.rows(".admin", "log.tsv")]
        self.assertLess(kinds.index(("ruling", "R1")), kinds.index(("thread", "recovering:t9@2")))
        self.assertEqual(self.meta(".admin")["thread"], "recovering:t9")
