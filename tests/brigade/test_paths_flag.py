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
    SCRIPT,
    _owner_words,
    call,
)


class PathsFlagTest(BrigadeTest):
    REFUSAL = "brigade: --paths given twice. Pass one comma-separated value."

    def refused(self, *args):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), *(str(arg) for arg in args)])
        return outcome.code, outcome.stderr_text().strip()

    def test_ticket_add_refuses_a_second_paths_flag_and_changes_nothing(self):
        self.open()
        before = self.store_files()
        self.assertEqual(self.refused("ticket", "add", "--kind", "fix", "--summary", "s",
                                      "--paths", "a.txt", "--paths", "b.txt"),
                         (1, self.REFUSAL))
        self.assertEqual(self.store_files(), before)

    def test_ticket_set_refuses_a_second_paths_flag_and_changes_nothing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        before = self.store_files()
        self.assertEqual(self.refused("ticket", "set", "T1",
                                      "--paths", "a.txt", "--paths", "b.txt"),
                         (1, self.REFUSAL))
        self.assertEqual(self.store_files(), before)

    def test_fire_refuses_a_second_paths_flag_and_changes_nothing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        before = self.store_files()
        self.assertEqual(self.refused("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                      "--paths", "a.txt", "--paths", "b.txt"),
                         (1, self.REFUSAL))
        self.assertEqual(self.store_files(), before)

    def test_dish_refuses_a_second_paths_flag_and_changes_nothing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s")
        before = self.store_files()
        self.assertEqual(self.refused("dish", "D1",
                                      "--paths", "a.txt", "--paths", "b.txt"),
                         (1, self.REFUSAL))
        self.assertEqual(self.store_files(), before)

    def test_brief_refuses_a_second_paths_flag_and_changes_nothing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s")
        self.brigade("set", "--thread", "thread-coord")
        before = self.store_files()
        self.assertEqual(self.refused("brief", "D1", "--goal", "g", "--acceptance", "a",
                                      "--contract", "c", "--verify", "v", "--base", "origin/main",
                                      "--paths", "a.txt", "--paths", "b.txt"),
                         (1, self.REFUSAL))
        self.assertEqual(self.store_files(), before)
