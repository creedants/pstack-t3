from ..support import CLAUDE, CODEX

class FireKindAMixin:
    def test_a_dish_update_and_a_review_pass_leave_the_items_kind_and_goal_cells_as_fire_wrote_them(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc", "--thread", "w1")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("feature", "G1"), ("feature", "G1")))
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "merged")

    def test_a_dropped_items_lead_ticket_waits_again_with_the_kind_fire_wrote_on_it(self):
        self.open()
        self.rail_before_kinds("a")
        self.brigade("fire", "--tickets", "T1", "--station", "investigation", "--summary", "Look at a")
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
        row = self.table_row(self.at, "rail.tsv", "T1")
        self.assertEqual((row["state"], row["kind"], row["goal"]), ("waiting", "study", ""))
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a"), "D2")
        self.assertEqual(self.label("dishes.tsv", "D2"), ("study", ""))

    def test_an_item_and_an_assigned_ticket_whose_kind_cells_hold_no_kind_are_warned_of_and_are_in_no_count_by_kind(self):
        self.fired_bug_fix()
        self.hand_edit("rail.tsv", "T1", kind="chore")
        self.hand_edit("dishes.tsv", "D1", kind="chore")
        out = self.assert_each_printing_command_warns(
            f"T1 has kind 'chore', {self.NOT_A_KIND}; {self.NOT_WHILE} T1 is assigned; correct it in rail.tsv by hand",
            f"D1 has kind 'chore', {self.NOT_A_KIND}; no command changes this cell; correct it in dishes.tsv by hand")
        self.assertEqual(len(out["status"].splitlines()), 4)
        self.assertEqual(out["ticket list"], "T1 assigned normal [user] s")
        self.assertEqual(out["close --dry-run"].splitlines()[4:], ["## In progress", "", "- D1 (T1): Fix s",
                                                                  "", "## Spend", "",
                                                                  "goal not recorded  wall open  rounds 0  send-backs 0  threads not recorded"])
        self.assertNotIn("chore", "".join(out.values()))

    def test_close_prints_no_goal_heading_in_a_store_whose_tickets_and_items_have_kinds_and_no_goal(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.assertEqual(self.close_run("--dry-run")[1].splitlines()[4:],
                         ["## In progress", "", "- fix D1 (T1): Fix a", "", "## New tickets, not started", "", "- polish T2: b",
                          "", "## Spend", "", "goal not recorded  wall open  rounds 0  send-backs 0  threads not recorded"])

    def test_fire_gives_the_item_the_kind_and_goal_of_its_lead_ticket_and_leaves_that_tickets_two_cells_as_they_were(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "polish", "--goal", "G2")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("polish", "G2"))
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", "G2"))

    def test_fire_groups_waiting_tickets_into_one_dish(self):
        self.open()
        self.brigade("set", "--intake", "github")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Startup is slow on cold boot", "--source", "github", "--ref", "#12"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Splash screen hangs"), "T2")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "perf-issue", "--summary", "Cut cold start time"), "D1")
        self.assertEqual(self.brigade("ticket", "list"),
                         "T1 assigned normal fix [github] Startup is slow on cold boot #12\nT2 assigned normal fix [user] Splash screen hangs")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "again", ok=False),
                         "brigade: T1 is assigned, not waiting")

    def test_fire_kind_gives_the_kind_to_the_item_and_to_a_lead_ticket_that_has_none(self):
        self.open()
        self.rail_before_kinds("a")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "upkeep"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("upkeep", ""))
        self.assertEqual(self.label("rail.tsv", "T1"), ("upkeep", ""))

    def test_fire_kind_is_taken_over_the_kind_of_the_station(self):
        self.open()
        self.rail_before_kinds("a")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a", "--kind", "polish")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("polish", ""), ("polish", "")))

    def test_fire_kind_refuses_a_word_that_is_none_of_the_five(self):
        self.open()
        self.rail_before_kinds("a")
        before = self.store_files()
        self.assert_refused_choice(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "chore",
                                                ok=False), "--kind", "chore", ("feature", "fix", "polish", "upkeep", "study"))
        self.assertEqual(self.store_files(), before)

    def test_fire_kind_that_differs_from_the_lead_tickets_kind_is_refused_and_an_equal_one_is_accepted(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "feature", ok=False),
                         "brigade: T1 has kind fix, and --kind feature does not match it; "
                         "drop --kind, or run ticket set T1 --kind feature first when no item in flight names T1")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "fix"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", ""))

    def test_fire_mode_full_records_the_escalation_after_the_dish_row(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "lock order")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the lock order",
                                      "--mode", "full", "--reason", "tickets name a lock"), "D1")
        rows = [line.split("\t")[1:4] for line in (self.at / "log.tsv").read_text().splitlines()[1:]]
        self.assertEqual(rows[-3:], [["dish", "D1", "in-progress"], ["mode", "D1", "full"], ["ticket", "T1", "assigned"]])
        self.assertEqual(self.mode_rows()[0][3], "tickets name a lock")

    def test_fire_of_a_lead_ticket_whose_goal_has_no_row_warns_starts_an_unassigned_item_and_leaves_the_tickets_cell(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2")
        self.hand_edit("rail.tsv", "T1", goal="G9")
        self.hand_edit("rail.tsv", "T2", goal="G8")
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"),
                         (0, "D1\n", "brigade: warning: T1 names goal 'G9', which has no row in goals.tsv; it reads as unassigned; "
                                     "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("fix", ""), ("fix", "G9")))

    def test_fire_of_a_lead_ticket_whose_kind_cell_holds_no_kind_warns_needs_a_kind_and_writes_it_over_the_cell(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="bugfix")
        warning = ("brigade: warning: T1 has kind 'bugfix', which is not feature, fix, polish, upkeep, or study; it reads as no kind; "
                   "run ticket set T1 --kind <kind>\n")
        before = self.store_files()
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a"),
                         (1, "", warning + f"brigade: T1 has no kind and station feature gives none; "
                                           f"pass --kind {self.KIND_CHOICE}\n"))
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "polish"),
                         (0, "D1\n", warning))
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("polish", ""), ("polish", "")))

    def test_fire_of_a_lead_ticket_whose_kind_cell_holds_no_kind_writes_the_kind_of_its_station_over_the_cell(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="bugfix")
        code, out, err = self.run_raw("fire", "--tickets", "T1", "--station", "investigation", "--summary", "Do a")
        self.assertEqual((code, out), (0, "D1\n"))
        self.assertIn("T1 has kind 'bugfix'", err)
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("study", ""), ("study", "")))
