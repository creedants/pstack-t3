if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import (
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




class ReviewsTest(BrigadeTest):
    def test_pass_refuses_a_verifier_from_the_author_family(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        error = self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass",
                             "--author", CLAUDE, "--verifier", "cursor/claude-sonnet-5-5", ok=False)
        self.assertIn("same model family", error)
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass",
                                      "--author", CLAUDE, "--verifier", "cursor/claude-sonnet-5-5", "--same-family"), "D1 passed")
        self.assertIn("same model family", (self.at / "pass.tsv").read_text())

    def test_pass_check_json_prints_a_cross_family_pass(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"verdict": "pass", "author": CLAUDE, "verifier": CODEX, "note": "", "crossFamily": True})
        self.assertIn('"crossFamily": true', out)
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")

    def test_pass_check_json_exits_1_when_a_send_back_voids_the_pass(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        self.record("abc", "send-back", "--note", "test asserts the bug")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (1, "brigade: D1 at abc: send-back (test asserts the bug)"))
        self.assertIn('"verdict": "send-back"', out)
        self.assertEqual(json.loads(out)["note"], "test asserts the bug")

    def test_pass_check_json_marks_a_same_family_pass(self):
        self.fired_bug_fix()
        self.record("abc", "pass", "--same-family", verifier="cursor/claude-sonnet-5-5")
        self.record("def", "pass", "--same-family")
        with (self.at / "pass.tsv").open("a") as table:
            table.write(f"2026-10-08T00:00:00+00:00\tD1\t\tghi\tpass\t{CLAUDE}\tcursor/claude-sonnet-5-5\t\n")
        for sha, note in (("abc", "same model family;"), ("def", "same model family;"), ("ghi", "")):
            code, out, err = self.check_json(sha)
            self.assertEqual((code, err), (0, ""))
            self.assertIn('"crossFamily": false', out)
            self.assertEqual(json.loads(out)["note"], note)

    def test_pass_record_refuses_a_bedrock_claude_author_with_a_claude_verifier(self):
        self.assert_one_family(self.BEDROCK, CLAUDE)

    def test_pass_record_refuses_an_underscored_claude_author_with_a_claude_verifier(self):
        self.assert_one_family("anthropic/claude_opus_5", "cursor/claude-sonnet-5-5")

    def test_pass_record_refuses_a_dated_claude_author_with_a_dated_claude_verifier(self):
        self.assert_one_family("vertex/claude-sonnet-5-5@20260101", "claudeAgent/claude-haiku-5-5-20260301")

    def test_pass_record_refuses_a_namespaced_muse_author_with_a_muse_verifier(self):
        self.assert_one_family(self.NAMESPACED_MUSE, "openrouter/muse-lite-2")

    def test_pass_record_records_two_families_with_no_flag(self):
        self.fired_bug_fix()
        for sha, author, verifier in (("abc", CLAUDE, CODEX), ("def", self.NAMESPACED_MUSE, CLAUDE)):
            self.assertEqual(self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "pass",
                                          "--author", author, "--verifier", verifier), "D1 passed")
        self.assertEqual([row[5:8] for row in self.pass_rows()], [[CLAUDE, CODEX, ""], [self.NAMESPACED_MUSE, CLAUDE, ""]])

    def test_pass_check_json_reads_a_stored_bedrock_row_as_one_family(self):
        self.fired_bug_fix()
        with (self.at / "pass.tsv").open("a") as table:
            table.write(f"2026-10-08T00:00:00+00:00\tD1\t\tabc\tpass\t{self.BEDROCK}\t{CLAUDE}\t\n")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertIn('"crossFamily": false', out)

    def test_pass_check_json_at_another_sha_has_no_verdict(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        self.assertEqual(self.check_json("def"), (1, "", "brigade: D1 has no review verdict for def"))

    def test_a_pass_table_holds_rows_written_before_and_after_the_report_and_member_columns(self):
        self.fired_bug_fix()
        stamp = "2026-10-08T00:00:00+00:00"
        old = f"{stamp}\tD1\t\tabc\tpass\t{CLAUDE}\t{CODEX}\told row"
        table = self.at / "pass.tsv"
        table.write_text("at\tdish\tpr\tsha\tverdict\tauthor\tverifier\tnote\n" + old + "\n"
                         + f"{stamp}\tD1\t\tdef\tsend-back\t{CLAUDE}\t{CODEX}\tnew row\tD1-review-2.md\t\n")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "def", ok=False),
                         "brigade: D1 at def: send-back (new row)")
        self.record("ghi", "pass")
        self.assertEqual(table.read_text().splitlines()[1], old)
        self.assertEqual(self.pass_rows()[2][3:], ["ghi", "pass", CLAUDE, CODEX, "", "", "", ""])
        for fields, line in ((old.split("\t")[:7], 5), ([*old.split("\t"), "r.md", "", "extra", "extra"], 5)):
            table.write_text(table.read_text() + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                             f"brigade: bridge-kit/perf/pass.tsv line {line} is malformed; fix or remove it")
            table.write_text("\n".join(table.read_text().splitlines()[:-1]) + "\n")

    def test_pass_record_stores_the_bare_name_of_an_existing_review_report(self):
        self.fired_bug_fix()
        path = self.review_file("D1-review-1.md")
        self.review_file("D1-review.md")
        self.review_file("D1-review-2-panel-1.md")
        self.record("abc", "send-back", "--report", str(path))
        self.record("abc", "send-back", "--report", "reports/D1-review.md")
        self.record("abc", "send-back", "--report", "D1-review-2-panel-1.md")
        self.assertEqual([row[8] for row in self.pass_rows()], ["D1-review-1.md", "D1-review.md", "D1-review-2-panel-1.md"])
        before = (self.at / "pass.tsv").read_bytes()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.brigade(*record, "--report", "D1-review-9.md", ok=False),
                         "brigade: reports/D1-review-9.md does not exist; write the review report first")
        self.review_file("D2-review-1.md")
        self.review_file("D1.md")
        self.review_file("D11-review.md")
        for name in ("D2-review-1.md", "D1.md", "D11-review.md"):
            self.assertEqual(self.brigade(*record, "--report", name, ok=False),
                             f"brigade: {name} is not a review report of D1; name a file like reports/D1-review-1.md")
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)
        self.assertEqual(self.dish_fields("state"), ("sent-back",))

    def test_pass_record_refuses_a_report_path_outside_the_reports_directory_of_the_store(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md")
        elsewhere = Path(self.temporary.name) / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "D1-review-1.md").write_text("another file\n")
        before = (self.at / "pass.tsv").read_bytes()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        for report in (str(elsewhere / "D1-review-1.md"), "no/such/dir/D1-review-1.md"):
            self.assertEqual(self.brigade(*record, "--report", report, ok=False),
                             f"brigade: {report} is outside this store's reports/; name reports/D1-review-1.md")
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)

    def test_pass_record_refuses_a_directory_a_live_link_a_dangling_link_and_a_file_under_a_linked_reports(self):
        self.fired_bug_fix()
        reports = self.review_file("D1-review-4.md").parent
        outside = Path(self.temporary.name) / "outside"
        (outside / "reports").mkdir(parents=True)
        (outside / "findings.md").write_text("findings\n")
        (outside / "reports" / "D1-review-5.md").write_text("findings\n")
        (reports / "D1-review-1.md").mkdir()
        (reports / "D1-review-2.md").symlink_to(outside / "findings.md")
        (reports / "D1-review-3.md").symlink_to(outside / "gone.md")
        self.record("abc", "send-back", "--report", "D1-review-4.md")
        before = (self.at / "pass.tsv").read_bytes()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.brigade(*record, "--report", "D1-review-1.md", ok=False),
                         "brigade: reports/D1-review-1.md is not a regular file; nothing recorded")
        self.assertEqual(self.brigade(*record, "--report", "D1-review-2.md", ok=False),
                         "brigade: reports/D1-review-2.md is a symbolic link; nothing recorded")
        self.assertEqual(self.brigade(*record, "--report", "D1-review-3.md", ok=False),
                         "brigade: reports/D1-review-3.md is a symbolic link; nothing recorded")
        reports.rename(self.at / "kept")
        reports.symlink_to(outside / "reports")
        self.assertEqual(self.brigade(*record, "--report", "D1-review-5.md", ok=False),
                         "brigade: reports/D1-review-5.md resolves outside this store's reports/; nothing recorded")
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)
        self.assertEqual([row[8] for row in self.pass_rows()], ["D1-review-4.md"])

    def test_a_member_send_back_recorded_before_the_passing_rows_changes_no_verdict(self):
        self.fired_bug_fix()
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/9", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", "grok/grok-4.7", "--note", "nit", "--member"),
                         "D1: member send-back on record; D1 stays in-progress")
        self.assertEqual(self.dish_fields("state", "pr", "sha"), ("in-progress", "", ""))
        self.assertEqual(self.check_json("abc"), (1, "", "brigade: D1 has no review verdict for abc"))
        self.record("abc", "pass")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"verdict": "pass", "author": CLAUDE, "verifier": CODEX, "note": "", "crossFamily": True})
        self.assertEqual([row[4:] for row in self.pass_rows()],
                         [["send-back", CLAUDE, "grok/grok-4.7", "nit", "", "yes", ""], ["pass", CLAUDE, CODEX, "", "", "", ""]])

    def test_a_member_row_follows_the_family_rule_and_takes_no_late_flag(self):
        self.fired_bug_fix()
        member = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--member")
        self.assertIn("same model family", self.brigade(*member, "--verifier", "cursor/claude-sonnet-5-5", ok=False))
        self.assertIn("not allowed with argument", self.brigade(*member, "--verifier", CODEX, "--late", ok=False))
        self.assertEqual(self.pass_rows(), [])

    def test_pass_record_for_an_unknown_item_leaves_no_row(self):
        self.fired_bug_fix()
        for flags in ((), ("--member",), ("--late",)):
            self.assertEqual(self.brigade("pass", "record", "D9", "--sha", "abc", "--verdict", "pass",
                                          "--author", CLAUDE, "--verifier", CODEX, *flags, ok=False),
                             "brigade: no D9 in dishes.tsv")
        self.assertEqual(self.pass_rows(), [])

    def test_pass_record_writes_the_reviewer_row_and_child_rows_with_the_thread_and_the_report(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n")
        self.record("abc", "pass")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  f"- code delegate `{THREAD_B}` opencode-go/step-5-preview-free\n"
                                  f"- claims check `{THREAD_C}` {CODEX}\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, "--reviewer-thread", THREAD_D),
                         "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                          ["D1", "1", "reviewer", THREAD_D, CODEX, "", "stamp"],
                          ["D1", "1", "code delegate", THREAD_B, "opencode-go/step-5-preview-free", "", "stamp"],
                          ["D1", "1", "claims check", THREAD_C, CODEX, "", "stamp"]])
        after = (self.at / "threads.tsv").read_bytes()
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, "--reviewer-thread", THREAD_D,
                                      "--same-family"), "D1 sent-back")
        self.assertEqual((self.at / "threads.tsv").read_bytes(), after)

    def test_pass_record_reads_a_report_with_a_threads_section_with_none_and_with_one_unreadable_line(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n")
        self.record("abc", "pass")
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  f"- claims check `{THREAD_B}` grok/grok-4.7\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX), "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                          ["D1", "1", "claims check", THREAD_B, "grok/grok-4.7", "", "stamp"]])
        self.review_file("D1.md", "findings\n\n## Threads\n\n- claims check\n")
        before = self.store_files()
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "ghi", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, ok=False),
                         "brigade: D1: reports/D1.md cannot be read; line 5 is not one list item of the form "
                         "- <role> `<thread id>` <seat>; nothing recorded")
        self.assertEqual(self.store_files(), before)
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                          ["D1", "1", "claims check", THREAD_B, "grok/grok-4.7", "", "stamp"]])

    def test_a_prose_line_in_a_threads_section_skips_the_line_and_records_both_rows(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  f"- code delegate `{THREAD_B}` opencode-go/step-5-preview-free\n"
                                  "Both children finished before the reviewer read the report.\n"
                                  f"- claims check `{THREAD_C}` {CODEX}\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX), "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                          ["D1", "1", "code delegate", THREAD_B, "opencode-go/step-5-preview-free", "", "stamp"],
                          ["D1", "1", "claims check", THREAD_C, CODEX, "", "stamp"]])

    def test_a_malformed_list_item_in_a_threads_section_is_refused_by_its_number(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  f"- code delegate {THREAD_B} opencode-go/step-5-preview-free\n")
        before = self.store_files()
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, ok=False),
                         "brigade: D1: reports/D1.md cannot be read; line 5 is not one list item of the form "
                         "- <role> `<thread id>` <seat>; nothing recorded")
        self.assertEqual(self.store_files(), before)
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])
