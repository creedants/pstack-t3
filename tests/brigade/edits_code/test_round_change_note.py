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
OLD_FRAGMENT = "changes/pstack-t3%2Fd0.md"
REWORDED_NOTE = "- kind fix\n- the note the fix round reworded\n"


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

    def renamed_round(self, head_note, *note_edits):
        """(reviewed, head) of a round whose fix renamed pstack-t3/d0's note to pstack-t3/d1's and committed it."""
        reviewed = self.edits_store(**{OLD_FRAGMENT: OLD_NOTE})
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("dish", "D1", "--branch", BRANCH)
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello"), *note_edits))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        (self.project / OLD_FRAGMENT).unlink()
        return reviewed, self.applied_head(**{FRAGMENT: head_note})

    def renamed_pass(self, reviewed, head):
        return (0, f"edited {OLD_FRAGMENT}\nskipped {FRAGMENT}: this round's change note\n"
                   f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", "")

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

    def test_a_rename_with_the_edits_applied_verifies_instead_of_reporting_the_old_note_deleted(self):
        reviewed, head = self.renamed_round(REWORDED_NOTE,
                                            block("2", OLD_FRAGMENT, OLD_NOTE, REWORDED_NOTE))
        self.assertEqual(self.verify(reviewed, head), self.renamed_pass(reviewed, head))
        self.assertEqual(self.edits_rows(), [["D1", reviewed, head, "D1-review-1.md"]])

    def test_a_rename_whose_note_differs_from_the_edited_note_still_fails(self):
        reviewed, head = self.renamed_round(NOTE,
                                            block("2", OLD_FRAGMENT, OLD_NOTE, REWORDED_NOTE))
        self.assertEqual(self.edge(reviewed, head),
                         (1, [f"FAIL {OLD_FRAGMENT}: differs from {reviewed} with the edits applied",
                              f"skipped {FRAGMENT}: this round's change note",
                              "built out/greeting.txt",
                              "edited src/greeting.txt"],
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_a_rename_with_no_edit_to_the_note_and_unchanged_text_verifies(self):
        reviewed, head = self.renamed_round(OLD_NOTE)
        self.assertEqual(self.verify(reviewed, head), self.renamed_pass(reviewed, head))
        self.assertEqual(self.edits_rows(), [["D1", reviewed, head, "D1-review-1.md"]])

    def test_a_deleted_file_that_is_not_a_change_note_rename_still_fails(self):
        reviewed = self.edits_store(**{"notes/round.md": OTHER_NOTE})
        self.brigade("dish", "D1", "--branch", BRANCH)
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello")))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        (self.project / "notes/round.md").unlink()
        head = self.applied_head()
        self.assertEqual(self.edge(reviewed, head),
                         (1, ["FAIL notes/round.md: differs from " + f"{reviewed} with the edits applied",
                              "built out/greeting.txt",
                              "edited src/greeting.txt"],
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])
