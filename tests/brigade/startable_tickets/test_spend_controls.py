if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

class SpendStandingMixin:
    def test_startable_with_no_waiting_ticket_prints_the_workers_line_and_says_so(self):
        self.queue()
        self.filed("Done already")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("startable"), self.NONE_RUNNING + "\nno waiting tickets")

    def test_startable_lists_a_ticket_of_a_paused_goal_with_its_reason_and_it_is_not_startable(self):
        self.queue()
        self.goal("activity view")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("goal", "set", "G1", "--state", "paused")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, goal paused: 1",
            "T1 normal fix G1, 0m: its goal is paused; a.txt",
        ]))
        self.brigade("goal", "set", "G1", "--state", "open")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, startable: 1",
            "T1 normal fix G1, 0m: startable; a.txt",
            "  Fix a",
        ]))

    def test_startable_lists_a_ticket_of_a_kind_at_its_cap_with_its_reason_and_it_is_not_startable(self):
        self.queue()
        self.brigade("set", "--cap", "fix=1")
        self.filed("Fix a", "--paths", "a.txt")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.filed("Fix b", "--paths", "b.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 1, kind cap: 1",
            "T2 normal fix, 0m: the fix cap holds (1 of 1); b.txt",
        ]))
        self.brigade("set", "--cap", "fix=")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 1, startable: 1",
            "T2 normal fix, 0m: startable; b.txt",
            "  Fix b",
        ]))

    def test_startable_lists_a_ticket_of_a_goal_at_its_share_with_its_reason_and_it_is_not_startable(self):
        self.queue()
        self.goal("activity view", "--share", "1")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.filed("Fix b", "--paths", "b.txt", "--goal", "G1")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 1, goal share: 1",
            "T2 normal fix G1, 0m: the G1 share holds (1 of 1); b.txt",
        ]))
        self.brigade("goal", "set", "G1", "--share", "2")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 1, startable: 1",
            "T2 normal fix G1, 0m: startable; b.txt",
            "  Fix b",
        ]))

    def test_startable_count_line_names_the_three_new_standings_when_they_occur(self):
        self.queue()
        self.goal("activity view", "--share", "1")
        self.goal("current with upstream")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.brigade("set", "--cap", "fix=1")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.brigade("ticket", "add", "--summary", "Tidy b", "--kind", "polish", "--paths", "b.txt", "--goal", "G1")
        self.filed("Fix c", "--paths", "c.txt", "--goal", "G2")
        self.filed("Fix d", "--paths", "d.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 3, goal paused: 1, kind cap: 1, goal share: 1",
            "T2 normal polish G1, 0m: the G1 share holds (1 of 1); b.txt",
            "T3 normal fix G2, 0m: its goal is paused; c.txt",
            "T4 normal fix, 0m: the fix cap holds (1 of 1); d.txt",
        ]))

    def test_startable_counts_a_ticket_that_starts_against_the_kind_and_the_goal_it_holds_a_slot_under(self):
        self.queue()
        self.brigade("set", "--cap", "fix=1")
        self.goal("activity view", "--share", "1")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.filed("Fix b", "--paths", "b.txt", "--goal", "G1")
        self.filed("Tidy c", "--paths", "c.txt", "--goal", "G1", "--kind", "polish")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, kind cap: 1, goal share: 1",
            "T1 normal fix G1, 0m: startable; a.txt",
            "  Fix a",
            "T2 normal fix G1, 0m: the fix cap holds (1 of 1); b.txt",
            "T3 normal polish G1, 0m: the G1 share holds (1 of 1); c.txt",
        ]))

    def test_startable_gives_each_spend_control_its_own_line(self):
        self.queue()
        self.goal("activity view", "--share", "1")
        self.goal("current with upstream")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.brigade("set", "--cap", "fix=1")
        self.brigade("goal", "set", "G2", "--state", "paused")
        for summary, kind, goal, paths, priority in (("Start one", "study", "", "one.txt", ""),
                                                     ("Sync two", "fix", "G2", "two.txt", ""),
                                                     ("Fix three", "fix", "", "three.txt", ""),
                                                     ("Tidy four", "polish", "G1", "four.txt", ""),
                                                     ("Measure five", "study", "", "five.txt", ""),
                                                     ("Tidy six", "polish", "", "six.txt", "low")):
            self.brigade("ticket", "add", "--summary", summary, "--kind", kind, "--paths", paths,
                         *(["--priority", priority] if priority else []), *(["--goal", goal] if goal else []))
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 6, startable: 1, blocked: 1, below auto-start: 1, goal paused: 1, kind cap: 1, goal share: 1",
            "T2 normal study, 0m: startable; one.txt",
            "  Start one",
            "T3 normal fix G2, 0m: its goal is paused; two.txt",
            "T4 normal fix, 0m: the fix cap holds (1 of 1); three.txt",
            "T5 normal polish G1, 0m: the G1 share holds (1 of 1); four.txt",
            "T6 normal study, 0m: blocked, waiting for an idle worker; five.txt",
            "T7 low polish, 0m: below auto-start; six.txt",
        ]))

    def test_startable_never_lists_a_low_ticket_of_a_paused_goal_as_riding_with_another_ticket(self):
        # The paused check comes before the ride-along branch, so a paused goal starts nothing even as a rider.
        self.queue()
        self.goal("activity view")
        self.goal("current with upstream", "--share", "1")
        self.filed("a", "--paths", "a.txt", "--goal", "G2")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a", "--paths", "a.txt")
        self.brigade("set", "--cap", "fix=1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--paths", "b.txt", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "fix", "--paths", "c.txt", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "d", "--kind", "polish", "--paths", "d.txt", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "e", "--kind", "polish", "--paths", "e.txt", "--priority", "low", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Add e", "--kind", "feature", "--paths", "e.txt", "--priority", "urgent")
        self.brigade("goal", "set", "G1", "--state", "paused")
        before = self.store_files()
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 5, startable: 1, goal paused: 2, kind cap: 1, goal share: 1",
            "T6 urgent feature, 0m: startable; e.txt",
            "  Add e",
            "T2 normal fix G1, 0m: its goal is paused; b.txt",
            "T3 normal fix G2, 0m: the fix cap holds (1 of 1); c.txt",
            "T4 normal polish G2, 0m: the G2 share holds (1 of 1); d.txt",
            "T5 low polish G1, 0m: its goal is paused; e.txt",
        ]))
        self.assertEqual(self.store_files(), before)

    def test_startable_rides_a_low_ticket_of_an_open_goal_and_pauses_one_of_a_paused_goal(self):
        # The paused check leaves the open goal's rider alone and holds the paused goal's rider even when it overlaps a startable ticket.
        self.queue()
        self.goal("activity view")
        self.goal("current with upstream")
        self.filed("Fix a", "--paths", "a.txt")
        self.brigade("ticket", "add", "--summary", "Tidy a", "--kind", "polish", "--paths", "a.txt", "--priority", "low", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Pause a", "--kind", "polish", "--paths", "a.txt", "--priority", "low", "--goal", "G2")
        self.brigade("goal", "set", "G2", "--state", "paused")
        before = self.store_files()
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, riding: 1, goal paused: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 low polish G1, 0m: rides with T1; a.txt",
            "  Tidy a",
            "T3 low polish G2, 0m: its goal is paused; a.txt",
        ]))
        self.assertEqual(self.store_files(), before)

    def test_startable_pauses_a_waiting_rider_of_a_goal_whose_running_item_keeps_running(self):
        # A paused goal holds its waiting tickets and lets the item already in flight finish.
        self.queue()
        self.goal("activity view")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a", "--paths", "a.txt")
        self.brigade("goal", "set", "G1", "--state", "paused")
        self.brigade("ticket", "add", "--summary", "Paused rider", "--kind", "polish", "--paths", "b.txt", "--priority", "low", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Add b", "--kind", "feature", "--paths", "b.txt", "--priority", "urgent")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 2, startable: 1, goal paused: 1",
            "T3 urgent feature, 0m: startable; b.txt",
            "  Add b",
            "T2 low polish G1, 0m: its goal is paused; b.txt",
        ]))
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
        self.assertEqual(self.brigade("ticket", "list", "--state", "assigned"), "T1 assigned normal fix G1 [user] Fix a")
