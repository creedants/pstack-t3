from .support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    HELD_LINE,
    Path,
    ROOT,
    SCRIPT,
    STILL_OPEN,
    call,
    datetime,
    json,
    os,
    runpy,
    shlex,
    subprocess,
    sys,
    time,
    timedelta,
    timezone,
)


class StartableTest(BrigadeTest):
    def test_startable_lists_a_ticket_whose_paths_are_free_as_startable_with_its_summary(self):
        self.queue()
        self.filed("Fix a", "--paths", "a.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, startable: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
        ]))

    def test_startable_names_the_lease_and_holder_that_block_a_ticket(self):
        self.queue()
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, blocked: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
        ]))

    def test_startable_lists_a_ticket_that_records_no_paths_as_unknown(self):
        self.queue()
        self.filed("Fix something")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, unknown: 1",
            "T1 normal fix, 0m: unknown, no paths recorded",
        ]))

    def test_startable_lists_a_decision_ticket_as_a_decision_with_or_without_paths(self):
        self.queue()
        self.filed("Pick a name", "--decision", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Pick a color", "--decision")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, decisions: 2",
            "T1 urgent fix, 0m: decision for the owner; a.txt",
            "T2 normal fix, 0m: decision for the owner",
        ]))

    def test_startable_lists_free_tickets_below_the_auto_start_level_as_below_it(self):
        self.queue()
        self.brigade("set", "--autofire", "urgent")
        self.filed("Sync", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Fix b", "--paths", "b.txt")
        self.filed("Tidy c", "--paths", "c.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: urgent",
            "waiting tickets: 3, startable: 1, below auto-start: 2",
            "T1 urgent fix, 0m: startable; a.txt",
            "  Sync",
            "T2 normal fix, 0m: below auto-start; b.txt",
            "T3 low fix, 0m: below auto-start; c.txt",
        ]))

    def test_startable_lists_no_ticket_as_startable_at_level_off(self):
        self.queue()
        self.brigade("set", "--autofire", "off")
        self.filed("Sync", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Tidy c", "--paths", "c.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: off",
            "waiting tickets: 2, below auto-start: 2",
            "T1 urgent fix, 0m: below auto-start; a.txt",
            "T2 low fix, 0m: below auto-start; c.txt",
        ]))

    def test_startable_lists_a_low_ticket_on_a_startable_ticket_file_or_under_its_directory_as_riding_with_it(self):
        self.queue()
        self.filed("Fix the guide and the scripts", "--paths", "docs/guide.md,scripts")
        self.filed("Tidy the guide", "--paths", "docs/guide.md", "--priority", "low")
        self.filed("Tidy the installer", "--paths", "scripts/install.py", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, riding: 2",
            "T1 normal fix, 0m: startable; docs/guide.md,scripts",
            "  Fix the guide and the scripts",
            "T2 low fix, 0m: rides with T1; docs/guide.md",
            "  Tidy the guide",
            "T3 low fix, 0m: rides with T1; scripts/install.py",
            "  Tidy the installer",
        ]))

    def test_startable_blocks_a_second_normal_ticket_that_shares_a_path_with_a_startable_one(self):
        self.queue()
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix a and b", "--paths", "a.txt,b.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 normal fix, 0m: blocked, starts after T1; a.txt,b.txt",
        ]))

    def test_startable_blocks_a_low_ticket_that_shares_paths_with_two_startable_tickets_and_names_both(self):
        self.queue("--workers", "3")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix b", "--paths", "b.txt")
        self.filed("Tidy both", "--paths", "a.txt,b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 3 running, 3 idle, auto-start: normal",
            "waiting tickets: 3, startable: 2, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 normal fix, 0m: startable; b.txt",
            "  Fix b",
            "T3 low fix, 0m: blocked, starts after T1, T2; a.txt,b.txt",
        ]))

    def test_startable_blocks_a_low_ticket_with_one_path_on_a_startable_ticket_file_and_one_path_outside_its_paths(self):
        self.queue()
        self.filed("Fix the guide", "--paths", "docs/guide.md")
        self.filed("Tidy the docs", "--paths", "docs/guide.md,docs/cli.md", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; docs/guide.md",
            "  Fix the guide",
            "T2 low fix, 0m: blocked, starts after T1; docs/cli.md,docs/guide.md",
        ]))

    def test_startable_blocks_a_low_ticket_on_a_directory_above_a_startable_ticket_file_at_a_cap_of_one(self):
        self.queue("--workers", "1")
        self.filed("Fix one", "--paths", "src/a/one.py", "--priority", "urgent")
        self.filed("Fix two", "--paths", "src/b/two.py")
        self.filed("Tidy the source", "--paths", "src", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: normal",
            "waiting tickets: 3, startable: 1, blocked: 2",
            "T1 urgent fix, 0m: startable; src/a/one.py",
            "  Fix one",
            "T2 normal fix, 0m: blocked, waiting for an idle worker; src/b/two.py",
            "T3 low fix, 0m: blocked, starts after T1; src",
        ]))

    def test_startable_blocks_a_free_ticket_when_the_worker_cap_is_reached(self):
        self.queue("--workers", "1")
        self.filed("Running")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Running")
        self.filed("Fix b", "--paths", "b.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 1 running, 0 idle, auto-start: normal",
            "waiting tickets: 1, blocked: 1",
            "T2 normal fix, 0m: blocked, waiting for an idle worker; b.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_from_the_last_idle_worker_while_a_normal_ticket_waits(self):
        self.queue()
        self.brigade("set", "--autofire", "low")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, held back: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            f"T2 low fix, 0m: {self.HELD_BACK}; b.txt",
        ]))

    def test_startable_lists_a_free_low_ticket_as_startable_at_a_cap_of_one_when_no_urgent_or_normal_ticket_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 1, startable: 1",
            "T1 low fix, 0m: startable; a.txt",
            "  Tidy a",
        ]))

    def test_startable_lists_two_free_low_tickets_as_startable_at_a_cap_of_two_when_no_urgent_or_normal_ticket_waits(self):
        self.queue()
        self.brigade("set", "--autofire", "low")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: low",
            "waiting tickets: 2, startable: 2",
            "T1 low fix, 0m: startable; a.txt",
            "  Tidy a",
            "T2 low fix, 0m: startable; b.txt",
            "  Tidy b",
        ]))

    def test_startable_blocks_a_free_low_ticket_for_no_idle_worker_when_a_normal_ticket_takes_the_one_worker(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 low fix, 0m: blocked, waiting for an idle worker; b.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_while_a_normal_ticket_blocked_by_a_lease_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, blocked: 1, held back: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
            f"T2 low fix, 0m: {self.HELD_BACK}; a.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_while_an_urgent_ticket_with_no_paths_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Fix something", "--priority", "urgent")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, unknown: 1, held back: 1",
            "T1 urgent fix, 0m: unknown, no paths recorded",
            f"T2 low fix, 0m: {self.HELD_BACK}; a.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_while_a_normal_decision_ticket_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Pick a name", "--decision")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, decisions: 1, held back: 1",
            "T1 normal fix, 0m: decision for the owner",
            f"T2 low fix, 0m: {self.HELD_BACK}; a.txt",
        ]))

    def test_startable_flags_a_low_ticket_filed_more_than_seven_days_ago_and_no_other(self):
        self.queue()
        self.filed("Old and low", "--paths", "a.txt", "--priority", "low")
        self.filed("Low", "--paths", "b.txt", "--priority", "low")
        self.filed("Old", "--paths", "c.txt")
        self.refile("T1", days=7, minutes=1)
        self.refile("T2", days=6, hours=23)
        self.refile("T3", days=9)
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, below auto-start: 2",
            "T3 normal fix, 9d: startable; c.txt",
            "  Old",
            "T1 low fix, 7d (over a week): below auto-start; a.txt",
            "T2 low fix, 6d: below auto-start; b.txt",
        ]))

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

    def test_startable_orders_tickets_by_priority_then_oldest_filed_then_the_number_in_the_id(self):
        self.open()
        stamp = (datetime.now(timezone.utc) - timedelta(hours=5, minutes=1)).isoformat()
        rows = [("T10", stamp, "", "ten"), ("T9", stamp, "", "nine"), ("T11", stamp, "low", "eleven"),
                ("T12", datetime.now(timezone.utc).isoformat(), "urgent", "twelve"),
                ("T13", (datetime.now(timezone.utc) - timedelta(days=2, minutes=1)).isoformat(), "", "thirteen")]
        (self.at / "rail.tsv").write_text("id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n" + "".join(
            f"{ident}\t{at}\twaiting\tuser\t\t\t{summary}\t{priority}\t\t\n" for ident, at, priority, summary in rows))
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 5, unknown: 5",
            "T12 urgent, 0m: unknown, no paths recorded",
            "T13 normal, 2d: unknown, no paths recorded",
            "T9 normal, 5h: unknown, no paths recorded",
            "T10 normal, 5h: unknown, no paths recorded",
            "T11 low, 5h: unknown, no paths recorded",
        ]))

    def test_startable_lists_rail_rows_written_before_the_paths_cell_as_unknown(self):
        self.open()
        self.old_rail((datetime.now(timezone.utc) - timedelta(days=3, minutes=1)).isoformat())
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, unknown: 3",
            "T3 urgent, 3d: unknown, no paths recorded",
            "T1 normal, 3d: unknown, no paths recorded",
            "T2 low, 3d: unknown, no paths recorded",
        ]))
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_startable_without_an_owner_changes_no_store_file_and_no_landing_row(self):
        self.queue()
        self.brigade("set", "--thread", "t1")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix something")
        store, landing = self.store_files(), self.landing_rows()
        self.assertEqual(self.brigade("startable", owner=False), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, blocked: 1, unknown: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
            "T2 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T3 normal fix, 0m: unknown, no paths recorded",
        ]))
        self.assertEqual((self.store_files(), self.landing_rows()), (store, landing))
        self.assertEqual(len(landing["lease"]), 1)

    def test_startable_agrees_with_lease_check_on_a_directory_lease_and_on_a_name_that_shares_its_prefix(self):
        self.queue()
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix x", "--paths", "src/app/x.py")
        self.filed("Fix srcx", "--paths", "srcx")
        checks = [subprocess.run([sys.executable, str(ROOT / "t3/added/landing/scripts/land.py"), "--repo", str(self.project),
                                  "lease", "check", "--holder", holder, "--paths", paths],
                                 capture_output=True, text=True, env=self.land_env())
                  for holder, paths in (("perf/T1", "src/app/x.py"), ("perf/T2", "srcx"))]
        self.assertEqual([(check.returncode, check.stdout.strip()) for check in checks],
                         [(1, "L1 held by engine/D1 on src"), (0, "free")])
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app/x.py",
            "T2 normal fix, 0m: startable; srcx",
            "  Fix srcx",
        ]))

    def vendor_layout(self):
        target = self.project / "scripts"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("build.py", "cli_reference.py"):
            (target / name).write_bytes((ROOT / "scripts" / name).read_bytes())

    def note_lease_fixture(self):
        """A lease on one activity path, one test path, and the next dish's default change note.

        The lease's test path and the ticket's first path are siblings under `tests/brigade/`.
        """
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.land("lease", "claim", "--holder", "engine/D1",
                  "--paths", "t3/added/brigade/scripts/activity.py,tests/brigade/test_context_cap.py,changes/perf%2Fd1.md")
        self.filed("Fix the cap", "--paths", "tests/brigade/support.py,tests/brigade/test_startable.py")

    def test_startable_does_not_block_on_a_lease_whose_only_match_is_the_next_change_note(self):
        # Before the fix, the preview matched this ticket only through the note it named for the next dish.
        self.note_lease_fixture()
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, startable: 1",
            "T1 normal fix, 0m: startable; tests/brigade/support.py,tests/brigade/test_startable.py",
            "  Fix the cap",
        ]))

    def test_startable_agrees_with_fire_on_a_lease_whose_only_match_is_the_next_change_note(self):
        self.note_lease_fixture()
        self.assertIn("T1 normal fix, 0m: startable; tests/brigade/support.py,tests/brigade/test_startable.py",
                      self.brigade("startable"))
        # The branch is the coordinator's choice, so the note `fire` claims is not the note the lease holds.
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the cap",
                                      "--paths", "tests/brigade/support.py,tests/brigade/test_startable.py",
                                      "--branch", "perf/d2"),
                         "D1 (lease L2 held by perf/D1)")

    def test_startable_prints_one_line_for_a_ticket_the_landing_queue_refuses_in_a_project_that_is_not_a_git_repository(self):
        self.open()
        self.filed("Fix a", "--paths", "src/a.py")
        lines = self.brigade("startable").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[2].startswith("T1 normal fix, 0m: blocked, waiting on the landing queue ("), lines[2])

    def test_startable_keeps_ticket_order_with_kinds_and_goals_with_paused_and_share_where_they_hold(self):
        self.queue("--workers", "3")
        self.goal("activity view", "--share", "1")
        self.goal("current with upstream")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Add b", "--kind", "feature", "--paths", "b.txt", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Sync c", "--kind", "upkeep", "--paths", "c.txt", "--goal", "G2",
                     "--priority", "urgent")
        self.brigade("ticket", "add", "--summary", "Tidy d", "--kind", "polish", "--paths", "d.txt", "--priority", "low")
        self.brigade("ticket", "add", "--summary", "Look at e", "--kind", "study")
        self.brigade("ticket", "add", "--summary", "Add f", "--kind", "feature", "--paths", "a.txt", "--goal", "G2")
        self.brigade("goal", "set", "G2", "--state", "paused")
        lines = ["workers: 0 of 3 running, 3 idle, auto-start: normal",
                 "waiting tickets: 6, startable: 1, unknown: 1, below auto-start: 1, goal paused: 2, goal share: 1",
                 "T3 urgent{}, 0m: its goal is paused; c.txt",
                 "T1 normal{}, 0m: startable; a.txt", "  Fix a",
                 "T2 normal{}, 0m: the G1 share holds (1 of 1); b.txt",
                 "T5 normal{}, 0m: unknown, no paths recorded",
                 "T6 normal{}, 0m: its goal is paused; a.txt",
                 "T4 low{}, 0m: below auto-start; d.txt"]
        words = iter((" upkeep G2", " fix G1", " feature G1", " study", " feature G2", " polish"))
        self.assertEqual(self.brigade("startable").splitlines(),
                         [line.format(next(words)) if "{}" in line else line for line in lines])
        for number in range(1, 7):
            self.hand_edit("rail.tsv", f"T{number}", kind="", goal="")
        self.assertEqual(self.brigade("startable").splitlines(), ["workers: 0 of 3 running, 3 idle, auto-start: normal",
                                                                  "waiting tickets: 6, startable: 3, blocked: 1, unknown: 1, "
                                                                  "below auto-start: 1",
                                                                  "T3 urgent, 0m: startable; c.txt", "  Sync c",
                                                                  "T1 normal, 0m: startable; a.txt", "  Fix a",
                                                                  "T2 normal, 0m: startable; b.txt", "  Add b",
                                                                  "T5 normal, 0m: unknown, no paths recorded",
                                                                  "T6 normal, 0m: blocked, starts after T1; a.txt",
                                                                  "T4 low, 0m: below auto-start; d.txt"])
