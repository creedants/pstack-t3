if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import CLAUDE, CODEX, datetime, json, timezone

class GoalsBMixin:
    def test_goal_set_closes_a_paused_goal_with_evidence(self):
        self.open()
        self.goal()
        self.brigade("goal", "set", "G1", "--state", "paused")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "dropped", "--evidence", "not wanted"), "G1 dropped")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "dropped")

    def test_goal_set_moves_an_open_goal_to_paused_and_a_paused_goal_to_open_and_logs_each_move(self):
        self.open()
        self.goal()
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "paused"), "G1 paused")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "paused")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "open"), "G1 open")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "open")
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"], ["G1", "paused", "activity view"],
                                           ["G1", "open", "activity view"]])

    def test_goal_set_prints_its_changes_in_the_order_state_name_purpose_done_test_share_and_writes_each(self):
        self.open()
        self.goal()
        self.assertEqual(self.brigade("goal", "set", "G1", "--share", "3", "--done", "Two pages ship", "--purpose", "Why",
                                      "--name", "Activity\tpage", "--state", "paused"),
                         "G1 paused; name Activity page; purpose changed; done test changed; share 3")
        row = self.table_row(self.at, "goals.tsv", "G1")
        self.assertEqual([row[key] for key in ("state", "name", "purpose", "done", "share", "closed")],
                         ["paused", "Activity page", "Why", "Two pages ship", "3", ""])
        self.assertEqual(self.goal_log()[-1], ["G1", "paused", "Activity page"])

    def test_goal_set_refuses_a_move_from_done_or_dropped_to_a_state_other_than_open_and_changes_no_file(self):
        self.open()
        self.goal("a")
        self.goal("b")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.brigade("goal", "set", "G2", "--state", "dropped", "--evidence", "not wanted")
        before = self.store_files()
        for ident, state, flags in (("G1", "done", ("--state", "dropped", "--evidence", "x")), ("G1", "done", ("--state", "paused")),
                                    ("G2", "dropped", ("--state", "done", "--evidence", "x")), ("G2", "dropped", ("--state", "paused"))):
            with self.subTest(ident=ident, flags=flags):
                self.assertEqual(self.brigade("goal", "set", ident, *flags, ok=False),
                                 f"brigade: {ident} is {state}; a {state} goal moves only to open; "
                                 f"run goal set {ident} --state open first")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_refuses_a_move_to_done_and_a_move_to_dropped_with_no_evidence_or_with_evidence_that_is_only_whitespace(self):
        self.open()
        self.goal()
        before = self.store_files()
        for state in ("done", "dropped"):
            with self.subTest(state=state):
                self.assertEqual(self.brigade("goal", "set", "G1", "--state", state, ok=False),
                                 f"brigade: --state {state} needs --evidence <one line>")
                self.assertEqual(self.brigade("goal", "set", "G1", "--state", state, "--evidence", " ", ok=False),
                                 f"brigade: --state {state} needs --evidence <one line>")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_refuses_a_name_a_purpose_or_a_done_test_that_is_only_whitespace_and_changes_no_file(self):
        self.open()
        self.goal()
        before = self.store_files()
        for flag in ("--name", "--purpose", "--done"):
            with self.subTest(flag=flag):
                self.assertEqual(self.brigade("goal", "set", "G1", flag, " ", "--state", "paused", ok=False),
                                 f"brigade: goal set {flag} takes text; a goal keeps a name, a purpose, and a done test")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_refuses_an_empty_id_and_goal_add_gives_the_next_id_when_goals_tsv_has_a_row_with_no_id(self):
        self.two_goals()
        self.copy_goal_row("", "no id")
        before = self.store_files()
        self.assertEqual(self.brigade("goal", "set", "", "--name", "x", ok=False),
                         "brigade: warning: goals.tsv has a row with no id; it is not a goal; remove it from goals.tsv by hand\n"
                         "brigade: no goal ; run goal list --all to see every goal")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("goal", "add", "--name", "third", "--purpose", "p", "--done", "d"), "G3")

    def test_goal_set_refuses_an_id_with_no_row_and_a_call_with_no_change_flag_and_changes_no_file(self):
        self.open()
        self.goal()
        before = self.store_files()
        self.assertEqual(self.brigade("goal", "set", "G7", "--state", "paused", ok=False),
                         "brigade: no goal G7; run goal list --all to see every goal")
        self.assertEqual(self.brigade("goal", "set", "G1", ok=False),
                         "brigade: goal set needs --state, --name, --purpose, --done, or --share")
        self.assertEqual(self.brigade("goal", "set", "G1", "--evidence", "x", ok=False),
                         "brigade: goal set needs --state, --name, --purpose, --done, or --share")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_refuses_done_and_dropped_while_a_ticket_of_the_goal_waits_and_names_each_such_ticket(self):
        self.open()
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "feature", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "d", "--kind", "polish")
        before = self.store_files()
        for state in ("done", "dropped"):
            with self.subTest(state=state):
                self.assertEqual(self.brigade("goal", "set", "G1", "--state", state, "--evidence", "why", ok=False),
                                 "brigade: G1 still has work: T1, T3 waiting; give each waiting ticket another goal with "
                                 "ticket set --goal, or none with --no-goal; a waiting ticket that an item in flight names keeps its goal until "
                                 "that item is merged or dropped; then run this again")
        self.assertEqual(self.store_files(), before)
        self.brigade("ticket", "set", "T1", "--no-goal")
        self.brigade("ticket", "set", "T3", "--goal", "G2")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "why"), "G1 done")

    def test_goal_set_refuses_done_and_dropped_while_an_item_of_the_goal_is_in_flight_and_names_the_item(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        for state in ("in-progress", "in-review", "sent-back", "blocked"):
            with self.subTest(item=state):
                self.brigade("dish", "D1", "--state", state, "--sha", "abc")
                self.assert_goal_keeps_open("G1", "D1 in flight", "finish or drop each item in flight")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.assert_goal_keeps_open("G1", "D1 in flight", "finish or drop each item in flight")
        self.brigade("dish", "D1", "--state", "queued")
        self.assert_goal_keeps_open("G1", "D1 in flight", "finish or drop each item in flight")
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "merged"), "G1 done")

    def test_goal_set_refuses_evidence_beside_state_open_state_paused_or_no_state_and_changes_no_file(self):
        self.open()
        self.goal()
        before = self.store_files()
        for flags in (("--state", "paused"), ("--state", "open"), ("--name", "other")):
            with self.subTest(flags=flags):
                self.assertEqual(self.brigade("goal", "set", "G1", *flags, "--evidence", "x", ok=False),
                                 "brigade: --evidence goes only with --state done or --state dropped")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_state_done_and_state_dropped_store_the_time_and_the_evidence_in_the_closed_cell_and_log_the_name_and_the_evidence(self):
        self.open()
        self.goal("activity view")
        self.goal("current with upstream")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "The page\tshipped in #170"), "G1 done")
        self.assertEqual(self.brigade("goal", "set", "G2", "--state", "dropped", "--evidence", "Upstream is frozen"), "G2 dropped")
        first, second = (self.table_row(self.at, "goals.tsv", ident) for ident in ("G1", "G2"))
        self.assertEqual((first["state"], second["state"]), ("done", "dropped"))
        stamp, evidence = first["closed"].split(" ", 1)
        self.assertEqual(evidence, "The page shipped in #170")
        self.assertEqual(datetime.fromisoformat(stamp).tzinfo, timezone.utc)
        self.assertEqual(second["closed"].split(" ", 1)[1], "Upstream is frozen")
        self.assertEqual(self.goal_log()[2:], [["G1", "done", "activity view: The page shipped in #170"],
                                               ["G2", "dropped", "current with upstream: Upstream is frozen"]])

    def test_goal_set_state_done_warns_of_a_waiting_ticket_whose_goal_cell_is_that_goals_id_padded(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("rail.tsv", "T1", goal=" G1 ")
        self.assertEqual(self.run_raw("goal", "set", "G2", "--state", "paused"), (0, "G2 paused\n", ""))
        self.assertEqual(self.run_raw("goal", "set", "G1", "--state", "done", "--evidence", "shipped"),
                         (0, "G1 done\n", f"brigade: warning: T1 names goal ' G1 ', {self.NO_GOAL_ROW}; "
                                          "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "state", "goal"), (b"waiting", b" G1 "))

    def test_goal_set_state_open_on_a_done_goal_and_on_a_dropped_goal_empties_the_closed_cell_and_logs_the_move(self):
        self.open()
        self.goal("a")
        self.goal("b")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.brigade("goal", "set", "G2", "--state", "dropped", "--evidence", "not wanted")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "open"), "G1 open")
        self.assertEqual(self.brigade("goal", "set", "G2", "--state", "open"), "G2 open")
        for ident in ("G1", "G2"):
            row = self.table_row(self.at, "goals.tsv", ident)
            self.assertEqual((row["state"], row["closed"]), ("open", ""))
        self.assertEqual(self.goal_log()[2:], [["G1", "done", "a: shipped"], ["G2", "dropped", "b: not wanted"],
                                               ["G1", "open", "a"], ["G2", "open", "b"]])

    def test_goal_set_state_refuses_a_state_that_is_none_of_the_four(self):
        self.open()
        self.goal()
        self.assert_refused_choice(self.brigade("goal", "set", "G1", "--state", "finished", ok=False), "--state", "finished",
                                   ("open", "paused", "done", "dropped"))

    def test_goal_set_that_changes_no_state_adds_no_log_row(self):
        self.open()
        self.goal()
        before = ((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes())
        self.assertEqual(self.brigade("goal", "set", "G1", "--name", "other", "--share", "4"), "G1 name other; share 4")
        self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes()), before)

    def test_goal_set_with_the_state_the_goal_has_prints_already_and_changes_no_file(self):
        self.open()
        self.goal("a")
        self.goal("b")
        self.goal("c")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.brigade("goal", "set", "G3", "--state", "done", "--evidence", "shipped")
        before = self.store_files()
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "open"), "G1 already open")
        self.assertEqual(self.brigade("goal", "set", "G2", "--state", "paused"), "G2 already paused")
        self.assertEqual(self.brigade("goal", "set", "G3", "--state", "done"), "G3 already done")
        self.assertEqual(self.brigade("goal", "set", "G3", "--state", "done", "--evidence", "other words"), "G3 already done")
        self.assertEqual(self.store_files(), before)
