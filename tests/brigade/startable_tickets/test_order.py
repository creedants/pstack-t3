from ..support import ROOT, datetime, subprocess, sys, timedelta, timezone


class OrderAgreementMixin:
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
