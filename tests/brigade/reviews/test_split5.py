from ..support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    HELD_QUESTION,
    ITEM_DECISION,
    Path,
    STILL_OPEN,
    THREAD_A,
    THREAD_B,
    THREAD_C,
    THREAD_D,
    datetime,
    json,
    os,
    shutil,
    subprocess,
    timezone,
    unittest,
)


class ReviewsSplit5Test(BrigadeTest):
    def test_next_runs_each_conditional_action(self):
        self.fired_bug_fix()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        liveness = self.next_sheet("liveness")
        self.assertEqual(self.execute_conditionals(liveness), [
            "next merge D1",
            "next --failure stuck",
            "set --schedule liveness=",
        ])
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        checks = self.next_sheet("checks", "D1", "--result", "fail")
        self.assertEqual(self.execute_conditionals(checks), [
            "next merge D1",
            "next checks D1 --result fail --failure stuck",
        ])
        self.fresh()
        review = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_conditionals(review), ["dish D1 --state in-progress"])
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        owed = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("if D1 reaches the round budget and no decision is open: run 86 add --dish D1 --round-budget", owed)
        self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_commands([["86", "add", "--dish", "D1", "--round-budget"]]),
                         ["86 add --dish D1 --round-budget"])
        shutil.rmtree(self.at)
        self.open()
        inbox = self.at / "inbox"
        inbox.mkdir()
        (inbox / "A1.line").write_text("please fix the gate\n")
        admin = self.next_sheet("admin", "A1")
        self.assertEqual(self.execute_conditionals(admin), ["inbox done A1"])
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        landed = self.next_sheet()
        self.assertIn("if the landing queue has an entry for D1: run land.py status --holder perf/D1 --sha abc", landed)
        self.init_landing()
        self.assertEqual(self.execute_conditionals(landed), ["land.py status --holder perf/D1 --sha abc"])

    def test_next_omits_the_fix_round_at_the_round_budget(self):
        self.fresh()
        below = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("if the old worker's wait for D1 is terminal and D1 may take a fix round: run dish D1 --state in-progress", below)
        self.assertNotIn("round-budget", below)
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_conditionals(below), ["dish D1 --state in-progress"])
        self.fresh()
        for number in range(3):
            self.brigade("pass", "record", "D1", "--sha", f"s{number}", "--verdict", "send-back",
                         "--author", CLAUDE, "--verifier", CODEX)
        owed = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("\nrun 86 add --dish D1 --round-budget\n", f"\n{owed}\n")
        self.assertNotIn("run dish D1 --state in-progress", owed)
        self.execute_sheet(owed)
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        crossing = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("if D1 reaches the round budget and no decision is open: run 86 add --dish D1 --round-budget", crossing)
        self.assertNotIn("run dish D1 --state in-progress", crossing)
        self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_conditionals(crossing), ["86 add --dish D1 --round-budget"])

    def test_next_restarts_a_pass_with_edits_review_after_two_send_backs(self):
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        sheet = self.next_sheet("review", "D1", "--verdict", "pass-with-edits")
        self.assertIn(
            "if the old worker's wait for D1 is terminal and D1 may take a fix round: run dish D1 --state in-progress",
            sheet)
        self.assertNotIn("round-budget", sheet)
        self.assertEqual(sheet.splitlines()[-1], "reply: yes (sent-back: D1)")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), name="D1-review-3.md")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "pass-with-edits",
                                      "--author", CLAUDE, "--verifier", CODEX, "--edits", "D1-review-3.md"),
                         "D1 sent-back")
        self.assertEqual(self.execute_conditionals(sheet), ["dish D1 --state in-progress"])
        self.fresh()
        for number in range(3):
            self.brigade("pass", "record", "D1", "--sha", f"s{number}", "--verdict", "send-back",
                         "--author", CLAUDE, "--verifier", CODEX)
        at_budget = self.next_sheet("review", "D1", "--verdict", "pass-with-edits")
        self.assertNotIn("run dish D1 --state in-progress", at_budget)
        self.assertIn("run 86 add --dish D1 --round-budget", at_budget)

    def test_a_reason_is_refused_with_a_verdict_other_than_send_back(self):
        self.fired_bug_fix()
        before = (self.at / "pass.tsv").read_bytes()
        refusal = "brigade: --reason goes only with --verdict send-back"
        for verdict in ("pass", "blocked", "pass-with-edits"):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", verdict,
                                              "--author", CLAUDE, "--verifier", CODEX, "--reason", "scope", ok=False), refusal)
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)

    def test_a_reason_outside_the_list_is_refused_and_writes_no_row(self):
        self.fired_bug_fix()
        before = (self.at / "pass.tsv").read_bytes()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX, "--reason", "nonsense", ok=False)
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)
        self.assertEqual(self.pass_rows(), [])

    def test_a_send_back_stores_its_reason(self):
        self.fired_bug_fix()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX, "--reason", "missing-test")
        self.assertEqual(self.pass_rows()[-1][-1], "missing-test")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                         "brigade: D1 at abc: send-back (no note)")

    def test_a_row_written_before_the_reason_column_reads_as_empty_and_counts_as_unrecorded(self):
        self.fired_bug_fix()
        stamp = "2026-10-08T00:00:00+00:00"
        table = self.at / "pass.tsv"
        table.write_text("at\tdish\tpr\tsha\tverdict\tauthor\tverifier\tnote\treport\tmember\n"
                         + f"{stamp}\tD1\t\ts1\tsend-back\t{CLAUDE}\t{CODEX}\t\t\t\n"
                         + f"{stamp}\tD1\t\ts2\tpass\t{CLAUDE}\t{CODEX}\t\t\t\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, "--reason", "scope"), "D1 sent-back")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "s2"), f"D1 at s2 passed review by {CODEX}")
        self.assertEqual(self.brigade("pass", "stats"), "\n".join([
            "last 1 item with a verdict",
            "first pass overall: 0 of 1 (0.0%)",
            f"first pass {CLAUDE}: 0 of 1 (0.0%)",
            "send-backs: scope 1, unrecorded 1",
        ]))

    def test_pass_stats_prints_the_first_pass_rate_per_author_and_the_send_back_reasons(self):
        self.fired_bug_fix()
        self.record("abc", "pass", author=CODEX, verifier=CLAUDE)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix t")
        self.brigade("pass", "record", "D2", "--sha", "d2a", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX, "--reason", "missing-test")
        self.brigade("pass", "record", "D2", "--sha", "d2b", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "u")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "Fix u")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), name="D3-review-1.md")
        self.brigade("pass", "record", "D3", "--sha", "d3", "--verdict", "pass-with-edits",
                     "--author", CLAUDE, "--verifier", CODEX, "--edits", "D3-review-1.md")
        self.assertEqual(self.brigade("pass", "stats", "--last", "3"), "\n".join([
            "last 3 items with a verdict",
            "first pass overall: 1 of 3 (33.3%)",
            f"first pass {CLAUDE}: 0 of 2 (0.0%)",
            f"first pass {CODEX}: 1 of 1 (100.0%)",
            "send-backs: missing-test 1, unrecorded 1",
        ]))

    def test_pass_stats_prints_no_items_with_a_verdict_when_no_pass_row_exists(self):
        self.open()
        before = {p.relative_to(self.at): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.at.rglob("*") if p.is_file()}
        self.assertEqual(self.brigade("pass", "stats"), "no items with a verdict")
        self.assertEqual({p.relative_to(self.at): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.at.rglob("*") if p.is_file()}, before)


