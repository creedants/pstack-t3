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


class AfterStartableTest(BrigadeTest):
    def test_a_held_ticket_is_blocked_on_its_dependency_even_while_the_worker_cap_is_full(self):
        self.queue("--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("ticket", "set", "T2", "--after", "T1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one", "--paths", "src")
        lines = self.brigade("startable").splitlines()
        self.assertEqual(lines[0], "workers: 1 of 1 running, 0 idle, auto-start: normal")
        self.assertEqual(lines[1], "waiting tickets: 1, blocked: 1")
        self.assertEqual(lines[2], "T2 normal fix, 0m: blocked, waiting on T1")

    def test_a_dependency_that_is_done_frees_the_ticket_to_start_and_names_its_summary(self):
        self.queue()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two", "--paths", "src")
        self.brigade("ticket", "set", "T2", "--after", "T1")
        self.brigade("ticket", "set", "T1", "--state", "done")
        lines = self.brigade("startable").splitlines()
        self.assertEqual(lines[1], "waiting tickets: 1, startable: 1")
        self.assertEqual(lines[2], "T2 normal fix, 0m: startable; src")
        self.assertEqual(lines[3], "  two")


class AfterSiblingStartableTest(StoresBase, unittest.TestCase):
    def test_a_ticket_waiting_on_an_open_sibling_ticket_is_blocked_and_names_the_sibling(self):
        self.open("core")
        self.open("engine")
        self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the app", "--after", "engine/T1")
        lines = self.brigade("core", "startable").splitlines()
        self.assertEqual(lines[1], "waiting tickets: 1, blocked: 1")
        self.assertEqual(lines[2], "T1 normal fix, 0m: blocked, waiting on engine/T1")

    def test_a_ticket_waiting_on_a_sibling_whose_rail_cannot_be_read_is_blocked_with_the_reason(self):
        self.open("core")
        self.open("engine")
        self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the app", "--after", "engine/T1")
        self.dir("engine").joinpath("rail.tsv").write_text("id\nT1\tbroken\n")
        lines = self.brigade("core", "startable").splitlines()
        self.assertEqual(lines[2], "T1 normal fix, 0m: blocked, waiting on engine/T1; cannot read engine's rail")


if __name__ == "__main__":
    unittest.main()
