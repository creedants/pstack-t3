from ..support import BULLETS, Path, ProcessReason, SCRIPT, fcntl, os, real_popen, sys, time

class ReportInputMixin:
    def test_from_report_dry_run_waits_for_a_writer_that_holds_the_store_lock(self):
        self.fired_bug_fix()
        self.review_file("D1.md", BULLETS)
        held = os.open(self.at / "restaurant.lock", os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, held)
        fcntl.flock(held, fcntl.LOCK_EX)
        # lock held by the test process
        dry_run = real_popen(
            ProcessReason.OTHER_LOCK,
            [sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
             "ticket", "add", "--from-report", "reports/D1.md", "--dry-run"],
        )
        try:
            time.sleep(0.3)
            self.assertIsNone(dry_run.poll())
            fcntl.flock(held, fcntl.LOCK_UN)
            out, err = dry_run.communicate(timeout=10)
        finally:
            dry_run.kill()
        self.assertEqual([line.split(":")[0] for line in out.splitlines()],
                         ["would add from perf/reports/D1.md#1",
                          "not filed, D1.md takes one ticket per item",
                          "not filed, D1.md takes one ticket per item"], err)

    def test_from_report_skips_the_second_of_two_items_with_the_same_text(self):
        self.fired_bug_fix()
        twice = "## Follow-ups\n\n- Correctness: Same thing.\n- Correctness: same  THING.\n- Correctness: Other thing.\n"
        self.assertEqual(self.follow_ups_in(twice, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: Same thing.",
            "skipped perf/reports/D1.md#2, same text as #1 above: Correctness: same  THING.",
            "not filed, D1.md takes one ticket per item: Correctness: Other thing.",
        ])
        self.assertEqual(self.follow_ups_in(twice).splitlines(), [
            "T2 added from perf/reports/D1.md#1: Correctness: Same thing.",
            "skipped perf/reports/D1.md#2, same text as T2 (waiting): Correctness: same  THING.",
            "not filed, D1.md takes one ticket per item: Correctness: Other thing.",
        ])

    def test_from_report_dry_run_prints_a_list_item_with_no_text_as_not_filed(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- \n- Correctness: One thing.\n", "--dry-run"),
                         "would add from perf/reports/D1.md#1: Correctness: One thing.\nnot filed, list item with no text")

    def test_from_report_takes_the_report_by_name_or_by_a_path_into_this_store(self):
        self.fired_bug_fix()
        path = self.review_file("D1.md", "## Follow-ups\n\n- Correctness: One thing.\n")
        for spelling in ("D1.md", "reports/D1.md", str(path)):
            self.assertEqual(self.brigade("ticket", "add", "--from-report", spelling, "--dry-run"),
                             "would add from perf/reports/D1.md#1: Correctness: One thing.", spelling)

    def test_from_report_refuses_what_is_not_an_item_report_of_this_store(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n- One thing.\n"
        self.review_file("D1.md", body)
        before = (self.at / "rail.tsv").read_bytes()
        add = ("ticket", "add", "--from-report")
        for name in ("D1-review-1.md", "notes.md", "D1-attempt1.md"):
            self.review_file(name, body)
            self.assertEqual(self.brigade(*add, f"reports/{name}", ok=False),
                             f"brigade: {name} is not an item report; name a file like reports/D2.md")
        elsewhere = Path(self.temporary.name) / "elsewhere" / "D1.md"
        elsewhere.parent.mkdir()
        elsewhere.write_text(body)
        self.assertEqual(self.brigade(*add, str(elsewhere), ok=False),
                         f"brigade: {elsewhere} is outside this store's reports/; name reports/D1.md")
        self.assertEqual(self.brigade(*add, "reports/D7.md", ok=False),
                         "brigade: reports/D7.md does not exist; nothing added")
        self.review_file("D9.md", body)
        self.assertEqual(self.brigade(*add, "reports/D9.md", ok=False), "brigade: no D9 in dishes.tsv")
        (self.at / "reports" / "D1.md").write_bytes(b"## Follow-ups\n\n- caf\xe9\n")
        self.assertEqual(self.brigade(*add, "reports/D1.md", ok=False),
                         "brigade: reports/D1.md is not UTF-8 text; nothing added")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_refuses_a_directory_named_like_an_item_report(self):
        self.fired_bug_fix()
        (self.at / "reports" / "D1.md").mkdir(parents=True)
        before = (self.at / "rail.tsv").read_bytes()
        for extra in ((), ("--dry-run",)):
            self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md", *extra, ok=False),
                             "brigade: reports/D1.md is not a regular file; nothing added")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_refuses_a_symbolic_link_and_a_file_that_resolves_outside_reports(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n- Outside file was read.\n"
        report = self.at / "reports" / "D1.md"
        outside = Path(self.temporary.name) / "outside"
        (outside / "reports").mkdir(parents=True)
        (outside / "D1.md").write_text(body)
        (outside / "reports" / "D1.md").write_text(body)
        notes = self.review_file("notes.md", body)
        before = (self.at / "rail.tsv").read_bytes()
        add = ("ticket", "add", "--from-report")
        for target in (outside / "D1.md", notes):
            report.symlink_to(target)
            for spelling in ("D1.md", "reports/D1.md", str(report)):
                for extra in ((), ("--dry-run",)):
                    self.assertEqual(self.brigade(*add, spelling, *extra, ok=False),
                                     "brigade: reports/D1.md is a symbolic link; nothing added")
            self.assertEqual(self.brigade(*add, str(outside / "D1.md"), "--dry-run", ok=False),
                             f"brigade: {outside / 'D1.md'} is outside this store's reports/; name reports/D1.md")
            report.unlink()
        notes.unlink()
        (self.at / "reports").rmdir()
        (self.at / "reports").symlink_to(outside / "reports")
        for extra in ((), ("--dry-run",)):
            self.assertEqual(self.brigade(*add, "reports/D1.md", *extra, ok=False),
                             "brigade: reports/D1.md resolves outside this store's reports/; nothing added")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_refuses_a_path_with_a_dot_dot_component_wherever_it_resolves(self):
        self.fired_bug_fix()
        reports = self.review_file("D1.md", "## Follow-ups\n\n- One thing.\n").parent
        before = (self.at / "rail.tsv").read_bytes()
        for spelling in ("reports/../reports/D1.md", "../D1.md", f"{reports}/../reports/D1.md",
                         f"{reports}/../../../../outside/D1.md", "reports/.."):
            for extra in ((), ("--dry-run",)):
                self.assertEqual(self.brigade("ticket", "add", "--from-report", spelling, *extra, ok=False),
                                 f"brigade: {spelling} has a .. component; name a file like reports/D2.md")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_a_report_that_follows_the_brief_is_read_by_ticket_add_from_report(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--paths", "src/boot.ts",
                            "--lease", "L4", "--base", "origin/main")
        line = next(line for line in text.splitlines() if line.startswith("- Write it to "))
        asked = "List each follow-up as one top-level list item under a `"
        heading = line.split(asked, 1)[1].split("`", 1)[0]
        self.assertIn(f"{asked}{heading}` heading, or write `None.` there. ", line)
        self.assertIn("Mark a follow-up that is safety, privacy, or correctness with `Safety:`, `Privacy:`, or `Correctness:` at the start of its text, after any list marker. ", line)
        self.assertTrue(line.endswith("Only a marked follow-up can become a ticket, at most one per item. "
                                      "Every other follow-up goes to the store's notes ledger, unless its text is already on the rail. "
                                      "Name each file a follow-up touches by its repository path in backticks."), line)
        self.review_file("D1.md", f"# D1\n\nStatus: done\n\n{heading}\n\n- Correctness: Warm start is still slow.\n")
        self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md"),
                         "T2 added from perf/reports/D1.md#1: Correctness: Warm start is still slow.")
        self.review_file("D1.md", f"# D1\n\nStatus: done\n\n{heading}\n\nNone.\n")
        self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md"),
                         "reports/D1.md lists no follow-ups; nothing added\nnot filed, says no work is needed: None.")

    def test_from_report_refuses_priority_nonempty_paths_and_decision(self):
        self.fired_bug_fix()
        before = self.store_files()
        for extra in (("--priority", "low"), ("--paths", "a.txt"), ("--decision",)):
            self.assertEqual(self.follow_ups_in(BULLETS, *extra, ok=False),
                             "brigade: --from-report takes no --priority, nonempty --paths, or --decision", extra)
        self.review_file("D1.md", BULLETS)
        self.assertEqual({name: data for name, data in self.store_files().items() if name != "reports/D1.md"}, before)

    def test_ticket_add_from_report_refuses_kind_and_goal_and_files_no_ticket(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        (self.at / "reports").mkdir(exist_ok=True)
        (self.at / "reports" / "D1.md").write_text(BULLETS)
        before = self.store_files()
        refusal = "brigade: --from-report takes no --kind or --goal; each ticket's kind and goal follow from the report's item"
        for flags in (("--kind", "fix"), ("--goal", "G1"), ("--goal", ""), ("--kind", "polish", "--goal", "G1")):
            with self.subTest(flags=flags):
                self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md", *flags, ok=False), refusal)
                self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md", "--dry-run", *flags, ok=False), refusal)
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_from_report_of_an_item_with_a_bad_kind_cell_and_a_bad_goal_cell_warns_of_both_and_files_a_ticket_with_no_kind_and_no_goal(self):
        warnings = self.item_with_two_bad_cells()
        self.review_file("D1.md", "## Follow-ups\n\n- Correctness: Tidy guide.\n")
        self.assertEqual(self.run_raw("ticket", "add", "--from-report", "reports/D1.md"),
                         (0, "T2 added from perf/reports/D1.md#1: Correctness: Tidy guide.\n", warnings))
        self.assertEqual(self.raw_cells("rail.tsv", "T2", "state", "kind", "goal"), (b"waiting", b"", b""))
        self.assertEqual(self.raw_cells("dishes.tsv", "D1", "kind", "goal"), (b"chore", b"G9"))

    def test_ticket_add_from_report_dry_run_prints_the_same_warnings_and_changes_no_file(self):
        warnings = self.item_with_two_bad_cells()
        self.review_file("D1.md", "## Follow-ups\n\n- Correctness: Tidy guide.\n")
        before = self.store_files()
        self.assertEqual(self.run_raw("ticket", "add", "--from-report", "reports/D1.md", "--dry-run"),
                         (0, "would add from perf/reports/D1.md#1: Correctness: Tidy guide.\n", warnings))
        self.assertEqual(self.store_files(), before)
