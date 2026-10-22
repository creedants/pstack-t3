from ..support import BrigadeTest, setUpModule


OLD_TEN = "\n".join(f"old line {number}" for number in range(10))
NEW_TEN = "\n".join(f"new line {number}" for number in range(10))
NEW_ELEVEN = NEW_TEN + "\nnew line 10"
OLD_FIFTEEN = "\n".join(f"old line {number}" for number in range(15))
NEW_FIFTEEN = "\n".join(f"new line {number}" for number in range(15))
MARKDOWN = "t3/added/brigade/verdict-protocol.md"


def block(number, path, old, new):
    return (f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n```\n{old}\n```\n\n"
            f"New text:\n\n```\n{new}\n```\n")


def report(*blocks):
    return "Verdict: pass with edits\n\n## Edits\n\n" + "\n".join(blocks)


class EditsCapTest(BrigadeTest):
    def record_report(self, body, ok=True):
        self.review_file("D1-review-1.md", body)
        return self.record_edits("abc", "--edits", "reports/D1-review-1.md", ok=ok)

    def test_a_code_edit_report_at_exactly_20_changed_lines_is_recorded_because_the_cap_refuses_only_more_than_20(self):
        self.fired_bug_fix()
        self.assertEqual(self.record_report(report(block("1", "src/greeting.txt", OLD_TEN, NEW_TEN))), "D1 sent-back")
        self.assertEqual([(row[3], row[4], row[8]) for row in self.pass_rows()],
                         [("abc", "pass-with-edits", "D1-review-1.md")])

    def test_a_code_edit_report_at_21_changed_lines_is_refused_with_the_cap_message_and_writes_no_pass_row(self):
        self.fired_bug_fix()
        self.assertEqual(self.record_report(report(block("1", "src/greeting.txt", OLD_TEN, NEW_ELEVEN)), ok=False),
                         "brigade: reports/D1-review-1.md: its edits change 21 lines; a report whose edits name a file "
                         "whose path does not end in .md changes at most 20 lines in total, counting a deleted or "
                         "inserted line as one and a replaced line as two")
        self.assertEqual(self.pass_rows(), [])

    def test_a_mixed_report_over_the_cap_is_refused_though_its_code_edit_alone_is_under_it(self):
        self.fired_bug_fix()
        code = block("1", "src/greeting.txt", "old one\nold two\nold three", "new one\nnew two\nnew three")
        wording = block("2", MARKDOWN, "\n".join(f"old line {number}" for number in range(7)),
                        "\n".join(f"new line {number}" for number in range(8)))
        self.assertEqual(self.record_report(report(code, wording), ok=False),
                         "brigade: reports/D1-review-1.md: its edits change 21 lines; a report whose edits name a file "
                         "whose path does not end in .md changes at most 20 lines in total, counting a deleted or "
                         "inserted line as one and a replaced line as two")
        self.assertEqual(self.pass_rows(), [])

    def test_a_markdown_only_report_over_20_changed_lines_is_recorded_because_the_cap_skips_md_files(self):
        self.fired_bug_fix()
        self.assertEqual(self.record_report(report(block("1", MARKDOWN, OLD_FIFTEEN, NEW_FIFTEEN))), "D1 sent-back")
        self.assertEqual([(row[3], row[4], row[8]) for row in self.pass_rows()],
                         [("abc", "pass-with-edits", "D1-review-1.md")])
