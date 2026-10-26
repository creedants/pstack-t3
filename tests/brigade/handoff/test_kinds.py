if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import json


class HandoffKindsMixin:
    def test_ticket_move_writes_the_kind_and_no_goal_into_the_handoff_and_says_which_goal_the_ticket_leaves(self):
        said = "T1 moved to engine; no thread recorded for engine; it leaves G1 here and arrives unassigned"
        self.assertEqual(self.labelled_handoff(), said)
        self.assertEqual(json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()),
                         {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "user", "ref": "",
                          "priority": "", "paths": "", "decision": "", "kind": "polish"})
        self.assertEqual(self.rows("core", "rail.tsv")[0][2:],
                         ["moved", "user", "", "to:engine", "Tidy the guide", "", "", "", "polish", "G1", ""])
        self.assertEqual(self.brigade("core", "ticket", "move", "T1", "--to", "engine"), said)

    def test_ticket_take_files_a_moved_ticket_with_its_kind_and_no_goal(self):
        self.labelled_handoff()
        self.brigade("engine", "goal", "add", "--name", "docs", "--purpose", "p", "--done", "d")
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][2:],
                         ["waiting", "user (from app/core/T1)", "", "", "Tidy the guide", "", "", "", "polish", "", ""])

    def test_ticket_move_of_a_ticket_with_no_kind_and_no_goal_writes_the_seven_keys_the_handoff_had_before_kinds(self):
        self.open("core")
        self.open("engine")
        (self.dir("core") / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n"
            "T1\t2026-10-01T00:00:00+00:00\twaiting\tuser\t\t\tTidy the guide\tlow\tdocs\t\n")
        self.assertEqual(self.brigade("core", "ticket", "move", "T1", "--to", "engine"),
                         "T1 moved to engine; no thread recorded for engine")
        self.assertEqual((self.dir("engine") / "inbox" / "app~core~T1.json").read_text(), json.dumps(
            {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "user", "ref": "",
             "priority": "low", "paths": "docs", "decision": ""}, indent=2) + "\n")

    def test_ticket_move_of_a_ticket_whose_kind_cell_holds_no_kind_writes_no_kind_into_the_handoff(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--summary", "Tidy the guide", "--kind", "polish")
        rail = self.dir("core") / "rail.tsv"
        rail.write_text(rail.read_text().replace("\tpolish\t", "\tbugfix\t"))
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertNotIn("kind", json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()))
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["", "", ""])

    def test_ticket_take_files_a_handoff_with_no_kind_key_as_a_ticket_with_no_kind_and_no_goal(self):
        self.assertEqual(self.take_handoff(), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][2:],
                         ["waiting", "user (from app/core/T1)", "", "", "Tidy the guide", "low", "docs", "", "", "", ""])

    def test_ticket_take_files_a_handoff_with_a_kind_as_a_ticket_of_that_kind(self):
        self.assertEqual(self.take_handoff(kind="upkeep"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["upkeep", "", ""])

    def test_ticket_take_files_a_handoff_with_a_kind_that_is_none_of_the_five_as_a_ticket_with_no_kind_and_warns_on_standard_error(self):
        self.handed({"kind": "chore"})
        took = self.run_brigade("engine", "ticket", "take")
        self.assertEqual((took.returncode, took.stdout, took.stderr),
                         (0, "T1 from app/core/T1: Tidy the guide\n",
                          "brigade: warning: T1 came from app/core/T1 with kind 'chore', which is not one this version knows, "
                          "so it has no kind; run ticket set T1 --kind <feature|fix|polish|upkeep|study>\n"))
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["", "", ""])
        again = self.run_brigade("engine", "ticket", "take")
        self.assertEqual((again.returncode, again.stdout, again.stderr), (0, "nothing handed to you\n", ""))

    def test_inbox_take_warns_on_standard_error_of_a_handoff_kind_that_is_a_padded_fix(self):
        self.handed({"kind": " fix "})
        took = self.run_brigade("engine", "inbox", "take")
        self.assertEqual((took.returncode, took.stdout, took.stderr),
                         (0, "T1 from app/core/T1: Tidy the guide\n", self.unknown_kind("T1", "' fix '")))
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["", "", ""])

    def test_a_take_of_two_handoffs_with_unknown_kinds_prints_one_warning_for_each(self):
        self.handed({"kind": "chore"}, {"kind": "chore"})
        took = self.run_brigade("engine", "ticket", "take")
        self.assertEqual((took.returncode, took.stdout, took.stderr),
                         (0, "T1 from app/core/T1: Tidy the guide\nT2 from app/core/T2: Tidy the guide\n",
                          self.unknown_kind("T1", "'chore'") + self.unknown_kind("T2", "'chore'")))
        self.assertEqual([row[10:] for row in self.rows("engine", "rail.tsv")], [["", "", ""], ["", "", ""]])

    def test_ticket_move_of_a_ticket_with_a_bad_kind_cell_and_a_bad_goal_cell_warns_of_both_on_standard_error_and_again_on_a_second_move(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--summary", "Tidy the guide", "--kind", "polish")
        rail = self.dir("core") / "rail.tsv"
        rail.write_text(rail.read_text().replace("\tpolish\t\t\n", "\tchore\tG9\t\n"))
        by_hand = "ticket set does not change this cell while T1 is moved; correct it in rail.tsv by hand\n"
        said = (0, "T1 moved to engine; no thread recorded for engine\n",
                "brigade: warning: T1 has kind 'chore', which is not feature, fix, polish, upkeep, or study; it reads as no kind; " + by_hand
                + "brigade: warning: T1 names goal 'G9', which has no row in goals.tsv; it reads as unassigned; " + by_hand)
        for _ in range(2):
            moved = self.run_brigade("core", "ticket", "move", "T1", "--to", "engine")
            self.assertEqual((moved.returncode, moved.stdout, moved.stderr), said)
        self.assertEqual(self.rows("core", "rail.tsv")[0][10:], ["chore", "G9", ""])
        self.assertEqual(json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()),
                         {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "user", "ref": "",
                          "priority": "", "paths": "", "decision": ""})

    def test_ticket_take_files_a_handoff_whose_kind_is_empty_and_one_whose_kind_is_null_as_tickets_with_no_kind(self):
        self.open("core")
        self.open("engine")
        inbox = self.dir("engine") / "inbox"
        inbox.mkdir()
        for number, kind in ((1, ""), (2, None)):
            (inbox / f"app~core~T{number}.json").write_text(json.dumps(
                {"handoff": f"app/core/T{number}", "summary": f"Tidy guide {number}", "source": "user", "ref": "",
                 "priority": "", "paths": "", "decision": "", "kind": kind}))
        self.assertEqual(self.brigade("engine", "ticket", "take"),
                         "T1 from app/core/T1: Tidy guide 1\nT2 from app/core/T2: Tidy guide 2")
        self.assertEqual([row[10:] for row in self.rows("engine", "rail.tsv")], [["", "", ""], ["", "", ""]])

    def test_ticket_take_ignores_a_goal_key_in_the_handoff(self):
        self.assertEqual(self.take_handoff(kind="fix", goal="G1"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["fix", "", ""])

    def test_ticket_set_with_no_flag_on_a_moved_ticket_prints_the_moved_refusal(self):
        self.handoff()
        self.assertEqual(self.brigade("core", "ticket", "set", "T1", ok=False),
                         "brigade: T1 moved to engine; it is that coordinator's ticket now")

    def test_ticket_set_kind_or_no_goal_on_a_moved_ticket_prints_the_moved_refusal(self):
        self.handoff()
        for flags in (("--kind", "polish"), ("--no-goal",)):
            self.assertEqual(self.brigade("core", "ticket", "set", "T1", *flags, ok=False),
                             "brigade: T1 moved to engine; it is that coordinator's ticket now")
