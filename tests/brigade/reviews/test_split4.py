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


class ReviewsSplit4Test(BrigadeTest):
    def test_pass_record_refuses_an_edit_with_a_file_old_text_or_new_text_line_after_whitespace_a_block_quote_mark_or_a_list_mark_or_a_label_line_that_ends_in_whitespace(self):
        self.fired_bug_fix()
        for more in ("  File: `src/other.txt`", "1. File: `src/other.txt`, line 2", "> Old text:", "- New text:", "\tOld text:",
                     "Old text: ", "New text:\t", "   > - New text:  ", "\u00a0File: `src/other.txt`", "\u00a0Old text:",
                     "\x0cNew text:", "Old text:\u00a0", "> \u2003New text:"):
            with self.subTest(more=more):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{self.section()}\n{more}\n"),
                                 "brigade: reports/D1-review-1.md: Edit 1 has a File, Old text, or New text line after "
                                 "whitespace, a block quote mark, or a list mark, or an Old text or New text line that ends in "
                                 "whitespace")

    def test_edits_verify_and_pass_record_refuse_a_file_old_text_or_new_text_line_directly_below_a_line_that_is_none_of_empty_only_spaces_and_tabs_the_edit_heading_a_closing_fence_and_those_three_lines(self):
        cases = []
        for line in ("File: `src/greeting.txt`", "Old text:", "New text:"):
            for above, below in self.AROUND:
                body = self.one_edit_report().replace(line, "\n".join(filter(None, (above, line, below))))
                cases.append((body, f": line {body.split(chr(10)).index(line) + 1}{self.BELOW}"))
        self.assert_edits_verify_and_pass_record_refuse(cases)

    def test_edits_verify_and_pass_record_refuse_an_edit_with_a_line_that_is_not_empty_and_not_only_spaces_and_tabs_between_an_old_text_or_new_text_line_and_the_fence_below_it(self):
        cases = []
        for label, fence in (("Old text:", "```"), ("New text:", "```text")):
            for between in ("A note.\n", "\nA note.\n\n", "- item\n", "\u00a0\n", "> quoted\n\n"):
                body = self.one_edit_report().replace(f"{label}\n\n", f"{label}\n{between}")
                cases.append((body, f": line {body.split(chr(10)).index(fence) + 1}{self.APART}"))
        body = self.one_edit_report().replace("File: `src/greeting.txt`\n\nOld text:\n\n",
                                              "File: `src/greeting.txt` ``\nOld text:\n`` and a note\n\n")
        self.assert_edits_verify_and_pass_record_refuse(cases + [(body, ": line 11" + self.APART)])

    def test_edits_verify_and_pass_record_refuse_an_edit_with_a_line_that_starts_once_its_indentation_block_quote_marks_list_marks_and_end_whitespace_are_removed_with_file_and_a_backtick_and_a_path_that_starts_with_a_space_or_a_backtick_ends_at_no_backtick_or_ends_at_two(self):
        report = self.one_edit_report()
        cases = [report.replace("File: `src/greeting.txt`", line)
                 for line in ("File: `src/greeting.txt`` b`", "File: ` src/greeting.txt `", "File: ``src/greeting.txt``",
                              "File: `src/greeting.txt", "File: ``")]
        cases += [report.replace("### Edit 1\n", f"### Edit 1\n\n{line}\n")
                  for line in ("> File: ``src/other.txt``", "  - File: ` src/other.txt `", "\u00a0File: `src/other.txt``")]
        self.assert_edits_verify_and_pass_record_refuse([(body, self.MALFORMED) for body in cases])

    def test_edits_verify_and_pass_record_refuse_a_report_that_starts_with_a_byte_order_mark_and_one_that_holds_a_nul_character(self):
        report = self.one_edit_report()
        self.assert_edits_verify_and_pass_record_refuse([("\ufeff" + report, " starts with a byte order mark"),
                                                         (report.replace("wrold", "wr\0old"), " holds a NUL character"),
                                                         ("Notes\0\n\n" + report, " holds a NUL character")])

    def test_edits_verify_reads_an_edit_whose_lines_stand_directly_below_one_another_in_a_report_with_lines_that_start_with_a_digit_that_is_not_ascii_and_a_period_or_a_closing_parenthesis(self):
        reviewed = self.edits_store()
        notes = ("## Notes\n\n\u0661. ```\n\n\uff11) ~~~\n\nNote\n\u0661. ---\n\n\u0661. ## Edits\n\n"
                 "\u0967. <details>\n\n")
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n" + notes + "## Edits\n\n"
                         "### Edit 1\nFile: `src/greeting.txt`, line 1.\nOld text:\n```\nhelo\n```\nNew text:\n```text\nhello\n```\n\n"
                         "\u0661. Old text:\n\n\u0661. File: `src/other.txt`\n\n"
                         "### Edit 2\n\nFile: `src/greeting.txt`\nOld text:\n\n```\nwrold\n```\nNew text:\n```\nworld\n```\n")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    @unittest.skipIf(os.geteuid() == 0, "the superuser reads a file with no read permission")
    def test_pass_record_refuses_a_report_that_cannot_be_read(self):
        self.fired_bug_fix()
        report = self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n" + self.section())
        report.chmod(0)
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", ok=False),
                         "brigade: reports/D1-review-1.md cannot be read: Permission denied")
        self.assertEqual(self.pass_rows(), [])

    def test_pass_record_refuses_a_report_that_holds_a_carriage_return(self):
        self.fired_bug_fix()
        report = "Verdict: pass with edits\n\n" + self.section()
        for body in (report.replace("\n", "\r\n"), report.replace("wrold", "wrold\r"), "Notes\r" + report):
            with self.subTest(body=body):
                self.assertEqual(self.refused_edits(body), "brigade: reports/D1-review-1.md holds a carriage return")

    def test_pass_record_refuses_a_report_that_is_not_utf_8(self):
        self.fired_bug_fix()
        report = ("Verdict: pass with edits\n\n" + self.section()).encode()
        self.assertEqual(self.refused_edits(report.replace(b"world", b"w\xf6rld")), "brigade: reports/D1-review-1.md is not UTF-8")

    def test_edits_verify_reads_tilde_fences_and_closing_fences_with_spaces_around_them_below_notes_that_report_parts_does_not_refuse(self):
        reviewed = self.edits_store()
        notes = ("## Notes\n\n> quoted\n\n- item\n\n      nested\n\n-```\n\n```a`b\n\n``\n\n    <details>\n\n<\u00e9\n\n"
                 "< 3 items\n\n---\n\n## Rule\n---\n\n```\ncode\n```\n---\n\n- File: `src/other.txt`\n\n  Old text:\n\n"
                 "#hashtag\n\n####### seven\n\n")
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n" + notes + "## Edits\n\n"
                         "### Edit 1\n\nFile: `src/greeting.txt`\n\nOld text:\n\n~~~\nhelo\n~~~\n\n"
                         "New text:\n\n~~~~text one `two`\nhello\n  ~~~~~ \t\n\n"
                         "### Edit 2\n\nFile: `src/greeting.txt`\n\nOld text:\n\n```\nwrold\n   ```\n\nNew text:\n\n```\nworld\n```  \n")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_refuses_a_build_command_that_names_a_program_that_does_not_exist(self):
        command = "no-such-program-d152 gen"
        reviewed = self.edits_store(build=command)
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        code, out, err = self.verify(reviewed, head)
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith(f"brigade: cannot run {command}: "), err)
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_head_that_left_an_edit_unapplied(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello wrold\nsecond line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\n"
                             f"FAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_refuses_a_report_that_no_longer_has_an_edits_section(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n## Blockers\n\nNone.\n\n## Edits verified\n\nYes.\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "", "brigade: reports/D1-review-1.md holds no edit under a ## Edits heading"))
        (self.at / "reports" / "D1-review-1.md").unlink()
        self.assertEqual(self.verify(reviewed, head),
                         (1, "", "brigade: reports/D1-review-1.md does not exist; nothing verified"))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_head_whose_build_output_is_stale(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED, out=self.SOURCE.upper())
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\nedited src/greeting.txt",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_fails_when_the_check_command_exits_non_zero(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "second", "forbidden"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("helo wrold\nforbidden line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"built out/greeting.txt\nedited src/greeting.txt\nFAIL check: {self.CHECK_COMMAND} exited 3",
                          f"check saw forbidden\n{self.not_verified(reviewed, head)}"))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_when_the_build_command_exits_non_zero(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "second", "unbuildable"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("helo wrold\nunbuildable line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL build: {self.BUILD_COMMAND} exited 2",
                          f"build refuses unbuildable\n{self.not_verified(reviewed, head)}"))

    def test_edits_verify_reads_a_four_backtick_fence_around_three_backticks_and_an_empty_new_text(self):
        note = "Intro\n```\ncode\n```\nremove me\nEnd\n"
        reviewed = self.edits_store(**{"docs/note.md": note})
        self.edits_report(self.edit_block("1", "docs/note.md", "```\ncode\n```", "```\ncoda\n```", fence="````"),
                          "### Edit 2\n\nFile: `docs/note.md`:5\n\nOld text:\n\n```\nremove me\n```\n\nNew text:\n\n```\n```\n")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"docs/note.md": "Intro\n```\ncoda\n```\n\nEnd\n"})
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"edited docs/note.md\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_and_pass_record_refuse_a_path_that_is_absolute_or_has_a_dot_dot_part(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        for path in ("/srv/app/greeting.txt", "src/../../greeting.txt"):
            self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), self.edit_block("7b", path, "helo", "hello"))
            refusal = (f"brigade: reports/D1-review-1.md: Edit 7b names {path}; "
                       "a path is relative to the repository and has no .. part")
            self.assertEqual(self.verify(reviewed, head), (1, "", refusal))
            self.assertEqual(self.record_edits(reviewed, "--edits", "D1-review-1.md", ok=False), refusal)
        self.edits_report(self.edit_block("3", "src/greeting.txt", "", "hello"))
        self.assertEqual(self.verify(reviewed, head), (1, "", "brigade: reports/D1-review-1.md: Edit 3 has an empty old text"))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_leaves_the_worktree_and_its_head_unchanged(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        (self.project / "untracked.txt").write_text("mine\n")
        (self.project / "src/greeting.txt").write_text("half an edit\n")
        state = lambda: tuple(subprocess.run(("git", *command), cwd=self.project, capture_output=True, text=True,
                                             check=True).stdout
                              for command in (("status", "--porcelain"), ("rev-parse", "HEAD"), ("diff",)))
        before = state()
        self.assertEqual(before[0], " M src/greeting.txt\n?? untracked.txt\n")
        self.assertEqual(self.verify(reviewed, head)[0], 0)
        self.assertEqual(self.verify(reviewed, reviewed)[0], 1)
        self.assertEqual(state(), before)

    def test_a_pass_after_a_pass_with_edits_round_is_refused_until_the_edits_are_verified_for_that_head(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
        self.assertEqual(self.record_pass_at(head, "--late", ok=False), self.gate(reviewed, head))
        self.assertEqual(len(self.pass_rows()), 1)
        self.assertEqual(self.verify(reviewed, head)[0], 0)
        self.assertEqual(self.record_pass_at(head), "D1 passed")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", head), f"D1 at {head} passed review by {CLAUDE}")

    def test_verified_edits_do_not_lift_the_refusal_of_a_pass_at_another_head_or_at_the_same_head_written_shorter(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head)[0], 0)
        later = self.head_commit(self.FIXED + "one more line\n")
        self.assertEqual(self.record_pass_at(later, ok=False), self.gate(reviewed, later))
        self.assertEqual(self.record_pass_at(head[:12], ok=False), self.gate(reviewed, head[:12]))
        self.assertEqual(len(self.pass_rows()), 1)

    def test_a_member_pass_after_a_pass_with_edits_round_is_recorded_without_verified_edits(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "anyhead", "--verdict", "pass", "--author", CODEX,
                                      "--verifier", "grok/grok-4.7", "--member"),
                         "D1: member pass on record; D1 stays sent-back")
        self.assertEqual(self.record_pass_at("anyhead", ok=False), self.gate(reviewed, "anyhead"))

    def test_close_groups_in_progress_and_new_tickets_by_goal_and_prints_the_kind_on_the_lines_of_merged_in_progress_and_new_tickets(self):
        self.close_setup()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.record("abc", "pass")
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("dish", "D4", "--state", "in-review", "--sha", "def")
        self.brigade("pass", "record", "D4", "--sha", "def", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX,
                     "--note", "still hangs")
        code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[4:], [
            "## Merged", "",
            "- study D1 (T1): Measure capacity", "",
            "## Sent back after review", "",
            "- D4 (T5): Fix the splash", "",
            "## In progress", "",
            "### G1 activity view", "",
            "- fix D2 (T2, T3): Fix the lease check", "",
            "### Unassigned", "",
            "- upkeep D3 (T4): Follow the nightly", "",
            "## New tickets, not started", "",
            "### G1 activity view", "",
            "- fix T6: Drops a row", "",
            "### G2 current with upstream", "",
            "- polish T7: Later", "",
            "### Unassigned", "",
            "- polish T8: Tidy the docs",
            "- T9: Installer",
            "", "## Spend", "",
            "goal not recorded  wall 0m (1 open)  rounds 1  send-backs 0  threads not recorded",
            "G1  wall open  rounds 0  send-backs 0  threads not recorded",
            "G2  wall open  rounds 1  send-backs 1  threads not recorded",
        ])
