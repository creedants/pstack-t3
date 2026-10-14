from .support import (
    setUpModule,
    BULLETS,
    BrigadeTest,
    Path,
    ROOT,
    SCRIPT,
    _owner_words,
    json,
    subprocess,
    sys,
)

sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))

from brigade_lib.leases import Flight, KindLimits, admit, flight_of, kind_limits, with_item  # noqa: E402
from brigade_lib.tickets import Goal, Label  # noqa: E402


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

    def test_kind_limits_reads_a_legal_caps_object_and_a_legal_review_object(self):
        meta = {"caps": {"polish": 1, "study": 2}, "review": {"feature": "full", "fix": "light"}}
        self.assertEqual(kind_limits(meta), KindLimits({"polish": 1, "study": 2}, {"feature": "full", "fix": "light"}))

    def test_kind_limits_drops_an_entry_the_set_flags_refuse(self):
        # set --cap and set --review refuse each of these, so an entry this drops was written by hand.
        meta = {"caps": {"chore": 2, "polish": 0, "fix": 1.5, "study": True, "upkeep": "2"},
                "review": {"polish": "deep", "chore": "full", "fix": "light"}}
        self.assertEqual(kind_limits(meta), KindLimits({}, {"fix": "light"}))
        self.assertEqual(kind_limits({"caps": "polish=1", "review": None}), KindLimits({}, {}))
        self.assertEqual(kind_limits({}), KindLimits({}, {}))

    def test_flight_of_counts_one_pair_per_kind_and_per_goal_and_skips_an_empty_kind_or_goal(self):
        flight = flight_of([("fix", "G1"), ("fix", "G1"), ("polish", ""), ("", "G2"), ("", "")])
        self.assertEqual((flight.by_kind, flight.by_goal), ({"fix": 2, "polish": 1}, {"G1": 2, "G2": 1}))
        self.assertEqual(flight_of([]), Flight({}, {}))

    def test_with_item_raises_one_count_for_the_items_kind_and_goal(self):
        flight = Flight({"fix": 1}, {"G1": 1})
        self.assertEqual(with_item(flight, Label("fix", "G1")), Flight({"fix": 2}, {"G1": 2}))
        self.assertEqual(with_item(flight, Label("study", "G2")), Flight({"fix": 1, "study": 1}, {"G1": 1, "G2": 1}))
        self.assertEqual(with_item(flight, Label("", "")), flight)
        self.assertEqual(with_item(flight, Label("study", "")), Flight({"fix": 1, "study": 1}, {"G1": 1}))

    def test_admit_holds_a_paused_goal_before_a_kind_cap_and_a_cap_before_a_goal_share(self):
        paused = Goal("G1", "paused", "name", "purpose", "done", "3", "")
        opened = Goal("G1", "open", "name", "purpose", "done", "3", "")
        limits, flight = KindLimits({"polish": 1}, {}), Flight({"polish": 1}, {"G1": 3})
        self.assertEqual([admit(Label("polish", "G1"), goal, flight, limits).standing for goal in (paused, opened)],
                         ["paused", "cap"])
        self.assertEqual(admit(Label("fix", "G1"), opened, flight, limits).standing, "share")
        self.assertEqual(admit(Label("polish", "G1"), opened, Flight({}, {}), limits), None)
        # Unassigned work is not under the paused goal, the way Refill.hold reads it.
        self.assertIsNone(admit(Label("fix", ""), None, flight, limits))
