class BareItemsMixin:
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
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Correctness: Fix a.\n---\n- Safety: Fix b.\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: Fix a.",
            "not filed, D1.md takes one ticket per item: Safety: Fix b.",
            "would note perf/reports/D1.md#2, no tracked path: Safety: Fix b.",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nCorrectness: Reword the gate.\n\n---\n\nSafety: Name the file.\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: Reword the gate.",
            "not filed, D1.md takes one ticket per item: Safety: Name the file.",
            "would note perf/reports/D1.md#2, no tracked path: Safety: Name the file.",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Correctness: Run it.\n\n```\n---\n```\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: Run it. ``` --- ```",
        ])
        for body, line in (("## Follow-ups\n\nCorrectness: ----\n", "Correctness: ----"),
                           ("## Follow-ups\n\n* Correctness: ---\n", "Correctness: ---"),
                           ("## Follow-ups\n\nCorrectness: --- extra\n", "Correctness: --- extra"),
                           ("## Follow-ups\n\n1. Correctness: ---\n", "Correctness: ---")):
            self.assertEqual(self.follow_ups_in(body, "--dry-run"), f"would add from perf/reports/D1.md#1: {line}")

    def test_from_report_keeps_a_separator_line_inside_a_tilde_fence(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n- Correctness: Preserve the YAML document marker in `src/config.py`:\n  ~~~yaml\n  ---\n  key: value\n  ~~~\n"
        filed = "would add from perf/reports/D1.md#1: Correctness: Preserve the YAML document marker in `src/config.py`: ~~~yaml --- key: value ~~~"
        self.assertEqual(self.follow_ups_in(body, "--dry-run"), filed)

    def test_a_tilde_line_inside_a_backtick_fence_does_not_close_it(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n- Correctness: Run it.\n\n```\n~~~\n---\n```\n"
        self.assertEqual(self.follow_ups_in(body, "--dry-run"), "would add from perf/reports/D1.md#1: Correctness: Run it. ``` ~~~ --- ```")

    def test_a_paragraph_above_a_list_item_with_no_text_is_filed_when_no_other_list_item_follows(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nCorrectness: Reword the guide.\n\n- \n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: Reword the guide.",
            "not filed, list item with no text",
        ])

    def test_a_paragraph_above_a_list_item_with_no_text_and_then_a_list_item_introduces_the_list(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nTwo things remain.\n\n- \n- Correctness: Fix a.\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: Fix a.",
            "not filed, prose that introduces a list: Two things remain.",
            "not filed, list item with no text",
        ])

    def test_a_paragraph_below_a_list_item_with_no_text_that_follows_the_last_list_item_with_text_closes_the_section(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Correctness: Fix a.\n\n- \n\nThat is all.\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: Fix a.",
            "not filed, list item with no text",
            "not filed, prose after the last list item: That is all.",
        ])
