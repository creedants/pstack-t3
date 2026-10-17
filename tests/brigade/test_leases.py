from .support import (
    setUpModule,
    BULLETS,
    BrigadeTest,
    Path,
    SCRIPT,
    _owner_words,
    json,
    subprocess,
    sys,
)


class LeasesTest(BrigadeTest):
    def test_from_report_dry_run_changes_no_table_and_no_log_and_needs_no_owner(self):
        self.fired_bug_fix()
        self.brigade("set", "--thread", "t1")
        before = self.tables()
        self.assertIn("log.tsv", before)
        self.assertEqual(self.follow_ups_in(BULLETS, "--dry-run", owner=False).splitlines()[2],
                         "would add from perf/reports/D1.md#3: PR #87 was not commented on, approved, or closed.")
        self.assertEqual(self.follow_ups_in(BULLETS, ok=False, owner=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.tables(), before)

    def test_a_shell_reads_brief_fields_without_running_them(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        fields = self.literal_brief_fields()
        flagged = self.brief_from_flags(fields)
        work = Path(self.temporary.name) / "shell"
        work.mkdir()
        script = (
            f"FIELDS='{work / 'fields.json'}'\n"
            f"B='{sys.executable} {SCRIPT} --store {self.store} --at {self.at} {' '.join(_owner_words(self.at))}'\n"
            "cat > \"$FIELDS\" <<'JSON'\n"
            + json.dumps(fields) + "\n"
            "JSON\n"
            "$B brief D1 --fields \"$FIELDS\"\n"
        )
        result = subprocess.run(["bash", "-c", script], cwd=work, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), flagged)
        self.assertFalse((work / "sentinel").exists())
        self.assertIsNone(json.loads((self.at / "restaurant.json").read_text())["lastReportAt"])

    def test_contract_prints_the_given_text_and_one_newline_for_a_text_with_whitespace_at_its_ends_and_one_with_a_carriage_return(self):
        self.briefable()
        returned = "First line.\r\nSecond line."
        for contract in (self.VERBATIM, returned, "none"):
            for form, words in (("flag", (*self.FLAGS, "--contract", contract)), ("fields", self.contract_fields(contract))):
                with self.subTest(contract=contract, form=form):
                    brief = self.raw_stdout("brief", "D1", *words)
                    rendered = f"\nCONTRACT:\n{contract}\n\nVERIFY:\n".encode()
                    self.assertIn(rendered, brief)
                    self.assertIn(rendered, (self.at / "briefs" / "D1.md").read_bytes())
                    self.assertEqual(self.raw_stdout("contract", "D1"), (contract + "\n").encode())

    def test_set_thread_refuses_to_replace_a_recorded_thread(self):
        self.open()
        self.brigade("set", "--thread", "thread-1")
        self.brigade("set", "--thread", "thread-1")
        before = (self.at / "restaurant.json").read_bytes()
        error = self.brigade("set", "--thread", "thread-2", ok=False)
        self.assertEqual(error, "brigade: thread thread-1 already recorded")
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
        self.brigade("set", "--thread", "thread-2", "--replace")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["thread"], "thread-2")

    def write_meta(self, **changes):
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta.update(changes)
        (self.at / "restaurant.json").write_text(json.dumps(meta))

    def fired_polish(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "polish", "--summary", "Tidy the guide")
        return ("fire", "--tickets", "T1", "--station", "perf-issue", "--summary", "Tidy the guide")

    def test_a_fire_refuses_a_stored_cap_that_does_not_parse_and_names_it(self):
        fire = self.fired_polish()
        for stored, shown in (("polish=1", "'polish=1'"), ({"polish": "1"}, "'1'")):
            with self.subTest(stored=stored):
                self.write_meta(caps=stored)
                before = self.store_files()
                error = self.brigade(*fire, ok=False)
                self.assertIn(f"brigade: warning: restaurant.json caps {shown} is invalid:", error)
                self.assertEqual(self.store_files(), before)

    def test_a_fire_succeeds_when_caps_is_missing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "polish", "--summary", "Tidy the guide")
        self.assertNotIn("caps", json.loads((self.at / "restaurant.json").read_text()))
        self.assertTrue(self.brigade("fire", "--tickets", "T1", "--station", "perf-issue",
                                     "--summary", "Tidy the guide").startswith("D1"))

    def test_a_brief_refuses_a_stored_review_setting_that_does_not_parse_and_names_it(self):
        self.briefable()
        for stored, shown in (("light", "'light'"), ({"fix": "fullish"}, "'fullish'")):
            with self.subTest(stored=stored):
                self.write_meta(review=stored)
                before = self.store_files()
                error = self.brigade("brief", "D1", *self.FLAGS, "--contract", "none", ok=False)
                self.assertIn(f"brigade: warning: restaurant.json review {shown} is invalid:", error)
                self.assertEqual(self.store_files(), before)

    def test_a_brief_succeeds_when_review_is_missing(self):
        self.briefable()
        self.assertNotIn("review", json.loads((self.at / "restaurant.json").read_text()))
        self.assertTrue(self.brigade("brief", "D1", *self.FLAGS, "--contract", "none")
                        .startswith("Use the poteto-mode skill"))

    def test_set_repairs_a_stored_cap_that_does_not_parse(self):
        self.open()
        self.write_meta(caps={"polish": "1"})
        self.brigade("set", "--cap", "polish=1")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["caps"], {"polish": 1})

    def test_change_note_dir_defaults_to_unset_and_fire_leases_no_fragment(self):
        self.init_landing()
        self.landing_env()
        self.open()
        self.assertNotIn("changeNoteDir", json.loads((self.at / "restaurant.json").read_text()))
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                      "--branch", "perf/d1", "--paths", "src"),
                         "D1 (lease L1 held by perf/D1)")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], "src")

    def test_change_note_dir_set_leases_the_encoded_fragment(self):
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                      "--branch", "docs/a", "--paths", "src/a.py"),
                         "D1 (lease L1 held by perf/D1)")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], "src/a.py,changes/docs%2Fa.md")
        self.brigade("set", "--change-note-dir", "notes/sub/")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["changeNoteDir"], "notes/sub")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "t",
                                      "--branch", "docs/a%b", "--paths", "src/b.py"),
                         "D2 (lease L2 held by perf/D2)")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D2")["paths"], "src/b.py,notes/sub/docs%2Fa%25b.md")

    def test_set_change_note_dir_refuses_a_bad_flag_value_and_writes_nothing(self):
        self.open()
        for text in ("/abs", "../up", "a/../..", "a,b", "has space"):
            with self.subTest(text=text):
                before = self.store_files()
                error = self.brigade("set", "--change-note-dir", text, ok=False)
                self.assertIn("--change-note-dir", error)
                self.assertIn(f"got '{text}'", error)
                self.assertEqual(self.store_files(), before)

    def test_a_fire_refuses_a_stored_change_note_dir_that_does_not_parse_and_claims_no_lease(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        for stored in ("/abs", "../up", "a,b", "has space", 5, ["changes"]):
            with self.subTest(stored=stored):
                self.write_meta(changeNoteDir=stored)
                before = self.store_files()
                error = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                     "--paths", "src", ok=False)
                self.assertIn("changeNoteDir", error)
                self.assertEqual(self.store_files(), before)

    def test_set_refuses_a_malformed_change_note_dir_while_changing_another_setting(self):
        self.open()
        self.write_meta(changeNoteDir="/abs")
        before = self.store_files()
        error = self.brigade("set", "--reporting", "digest", ok=False)
        self.assertIn("changeNoteDir", error)
        self.assertEqual(self.store_files(), before)

    def test_set_clears_change_note_dir_with_an_empty_value(self):
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["changeNoteDir"], "changes")
        self.brigade("set", "--change-note-dir", "")
        self.assertNotIn("changeNoteDir", json.loads((self.at / "restaurant.json").read_text()))
