from ..support import BULLETS, Path, SCRIPT, os, runpy

class FireKindBMixin:
    def test_fire_of_a_lead_ticket_with_no_kind_on_a_station_that_gives_none_is_refused_and_changes_no_file(self):
        self.open()
        self.rail_before_kinds("a")
        before = self.store_files()
        for station in ("feature", "refactoring", "docs"):
            with self.subTest(station=station):
                self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", station, "--summary", "Do a", ok=False),
                                 f"brigade: T1 has no kind and station {station} gives none; pass --kind {self.KIND_CHOICE}")
        self.assertEqual(self.store_files(), before)

    def test_fire_of_a_ticket_whose_goal_cell_and_whose_goals_tsv_row_id_are_the_same_padded_text_warns_of_both_and_starts_an_unassigned_item(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("goals.tsv", "G1", id=" G1 ")
        self.hand_edit("rail.tsv", "T1", goal=" G1 ")
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"),
                         (0, "D1\n", f"brigade: warning: {self.PADDED_GOAL_ROW}\n"
                                     f"brigade: warning: T1 names goal ' G1 ', {self.NO_GOAL_ROW}; "
                                     "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.raw_cells("dishes.tsv", "D1", "kind", "goal"), (b"fix", b""))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "state", "kind", "goal"), (b"assigned", b"fix", b" G1 "))
        self.assertEqual(self.raw_cells("goals.tsv", " G1 ", "id"), (b" G1 ",))

    def test_fire_of_a_ticket_with_no_kind_prints_the_owner_refusal_and_the_not_waiting_refusal_first(self):
        self.open()
        self.rail_before_kinds("a", "b")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.brigade("set", "--thread", "t1")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "x", ok=False, owner=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.brigade("--owner", "t0@1", "fire", "--tickets", "T1", "--station", "feature", "--summary", "x", ok=False),
                         "brigade: owner t0@1 is stale; this store is owned by t1@1")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "feature", "--summary", "x", ok=False),
                         "brigade: T2 is done, not waiting")
        self.assertEqual(self.store_files(), before)

    def test_fire_refuses_a_ticket_of_an_item_that_owes_its_decision(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--state", "waiting"), "T1 waiting")
        before = self.unchanged()
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.unchanged(before)
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] one")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "redesign"), "Q1 answered")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")

    def test_fire_refuses_an_item_of_a_goal_at_its_share(self):
        self.open()
        self.goal("activity view", "--share", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one", "--goal", "G1")
        self.brigade("ticket", "add", "--kind", "polish", "--summary", "two", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix one")
        before = self.tables()
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "docs", "--summary", "Tidy two", ok=False),
                         "brigade: nothing fired: the G1 share holds (1 of 1)")
        self.assertEqual(self.tables(), before)

    def test_fire_takes_the_lead_tickets_kind_over_the_kind_of_its_station(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "upkeep")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("upkeep", ""), ("upkeep", "")))

    def test_fire_takes_the_ticket_of_an_item_under_its_budget(self):
        self.started()
        self.sent_back(2)
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")

    def test_fire_warns_of_a_bad_cell_on_a_ticket_after_the_lead_and_reads_that_ticket_as_unassigned(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2")
        self.hand_edit("rail.tsv", "T2", goal="G9")
        self.assertEqual(self.run_raw("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both"),
                         (0, "D1\n", "brigade: warning: T2 names goal 'G9', which has no row in goals.tsv; it reads as unassigned; "
                                     "run ticket set T2 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.label("rail.tsv", "T2"), ("fix", "G9"))

    def test_fire_with_no_kind_from_the_ticket_or_the_flag_gives_the_item_and_the_lead_ticket_the_kind_of_its_station(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "9")
        self.rail_before_kinds("a", "b", "c")
        for number, (station, kind) in enumerate((("bug-fix", "fix"), ("perf-issue", "fix"), ("investigation", "study")), 1):
            with self.subTest(station=station):
                self.assertEqual(self.brigade("fire", "--tickets", f"T{number}", "--station", station, "--summary", f"Do {number}"),
                                 f"D{number}")
                self.assertEqual(self.label("dishes.tsv", f"D{number}"), (kind, ""))
                self.assertEqual(self.label("rail.tsv", f"T{number}"), (kind, ""))

    def test_fire_with_no_ticket_needs_a_kind_from_the_flag_or_the_station(self):
        self.open()
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", ",", "--station", "feature", "--summary", "No ticket", ok=False),
                         "brigade: no ticket gives this item a kind and station feature gives none; "
                         f"pass --kind {self.KIND_CHOICE}")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("fire", "--tickets", ",", "--station", "feature", "--summary", "No ticket", "--kind", "study"), "D1")
        self.assertEqual(self.brigade("fire", "--tickets", ",", "--station", "bug-fix", "--summary", "No ticket"), "D2")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("dishes.tsv", "D2")), (("study", ""), ("fix", "")))

    def test_from_report_gives_each_ticket_the_kind_polish_and_the_goal_of_the_reports_item(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G2")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.assertEqual(len(self.follow_ups_in(BULLETS).splitlines()), 3)
        self.assertEqual(self.label("rail.tsv", "T2"), ("polish", "G2"))
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), [
            f"T2 waiting low polish G2 [report] Correctness: A no-ref `--source user` add still has no CLI dedupe. The new Refusals rule tells the coordinator to read `ticket list` before refiling. A `brigade.py` guard would make that structural. perf/reports/D1.md#1",
        ])

    def test_from_report_on_an_item_with_no_kind_and_no_goal_files_tickets_with_no_kind_and_no_goal(self):
        self.open()
        self.rail_before_kinds("a")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.hand_edit("dishes.tsv", "D1", kind="")
        self.follow_ups_in(BULLETS)
        self.assertEqual([self.label("rail.tsv", ident) for ident in ("T2",)], [("", "")])

    def test_report_entry_names_the_kind_of_each_entry_under_reports(self):
        self.fired_bug_fix()
        reports = self.review_file("file.md").parent
        (Path(self.temporary.name) / "findings.md").write_text("findings\n")
        self.linked("live.md", "findings.md")
        self.linked("dangling.md", "gone.md")
        (reports / "directory.md").mkdir()
        expected = {"file.md": "file", "live.md": "link", "dangling.md": "link", "directory.md": "other", "absent.md": "missing"}
        if hasattr(os, "mkfifo"):
            os.mkfifo(reports / "fifo.md")
            expected["fifo.md"] = "other"
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        restaurant = glob["Restaurant"](self.at)
        self.assertEqual({name: glob["report_entry"](restaurant, name) for name in expected}, expected)
        reports.rename(self.at / "kept")
        reports.symlink_to(self.at / "kept")
        self.assertEqual({name: glob["report_entry"](restaurant, name) for name in expected},
                         {**expected, "file.md": "elsewhere"})
        reports.unlink()
        reports.write_text("not a directory\n")
        self.assertEqual({glob["report_entry"](restaurant, name) for name in expected}, {"missing"})

    def test_ticket_add_goal_ticket_set_goal_and_fire_warn_of_a_goals_tsv_row_whose_id_an_earlier_row_has(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.copy_goal_row("G1", "second")
        warning = ("brigade: warning: goals.tsv has more than one row with id G1; the first is the goal and the others are not read; "
                   "remove the others from goals.tsv by hand\n")
        self.assertEqual(self.run_raw("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1"), (0, "T2\n", warning))
        self.assertEqual(self.run_raw("ticket", "set", "T1", "--goal", "G1"), (0, "T1 goal G1\n", warning))
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"), (0, "D1\n", warning))
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T2")), (("fix", "G1"), ("fix", "G1")))
