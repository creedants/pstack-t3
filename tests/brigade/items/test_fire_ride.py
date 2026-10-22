from ..support import Path, ProcessReason, ROOT, _race_args, _wait_for_path, json, os, real_popen, sys

class FireRideMixin:
    def test_a_blocked_fire_with_no_kind_flag_and_no_ride_along_writes_the_note_keys_it_wrote_before_kinds(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Do b", "--branch", "docs/gate", ok=False)
        self.assertEqual(self.blocked_note(self.at, "T2"),
                         {"kind": "workers", "tickets": ["T2"], "station": "bug-fix", "summary": "Do b", "paths": "", "timebox": 60,
                          "branch": "docs/gate"})

    def test_a_fire_refused_by_a_spend_control_releases_no_lease_and_leaves_the_ticket_waiting(self):
        self.queue()
        self.brigade("set", "--cap", "fix=1")
        self.filed("Fix a", "--paths", "a.txt")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a", "--paths", "a.txt")
        self.filed("Fix b", "--paths", "b.txt")
        before = self.land("lease", "list")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix b",
                                      "--paths", "b.txt", ok=False),
                         "brigade: nothing fired: the fix cap holds (1 of 1)")
        self.assertEqual(self.land("lease", "list"), before)
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] Fix b")

    def test_a_replaced_coordinators_paused_commands_change_nothing(self):
        self.init_landing()
        self.landing_env()
        docs = self.store / "bridge-kit" / "docs"
        self.run_at(docs, "open", "--project-root", str(self.project), "--name", "Docs")
        (docs / "menu.md").write_text("## Purpose\n\nDocs.\n")
        self.run_at(docs, "set", "--thread", "t1")
        old = ("--owner", "t1@1")
        self.assertIn("owner t1@1", self.run_at(docs, *old, "status"))
        self.run_at(docs, *old, "ticket", "add", "--kind", "fix", "--summary", "one")
        self.assertEqual(self.run_at(docs, *old, "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                     "--paths", "README.md"), "D1 (lease L1 held by docs/D1)")
        self.run_at(docs, *old, "dish", "D1", "--thread", "w1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0", "--owner", "docs/@1")
        stale_brigade = "brigade: owner t1@1 is stale; this store is owned by t2@2"
        stale_land = "land: owner docs/@1 is stale; docs/ is at generation 2"
        land_script = str(ROOT / "t3/added/landing/scripts/land.py")
        commands = [
            ("pause", ["--store", str(self.store), "--at", str(docs), *old, "ticket", "add", "--kind", "fix", "--summary", "late"], stale_brigade),
            ("pause", ["--store", str(self.store), "--at", str(docs), *old, "dish", "D1", "--state", "dropped", "--stopped", "r1"],
             stale_brigade),
            ("land-pause", ["--repo", str(self.project), "lease", "release", "L1", "--owner", "docs/@1"], stale_land),
            ("land-pause", ["--repo", str(self.project), "lease", "renew", "L1", "--owner", "docs/@1"], stale_land),
        ]
        running = []
        support = Path(__file__).resolve().parent.parent / "support.py"
        for number, (mode, args, message) in enumerate(commands):
            case = Path(self.temporary.name) / f"stale{number}"
            case.mkdir()
            proc = real_popen(
                ProcessReason.RACE,
                [sys.executable, str(support), *_race_args(mode, case, args)],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True)
            running.append((case, proc, message))
        for case, _, _ in running:
            _wait_for_path(case / "paused", timeout=20)
        self.run_at(docs, "set", "--thread", "t2", "--replace")
        self.assertIn("owner t2@2", self.run_at(docs, "--owner", "t2@2", "status"))
        tables = ("rail.tsv", "log.tsv", "dishes.tsv", "restaurant.json")
        after_replace = {name: (docs / name).read_bytes() for name in tables}
        row = self.lease_row(1)
        for case, proc, message in running:
            (case / "proceed").touch()
            code, _, err = self._finish_race(proc)
            self.assertEqual((code, err), (1, message))
        self.assertEqual(self.run_at(docs, *old, "set", "--thread", "t2", "--workers", "9", ok=False), stale_brigade)
        self.assertEqual(self.run_at(docs, *old, "set", "--thread", "t1", ok=False), "brigade: thread t2 already recorded")
        self.assertEqual({name: (docs / name).read_bytes() for name in tables}, after_replace)
        self.assertEqual(self.lease_row(1), row)
        new = ("--owner", "t2@2")
        self.assertEqual(self.run_at(docs, *new, "ticket", "add", "--kind", "fix", "--summary", "late"), "T2")
        self.assertEqual(self.land("lease", "renew", "L1", "--owner", "docs/@2"), "L1 renewed")
        self.assertEqual(self.land("lease", "release", "L1", "--owner", "docs/@2"), "L1 released")
        self.assertEqual(self.run_at(docs, *new, "dish", "D1", "--state", "dropped", "--stopped", "r1"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "2"), "docs/ at generation 2")
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "1", ok=False),
                         "land: docs/ is at generation 2; a floor never lowers")

    def test_close_prints_no_claims_check_warning_for_an_item_in_progress_sent_back_blocked_or_merged(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n\nStatus: done\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.record("abc", "send-back")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.record("abc", "blocked")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.record("abc", "pass")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("passed")))
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

    def test_dropping_a_queued_item_whose_entry_bounced_releases_its_lease(self):
        self.started("--check", "test ! -e BROKEN", paths="README.md,BROKEN")
        self.brigade("dish", "D1", "--thread", "w1")
        sha = self.worker_commit("perf/d1", {"BROKEN": "x\n"})
        self.passed(sha)
        self.submit(sha)
        self.brigade("dish", "D1", "--state", "queued")
        self.land("land")
        self.assertIn("D1 holds L1", self.brigade("dish", "D1", "--state", "dropped", ok=False))
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", "--stopped", "idle"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_fire_refuses_a_low_feature_named_in_ride_along_that_is_under_the_lead_tickets_goal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--goal", "G1", "--priority", "low")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x",
                                      ok=False),
                         "brigade: T2 is a feature, and a feature does not ride along; start it as its own item")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "x"), "D1")

    def test_fire_refuses_a_ride_along_id_that_is_not_in_tickets_and_one_that_is_the_lead_ticket(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1", "--priority", "low")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x", ok=False),
                         "brigade: --ride-along names T2, which is not in --tickets; add it there or drop it")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T1,T2", "--station", "bug-fix", "--summary", "x",
                                      ok=False),
                         "brigade: T1 is the lead ticket, and the lead ticket cannot ride along; "
                         "put the ticket whose goal the item takes first in --tickets")
        self.assertEqual(self.store_files(), before)

    def test_fire_refuses_a_ride_along_ticket_of_a_paused_goal_and_starts_no_item(self):
        # The lead ticket T4 has no goal and the rider T5 is under paused G1, so the fire must read both.
        self.queue()
        self.goal("activity view")
        self.filed("a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a", "--paths", "a.txt")
        self.brigade("set", "--cap", "fix=1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--paths", "b.txt", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "fix", "--paths", "c.txt")
        self.brigade("ticket", "add", "--summary", "Add d", "--kind", "feature", "--paths", "d.txt", "--priority", "urgent")
        self.brigade("ticket", "add", "--summary", "Paused rider", "--kind", "polish", "--paths", "d.txt", "--priority", "low", "--goal", "G1")
        self.brigade("goal", "set", "G1", "--state", "paused")
        before = self.tables()
        self.assertEqual(self.brigade("fire", "--tickets", "T4,T5", "--ride-along", "T5", "--station", "feature", "--summary", "Add d", code=1),
                         "brigade: nothing fired: its goal is paused")
        self.assertEqual(self.tables(), before)
        self.assertEqual(self.brigade("ticket", "list", "--state", "assigned"), "T1 assigned normal fix G1 [user] a")

    def test_fire_refuses_a_ride_along_ticket_of_priority_normal_or_urgent(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "polish", "--goal", "G2", "--priority", "urgent")
        before = self.store_files()
        for ident, priority in (("T2", "normal"), ("T3", "urgent")):
            with self.subTest(ident=ident):
                self.assertEqual(self.brigade("fire", "--tickets", f"T1,{ident}", "--ride-along", ident, "--station", "bug-fix",
                                              "--summary", "x", ok=False),
                                 f"brigade: {ident} is {priority}; only a low ticket rides along; "
                                 f"start it on its own, or run ticket set {ident} --priority low")
        self.assertEqual(self.store_files(), before)
        self.brigade("ticket", "set", "T2", "--priority", "low")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x"), "D1")

    def test_fire_refuses_a_ride_along_ticket_that_is_a_feature(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x",
                                      ok=False),
                         "brigade: T2 is a feature, and a feature does not ride along; start it as its own item")
        self.assertEqual(self.store_files(), before)

    def test_fire_refuses_a_second_item_of_a_kind_at_its_cap(self):
        self.open()
        self.brigade("set", "--cap", "fix=1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix one")
        before = self.tables()
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix two", ok=False),
                         "brigade: nothing fired: the fix cap holds (1 of 1)")
        self.assertEqual(self.tables(), before)
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", ""))

    def test_fire_refuses_a_ticket_after_the_lead_that_is_under_another_goal_and_takes_it_as_a_ride_along(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", ok=False),
                         "brigade: T2 is under G2 and the lead ticket T1 is under G1; "
                         "name T2 in --ride-along, which takes a low ticket that is not a feature, or start it on its own")
        self.assertEqual(self.store_files(), before)
        rail = self.table_row(self.at, "rail.tsv", "T2")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do both"),
                         "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", "G1"))
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2"), {**rail, "state": "assigned", "dish": "D1"})
        self.assertEqual(self.label("rail.tsv", "T2"), ("polish", "G2"))

    def test_fire_refuses_a_ticket_of_a_paused_goal_and_starts_no_dish_and_logs_no_block(self):
        self.open()
        self.goal("activity view")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one", "--goal", "G1")
        self.brigade("goal", "set", "G1", "--state", "paused")
        before = self.tables()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix one", ok=False),
                         "brigade: nothing fired: its goal is paused")
        self.assertEqual(self.tables(), before)
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix G1 [user] one")
        self.brigade("goal", "set", "G1", "--state", "open")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix one"), "D1")

    def test_fire_refuses_a_ticket_under_a_goal_after_an_unassigned_lead_ticket_and_takes_it_as_a_ride_along(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G1", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", ok=False),
                         "brigade: T2 is under G1 and the lead ticket T1 is unassigned; "
                         "name T2 in --ride-along, which takes a low ticket that is not a feature, or start it on its own")
        self.assertEqual(self.store_files(), before)
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do both")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T2")), (("fix", ""), ("polish", "G1")))

    def test_fire_takes_a_report_ticket_with_an_empty_priority_cell_as_a_ride_along(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.follow_ups_in("## Follow-ups\n\n- Correctness: Tidy the guide.\n")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["priority"], "")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "fix", "--goal", "G2")
        self.assertEqual(self.brigade("fire", "--tickets", "T3,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x"), "D2")
        self.assertEqual((self.label("dishes.tsv", "D2"), self.label("rail.tsv", "T2")), (("fix", "G2"), ("polish", "G1")))

    def test_fire_takes_a_ticket_of_the_lead_tickets_goal_and_an_unassigned_ticket_with_no_ride_along_and_each_keeps_its_two_cells(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "polish")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2,T3", "--station", "bug-fix", "--summary", "Do all"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", "G1"))
        self.assertEqual([self.label("rail.tsv", ident) for ident in ("T1", "T2", "T3")],
                         [("fix", "G1"), ("feature", "G1"), ("polish", "")])

    def test_goal_set_refuses_done_and_dropped_while_a_ticket_of_the_goal_rides_along_in_another_goals_item(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do both")
        self.assert_goal_keeps_open("G2", "T2 riding along", "finish or drop the item each of those tickets rides along in")
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1, T2 waiting again")
        self.assert_goal_keeps_open("G2", "T2 waiting", "give each waiting ticket another goal with ticket set --goal, or none with --no-goal; a waiting "
                                    "ticket that an item in flight names keeps its goal until that item is merged or dropped")
