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


BRANCH = "pstack-t3/d1"
FRAGMENT = "changes/pstack-t3%2Fd1.md"
OTHER_FRAGMENT = "changes/pstack-t3%2Fother.md"
NOTE = "- kind fix\n- edits verify skips the round's own change note\n"
OTHER_NOTE = "- kind fix\n- a note for another branch\n"
OLD_NOTE = "- kind fix\n- the note the reviewed commit holds\n"


def block(number, path, old, new):
    return (f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n```\n{old}\n```\n\n"
            f"New text:\n\n```\n{new}\n```\n")


def report(*blocks):
    return "Verdict: pass with edits\n\n## Edits\n\n" + "\n".join(blocks)


class EditsRoundChangeNoteTest(BrigadeTest):
    def note_store(self, **files):
        """D1 in a store that records changes as its change-note directory, on branch pstack-t3/d1, with an edits round on record."""
        reviewed = self.edits_store(**files)
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("dish", "D1", "--branch", BRANCH)
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello")))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        return reviewed

    def applied_head(self, **files):
        """The new head of a round that applies the edit verbatim and commits these extra files."""
        return self.head_commit(self.QUOTED_APPLIED, **files)

    def edge(self, reviewed, head):
        """(exit code, the lines a refused verify prints, the refusal) of edits verify for the round."""
        code, out, err = self.verify(reviewed, head)
        return code, out.splitlines(), err

    def test_a_round_that_applies_the_edits_and_adds_its_own_change_note_verifies(self):
        reviewed = self.note_store()
        head = self.applied_head(**{FRAGMENT: NOTE})
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"skipped {FRAGMENT}: this round's change note\nbuilt out/greeting.txt\n"
                             f"edited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))
        self.assertEqual(self.edits_rows(), [["D1", reviewed, head, "D1-review-1.md"]])

    def test_a_round_that_adds_another_branchs_change_note_still_fails(self):
        reviewed = self.note_store()
        head = self.applied_head(**{OTHER_FRAGMENT: OTHER_NOTE})
        self.assertEqual(self.edge(reviewed, head),
                         (1, [f"FAIL {OTHER_FRAGMENT}: differs from {reviewed} with the edits applied",
                              "built out/greeting.txt",
                              "edited src/greeting.txt"],
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_a_round_that_changes_an_existing_change_note_still_fails(self):
        reviewed = self.note_store(**{FRAGMENT: OLD_NOTE})
        head = self.applied_head(**{FRAGMENT: NOTE})
        self.assertEqual(self.edge(reviewed, head),
                         (1, [f"FAIL {FRAGMENT}: differs from {reviewed} with the edits applied",
                              "built out/greeting.txt",
                              "edited src/greeting.txt"],
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_a_round_that_adds_another_file_still_fails(self):
        reviewed = self.note_store()
        head = self.applied_head(**{"notes/round.md": OTHER_NOTE})
        self.assertEqual(self.edge(reviewed, head),
                         (1, ["FAIL notes/round.md: " + f"differs from {reviewed} with the edits applied",
                              "built out/greeting.txt",
                              "edited src/greeting.txt"],
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_a_store_with_no_change_note_directory_skips_nothing(self):
        reviewed = self.edits_store()
        self.brigade("dish", "D1", "--branch", BRANCH)
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello")))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.applied_head(**{FRAGMENT: NOTE})
        self.assertEqual(self.edge(reviewed, head),
                         (1, [f"FAIL {FRAGMENT}: differs from {reviewed} with the edits applied",
                              "built out/greeting.txt",
                              "edited src/greeting.txt"],
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])