class RoundBudgetStoredTest(BrigadeTest):
    def write_budget(self, value):
        path = self.at / "restaurant.json"
        meta = json.loads(path.read_text())
        meta["roundBudget"] = value
        path.write_text(json.dumps(meta))

    def clear_budget(self):
        path = self.at / "restaurant.json"
        meta = json.loads(path.read_text())
        meta.pop("roundBudget", None)
        path.write_text(json.dumps(meta))

    def test_default_budget_owes_at_three_in_reviews_and_watch(self):
        self.fired_bug_fix()
        self.assertNotIn("roundBudget", json.loads((self.at / "restaurant.json").read_text()))
        self.assertEqual(self.sent_back(2), "D1 sent-back")
        self.assertNotIn("decision pending", self.brigade("watch"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("a3"), "D1 sent-back; 3 send-backs, decision pending")
        self.assertIn("3 send-backs, decision pending", self.brigade("watch"))
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--round-budget"), "Q1")

    def test_stored_two_forces_the_decision_at_two(self):
        self.fired_bug_fix()
        self.brigade("set", "--round-budget", "2")
        self.assertEqual(self.send_back("a1"), "D1 sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("a2"), "D1 sent-back; 2 send-backs, decision pending")
        self.assertIn("2 send-backs, decision pending", self.brigade("watch"))
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--round-budget"), "Q1")

    def test_stored_five_holds_no_decision_at_three(self):
        self.fired_bug_fix()
        self.brigade("set", "--round-budget", "5")
        self.assertEqual(self.sent_back(3), "D1 sent-back")
        self.assertNotIn("decision pending", self.brigade("watch"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        error = self.brigade("86", "add", "--dish", "D1", "--round-budget", ok=False)
        self.assertIn("opens at 5", error)

    def test_malformed_stored_budgets_are_refused_by_watch_and_86_add(self):
        self.fired_bug_fix()
        for bad, shown in (("two", "'two'"), (0, "0"), (-1, "-1"), (True, "True")):
            with self.subTest(bad=bad):
                self.write_budget(bad)
                before = self.store_files()
                wanted = (f"restaurant.json roundBudget {shown} is invalid: "
                          "it is not a whole number of 1 or more")
                self.assertIn(wanted, self.brigade("watch", code=1))
                self.assertIn(wanted, self.brigade("86", "add", "--dish", "D1", "--round-budget", code=1))
                self.assertEqual(self.store_files(), before)
                self.clear_budget()
