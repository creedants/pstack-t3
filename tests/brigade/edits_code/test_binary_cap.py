# A binary file over the cap is refused by the every-line count.
import os
from unittest import mock

from ..support import BrigadeTest, setUpModule
from . import cap_refusal


HEADER = "\x00BINARY\n"
OLD_BODY = "\n".join(f"old line {number}" for number in range(10))
NEW_BODY = "\n".join(f"new line {number}" if number == 5 else f"old line {number}" for number in range(10))
OLD_BIN = HEADER + OLD_BODY + "\n"
NEW_BIN = HEADER + NEW_BODY + "\n"


def block(number, path, old, new):
    return (f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n```\n{old}\n```\n\n"
            f"New text:\n\n```\n{new}\n```\n")


def report(*blocks):
    return "Verdict: pass with edits\n\n## Edits\n\n" + "\n".join(blocks)


class EditsBinaryCapTest(BrigadeTest):
    def test_a_binary_file_over_the_cap_is_refused_by_the_every_line_count(self):
        reviewed = self.edits_store(**{"tools/data.bin": OLD_BIN})
        self.review_file("D1-review-1.md", report(block("1", "tools/data.bin", "old line 5", "new line 5")))
        self.assertEqual(self.record_edits(reviewed, "--edits", "D1-review-1.md"), "D1 sent-back")
        head = self.head_commit(self.SOURCE, **{"tools/data.bin": NEW_BIN})
        with mock.patch.dict(os.environ, {"PSTACK_TEST_REAL_PROCESS": "1"}):
            self.assertEqual(self.verify(reviewed, head), (1, "", cap_refusal(22)))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
