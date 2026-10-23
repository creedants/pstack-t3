# The count is git's own, and the exemption stays exact.
from ..support import BrigadeTest, setUpModule
from . import cap_refusal


REPEATED_OLD = "\n".join(f'print("{char}")' for char in "caaabccc")
REPEATED_NEW = "\n".join(f'print("{char}")' for char in "ccbaacca")
UPPER_FIFTEEN = "\n".join(f"upper line {number}" for number in range(15))
LOWER_FIFTEEN = "\n".join(f"lower line {number}" for number in range(15))


def block(number, path, old, new):
    return (f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n```\n{old}\n```\n\n"
            f"New text:\n\n```\n{new}\n```\n")


def report(*blocks):
    return "Verdict: pass with edits\n\n## Edits\n\n" + "\n".join(blocks)


class EditsGitFloorTest(BrigadeTest):
    def test_three_repeated_block_files_are_refused_by_the_applied_count(self):
        reviewed = self.edits_store(**{f"tools/run{number}.py": REPEATED_OLD for number in range(3)})
        edits = [block(str(number + 1), f"tools/run{number}.py", REPEATED_OLD, REPEATED_NEW) for number in range(3)]
        self.review_file("D1-review-1.md", report(*edits))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{f"tools/run{number}.py": REPEATED_NEW for number in range(3)})
        self.assertEqual(self.verify(reviewed, head), (1, "", cap_refusal(24)))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))

    def test_a_path_ending_in_uppercase_MD_is_counted(self):
        reviewed = self.edits_store(**{"docs/notes.MD": UPPER_FIFTEEN + "\n"})
        self.review_file("D1-review-1.md", report(block("1", "docs/notes.MD", UPPER_FIFTEEN, LOWER_FIFTEEN)))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"docs/notes.MD": LOWER_FIFTEEN + "\n"})
        self.assertEqual(self.verify(reviewed, head), (1, "", cap_refusal(30)))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
