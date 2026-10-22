from ..support import BrigadeTest, setUpModule


OLD_TEN = "\n".join(f"old line {number}" for number in range(10))
NEW_TEN = "\n".join(f"new line {number}" for number in range(10))
OLD_THIRTY = "\n".join(f"old line {number}" for number in range(30))
NEW_THIRTY = "\n".join(f"new line {number}" for number in range(30))
OLD_COMMENTS = "\n".join(f"# old comment {number}" for number in range(15))
NEW_COMMENTS = "\n".join(f"# new comment {number}" for number in range(15))
OLD_DOCSTRING = '"""\n' + "\n".join(f"old docstring line {number}" for number in range(13)) + '\n"""'
NEW_DOCSTRING = '"""\n' + "\n".join(f"new docstring line {number}" for number in range(13)) + '\n"""'
RUN_LINES = "\n".join(f"x{number} = 0; y{number} = 1" for number in range(21)) + "\n"
SPLIT_LINES = "".join(f"x{number} = 0; \ny{number} = 1\n" for number in range(21))


def block(number, path, old, new):
    return (f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n```\n{old}\n```\n\n"
            f"New text:\n\n```\n{new}\n```\n")


def report(*blocks):
    return "Verdict: pass with edits\n\n## Edits\n\n" + "\n".join(blocks)


class EditsCapTest(BrigadeTest):
    def test_an_applied_change_at_exactly_20_counted_lines_verifies(self):
        reviewed = self.edits_store(**{"src/data.txt": OLD_TEN + "\n"})
        self.review_file("D1-review-1.md", report(block("1", "src/data.txt", OLD_TEN, NEW_TEN)))
        self.assertEqual(self.record_edits(reviewed, "--edits", "D1-review-1.md"), "D1 sent-back")
        head = self.head_commit(self.SOURCE, **{"src/data.txt": NEW_TEN + "\n"})
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"edited src/data.txt\nok check\n{self.verified(reviewed, head)}", ""))
        self.assertEqual(self.edits_rows(), [["D1", reviewed, head, "D1-review-1.md"]])

    def test_an_applied_change_over_20_counted_lines_is_refused_and_records_no_edits_row(self):
        reviewed = self.edits_store(**{"tools/run.py": RUN_LINES})
        edits = [block(str(number + 1), "tools/run.py", f"x{number} = 0; ", f"x{number} = 0; \n")
                 for number in range(21)]
        self.review_file("D1-review-1.md", report(*edits))
        self.assertEqual(self.record_edits(reviewed, "--edits", "D1-review-1.md"), "D1 sent-back")
        head = self.head_commit(self.SOURCE, **{"tools/run.py": SPLIT_LINES})
        self.assertEqual(self.verify(reviewed, head),
                         (1, "", "brigade: the applied change counts 63 lines, and a small code fix counts at most 20; "
                                 "it needs a second review. The count is each line the edits add or delete from the file "
                                 "at the reviewed commit to that file with the edits applied, so a replaced line counts "
                                 "two. A path ending in .md is not counted, and neither is a .py path whose code lines "
                                 "are the same once its comments, its docstrings, blank lines, and trailing whitespace "
                                 "are removed"))
        self.assertEqual(self.edits_rows(), [])

    def test_an_applied_change_to_only_comments_in_a_python_file_verifies(self):
        reviewed = self.edits_store(**{"tools/commented.py": OLD_COMMENTS + "\nvalue = 1\n"})
        self.review_file("D1-review-1.md", report(block("1", "tools/commented.py", OLD_COMMENTS, NEW_COMMENTS)))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"tools/commented.py": NEW_COMMENTS + "\nvalue = 1\n"})
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"edited tools/commented.py\nok check\n{self.verified(reviewed, head)}", ""))

    def test_an_applied_change_to_only_a_docstring_in_a_python_file_verifies(self):
        reviewed = self.edits_store(**{"tools/doc.py": OLD_DOCSTRING + "\nvalue = 1\n"})
        self.review_file("D1-review-1.md", report(block("1", "tools/doc.py", OLD_DOCSTRING, NEW_DOCSTRING)))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"tools/doc.py": NEW_DOCSTRING + "\nvalue = 1\n"})
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"edited tools/doc.py\nok check\n{self.verified(reviewed, head)}", ""))

    def test_a_mixed_applied_change_counts_only_the_paths_that_are_not_exempt(self):
        reviewed = self.edits_store(**{"docs/notes.md": OLD_THIRTY + "\n",
                                       "tools/commented.py": OLD_COMMENTS + "\nvalue = 1\n",
                                       "src/data.txt": OLD_TEN + "\n"})
        self.review_file("D1-review-1.md", report(block("1", "docs/notes.md", OLD_THIRTY, NEW_THIRTY),
                                                  block("2", "tools/commented.py", OLD_COMMENTS, NEW_COMMENTS),
                                                  block("3", "src/data.txt", OLD_TEN, NEW_TEN)))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"docs/notes.md": NEW_THIRTY + "\n",
                                                "tools/commented.py": NEW_COMMENTS + "\nvalue = 1\n",
                                                "src/data.txt": NEW_TEN + "\n"})
        self.assertEqual(self.verify(reviewed, head),
                         (0, "edited docs/notes.md\nedited src/data.txt\nedited tools/commented.py\nok check\n"
                             f"{self.verified(reviewed, head)}", ""))
