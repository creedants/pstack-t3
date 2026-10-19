from ..support import (
    ProcessReason,
    SCRIPT,
    _owner_words,
    real_popen,
    json,
    subprocess,
    sys,
    time,
)


class FieldsContractMixin:
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

