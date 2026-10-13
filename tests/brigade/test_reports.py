from .support import (
    setUpModule,
    A_LIST_THEN_A_PROSE_SECTION,
    BULLETS,
    BrigadeTest,
    FENCED,
    LEAD_IN,
    LONGER_THAN_A_NO_WORK_PHRASE,
    NESTED,
    NO_WORK,
    NUMBERED,
    PROSE,
    PROSE_ABOVE_A_LEAD_IN,
    Path,
    REAL_FOLLOW_UPS,
    ProcessReason,
    SCRIPT,
    TRACKED,
    TWO_SECTIONS,
    fcntl,
    os,
    real_popen,
    sys,
    time,
)


class ReportsTest(BrigadeTest):
    def test_from_report_files_a_numbered_list_and_prints_the_paragraph_after_it(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(NUMBERED, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: **Hillclimb.** `t3/overrides/poteto-mode/playbooks/hillclimb.md` has a "
            "code-delegate line with the same missing step 5 pointer. It was outside the lease.",
            "would add from perf/reports/D1.md#2: **`WAIT_RULE` in `t3/added/brigade/scripts/brigade.py`.** It states only "
            "the ten-minute clock for worker briefs. Decide whether it should point at the new step 5 bullet.",
            "would add from perf/reports/D1.md#3: No README or `docs/guide.md` edit is needed.",
            "not filed, prose after the last list item: Scratch is under `~/.cache/d87`.",
        ])

    def test_from_report_files_prose_that_neither_introduces_a_list_nor_follows_the_last_item(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(PROSE_ABOVE_A_LEAD_IN, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: **`README.md`.** Replace the `**Known limits.**` bullet (line 284), "
            "which still says \"Nothing deletes it or moves it back\", with these two bullets: ```markdown "
            "- **Interrupted runs are recovered.** Every later install or uninstall looks in those directories. "
            "- **Known limits.** Nothing returns or deletes it, so delete it by hand. ```",
            "would add from perf/reports/D1.md#2: **`docs/guide.md`.** Replace the first bullet under \"Known limits.\" "
            "(line 359) with this bullet, and keep the two bullets after it: ```markdown - An entry in a "
            "`.pstack-t3-scrap-*` directory is an unfinished copy. Delete it by hand. ```",
            "would add from perf/reports/D1.md#3: **Type the `side` that `holder_parents` yields**, from the comment review.",
            "would add from perf/reports/D1.md#4: **No test covers a skills directory inside the checkout**, which the "
            "sweep skips because `layout` refuses it.",
            "not filed, prose that introduces a list: Other follow-ups:",
            "not filed, prose after the last list item: Scratch files for this run are under `/tmp/d105-repro/`: "
            "`repro.py`, `before.txt`, and `full-suite.txt`.",
        ])

    def test_from_report_files_the_prose_of_a_section_with_no_list_beside_a_section_with_one(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(A_LIST_THEN_A_PROSE_SECTION, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: `docs/light-mode.md`, in its review-file table, still describes one "
            "`<item>-review.md` file per item.",
            "would add from perf/reports/D1.md#2: Schema setup in `land.py` still runs outside `BEGIN IMMEDIATE`.",
            "would add from perf/reports/D1.md#3: `integration.head()` is still read inside two transactions.",
        ])

    def test_from_report_does_not_file_a_block_that_is_one_of_eight_no_work_phrases_and_nothing_more(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(NO_WORK, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: **Docs.** `README.md` and `docs/guide.md` do not mention the "
            "`recovered`, `removed`, and `left` lines.",
            "not filed, says no work is needed: None.",
            "not filed, says no work is needed: **None required.**",
            "not filed, says no work is needed: NONE NEEDED",
            "not filed, says no work is needed: _Nothing._",
            "not filed, says no work is needed: `Nothing needed`.",
            "not filed, says no work is needed: n/a",
            "not filed, says no work is needed: Not applicable.",
            "not filed, says no work is needed: **No follow-ups**.",
        ])

    def test_from_report_files_every_block_longer_than_a_no_work_phrase(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n" + "".join(f"- {text}\n" for text in LONGER_THAN_A_NO_WORK_PHRASE)
        self.assertEqual(self.follow_ups_in(body, "--dry-run").splitlines(), [
            f"would add from perf/reports/D1.md#{number}: {text}"
            for number, text in enumerate(LONGER_THAN_A_NO_WORK_PHRASE, 1)])
        for text in LONGER_THAN_A_NO_WORK_PHRASE[-3:]:
            self.assertEqual(self.follow_ups_in(f"## Follow-ups\n\n{text}\n", "--dry-run"),
                             f"would add from perf/reports/D1.md#1: {text}")

    def test_from_report_folds_nested_bullets_and_an_indented_paragraph_into_their_item(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(NESTED, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: **D90-dependent wording.** These sentences hold for main and change if "
            "D90 lands: - Prompt caps step 1: \"It still applies the Haiku prompt cap.\" - Step 4: the rerun of a "
            "seat-launching child on `inherit`. If D90 makes the flag skip capped models, step 1 should say the flag never "
            "returns a capped model.",
            "would add from perf/reports/D1.md#2: **Docs batch.** - Change 4's Files list in `docs/light-mode.md` should "
            "add `t3/scripts/roles.py`. - `docs/light-mode.md` is not leased.",
        ])

    def test_from_report_keeps_a_fenced_block_in_its_item_and_reads_no_heading_inside_it(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(FENCED, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Run this command to set the user's panel. It must use a fresh catalog. "
            "```bash # write the panel python3 roles.py write --catalog <file> --keep \\ "
            "--set \"review backups=opencode/step-5-preview-free?variant=high\" ```",
            "would add from perf/reports/D1.md#2: `family()` keeps the provider path for namespaced ids such as "
            "`opencode/...`. The panel compares bare ids locally.",
        ])

    def test_from_report_files_each_paragraph_of_a_section_with_no_list(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(PROSE, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Schema setup in `land.py` still runs outside `BEGIN IMMEDIATE`.",
            "would add from perf/reports/D1.md#2: `integration.head()` is still read inside two transactions. "
            "Both sites predate this change.",
            "would add from perf/reports/D1.md#3: No test under `tests/` asserts these sentences. "
            "That directory is outside this lease.",
        ])

    def test_from_report_adds_nothing_for_a_section_that_says_none_or_a_report_without_one(self):
        self.fired_bug_fix()
        before = (self.at / "rail.tsv").read_bytes()
        for written, text in (("None.", "None."), ("**None.**", "**None.**"), ("  - n/a", "- n/a"),
                              ("No\nfollow-ups.", "No follow-ups.")):
            self.assertEqual(self.follow_ups_in(f"## Follow-ups\n\n{written}\n").splitlines(), [
                "reports/D1.md lists no follow-ups; nothing added",
                f"not filed, says no work is needed: {text}",
            ])
        self.assertEqual(self.follow_ups_in("# D1 report\n\nStatus: done\n\n## Deviations\n\n- None.\n"),
                         "reports/D1.md has no follow-ups section; nothing added")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_reads_five_heading_forms(self):
        self.fired_bug_fix()
        for heading, rest in (("## Follow-ups (outside lease L91)", ["(outside lease L91)"]), ("### Follow-ups", []),
                              ("## Follow-up", []), ("## Follow-ups outside my lease", ["outside my lease"]),
                              ("## follow-ups", [])):
            self.assertEqual(self.follow_ups_in(f"# D1 report\n\n{heading}\n\n- One thing.\n", "--dry-run").splitlines(),
                             ["would add from perf/reports/D1.md#1: One thing.",
                              *(f"not filed, text on the heading line: {text}" for text in rest)], heading)

    def test_from_report_prints_text_on_the_heading_line_and_does_not_file_it(self):
        self.fired_bug_fix()
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.follow_ups_in("## Follow-ups: None of the tests cover the retry path.\n").splitlines(), [
            "reports/D1.md lists no follow-ups; nothing added",
            "not filed, text on the heading line: None of the tests cover the retry path.",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\t: none\n").splitlines(), [
            "reports/D1.md lists no follow-ups; nothing added",
            "not filed, text on the heading line: none",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)
        self.assertEqual(self.follow_ups_in("## Follow-ups (outside lease L91)\n\n- One thing.\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: One thing.",
            "not filed, text on the heading line: (outside lease L91)",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- One thing.\n", "--dry-run"),
                         "would add from perf/reports/D1.md#1: One thing.")

    def test_from_report_prints_a_deeper_heading_inside_the_section_and_does_not_file_it(self):
        self.fired_bug_fix()
        before = (self.at / "rail.tsv").read_bytes()
        body = "## Follow-ups\n\nLead in.\n\n### Parser\n\n- fix a\n- fix b\n\n### Docs\n\nClosing note.\n"
        self.assertEqual(self.follow_ups_in(body, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: fix a",
            "would add from perf/reports/D1.md#2: fix b",
            "not filed, prose that introduces a list: Lead in.",
            "not filed, heading inside the section: Parser",
            "not filed, heading inside the section: Docs",
            "not filed, prose after the last list item: Closing note.",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n### None of these block the merge\n").splitlines(), [
            "reports/D1.md lists no follow-ups; nothing added",
            "not filed, heading inside the section: None of these block the merge",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_prints_a_deeper_heading_with_no_text_and_not_the_follow_ups_heading_with_no_text(self):
        self.fired_bug_fix()
        before = (self.at / "rail.tsv").read_bytes()
        for extra in ((), ("--dry-run",)):
            self.assertEqual(self.follow_ups_in("## Follow-ups\n\n###\n", *extra).splitlines(), [
                "reports/D1.md lists no follow-ups; nothing added",
                "not filed, heading inside the section with no text",
            ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nLead in.\n\n###   \n\n- fix a\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: fix a",
            "not filed, prose that introduces a list: Lead in.",
            "not filed, heading inside the section with no text",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_prints_a_lead_in_above_a_list_and_does_not_file_it(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(LEAD_IN, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: `pass record` without `--pr` overwrites the item's recorded PR with an "
            "empty value, because `--pr` defaults to `\"\"`.",
            "would add from perf/reports/D1.md#2: `--same-family` is not checked. It writes the `same model family;` note "
            "even when the families differ.",
            "not filed, prose that introduces a list: The explainer found these in `brigade.py`. All are outside L81, "
            "so I did not fix them.",
        ])

    def test_from_report_numbers_the_items_of_two_sections_as_one_list(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(TWO_SECTIONS, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: `docs/light-mode.md`, in its review-file table, still describes one "
            "`<item>-review.md` file per item.",
            "would add from perf/reports/D1.md#2: The panel sentences in step 7 still say \"handle the send-back below\" "
            "while that bullet sits above them.",
            "would add from perf/reports/D1.md#3: Decide whether `ticket move` should refuse a ticket of an item that owes "
            "its decision.",
        ])

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
                         [f"would add from perf/reports/D1.md#{number}" for number in (1, 2, 3)], err)

    def test_from_report_skips_the_second_of_two_items_with_the_same_text(self):
        self.fired_bug_fix()
        twice = "## Follow-ups\n\n- Same thing.\n- same  THING.\n- Other thing.\n"
        self.assertEqual(self.follow_ups_in(twice, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Same thing.",
            "skipped perf/reports/D1.md#2, same text as #1 above: same  THING.",
            "would add from perf/reports/D1.md#3: Other thing.",
        ])
        self.assertEqual(self.follow_ups_in(twice).splitlines(), [
            "T2 added from perf/reports/D1.md#1: Same thing.",
            "skipped perf/reports/D1.md#2, same text as T2 (waiting): same  THING.",
            "T3 added from perf/reports/D1.md#3: Other thing.",
        ])

    def test_from_report_dry_run_prints_a_list_item_with_no_text_as_not_filed(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- \n- One thing.\n", "--dry-run"),
                         "would add from perf/reports/D1.md#1: One thing.\nnot filed, list item with no text")

    def test_from_report_takes_the_report_by_name_or_by_a_path_into_this_store(self):
        self.fired_bug_fix()
        path = self.review_file("D1.md", "## Follow-ups\n\n- One thing.\n")
        for spelling in ("D1.md", "reports/D1.md", str(path)):
            self.assertEqual(self.brigade("ticket", "add", "--from-report", spelling, "--dry-run"),
                             "would add from perf/reports/D1.md#1: One thing.", spelling)

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
        self.assertTrue(line.endswith(f"{asked}{heading}` heading, or write `None.` there. "
                                      "Name each file a follow-up touches by its repository path in backticks."), line)
        self.review_file("D1.md", f"# D1\n\nStatus: done\n\n{heading}\n\n- Warm start is still slow.\n")
        self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md"),
                         "T2 added from perf/reports/D1.md#1: Warm start is still slow.")
        self.review_file("D1.md", f"# D1\n\nStatus: done\n\n{heading}\n\nNone.\n")
        self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md"),
                         "reports/D1.md lists no follow-ups; nothing added\nnot filed, says no work is needed: None.")

    def test_from_report_records_the_tracked_paths_each_real_follow_up_quotes(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        printed = self.follow_ups_in("## Follow-ups\n\n" + "".join(f"- {text}\n" for text, _ in REAL_FOLLOW_UPS))
        self.assertEqual(printed.splitlines(), [f"T{number} added from perf/reports/D1.md#{number - 1}: {text}"
                                                for number, (text, _) in enumerate(REAL_FOLLOW_UPS, 2)])
        self.assertEqual(self.paths_cells(), {"T1": "", **{f"T{number}": paths
                                                           for number, (_, paths) in enumerate(REAL_FOLLOW_UPS, 2)}})

    def test_from_report_records_the_directory_before_the_first_marked_component_of_a_pattern_that_matches(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Reword `t3/**/*.md` and `tests/test_*.py`.\n"
                           "- Nothing matches `docs/*.rst`, `**` or `*.md` has no directory, and `src/*.py` is not tracked.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "t3,tests", "T3": ""})

    def test_from_report_records_the_paths_quoted_beside_a_double_backtick_code_span(self):
        self.tracked_project("src/a/one.py", "src/b/two.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Replace ``with `x` `` in `src/a/one.py` and `src/b/two.py`\n"
                           "- Change ``rows[0]`` in `src/a/one.py`\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "src/a/one.py,src/b/two.py", "T3": "src/a/one.py"})

    def test_from_report_skips_absolute_dot_dot_repository_and_non_numeric_suffix_quotes_and_records_the_normalized_path_of_a_line_suffixed_or_directory_quote(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- See `../README.md`, `/README.md`, `.`, `docs/../README.md`, "
                           "and `docs/guide.md:x`.\n- Fix `./docs//guide.md:7` and `docs/`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "", "T3": "docs,docs/guide.md"})

    def test_from_report_records_no_paths_when_the_project_is_not_a_git_repository(self):
        (self.project / "docs").mkdir()
        (self.project / "docs/guide.md").write_text("x\n")
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Fix `docs/guide.md`.\n"),
                         "T2 added from perf/reports/D1.md#1: Fix `docs/guide.md`.")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})

    def test_from_report_records_a_quoted_path_in_a_repository_that_tracks_a_file_name_that_is_not_utf_8(self):
        try:
            with open(os.fsencode(self.project) + b"/\xff.md", "wb") as handle:
                handle.write(b"x\n")
        except OSError:
            self.skipTest("the file system refuses a file name that is not UTF-8")
        self.tracked_project("guide.md")
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Fix `guide.md`.\n"),
                         "T2 added from perf/reports/D1.md#1: Fix `guide.md`.")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "guide.md"})

    def test_from_report_dry_run_in_a_git_project_changes_no_store_file(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        self.review_file("D1.md", "## Follow-ups\n\n- Fix `docs/guide.md`.\n")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md", "--dry-run"),
                         "would add from perf/reports/D1.md#1: Fix `docs/guide.md`.")
        self.assertEqual(self.store_files(), before)

    def test_from_report_records_the_tracked_file_for_each_quoted_file_name_with_no_directory_that_resolves_to_one_and_none_for_names_with_none_or_several(self):
        self.tracked_project("t3/added/x/scripts/tool.py", "skills/x/scripts/tool.py", "src/app/one.py", "lib/two.py",
                             "src/two.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Fix `tool.py:7` and `one.py`.\n"
                           "- Fix `two.py` and `nope.py`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "src/app/one.py,t3/added/x/scripts/tool.py", "T3": ""})

    def test_from_report_records_nothing_for_a_file_name_only_one_nested_file_has_quoted_with_a_dot_slash_before_it_a_slash_after_it_or_spaces_or_tabs_around_it(self):
        self.tracked_project("src/app/tool.py", "tool.py:12")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- One `./tool.py`.\n"
                           "- Two `tool.py/`.\n"
                           "- Three `./tool.py:12`.\n"
                           "- Four ` tool.py `.\n"
                           "- Five `\ttool.py\t`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "", "T3": "", "T4": "", "T5": "", "T6": ""})

    def test_from_report_records_nothing_for_a_quote_that_is_a_tracked_file_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12", "src/app/tool.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Fix `tool.py:12`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})

    def test_from_report_records_nothing_for_a_quote_that_is_a_tracked_directory_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12/notes.md", "src/app/tool.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Fix `tool.py:12`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})

    def test_from_report_on_a_report_whose_only_follow_up_is_a_list_item_with_no_text_adds_nothing(self):
        self.fired_bug_fix()
        before = self.tables()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- \n"),
                         "reports/D1.md lists no follow-ups; nothing added\nnot filed, list item with no text")
        self.assertEqual(self.tables(), before)

    def test_from_report_prints_a_line_that_is_only_a_list_marker_as_a_list_item_with_no_text_and_adds_nothing(self):
        self.fired_bug_fix()
        before = self.tables()
        for marker in ("-", "*", "1."):
            with self.subTest(marker=marker):
                self.assertEqual(self.follow_ups_in(f"## Follow-ups\n\n{marker}\n"),
                                 "reports/D1.md lists no follow-ups; nothing added\nnot filed, list item with no text")
                self.assertEqual(self.tables(), before)

    def test_from_report_skips_a_line_that_is_only_three_hyphens(self):
        self.fired_bug_fix()
        before = (self.at / "rail.tsv").read_bytes()
        skipped = "reports/D1.md lists no follow-ups; nothing added"
        for body in ("## Follow-ups\n\n---\n", "## Follow-ups\n\n  ---  \n", "## Follow-ups\n\n- ---\n",
                     "## Follow-ups\n\n  - ---  \n", "## Follow-ups\n\n-   ---\n"):
            self.assertEqual(self.follow_ups_in(body), skipped)
            self.assertEqual(self.follow_ups_in(body, "--dry-run"), skipped)
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_still_files_a_longer_hyphen_line_and_keeps_the_lines_around_a_skipped_one(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Fix a.\n---\n- Fix b.\n").splitlines(), [
            "T2 added from perf/reports/D1.md#1: Fix a.",
            "T3 added from perf/reports/D1.md#2: Fix b.",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nReword the gate.\n\n---\n\nName the file.\n").splitlines(), [
            "T4 added from perf/reports/D1.md#1: Reword the gate.",
            "T5 added from perf/reports/D1.md#2: Name the file.",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Run it.\n\n```\n---\n```\n").splitlines(), [
            "T6 added from perf/reports/D1.md#1: Run it. ``` --- ```",
        ])
        for body, line in (("## Follow-ups\n\n----\n", "----"),
                           ("## Follow-ups\n\n* ---\n", "---"),
                           ("## Follow-ups\n\n--- extra\n", "--- extra"),
                           ("## Follow-ups\n\n1. ---\n", "---")):
            self.assertEqual(self.follow_ups_in(body, "--dry-run"), f"would add from perf/reports/D1.md#1: {line}")

    def test_from_report_keeps_a_separator_line_inside_a_tilde_fence(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n- Preserve the YAML document marker in `src/config.py`:\n  ~~~yaml\n  ---\n  key: value\n  ~~~\n"
        filed = "T2 added from perf/reports/D1.md#1: Preserve the YAML document marker in `src/config.py`: ~~~yaml --- key: value ~~~"
        self.assertEqual(self.follow_ups_in(body), filed)

    def test_a_tilde_line_inside_a_backtick_fence_does_not_close_it(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n- Run it.\n\n```\n~~~\n---\n```\n"
        self.assertEqual(self.follow_ups_in(body), "T2 added from perf/reports/D1.md#1: Run it. ``` ~~~ --- ```")

    def test_a_paragraph_above_a_list_item_with_no_text_is_filed_when_no_other_list_item_follows(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nReword the guide.\n\n- \n").splitlines(), [
            "T2 added from perf/reports/D1.md#1: Reword the guide.",
            "not filed, list item with no text",
        ])

    def test_a_paragraph_above_a_list_item_with_no_text_and_then_a_list_item_introduces_the_list(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nTwo things remain.\n\n- \n- Fix a.\n").splitlines(), [
            "T2 added from perf/reports/D1.md#1: Fix a.",
            "not filed, prose that introduces a list: Two things remain.",
            "not filed, list item with no text",
        ])

    def test_a_paragraph_below_a_list_item_with_no_text_that_follows_the_last_list_item_with_text_closes_the_section(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Fix a.\n\n- \n\nThat is all.\n").splitlines(), [
            "T2 added from perf/reports/D1.md#1: Fix a.",
            "not filed, list item with no text",
            "not filed, prose after the last list item: That is all.",
        ])

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
        self.review_file("D1.md", "## Follow-ups\n\n- Tidy guide.\n")
        self.assertEqual(self.run_raw("ticket", "add", "--from-report", "reports/D1.md"),
                         (0, "T2 added from perf/reports/D1.md#1: Tidy guide.\n", warnings))
        self.assertEqual(self.raw_cells("rail.tsv", "T2", "state", "kind", "goal"), (b"waiting", b"", b""))
        self.assertEqual(self.raw_cells("dishes.tsv", "D1", "kind", "goal"), (b"chore", b"G9"))

    def test_ticket_add_from_report_dry_run_prints_the_same_warnings_and_changes_no_file(self):
        warnings = self.item_with_two_bad_cells()
        self.review_file("D1.md", "## Follow-ups\n\n- Tidy guide.\n")
        before = self.store_files()
        self.assertEqual(self.run_raw("ticket", "add", "--from-report", "reports/D1.md", "--dry-run"),
                         (0, "would add from perf/reports/D1.md#1: Tidy guide.\n", warnings))
        self.assertEqual(self.store_files(), before)
