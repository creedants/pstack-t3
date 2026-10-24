if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import BrigadeTest, setUpModule
from . import cap_refusal


VALUE_BEFORE = 'X = """\n' + "".join(f"line{number} \n" for number in range(15)) + '"""'
VALUE_AFTER = VALUE_BEFORE.replace(" \n", "\n")
BLANKS_BEFORE = 'X = """\n' + "".join(f"line{number}\n\n" for number in range(21)) + '"""'
BLANKS_AFTER = 'X = """\n' + "".join(f"line{number}\n" for number in range(21)) + '"""'
EOF_BEFORE = "".join(f"x{number} = 0\n" for number in range(9)) + "tail = 0"
EOF_AFTER = "added = 1\n" + "".join(f"x{number} = 1\n" for number in range(9)) + "tail = 0\n"
DOC_BEFORE = "".join(f'def f{number}(): "old"; return {number}\n' for number in range(15))
DOC_AFTER = DOC_BEFORE.replace('"old"', '"a longer new docstring"')


def block(number, path, old, new):
    return (f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n```\n{old}\n```\n\n"
            f"New text:\n\n```\n{new}\n```\n")


def report(*blocks):
    return "Verdict: pass with edits\n\n## Edits\n\n" + "\n".join(blocks)


class EditsAstExemptionTest(BrigadeTest):
    def test_trailing_whitespace_inside_a_multiline_value_is_refused_by_the_count(self):
        reviewed = self.edits_store(**{"tools/value.py": VALUE_BEFORE + "\n"})
        self.review_file("D1-review-1.md", report(block("1", "tools/value.py", VALUE_BEFORE, VALUE_AFTER)))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"tools/value.py": VALUE_AFTER + "\n"})
        self.assertEqual(self.verify(reviewed, head), (1, "", cap_refusal(30)))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))

    def test_blank_lines_inside_a_multiline_value_are_refused_by_the_count(self):
        reviewed = self.edits_store(**{"tools/blanks.py": BLANKS_BEFORE + "\n"})
        self.review_file("D1-review-1.md", report(block("1", "tools/blanks.py", BLANKS_BEFORE, BLANKS_AFTER)))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"tools/blanks.py": BLANKS_AFTER + "\n"})
        self.assertEqual(self.verify(reviewed, head), (1, "", cap_refusal(21)))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))

    def test_an_unterminated_last_line_gaining_a_newline_is_refused_by_the_count(self):
        reviewed = self.edits_store(**{"tools/eof.py": EOF_BEFORE})
        self.review_file("D1-review-1.md", report(block("1", "tools/eof.py", EOF_BEFORE, EOF_AFTER)))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"tools/eof.py": EOF_AFTER})
        self.assertEqual(self.verify(reviewed, head), (1, "", cap_refusal(21)))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))

    def test_a_docstring_followed_by_code_on_its_line_verifies(self):
        reviewed = self.edits_store(**{"tools/doc_code.py": DOC_BEFORE})
        self.review_file("D1-review-1.md", report(block("1", "tools/doc_code.py", DOC_BEFORE[:-1], DOC_AFTER[:-1])))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"tools/doc_code.py": DOC_AFTER})
        self.assertEqual(self.verify(reviewed, head),
                         (0, "edited tools/doc_code.py\nok check\n"
                             f"{self.verified(reviewed, head)}", ""))
