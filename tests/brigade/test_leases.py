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
