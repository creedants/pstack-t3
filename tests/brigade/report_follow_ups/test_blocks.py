from ..support import A_LIST_THEN_A_PROSE_SECTION, FENCED, LEAD_IN, LONGER_THAN_A_NO_WORK_PHRASE, NESTED, NO_WORK, NUMBERED, PROSE, PROSE_ABOVE_A_LEAD_IN, TWO_SECTIONS

class BlocksMixin:
    def test_from_report_files_a_numbered_list_and_prints_the_paragraph_after_it(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(NUMBERED, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: **Hillclimb.** `t3/overrides/poteto-mode/playbooks/hillclimb.md` has a "
            "code-delegate line with the same missing step 5 pointer. It was outside the lease.",
            "not filed, D1.md takes one ticket per item: Correctness: **`WAIT_RULE` in `t3/added/brigade/scripts/brigade.py`.** It states only "
            "the ten-minute clock for worker briefs. Decide whether it should point at the new step 5 bullet.",
            "would note perf/reports/D1.md#2, no tracked path: Correctness: **`WAIT_RULE` in `t3/added/brigade/scripts/brigade.py`.** It states only "
            "the ten-minute clock for worker briefs. Decide whether it should point at the new step 5 bullet.",
            "not filed, D1.md takes one ticket per item: Correctness: No README or `docs/guide.md` edit is needed.",
            "would note perf/reports/D1.md#3, no tracked path: Correctness: No README or `docs/guide.md` edit is needed.",
            "not filed, prose after the last list item: Scratch is under `~/.cache/d87`.",
        ])

    def test_from_report_files_prose_that_neither_introduces_a_list_nor_follows_the_last_item(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(PROSE_ABOVE_A_LEAD_IN, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: **`README.md`.** Replace the `**Known limits.**` bullet (line 284), "
            "which still says \"Nothing deletes it or moves it back\", with these two bullets: ```markdown "
            "- **Interrupted runs are recovered.** Every later install or uninstall looks in those directories. "
            "- **Known limits.** Nothing returns or deletes it, so delete it by hand. ```",
            "not filed, D1.md takes one ticket per item: Correctness: **`docs/guide.md`.** Replace the first bullet under \"Known limits.\" "
            "(line 359) with this bullet, and keep the two bullets after it: ```markdown - An entry in a "
            "`.pstack-t3-scrap-*` directory is an unfinished copy. Delete it by hand. ```",
            "would note perf/reports/D1.md#2, no tracked path: Correctness: **`docs/guide.md`.** Replace the first bullet under \"Known limits.\" "
            "(line 359) with this bullet, and keep the two bullets after it: ```markdown - An entry in a "
            "`.pstack-t3-scrap-*` directory is an unfinished copy. Delete it by hand. ```",
            "not filed, D1.md takes one ticket per item: Correctness: **Type the `side` that `holder_parents` yields**, from the comment review.",
            "would note perf/reports/D1.md#3, no tracked path: Correctness: **Type the `side` that `holder_parents` yields**, from the comment review.",
            "not filed, D1.md takes one ticket per item: Correctness: **No test covers a skills directory inside the checkout**, which the "
            "sweep skips because `layout` refuses it.",
            "would note perf/reports/D1.md#4, no tracked path: Correctness: **No test covers a skills directory inside the checkout**, which the "
            "sweep skips because `layout` refuses it.",
            "not filed, prose that introduces a list: Other follow-ups:",
            "not filed, prose after the last list item: Scratch files for this run are under `/tmp/d105-repro/`: "
            "`repro.py`, `before.txt`, and `full-suite.txt`.",
        ])

    def test_from_report_files_the_prose_of_a_section_with_no_list_beside_a_section_with_one(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(A_LIST_THEN_A_PROSE_SECTION, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: `docs/light-mode.md`, in its review-file table, still describes one "
            "`<item>-review.md` file per item.",
            "not filed, D1.md takes one ticket per item: Correctness: Schema setup in `land.py` still runs outside `BEGIN IMMEDIATE`.",
            "would note perf/reports/D1.md#2, no tracked path: Correctness: Schema setup in `land.py` still runs outside `BEGIN IMMEDIATE`.",
            "not filed, D1.md takes one ticket per item: Correctness: `integration.head()` is still read inside two transactions.",
            "would note perf/reports/D1.md#3, no tracked path: Correctness: `integration.head()` is still read inside two transactions.",
        ])

    def test_from_report_does_not_file_a_block_that_is_one_of_eight_no_work_phrases_and_nothing_more(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(NO_WORK, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: **Docs.** `README.md` and `docs/guide.md` do not mention the "
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
        first, *rest = LONGER_THAN_A_NO_WORK_PHRASE
        self.assertEqual(self.follow_ups_in(body, "--dry-run").splitlines(), [
            f"would add from perf/reports/D1.md#1: {first}",
            *[line for number, text in enumerate(rest, 2) for line in (
                f"not filed, D1.md takes one ticket per item: {text}",
                f"would note perf/reports/D1.md#{number}, no tracked path: {text}",
            )],
        ])
        for text in LONGER_THAN_A_NO_WORK_PHRASE[-3:]:
            self.assertEqual(self.follow_ups_in(f"## Follow-ups\n\n{text}\n", "--dry-run"),
                             f"would add from perf/reports/D1.md#1: {text}")

    def test_from_report_folds_nested_bullets_and_an_indented_paragraph_into_their_item(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(NESTED, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: **D90-dependent wording.** These sentences hold for main and change if "
            "D90 lands: - Prompt caps step 1: \"It still applies the Haiku prompt cap.\" - Step 4: the rerun of a "
            "seat-launching child on `inherit`. If D90 makes the flag skip capped models, step 1 should say the flag never "
            "returns a capped model.",
            "not filed, D1.md takes one ticket per item: Correctness: **Docs batch.** - Change 4's Files list in `docs/light-mode.md` should "
            "add `t3/scripts/roles.py`. - `docs/light-mode.md` is not leased.",
            "would note perf/reports/D1.md#2, no tracked path: Correctness: **Docs batch.** - Change 4's Files list in `docs/light-mode.md` should "
            "add `t3/scripts/roles.py`. - `docs/light-mode.md` is not leased.",
        ])

    def test_from_report_keeps_a_fenced_block_in_its_item_and_reads_no_heading_inside_it(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(FENCED, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: Run this command to set the user's panel. It must use a fresh catalog. "
            "```bash # write the panel python3 roles.py write --catalog <file> --keep \\ "
            "--set \"review backups=opencode/step-5-preview-free?variant=high\" ```",
            "not filed, D1.md takes one ticket per item: Correctness: `family()` keeps the provider path for namespaced ids such as "
            "`opencode/...`. The panel compares bare ids locally.",
            "would note perf/reports/D1.md#2, no tracked path: Correctness: `family()` keeps the provider path for namespaced ids such as "
            "`opencode/...`. The panel compares bare ids locally.",
        ])

    def test_from_report_files_each_paragraph_of_a_section_with_no_list(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(PROSE, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: Schema setup in `land.py` still runs outside `BEGIN IMMEDIATE`.",
            "not filed, D1.md takes one ticket per item: Correctness: `integration.head()` is still read inside two transactions. "
            "Both sites predate this change.",
            "would note perf/reports/D1.md#2, no tracked path: Correctness: `integration.head()` is still read inside two transactions. "
            "Both sites predate this change.",
            "not filed, D1.md takes one ticket per item: Correctness: No test under `tests/` asserts these sentences. "
            "That directory is outside this lease.",
            "would note perf/reports/D1.md#3, no tracked path: Correctness: No test under `tests/` asserts these sentences. "
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
            self.assertEqual(self.follow_ups_in(f"# D1 report\n\n{heading}\n\n- Correctness: One thing.\n", "--dry-run").splitlines(),
                             ["would add from perf/reports/D1.md#1: Correctness: One thing.",
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
        self.assertEqual(self.follow_ups_in("## Follow-ups (outside lease L91)\n\n- Correctness: One thing.\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: One thing.",
            "not filed, text on the heading line: (outside lease L91)",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Correctness: One thing.\n", "--dry-run"),
                         "would add from perf/reports/D1.md#1: Correctness: One thing.")

    def test_from_report_prints_a_deeper_heading_inside_the_section_and_does_not_file_it(self):
        self.fired_bug_fix()
        before = (self.at / "rail.tsv").read_bytes()
        body = "## Follow-ups\n\nLead in.\n\n### Parser\n\n- Correctness: fix a\n- Safety: fix b\n\n### Docs\n\nClosing note.\n"
        self.assertEqual(self.follow_ups_in(body, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: fix a",
            "not filed, D1.md takes one ticket per item: Safety: fix b",
            "would note perf/reports/D1.md#2, no tracked path: Safety: fix b",
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
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nLead in.\n\n###   \n\n- Correctness: fix a\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: fix a",
            "not filed, prose that introduces a list: Lead in.",
            "not filed, heading inside the section with no text",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_prints_a_lead_in_above_a_list_and_does_not_file_it(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(LEAD_IN, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: `pass record` without `--pr` overwrites the item's recorded PR with an "
            "empty value, because `--pr` defaults to `\"\"`.",
            "not filed, D1.md takes one ticket per item: Correctness: `--same-family` is not checked. It writes the `same model family;` note "
            "even when the families differ.",
            "would note perf/reports/D1.md#2, no tracked path: Correctness: `--same-family` is not checked. It writes the `same model family;` note "
            "even when the families differ.",
            "not filed, prose that introduces a list: The explainer found these in `brigade.py`. All are outside L81, "
            "so I did not fix them.",
        ])

    def test_from_report_numbers_the_items_of_two_sections_as_one_list(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(TWO_SECTIONS, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: `docs/light-mode.md`, in its review-file table, still describes one "
            "`<item>-review.md` file per item.",
            "not filed, D1.md takes one ticket per item: Correctness: The panel sentences in step 7 still say \"handle the send-back below\" "
            "while that bullet sits above them.",
            "would note perf/reports/D1.md#2, no tracked path: Correctness: The panel sentences in step 7 still say \"handle the send-back below\" "
            "while that bullet sits above them.",
            "not filed, D1.md takes one ticket per item: Correctness: Decide whether `ticket move` should refuse a ticket of an item that owes "
            "its decision.",
            "would note perf/reports/D1.md#3, no tracked path: Correctness: Decide whether `ticket move` should refuse a ticket of an item that owes "
            "its decision.",
        ])
