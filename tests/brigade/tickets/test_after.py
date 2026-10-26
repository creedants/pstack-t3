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


class AfterTicketTest(BrigadeTest):
    def ledger(self):
        return (self.at / "rail.tsv").read_bytes(), (self.at / "log.tsv").read_bytes()

    def file_two_waiting(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")

    def test_an_old_rail_without_an_after_cell_reads_as_no_dependency_and_a_rewrite_adds_the_cell_and_changes_no_other_cell(self):
        self.open()
        self.old_rail()
        old = [line.split("\t") for line in (self.at / "rail.tsv").read_text().splitlines()[1:]]
        self.assertEqual(self.brigade("startable").splitlines()[1], "waiting tickets: 3, unknown: 3")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")
        lines = (self.at / "rail.tsv").read_text().splitlines()
        rows = [line.split("\t") for line in lines[1:]]
        self.assertEqual(lines[0].split("\t")[-1], "after")
        self.assertEqual([row[:7] for row in rows], [row[:7] for row in old])
        self.assertEqual([row[7:] for row in rows], [["low", "", "", "", "", ""], ["", "", "", "", "", ""], ["", "", "", "", "", ""]])

    def test_ticket_add_refuses_an_unknown_ticket_as_its_dependency_and_writes_nothing(self):
        self.file_two_waiting()
        before = self.ledger()
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "three", "--after", "T9", code=1),
                         "brigade: T9 is not on this rail; nothing changed")
        self.assertEqual(self.ledger(), before)

    def test_ticket_set_refuses_a_ticket_as_its_own_dependency_and_writes_nothing(self):
        self.file_two_waiting()
        before = self.ledger()
        self.assertEqual(self.brigade("ticket", "set", "T1", "--after", "T1", code=1),
                         "brigade: T1 cannot wait on itself; nothing changed")
        self.assertEqual(self.ledger(), before)

    def test_ticket_set_refuses_a_dependency_that_already_waits_on_the_ticket_and_writes_nothing(self):
        self.file_two_waiting()
        self.assertEqual(self.brigade("ticket", "set", "T2", "--after", "T1"), "T2 after T1")
        before = self.ledger()
        self.assertEqual(self.brigade("ticket", "set", "T1", "--after", "T2", code=1),
                         "brigade: T1 would wait on itself through T2; nothing changed")
        self.assertEqual(self.ledger(), before)

    def test_ticket_set_after_an_empty_text_clears_the_dependency_and_the_ticket_lists_as_before(self):
        self.file_two_waiting()
        listed = self.brigade("ticket", "list")
        self.brigade("ticket", "set", "T2", "--after", "T1")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--after", ""), "T2 no after")
        self.assertEqual(self.brigade("ticket", "list"), listed)
        self.assertEqual(self.brigade("startable").splitlines()[1], "waiting tickets: 2, unknown: 2")

    def test_ticket_set_after_is_refused_on_a_ticket_that_is_not_waiting(self):
        self.file_two_waiting()
        self.brigade("ticket", "set", "T2", "--state", "done")
        before = self.ledger()
        self.assertEqual(self.brigade("ticket", "set", "T2", "--after", "T1", code=1),
                         "brigade: T2 is done; --after changes a waiting ticket only")
        self.assertEqual(self.ledger(), before)

    def test_after_refuses_a_text_that_is_neither_a_ticket_number_nor_a_sibling_ticket(self):
        self.file_two_waiting()
        before = self.ledger()
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "three", "--after", "next", code=1),
                         "brigade: next is not a ticket; use T<n> or <sibling>/T<n>")
        self.assertEqual(self.ledger(), before)

    def test_after_with_from_report_is_refused_and_writes_nothing(self):
        self.file_two_waiting()
        before = self.ledger()
        self.assertEqual(self.brigade("ticket", "add", "--from-report", "D1", "--after", "T1", code=1),
                         "brigade: --from-report takes no --after")
        self.assertEqual(self.ledger(), before)

    def test_fire_refuses_a_ticket_whose_dependency_is_not_done_and_writes_nothing(self):
        self.file_two_waiting()
        self.brigade("ticket", "set", "T2", "--after", "T1")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "two", code=1),
                         "brigade: T2 is waiting on T1")
        self.assertEqual(self.store_files(), before)


class AfterSiblingTicketTest(StoresBase, unittest.TestCase):
    def ledger(self, name):
        return (self.dir(name) / "rail.tsv").read_bytes(), (self.dir(name) / "log.tsv").read_bytes()

    def refusal(self, name, *args):
        result = self.run_brigade(name, *args)
        return result.returncode, result.stderr.strip()

    def core_and_engine(self):
        """core is open with no ticket. engine files T1, the ticket a dependency names."""
        self.open("core")
        self.open("engine")
        self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache")

    def test_ticket_add_refuses_a_sibling_the_restaurant_does_not_list_and_writes_nothing(self):
        self.open("core")
        before = self.ledger("core")
        self.assertEqual(self.refusal("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the app", "--after", "ghost/T1"),
                         (1, f"brigade: ghost is not a sibling coordinator on {self.project}"))
        self.assertEqual(self.ledger("core"), before)

    def test_ticket_add_refuses_a_sibling_whose_rail_cannot_be_read_and_writes_nothing(self):
        self.core_and_engine()
        (self.dir("engine") / "rail.tsv").write_text("id\nT1\tbroken\n")
        before = self.ledger("core")
        self.assertEqual(self.refusal("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the app", "--after", "engine/T1"),
                         (1, "brigade: cannot read engine's rail; nothing changed"))
        self.assertEqual(self.ledger("core"), before)

    def test_ticket_set_refuses_a_dependency_that_closes_a_cycle_through_a_sibling_and_writes_nothing(self):
        self.core_and_engine()
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the app")
        self.brigade("core", "ticket", "set", "T1", "--after", "engine/T1")
        before = self.ledger("engine")
        self.assertEqual(self.refusal("engine", "ticket", "set", "T1", "--after", "core/T1"),
                         (1, "brigade: T1 would wait on itself through core/T1; nothing changed"))
        self.assertEqual(self.ledger("engine"), before)

    def test_ticket_move_refuses_a_ticket_with_a_dependency_until_the_dependency_is_cleared(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the app", "--after", "T1")
        self.assertEqual(self.refusal("core", "ticket", "move", "T2", "--to", "engine"),
                         (1, "brigade: T2 waits on T1; ticket move does not carry it; run ticket set T2 --after '' first"))
        self.brigade("core", "ticket", "set", "T2", "--after", "")
        self.assertEqual(self.brigade("core", "ticket", "move", "T2", "--to", "engine").split(";")[0], "T2 moved to engine")
