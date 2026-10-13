from .support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    Path,
    ROOT,
    ProcessReason,
    SCRIPT,
    _owner_words,
    real_popen,
    _shown_root,
    _wait_for_path,
    json,
    os,
    runpy,
    subprocess,
    sys,
    time,
)


class BriefsTest(BrigadeTest):
    def test_brief_assembles_every_field_and_adds_the_exclusive_rule_for_measuring_stations(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "Cold start under 400 ms.", "--acceptance", "Median cold start below 400 ms",
                            "--verify", "npm run perf", "--paths", "src/boot.ts", "--lease", "L4", "--base", "origin/main")
        self.assertTrue(text.startswith("Use the poteto-mode skill and its `perf-issue` playbook."))
        report = f"{self.at}/reports/D1.md"
        for part in ("PURPOSE: Make startup fast.", "TICKETS: T1: Startup is slow", "branch `perf/d1`, started from `origin/main`",
                     "leased to you as L4: src/boot.ts", "- Median cold start below 400 ms", "slot --exclusive --",
                     "A later commit that changes only prose does not rerun it.",
                     "TIMEBOX: 60 minutes. The timebox orders the work and never waives a playbook step (How, Architect, investigation, or the implementation delegate). At the limit, write the report with what remains instead of skipping steps.",
                     f"Write it to {report}", "1. Write in plain engineering prose.",
                     f'call t3_thread_send to thread thread-coord with mode "auto" and the one-line message "D1 done: report at {report}".'):
            self.assertIn(part, text)
        orders = text.split("STANDING ORDERS:\n", 1)[1]
        numbered = orders.split("\n\n")[-1].splitlines()
        self.assertEqual(len(numbered), 10)
        self.assertEqual(numbered[0], "1. A test earns its place when it fails for a defect that no other check reports; name that defect in the test name.")
        self.assertEqual(numbered[-1], "10. The reviewer asks of each new test: which change to the code makes this fail and nothing else fail?")
        verify = text.split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        for part in ("While working, run only the affected test modules.",
                     "Run all builds and tests through",
                     "slot -- <command>",
                     "Run the full verification chain once, after the last commit."):
            self.assertIn(part, verify)
        self.assertNotIn("Run builds and tests through", verify)
        self.assertEqual((self.at / "briefs/D1.md").read_text().strip(), text)

    def test_brief_refuses_when_no_coordinator_thread_is_recorded(self):
        self.open()
        self.fire_one(station="bug-fix")
        error = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v",
                             "--paths", "a", "--lease", "L1", "--base", "origin/main", ok=False)
        self.assertIn("brigade.py set --thread", error)
        self.assertFalse((self.at / "briefs" / "D1.md").exists())

    def test_brief_refuses_a_menu_without_a_purpose_or_a_missing_acceptance(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("set", "--thread", "thread-coord")
        args = ["brief", "D1", "--contract", "none", "--goal", "g", "--verify", "v", "--paths", "a", "--lease", "L1", "--base", "origin/main"]
        self.assertIn("menu.md has no purpose yet", self.brigade(*args, "--acceptance", "a", ok=False))
        (self.at / "menu.md").write_text("## Purpose\n\nFix bugs.\n")
        self.assertIn("at least one --acceptance", self.brigade(*args, ok=False))
        self.assertNotIn("slot --exclusive", self.brigade(*args, "--acceptance", "a"))

    def test_fire_claims_the_lease_and_a_refused_claim_fires_nothing(self):
        import os
        from unittest import mock
        run = lambda *a: subprocess.run(a, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        (self.project / "a.txt").write_text("a\n")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        land = ROOT / "t3/added/landing/scripts/land.py"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.temporary.name) / "state")}):
            subprocess.run([sys.executable, str(land), "--repo", str(self.project), "init", "--trunk", "lane", "--mode", "local", "--base", "main"],
                           check=True, capture_output=True)
            self.open()
            (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"),
                             "D1 (lease L1 held by perf/D1)")
            self.assertIn("nothing fired: paths overlap L1 held by perf/D1",
                          self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "s", "--paths", "src/x.py", ok=False))
            self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] two blocked: lease")
            self.brigade("set", "--thread", "thread-coord")
            text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "refs/landing/lane")
            self.assertIn("leased to you as L1: src,changes/perf%2Fd1.md.", text)
            self.assertIn("branch `perf/d1`", text)

    def test_brief_fields_file_matches_flags_and_keeps_shell_text_literal(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        fields = self.literal_brief_fields()
        flagged = self.brief_from_flags(fields)
        path = self.at / "brief-fields.json"
        path.write_text(json.dumps(fields), encoding="utf-8")
        filed = self.brigade("brief", "D1", "--fields", str(path))
        self.assertEqual(filed, flagged)
        goal = fields["goal"]
        self.assertIn(goal, filed)
        for part in ("`", "$B status", "$B close", "$(touch sentinel)"):
            self.assertIn(part, filed)
        self.assertIsNone(json.loads((self.at / "restaurant.json").read_text())["lastReportAt"])
        self.assertEqual(list((self.at / "closeouts").glob("*")), [])

    def test_brief_fields_stdin_matches_the_file(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        fields = self.literal_brief_fields()
        raw = json.dumps(fields)
        path = self.at / "brief-fields.json"
        path.write_text(raw, encoding="utf-8")
        filed = self.brigade("brief", "D1", "--fields", str(path))
        piped = self.brigade("brief", "D1", "--fields", "-", stdin=raw)
        self.assertEqual(piped, filed)
        self.assertIn("$(touch sentinel)", piped)

    def test_a_brief_waiting_on_stdin_leaves_the_store_to_other_commands(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        # race between two processes
        waiting = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
             *_owner_words(self.at), "brief", "D1", "--fields", "-"],
            stdin=subprocess.PIPE,
        )
        try:
            time.sleep(0.3)
            self.assertIsNone(waiting.poll())
            others = []
            for args in (("status",), ("watch",), ("ticket", "add", "--kind", "fix", "--summary", "second")):
                # race between two processes
                others.append(real_popen(
                    ProcessReason.RACE,
                    [sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                     *_owner_words(self.at), *args],
                ))
            outputs = [other.communicate(timeout=10) for other in others]
            self.assertEqual([other.returncode for other in others], [0, 0, 0], outputs)
            self.assertEqual(outputs[2][0].strip(), "T2")
            out, err = waiting.communicate(json.dumps(self.literal_brief_fields()), timeout=10)
        finally:
            waiting.kill()
        self.assertEqual(waiting.returncode, 0, err)
        self.assertIn("TICKETS: T1: Startup is slow", out)

    def test_brief_fields_rejects_a_mix_and_bad_json(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        brief = self.at / "briefs" / "D1.md"
        fields = self.at / "brief-fields.json"
        error = self.brigade("brief", "D1", "--fields", str(fields), "--goal", "g", ok=False)
        self.assertIn("use either --fields or the field flags, not both", error)
        self.assertFalse(brief.exists())
        missing = self.at / "no-such-fields.json"
        error = self.brigade("brief", "D1", "--fields", str(missing), ok=False)
        self.assertIn("brief fields:", error)
        self.assertIn("no-such-fields.json", error)
        self.assertFalse(brief.exists())
        fields.write_text("[]\n", encoding="utf-8")
        error = self.brigade("brief", "D1", "--fields", str(fields), ok=False)
        self.assertIn("JSON must be an object", error)
        self.assertFalse(brief.exists())
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "contract": "none", "verify": "v", "base": "main", "nope": "x"}),
                          encoding="utf-8")
        error = self.brigade("brief", "D1", "--fields", str(fields), ok=False)
        self.assertIn("unknown key nope", error)
        self.assertFalse(brief.exists())
        fields.write_text(json.dumps({"goal": "g", "acceptance": "a", "contract": "none", "verify": "v", "base": "main"}), encoding="utf-8")
        error = self.brigade("brief", "D1", "--fields", str(fields), ok=False)
        self.assertIn("acceptance must be a list of strings", error)
        self.assertFalse(brief.exists())

    def test_brief_refuses_flags_with_no_contract_or_a_contract_of_only_whitespace(self):
        self.briefable()
        self.assertEqual(self.brigade("brief", "D1", *self.FLAGS, ok=False), self.NEEDS_CONTRACT)
        self.assertEqual(self.brigade("brief", "D1", *self.FLAGS, "--contract", " \n\t", ok=False), self.NEEDS_CONTRACT)
        self.assertFalse((self.at / "briefs" / "D1.md").exists())
        self.assertFalse((self.at / "briefs" / "D1.contract.md").exists())

    def test_brief_refuses_a_fields_file_that_has_goal_acceptance_verify_and_base_and_no_contract(self):
        self.briefable()
        fields = self.at / "brief-fields.json"
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": "origin/main"}),
                          encoding="utf-8")
        self.assertEqual(self.brigade("brief", "D1", "--fields", str(fields), ok=False), self.NEEDS_CONTRACT)
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": "origin/main",
                                      "contract": "  "}), encoding="utf-8")
        self.assertEqual(self.brigade("brief", "D1", "--fields", str(fields), ok=False), self.NEEDS_CONTRACT)
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": "origin/main",
                                      "contract": None}), encoding="utf-8")
        self.assertEqual(self.brigade("brief", "D1", "--fields", str(fields), ok=False),
                         "brigade: brief fields: contract is null")
        self.assertFalse((self.at / "briefs" / "D1.md").exists())

    def test_brief_refuses_a_contract_flag_next_to_a_fields_file(self):
        self.briefable()
        fields = self.at / "brief-fields.json"
        fields.write_text(json.dumps(self.literal_brief_fields()), encoding="utf-8")
        self.assertEqual(self.brigade("brief", "D1", "--fields", str(fields), "--contract", "none", ok=False),
                         "brigade: use either --fields or the field flags, not both")

    def test_brief_renders_the_contract_none_between_acceptance_and_verify(self):
        self.briefable()
        text = self.brigade("brief", "D1", *self.FLAGS, "--contract", "none")
        self.assertIn("\nACCEPTANCE:\n- a\n\nCONTRACT:\nnone\n\nVERIFY:\n- v\n", text)
        self.assertEqual((self.at / "briefs" / "D1.contract.md").read_text(), "none\n")

    def test_brief_renders_and_stores_a_contract_as_given_from_the_flag_and_from_a_fields_file(self):
        self.briefable()
        for form, words in (("flag", (*self.FLAGS, "--contract", self.VERBATIM)), ("fields", self.contract_fields(self.VERBATIM))):
            with self.subTest(form=form):
                text = self.brigade("brief", "D1", *words)
                self.assertIn(f"\nACCEPTANCE:\n- a\n\nCONTRACT:\n{self.VERBATIM}\n\nVERIFY:\n- v\n", text)
                self.assertEqual((self.at / "briefs" / "D1.contract.md").read_bytes(), (self.VERBATIM + "\n").encode())
                (self.at / "briefs" / "D1.contract.md").unlink()

    def test_contract_prints_the_text_a_brief_stored_from_flags_and_from_a_fields_file(self):
        self.briefable()
        contract = "A refused write changes no table.\n\n  - `log.tsv` gains no row."
        self.brigade("brief", "D1", *self.FLAGS, "--contract", contract)
        self.assertEqual(self.brigade("contract", "D1"), contract)
        fields = self.at / "brief-fields.json"
        other = "A reader never sees a half-written row.\nNothing else."
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": "origin/main",
                                      "paths": "src/boot.ts", "lease": "L4", "contract": other}), encoding="utf-8")
        self.brigade("brief", "D1", "--fields", str(fields))
        self.assertEqual(self.brigade("contract", "D1"), other)

    def test_contract_refuses_an_item_with_no_contract_file_and_an_item_that_does_not_exist(self):
        self.briefable()
        self.brigade("brief", "D1", *self.FLAGS, "--contract", "none")
        (self.at / "briefs" / "D1.contract.md").unlink()
        self.assertEqual(self.brigade("contract", "D1", ok=False),
                         "brigade: D1 has no contract on record; run brief with --contract")
        self.assertEqual(self.brigade("contract", "D9", ok=False), "brigade: no D9 in dishes.tsv")

    def test_fire_leases_the_branch_changelog_fragment_and_dish_records_a_new_lease(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
            self.brigade("set", "--thread", "thread-coord")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                         "--branch", "docs/a", "--paths", "src/a.py")
            text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")
            self.assertIn("leased to you as L1: src/a.py,changes/docs%2Fa.md.", text)
            self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "s",
                         "--branch", "docs/a%b", "--paths", "src/b.py")
            text = self.brigade("brief", "D2", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")
            self.assertIn("leased to you as L2: src/b.py,changes/docs%2Fa%25b.md.", text)
            self.assertEqual(text.count("changes/docs%2Fa%25b.md"), 1)
            self.brigade("dish", "D1", "--lease", "L9", "--paths", "src/a.py,changes/docs%2Fa.md")
            text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")
            self.assertIn("leased to you as L9: src/a.py,changes/docs%2Fa.md.", text)

    def test_brief_refuses_an_item_that_owes_its_decision(self):
        self.started()
        self.brigade("set", "--thread", "c1")
        self.sent_back(2)
        self.assertIn("GOAL: g\n", self.brigade(*self.FIX_BRIEF))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.send_back("a3")
        written = (self.at / "briefs" / "D1.md").read_bytes()
        before = self.unchanged()
        self.assertEqual(self.brigade(*self.FIX_BRIEF, ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--verify", "v", "--base", "origin/main", ok=False),
                         self.NO_FIX_ROUND)
        self.unchanged(before)
        self.brigade("dish", "D1", "--state", "in-review")
        self.assertEqual(self.brigade(*self.FIX_BRIEF, ok=False), self.NO_FIX_ROUND)
        self.assertEqual((self.at / "briefs" / "D1.md").read_bytes(), written)
        self.brigade(*self.ROUND_BUDGET)
        self.brigade("86", "answer", "Q1", "--answer", "redesign")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertIn("GOAL: g\n", self.brigade(*self.FIX_BRIEF))

    def test_the_brief_pastes_the_runtime_seat_rule_unchanged(self):
        runtime = (ROOT / "t3/runtime.md").read_text()
        self.assertIn(f"  ```text\n  {self.SEAT_RULE}\n  ```", runtime)

    def test_the_runtime_still_states_the_bounded_wait(self):
        runtime = (ROOT / "t3/runtime.md").read_text()
        self.assertIn("does not end its turn while a child is open", runtime)
        self.assertIn("Decide completion by `workState` alone", runtime)
        self.assertIn("Its finished turn does not wake the launcher.", runtime)
        self.assertIn("interrupt with `t3_thread_interrupt`", runtime)

    def test_wait_rule_waits_out_an_ended_run(self):
        rule = runpy.run_path(str(SCRIPT))["WAIT_RULE"]
        for phrase in (
            "returns at once while workState is still working or waiting_for_children",
            "t3_thread_wait and timeoutMs 120000 on your own thread",
            "with runId set to its activeRunId from t3_thread_read",
            "never a shell sleep",
            "whose run is still open and whose last message holds the result is stalled after two minutes with no new activity, per step 5",
            "count ten minutes from its last activity item",
        ):
            self.assertIn(phrase, rule)
        self.assertEqual(rule, self.WAIT)

    def test_the_report_section_opens_with_the_bounded_wait(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        lines = text.splitlines()
        start = lines.index("REPORT:")
        write = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("- Write it to "))
        self.assertEqual(lines[start:write + 1], ["REPORT:", self.WAIT, lines[write]])

    def test_the_report_section_names_each_file_by_repository_path(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        self.assertIn("Name each file a follow-up touches by its repository path in backticks.", text)
        write = next(line for line in text.splitlines() if line.startswith("- Write it to "))
        self.assertTrue(write.endswith("Name each file a follow-up touches by its repository path in backticks."))

    def test_an_orchestrate_brief_carries_the_same_bounded_wait(self):
        self.coordinator(station="orchestrate", mode="full")
        text = self.brigade(*self.BRIEF)
        self.assertTrue(text.startswith("Use the poteto-mode skill and its `orchestrate` playbook.\n"), text[:80])
        self.assertEqual(text.split("REPORT:\n", 1)[1].splitlines()[0], self.WAIT)

    def test_a_first_light_feature_brief_prints_the_first_waivers(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: light", "Mode source: restaurant.json", "Attempt: first",
            "Waived by mode: Arena, Interrogate, Comment Sicko"))
        report = text.split("REPORT:\n", 1)[1].splitlines()
        self.assertEqual(report[2:4], [self.REPEAT, self.CONTESTED])
        self.assertTrue(report[4].startswith("- After that file is written, call t3_thread_send"), report[4])
        self.assertEqual((self.at / "briefs/D1.md").read_text().strip(), text)
        self.assertEqual(self.mode_rows(), [])

    def test_a_brief_without_a_mode_prints_the_default(self):
        self.coordinator(mode=None)
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: full", "Mode source: default", "Attempt: first"))
        self.assertIn(self.REPEAT, text)
        self.assertNotIn("Contested:", text)

    def test_one_send_back_briefs_a_fix_attempt(self):
        self.coordinator()
        self.record("a1", "send-back")
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: light", "Mode source: restaurant.json", "Attempt: fix",
            "Waived by mode: How, Architect, Arena, Interrogate, Comment Sicko"))

    def test_a_replaced_worker_keeps_the_attempt_and_adds_no_send_back(self):
        self.coordinator()
        self.brigade("dish", "D1", "--thread", "w2")
        self.brigade("dish", "D1", "--thread", "w3")
        self.assertIn("Mode: light\nMode source: restaurant.json\nAttempt: first\n", self.brigade(*self.BRIEF))
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.brigade("dish", "D1", "--thread", "w4")
        self.brigade("dish", "D1", "--thread", "w5")
        self.assertIn("Mode: light\nMode source: restaurant.json\nAttempt: fix\n", self.brigade(*self.BRIEF))
        self.assertEqual(self.mode_rows(), [])

    def test_member_send_backs_do_not_move_the_item_to_full(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back", "--member", verifier="grok/grok-4.7")
        self.record("a2", "send-back", "--member", verifier="opencode/kimi-k3")
        self.assertIn("Mode: light\nMode source: restaurant.json\nAttempt: fix\n", self.brigade(*self.BRIEF))
        self.assertEqual(self.mode_rows(), [])

    def test_a_late_send_back_counts_and_leaves_the_fix_attempt_running(self):
        self.coordinator()
        self.record("a2", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "a1", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, "--note", "round 1", "--late"),
                         "D1: late send-back on record; D1 stays in-progress")
        self.assertEqual(self.dish_fields("state", "pr", "sha", "thread"), ("in-progress", "", "a2", ""))
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "a1", ok=False),
                         "brigade: D1 at a1: send-back (round 1)")
        self.assertEqual(self.pass_rows()[1][3:], ["a1", "send-back", CLAUDE, CODEX, "round 1", "", ""])
        self.assertIn("Mode: full\nMode source: escalated: second send-back\nAttempt: fix\n", self.brigade(*self.BRIEF))

    def test_a_fix_brief_names_the_report_of_the_latest_send_back(self):
        self.coordinator()
        first, second, panel = (self.review_file(f"D1-review-{name}.md") for name in ("1", "2", "2-panel-2"))
        line = "- A reviewer sent an earlier attempt back. Its findings: "
        self.record("a1", "send-back", "--report", first.name)
        self.assertIn(f"{line}{first}\n", self.brigade(*self.BRIEF))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back", "--report", second.name)
        self.record("a2", "send-back", "--member", "--report", panel.name, verifier="grok/grok-4.7")
        self.assertIn(f"{line}{second}\n", self.brigade(*self.BRIEF))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a3", "send-back")
        self.brigade("86", "add", "--dish", "D1", "--round-budget")
        self.brigade("86", "answer", "Q1", "--answer", "known limits")
        self.assertNotIn(line, self.brigade(*self.BRIEF))
        plain = self.review_file("D1-review.md")
        self.assertIn(f"{line}{plain}\n", self.brigade(*self.BRIEF))

    def test_a_queue_bounce_briefs_a_bounce_attempt(self):
        self.coordinator(station="bug-fix")
        self.record("a1", "pass")
        self.brigade("dish", "D1", "--state", "queued")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade(*self.BRIEF).split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/bug-fix.md", "Mode: light", "Mode source: restaurant.json", "Attempt: bounce",
            "Waived by mode: How, Why, Architect, Comment Sicko", station="bug-fix"))

    def test_the_escalation_lives_in_the_log_and_a_stale_write_changes_no_row(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back")
        self.brigade(*self.BRIEF)
        (self.at / "pass.tsv").write_text("\t".join(("at", "dish", "pr", "sha", "verdict", "author", "verifier", "note")) + "\n")
        self.brigade("set", "--mode", "light")
        self.assertIn("Mode: full\nMode source: escalated: second send-back\n", self.brigade(*self.BRIEF))
        self.brigade("set", "--thread", "c2", "--replace")
        before = (self.at / "log.tsv").read_bytes()
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "late", ok=False),
                         "brigade: owner c1@1 is stale; this store is owned by c2@2")
        self.assertEqual(self.brigade("--owner", "c1@1", *self.BRIEF, ok=False),
                         "brigade: owner c1@1 is stale; this store is owned by c2@2")
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)

    def test_set_mode_leaves_a_written_brief_unchanged(self):
        self.coordinator(mode="full")
        self.brigade(*self.BRIEF)
        written = (self.at / "briefs/D1.md").read_bytes()
        self.brigade("set", "--mode", "light")
        self.assertEqual((self.at / "briefs/D1.md").read_bytes(), written)
        self.assertIn("Mode: light\n", self.brigade(*self.BRIEF))

    def test_fire_mode_full_carries_its_reason_into_every_brief(self):
        self.coordinator("bug-fix", "light", "--mode", "full", "--reason", "tickets name a lock")
        self.assertIn("Mode: full\nMode source: escalated: tickets name a lock\nAttempt: first\nGate: brigade\n",
                      self.brigade(*self.BRIEF))
        self.assertEqual(len(self.mode_rows()), 1)

    def test_a_lease_over_a_configured_path_escalates_once(self):
        self.git_project()
        (self.project / ".pstack").mkdir()
        (self.project / ".pstack/t3-roles.json").write_text(json.dumps({"escalate": ["src/lock.py"]}))
        self.coordinator()
        brief = [*self.BRIEF]
        brief[brief.index("src/a.py")] = "src/lock.py"
        self.assertIn("Mode: full\nMode source: escalated: lease covers src/lock.py\n", self.brigade(*brief))
        self.assertIn("Mode: full\nMode source: escalated: lease covers src/lock.py\n", self.brigade(*self.BRIEF))
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D1", "full", "lease covers src/lock.py"]])

    def test_a_station_that_is_not_a_playbook_briefs_only_the_mode(self):
        self.coordinator(station="correct")
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head("Mode: light", "Mode source: restaurant.json", station="correct"))

    def test_roles_mode_refuses_to_run_under_a_store_lock(self):
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        glob["HELD_LOCKS"].append(Path("/store/held"))
        with self.assertRaises(glob["BrigadeError"]) as caught:
            glob["roles_mode"](None)
        self.assertEqual(str(caught.exception),
                         "roles.py may run git; release /store/held's store lock before resolving the mode")

    def test_a_roles_failure_refuses_the_brief_and_writes_nothing(self):
        self.coordinator()
        config = Path(os.environ["XDG_CONFIG_HOME"]) / "pstack-t3" / "roles.json"
        config.parent.mkdir(parents=True)
        config.write_text("{")
        before = (self.at / "log.tsv").read_bytes()
        error = self.brigade(*self.BRIEF, ok=False)
        self.assertTrue(error.startswith(f"brigade: roles.py mode refused the brief: {config}: invalid JSON"), error)
        self.assertFalse((self.at / "briefs/D1.md").exists())
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)

    def test_two_briefs_racing_append_one_mode_row(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back")
        case = Path(self.temporary.name) / "race"
        first = self.paused_brief(case)
        _wait_for_path(case / "paused", timeout=20)
        second = self.brigade(*self.BRIEF)
        (case / "proceed").touch()
        code, out, err = self._finish_race(first)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, second)
        self.assertEqual(len(self.mode_rows()), 1)

    def test_a_fix_brief_points_at_the_report_of_a_pass_with_edits_round(self):
        self.briefable()
        self.review_file("D1-review-1.md", "findings\n")
        self.send_back("a1", "--report", "D1-review-1.md")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), name="D1-review-2.md")
        self.record_edits("a2", "--edits", "D1-review-2.md")
        self.brigade("dish", "D1", "--state", "in-progress")
        text = self.brigade("brief", "D1", *self.FLAGS, "--contract", "none")
        self.assertIn(f"\n- A reviewer sent an earlier attempt back. Its findings: {self.at}/reports/D1-review-2.md\n", text)
        self.assertNotIn("D1-review-1.md", text)

    def test_ticket_list_startable_watch_86_list_close_walk_pass_check_and_contract_print_for_a_store_from_before_kinds_what_the_tool_printed_before_kinds_and_change_no_table(self):
        self.store_from_before_kinds()
        before = self.tables()
        waiting = ["T8 waiting normal [user] Importer crashes",
                   "T9 waiting normal [user] Tune the pool",
                   "T10 waiting low [report] Tidy the guide perf/reports/D7.md#1",
                   "T11 waiting urgent [user] Pick a database",
                   "T12 waiting low [user] Trim the pool"]
        self.assertEqual(self.brigade("ticket", "list").splitlines(), [
            "T1 assigned normal [user] Cold start is slow",
            "T2 assigned urgent [github] Splash hangs https://github.com/o/r/issues/2",
            "T3 assigned normal [user] Cache misses",
            "T4 assigned normal [user] Login loops",
            "T5 assigned normal [user] Export stalls",
            "T6 assigned low [user] Footer overlaps",
            "T7 done normal [user] Dates are wrong",
            *waiting,
            "T13 dropped normal [user] Old idea"])
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), waiting)
        self.assertEqual(self.brigade("startable").splitlines(), [
            self.OLD_WORKERS,
            "waiting tickets: 5, blocked: 1, unknown: 1, decisions: 1, below auto-start: 2",
            "T11 urgent, 0m: decision for the owner",
            "T8 normal, 0m: unknown, no paths recorded",
            "T9 normal, 0m: blocked, waiting for an idle worker; a.txt",
            "T10 low, 0m: below auto-start; docs/guide.md",
            "T12 low, 0m: below auto-start; a.txt"])
        self.assertEqual(self.brigade("watch").splitlines(), [
            self.OLD_WORKERS, "D1: running 0m of 60m (thread w1)", "D2: in review", "D3: passed, not submitted", "D4: sent back",
            "D5: parked", "D6: queued, but no entry at s6"])
        self.assertEqual(self.brigade("86", "list"), "Q1: Drop Windows 7 support? Options: yes, no. Default: no.")
        code, report, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(report.splitlines(), [
            "# Perf report", "",
            f"Since opening. {self.OLD_COUNTS}.", "",
            "## Merged", "", "- D7 (T7): Fix the dates https://github.com/o/r/pull/7", "",
            "## Passed review, not submitted", "", "- D3 (T3): Fix the cache", "",
            "## Waiting to land", "", "- D6 (T6): Fix the footer", "",
            "## Sent back after review", "", "- D4 (T4): Fix the login", "",
            "## Blocked", "", "- D5 (T5): Rework the export", "",
            "## In progress", "", "- D1 (T1): Fix the cold start", "- D2 (T2): Fix the splash https://github.com/o/r/pull/2", "",
            "## New tickets, not started", "",
            "- T8: Importer crashes", "- T9: Tune the pool", "- T10: Tidy the guide", "- T11: Pick a database", "- T12: Trim the pool", "",
            "## Dropped", "", "- T13: Old idea", "- D8 (T8): Split the importer", "",
            "## Decisions for you", "", "- Q1: Drop Windows 7 support? Options: yes, no. Default if no answer: no."])
        self.assertEqual(self.brigade("walk").splitlines(), [
            f"{_shown_root(self.project)}: local mode onto refs/landing/lane. empty.",
            f"  Perf (reports milestones): {self.OLD_COUNTS}",
            "    thread not recorded",
            "    Q1: Drop Windows 7 support?"])
        self.assertEqual(self.brigade("pass", "check", "D3", "--sha", "s3"), f"D3 at s3 passed review by {CODEX}")
        self.assertEqual(self.brigade("pass", "check", "D2", "--sha", "s2", ok=False), "brigade: D2 has no review verdict for s2")
        self.assertEqual(self.brigade("contract", "D1", ok=False), "brigade: D1 has no contract on record; run brief with --contract")
        self.assertEqual(self.tables(), before)
        self.assertFalse((self.at / "goals.tsv").exists())
