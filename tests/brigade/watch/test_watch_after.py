if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import unittest

from ..support import BrigadeTest, StoresBase, setUpModule

IDLE = "workers: 0 of 2 running, 2 idle, auto-start: normal"


class AfterWatchTest(BrigadeTest):
    def two_tickets(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")

    def test_watch_names_a_waiting_ticket_whose_dependency_is_still_open(self):
        self.two_tickets()
        self.brigade("ticket", "set", "T2", "--after", "T1")
        self.assertEqual(self.brigade("watch"), f"{IDLE}\nT2: waiting on T1")

    def test_watch_says_nothing_about_a_waiting_ticket_once_its_dependency_is_done(self):
        self.two_tickets()
        self.brigade("ticket", "set", "T2", "--after", "T1")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("watch"), f"{IDLE}\nno work in progress")

    def test_watch_names_a_dependency_that_was_dropped_and_how_to_stop_waiting(self):
        self.two_tickets()
        self.brigade("ticket", "set", "T2", "--after", "T1")
        self.brigade("ticket", "set", "T1", "--state", "dropped")
        self.assertEqual(self.brigade("watch"),
                         f"{IDLE}\nT2: waiting on T1, which was dropped and is never done; run ticket set T2 --after '' to stop waiting")

    def test_watch_names_a_dependency_that_is_not_on_this_rail(self):
        self.two_tickets()
        self.brigade("ticket", "set", "T2", "--after", "T1")
        self.at.joinpath("rail.tsv").write_text(
            self.at.joinpath("rail.tsv").read_text().replace("\nT1\t", "\nT7\t", 1))
        self.assertEqual(self.brigade("watch"), f"{IDLE}\nT2: waiting on T1, which is not on this rail")

    def test_watch_reads_an_old_rail_with_no_after_cell_as_before(self):
        self.open()
        self.old_rail()
        self.assertEqual(self.brigade("watch"), f"{IDLE}\nno work in progress")


class AfterSiblingWatchTest(StoresBase, unittest.TestCase):
    def test_watch_names_a_sibling_dependency_that_moved_and_writes_nothing_to_the_sibling(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "Fix the app", "--after", "core/T1")
        before = self.files("core")
        result = self.run_brigade("engine", "watch")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(result.stdout.strip(),
                         f"{IDLE}\nhanded to you: 1; run ticket take\nT1: waiting on core/T1, which moved to engine and is not done here; "
                         "point --after at the ticket engine filed for it")
        self.assertEqual(self.files("core"), before)


if __name__ == "__main__":
    unittest.main()
