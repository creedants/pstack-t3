if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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


class ReviewsSplit3Test(BrigadeTest):
    def test_a_15_cell_dishes_row_and_a_10_cell_rail_row_are_read_and_a_read_leaves_both_tables_byte_identical(self):
        self.open()
        self.old_item()
        before = self.tables()
        self.assertEqual(self.brigade("ticket", "list"), "T1 assigned normal [user] Fix login")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")
        self.assertEqual(self.brigade("status").splitlines()[1], "reporting: milestones, no landing contract, in review: 1")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False), "brigade: D1 has no review verdict for abc")
        self.assertEqual(self.tables(), before)

    def test_a_pass_with_edits_row_moves_the_item_to_sent_back_and_names_the_report(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.assertEqual(self.record_edits("abc", "--edits", "reports/D1-review-1.md"), "D1 sent-back")
        self.assertEqual(self.dish_fields("state", "sha"), ("sent-back", "abc"))
        self.assertEqual([row[3:5] + row[8:] for row in self.pass_rows()],
                         [["abc", "pass-with-edits", "D1-review-1.md", "", ""]])

    def test_pass_with_edits_rows_do_not_count_toward_the_round_budget(self):
        self.started()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress")
        for sha in ("b1", "b2"):
            self.assertEqual(self.record_edits(sha, "--edits", "D1-review-1.md"), "D1 sent-back")
            self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 2 send-backs since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.send_back("a3"), "D1 sent-back; 3 send-backs, decision pending")

    def test_pass_record_refuses_pass_with_edits_without_edits_and_edits_with_another_verdict_or_a_report(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.assertEqual(self.record_edits("abc", ok=False), "brigade: --verdict pass-with-edits needs --edits <review report>")
        self.assertEqual(self.record_edits("abc", "--report", "D1-review-1.md", ok=False),
                         "brigade: --verdict pass-with-edits needs --edits <review report>")
        for verdict in ("pass", "send-back", "blocked"):
            self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", verdict, "--author", CODEX,
                                          "--verifier", CLAUDE, "--edits", "D1-review-1.md", ok=False),
                             "brigade: --edits goes only with --verdict pass-with-edits", verdict)
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", "--report", "D1-review-1.md", ok=False),
                         "brigade: --edits names the round's report; drop --report")
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-9.md", ok=False),
                         "brigade: reports/D1-review-9.md does not exist; write the review report first")
        self.assertEqual(self.record_edits("abc", "--edits", "D1.md", ok=False),
                         "brigade: D1.md is not a review report of D1; name a file like reports/D1-review-1.md")
        self.assertEqual(self.pass_rows(), [])
        self.assertEqual(self.dish_fields("state"), ("in-progress",))

    def test_pass_record_refuses_a_pass_with_edits_report_whose_edits_do_not_parse(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n## Blockers\n\nNone.\n\n## Edits verified\n\nYes.\n")
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", ok=False),
                         "brigade: reports/D1-review-1.md holds no edit under a ## Edits heading")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                          "### Edit 2\n\nFile: `src/greeting.txt`\n\nOld text:\n\n```\nwrold\n```\n")
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", ok=False), self.PARTS_REFUSAL.format(2))
        self.assertEqual(self.pass_rows(), [])
        self.assertEqual(self.dish_fields("state"), ("in-progress",))

    def test_edits_verify_passes_a_head_that_is_the_edits_and_the_rebuilt_output_and_records_the_pair_once(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.edits_rows(), [])
        clean = (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", "")
        self.assertEqual(self.verify(reviewed, head), clean)
        self.assertEqual(self.edits_rows(), [["D1", reviewed, head, "D1-review-1.md"]])
        self.assertEqual(self.verify(reviewed, head), clean)
        self.assertEqual(self.edits_rows(), [["D1", reviewed, head, "D1-review-1.md"]])

    def test_edits_verify_ignores_an_edit_under_a_notes_edits_heading(self):
        reviewed = self.edits_store()
        self.two_edits(after="## Edits verified\n\nApplied in a scratch copy.\n\n## Notes edits\n\n"
                             + self.edit_block("1", "/srv/notes/review.md", "helo", "goodbye"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_parses_an_edits_section_that_ends_the_report(self):
        reviewed = self.edits_store()
        self.two_edits(after="")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_fails_a_head_with_one_more_changed_line_in_a_source_file(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello world\nsecond line, changed\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\n"
                             f"FAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_head_that_applied_an_edit_with_one_character_different(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello World\nsecond line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"built out/greeting.txt\nFAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_an_old_text_that_occurs_twice_at_the_reviewed_commit(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                          self.edit_block("2", "src/greeting.txt", "n", "N"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello wrold\nsecoNd liNe\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL src/greeting.txt: old text of Edit 2 occurs 2 times at {reviewed}",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_fails_two_edits_whose_old_texts_overlap_and_an_edit_to_a_path_with_no_file(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo wr", "hello wr"),
                          self.edit_block("2", "src/greeting.txt", "wrold", "world"),
                          self.edit_block("3", "src/absent.txt", "helo", "hello"),
                          self.edit_block("4", "src/greeting.txt", "second", "third"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello world\nthird line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL src/greeting.txt: Edit 1 overlaps another edit\n"
                             "FAIL src/greeting.txt: Edit 2 overlaps another edit\n"
                             f"FAIL src/absent.txt: no regular file at {reviewed}",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_fails_an_edit_that_names_a_symbolic_link_at_the_reviewed_commit(self):
        self.edits_store()
        (self.project / "src/link.txt").symlink_to("greeting.txt")
        reviewed = self.commit_files({})
        self.edits_report(self.edit_block("1", "src/link.txt", "helo", "hello"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL src/link.txt: no regular file at {reviewed}", self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_reads_the_edits_section_and_not_an_edits_line_in_an_earlier_fence(self):
        reviewed = self.edits_store()
        quoted = "## Edits\n\n" + self.edit_block("1", "tools/check.py", "import", "export")
        self.review_file("D1-review-1.md", f"Verdict: pass with edits\n\n## Blockers\n\n````\n{quoted}````\n\n## Edits\n\n"
                         + self.edit_block("1", "src/greeting.txt", "helo", "hello") + "\n"
                         + self.edit_block("2", "src/greeting.txt", "wrold", "world"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_fails_a_head_that_applied_an_edit_quoted_in_a_tilde_fence_and_not_the_edit_of_the_edits_section(self):
        self.assert_the_quoted_edit_is_not_read("~~~text", "~~~")

    def test_edits_verify_fails_a_head_that_applied_an_edit_quoted_in_a_backtick_fence_with_two_info_words_and_not_the_edit_of_the_edits_section(self):
        self.assert_the_quoted_edit_is_not_read("````text title", "````")

    def test_pass_record_refuses_a_report_with_a_heading_that_is_not_number_signs_one_space_and_its_text_from_the_first_column(self):
        self.fired_bug_fix()
        for heading in (" ## Edits", "## Edits ", "## Edits\t", "## Edits ##", "##  Edits", "##\tEdits", "> ## Edits", "- ## Edits",
                        "1. ## Edits", "    ## Edits", "\t## Edits", "##", "> - ### Edit 2", "### Edit 2 "):
            with self.subTest(heading=heading):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{heading}\n\nNotes.\n\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 3 starts like a heading and is not one to six #, one space, "
                                 "and the text, from the first column with nothing after the text")

    def test_pass_record_refuses_a_report_with_three_backticks_or_tildes_after_indentation_block_quote_marks_or_list_marks(self):
        self.fired_bug_fix()
        for opening in (" ```", "   ~~~~", "    ```", "\t~~~", "> ```", ">```", "- ```text", "1. ~~~", "  + ```", "> - ```", "2)\t~~~"):
            with self.subTest(opening=opening):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{opening}\nquoted\n\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 3 " + self.NESTED_FENCE)

    def test_pass_record_refuses_a_report_with_a_fence_that_no_later_line_closes(self):
        self.fired_bug_fix()
        for opening, last in (("````text", "```"), ("~~~", "```"), ("```", "``"), ("```", "    ```"), ("~~~~", "~~~~ ~")):
            with self.subTest(opening=opening, last=last):
                body = (f"Verdict: pass with edits\n\n{self.section()}\n{opening}\nThe sentence then reads so.\n{last}\n\n"
                        "### Edit 2\n\nFile: `src/greeting.txt`\n\nOld text:\n")
                self.assertEqual(self.refused_edits(body),
                                 "brigade: reports/D1-review-1.md: line 21 opens a fence that no later line closes")

    def test_pass_record_refuses_a_report_with_a_line_of_only_equals_signs_or_only_hyphens_after_its_nesting_and_below_a_line_that_is_not_empty_and_not_only_spaces_and_tabs(self):
        self.fired_bug_fix()
        for above, underline in (("Edits", "---"), ("Edits", "====="), ("Edits", " -"), ("Edits", "=  "), ("> Edits", "> ---"),
                                 ("> Edits", ">="), ("- Edits", "  ==="), ("+ Edits", "\t-"), ("1. Edits", "   --- \t"),
                                 ("> - Edits", "  >   ="), ("Edits", "- -"), ("\u00a0", "---"), ("\x0c", "==="),
                                 (" \u2003", "-"), ("\x1f\t", "=")):
            with self.subTest(above=above, underline=underline):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{above}\n{underline}\n\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 4 is only = or only - once its indentation, block quote "
                                 "marks, list marks, and trailing spaces and tabs are removed, and the line above it is not empty "
                                 "and not only spaces and tabs")

    def test_pass_record_refuses_a_report_with_a_line_that_starts_with_an_angle_bracket_and_then_an_ascii_letter_a_slash_an_exclamation_mark_or_a_question_mark(self):
        self.fired_bug_fix()
        for line in ("<details>", "   <!--", "</summary>", "<?php", "<item>: edits verified"):
            with self.subTest(line=line):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{line}\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 3 starts, after at most three spaces, with < and then an "
                                 "ASCII letter, /, !, or ?")

    def test_pass_record_refuses_a_report_with_two_headings_whose_text_is_edits(self):
        self.fired_bug_fix()
        for other in ("## Edits", "# Edits", "#### Edits"):
            with self.subTest(other=other):
                quoted = self.edit_block("8", "src/greeting.txt", "helo", "hello")
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{other}\n\n{quoted}\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md holds 2 headings whose text is Edits")

    def test_pass_record_refuses_a_heading_in_the_edits_section_that_is_not_an_edit_heading_and_has_three_or_more_number_signs(self):
        self.fired_bug_fix()
        for heading in ("#### Edit 2", "### Edit", "### Edit  2", "### Notes on the edits", "###### Edit 2"):
            with self.subTest(heading=heading):
                body = (f"Verdict: pass with edits\n\n{self.section()}\n{heading}\n\n"
                        + self.edit_block("2", "src/greeting.txt", "helo", "hello").split("\n", 1)[1])
                self.assertEqual(self.refused_edits(body),
                                 f"brigade: reports/D1-review-1.md: line {body.split(chr(10)).index(heading) + 1} is a heading in the "
                                 "## Edits section that is neither ### Edit <n> nor a heading of one or two #")

    def test_edits_verify_does_not_read_an_edit_below_a_heading_of_one_number_sign_that_follows_the_edits_section(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                          after="# Appendix\n\n" + self.edit_block("2", "src/greeting.txt", "wrold", "world"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.QUOTED_APPLIED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_pass_record_refuses_a_fenced_block_in_the_edits_section_above_its_first_edit_heading(self):
        self.fired_bug_fix()
        unheaded = self.edit_block("1", "src/greeting.txt", "helo", "hello").split("\n", 2)[2]
        self.assertEqual(self.refused_edits("Verdict: pass with edits\n\n## Edits\n\n" + unheaded + "\n"
                                            + self.edit_block("2", "src/greeting.txt", "wrold", "world")),
                         "brigade: reports/D1-review-1.md: line 9 starts a fenced block in the ## Edits section above its first "
                         "### Edit heading")

    def test_pass_record_refuses_an_edit_with_a_second_file_line_old_text_line_new_text_line_or_a_third_fenced_block(self):
        self.fired_bug_fix()
        unheaded = self.edit_block("2", "src/greeting.txt", "helo", "hello").split("\n", 2)[2]
        for more in ("File: `src/other.txt`\n", "Old text:\n", "New text:\n", "```\nThe sentence then reads so.\n```\n", unheaded):
            with self.subTest(more=more):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{self.section()}\n{more}"),
                                 self.PARTS_REFUSAL.format(1))
