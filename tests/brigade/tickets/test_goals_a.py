from ..support import os

class GoalsAMixin:
    def test_a_goal_state_that_is_none_of_the_four_reads_as_open_with_a_warning_and_goal_set_state_rewrites_the_cell(self):
        self.open()
        self.goal()
        self.hand_edit("goals.tsv", "G1", state="finished")
        warning = ("brigade: warning: G1 has state 'finished', which is not open, paused, done, or dropped; it reads as open; "
                   "run goal set G1 --state <state>\n")
        code, out, err = self.run_raw("goal", "list")
        self.assertEqual((code, err), (0, warning))
        self.assertEqual(out.splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")
        self.assertEqual(self.goal("activity view", ok=False), warning + "brigade: same name as G1 (open); nothing added")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "open"), "G1 open")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "open")
        self.assertEqual(self.run_raw("goal", "list")[2], "")
        self.hand_edit("goals.tsv", "G1", state="finished")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "paused"), "G1 paused")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "paused")

    def test_a_goals_tsv_row_whose_id_an_earlier_row_has_is_warned_of_once_for_the_id_and_goal_list_prints_and_goal_set_changes_the_first_row(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.copy_goal_row("G1", "second")
        self.copy_goal_row("G1", "third")
        out = self.assert_each_printing_command_warns("goals.tsv has more than one row with id G1; the first is the goal and the "
                                                      "others are not read; remove the others from goals.tsv by hand")
        self.assertEqual(out["goal list"].splitlines()[::3],
                         ["G1 open activity view: 1 waiting, 0 in flight, 0 merged, 0 send-backs",
                          "G2 open current with upstream: 0 waiting, 0 in flight, 0 merged, 0 send-backs",
                          self.NO_UNASSIGNED_WORK])
        for name in ("second", "third"):
            self.assertNotIn(name, "".join(out.values()))
        self.assertEqual(self.brigade("goal", "set", "G1", "--name", "renamed"), "G1 name renamed")
        self.assertEqual([row[3] for row in self.rows_of("goals.tsv")], ["renamed", "current with upstream", "second", "third"])

    def test_a_goals_tsv_row_whose_id_has_white_space_at_an_end_is_warned_of_by_each_printing_command_and_is_not_listed_as_a_goal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("goals.tsv", "G1", id=" G1 ")
        out = self.assert_each_printing_command_warns(
            self.PADDED_GOAL_ROW, f"T1 names goal 'G1', {self.NO_GOAL_ROW}; run ticket set T1 --goal G<n> or --no-goal")
        self.assertEqual(out["status"].splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: fix 1 waiting", "goals: unassigned 1 waiting"])
        self.assertEqual(out["goal list"].splitlines()[::3],
                         ["G2 open current with upstream: 0 waiting, 0 in flight, 0 merged, 0 send-backs",
                          "unassigned: 1 waiting, 0 in flight, 0 merged, 0 send-backs"])
        self.assertEqual(out["ticket list"], "T1 waiting normal fix [user] a")
        self.assertNotIn("activity view", "".join(out.values()))
        self.assertEqual(self.raw_cells("goals.tsv", " G1 ", "id"), (b" G1 ",))

    def test_a_goals_tsv_row_with_no_id_is_warned_of_is_not_listed_as_a_goal_and_adds_nothing_to_the_unassigned_counts(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.copy_goal_row("", "no id")
        out = self.assert_each_printing_command_warns("goals.tsv has a row with no id; it is not a goal; remove it from goals.tsv by hand")
        self.assertEqual(out["status"].splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: fix 2 waiting", "goals: G1 1 waiting; unassigned 1 waiting"])
        self.assertEqual(out["goal list"].splitlines()[::3],
                         ["G1 open activity view: 1 waiting, 0 in flight, 0 merged, 0 send-backs",
                          "G2 open current with upstream: 0 waiting, 0 in flight, 0 merged, 0 send-backs",
                          "unassigned: 1 waiting, 0 in flight, 0 merged, 0 send-backs"])
        self.assertEqual(out["close --dry-run"].count("T1: a"), 1)
        self.assertNotIn("no id", "".join(out.values()))

    def test_fire_doc_item_with_no_shared_doc_records_bare_note(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Plain work")
        proc = self.fire_with_fake_land(case, "--tickets", "T1", "--station", "bug-fix", "--summary", "Plain",
                                        "--paths", "src/app.py", "--doc-item")
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()), (0, ""))
        dish = out.strip().split()[0]
        self.assertEqual(self.doc_log_note(self.at, dish), "doc-item")

    def test_fire_shared_doc_refusal_changes_no_file(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            leases = self.land("lease", "list")
            before = self.store_files()
            log = (self.at / "log.tsv").read_bytes()
            rail = (self.at / "rail.tsv").read_bytes()
            dishes = (self.at / "dishes.tsv").read_bytes()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                          "--paths", "docs/guide.md", "--timebox", "120", ok=False),
                             self.doc_line(("docs/guide.md",), timebox=120))
            self.assertEqual(self.land("lease", "list"), leases)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "rail.tsv").read_bytes(),
                              (self.at / "dishes.tsv").read_bytes()), (log, rail, dishes))
        case = self.fake_land()
        self.brigade("open", "--project-root", str(self.project), "--name", "Second")
        second = self.store / "bridge-kit" / "second"
        self.seed_doc_settings(second)
        self.run_at(second, "ticket", "add", "--kind", "fix", "--summary", "Long doc work")
        before = {str(path.relative_to(second)): path.read_bytes() for path in sorted(second.rglob("*"))
                  if path.is_file() and path.name != "restaurant.lock"}
        proc = self.fire_with_fake_land(case, "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                        "--paths", "docs/guide.md", "--timebox", "120")
        _, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()), (1, self.doc_line(("docs/guide.md",), timebox=120)))
        self.assertFalse((case / "land-calls").exists())
        after = {str(path.relative_to(second)): path.read_bytes() for path in sorted(second.rglob("*"))
                 if path.is_file() and path.name != "restaurant.lock"}
        self.assertEqual(after, before)

    def test_fire_shared_doc_timebox_limit(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "50")
        self.seed_doc_settings(docTimebox=None)
        for doc in self.SHARED_DOCS:
            with self.subTest(doc=doc):
                first = self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Fix {doc} quickly")
                proc = self.fire_with_fake_land(case, "--tickets", first, "--station", "bug-fix", "--summary", f"Fix {doc}",
                                                "--paths", doc, "--timebox", "90")
                out, err = proc.communicate(timeout=20)
                self.assertEqual((proc.returncode, err.strip()), (0, ""), doc)
                dish = out.strip()
                self.assertTrue(dish.startswith("D"), (doc, out))
                second = self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Fix {doc} slowly")
                before = self.store_files()
                calls = (case / "land-calls").read_text() if (case / "land-calls").exists() else ""
                proc = self.fire_with_fake_land(case, "--tickets", second, "--station", "bug-fix", "--summary", f"Fix {doc}",
                                                "--paths", doc, "--timebox", "91")
                out, err = proc.communicate(timeout=20)
                self.assertEqual((proc.returncode, err.strip()), (1, self.doc_line((doc,), timebox=91)), doc)
                self.assertEqual(self.store_files(), before, doc)
                self.assertEqual((case / "land-calls").read_text() if (case / "land-calls").exists() else "", calls, doc)

    def test_fire_shared_doc_with_doc_item_records_the_doc_row(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "50")
        self.seed_doc_settings()
        for doc in self.SHARED_DOCS:
            with self.subTest(doc=doc):
                ident = self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Long work on {doc}")
                proc = self.fire_with_fake_land(case, "--tickets", ident, "--station", "bug-fix", "--summary", f"Long {doc}",
                                                "--paths", doc, "--timebox", "120", "--doc-item")
                out, err = proc.communicate(timeout=20)
                self.assertEqual((proc.returncode, err.strip()), (0, ""), doc)
                dish = out.strip().split()[0]
                self.assertEqual(self.doc_log_note(self.at, dish), f"doc-item: {doc}")
                row = self.table_row(self.at, "dishes.tsv", dish)
                self.assertEqual((row["tickets"], row["timebox"]), (ident, "120"))

    def test_fire_similar_paths_hold_no_shared_doc(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.seed_doc_settings()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Backup work")
        proc = self.fire_with_fake_land(case, "--tickets", "T1", "--station", "bug-fix", "--summary", "Backup",
                                        "--paths", "docs/guide.md.bak", "--timebox", "120")
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()), (0, ""), "a sibling path does not match")
        self.assertTrue(out.strip().startswith("D1"), out)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Nested readme work")
        proc = self.fire_with_fake_land(case, "--tickets", "T2", "--station", "bug-fix", "--summary", "Nested",
                                        "--paths", "notes/README.md", "--timebox", "120")
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()), (0, ""), "only the repository-root README.md counts")
        self.assertTrue(out.strip().startswith("D2"), out)

    def test_goal_add_and_goal_set_refuse_a_share_that_is_not_a_whole_number_of_1_or_more_and_change_no_file(self):
        self.open()
        self.goal("kept")
        before = self.store_files()
        for share in ("0", "00", "-1", "two", "1.5", "+2", "\u0663"):
            refusal = f'brigade: --share takes a whole number of 1 or more, or "" for no limit, got {share!r}'
            with self.subTest(share=share):
                self.assertEqual(self.goal("new", f"--share={share}", ok=False), refusal)
                self.assertEqual(self.brigade("goal", "set", "G1", f"--share={share}", ok=False), refusal)
        self.assertEqual(self.store_files(), before)

    def test_goal_add_and_goal_set_store_a_share_without_its_leading_zeros_and_an_empty_share_as_an_empty_cell(self):
        self.open()
        self.goal("a", "--share", "02")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["share"], "2")
        self.assertEqual(self.brigade("goal", "set", "G1", "--share", "010"), "G1 share 10")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["share"], "10")
        self.assertEqual(self.brigade("goal", "set", "G1", "--share", ""), "G1 no share")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["share"], "")

    def test_goal_add_and_goal_set_warn_of_each_bad_goals_tsv_row_and_of_no_ticket(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="chore", goal="G9")
        self.hand_edit("goals.tsv", "G2", state="finished")
        self.copy_goal_row("", "no id")
        warnings = ("brigade: warning: G2 has state 'finished', which is not open, paused, done, or dropped; it reads as open; "
                    "run goal set G2 --state <state>\n"
                    "brigade: warning: goals.tsv has a row with no id; it is not a goal; remove it from goals.tsv by hand\n")
        self.assertEqual(self.run_raw("goal", "add", "--name", "third", "--purpose", "p", "--done", "d"), (0, "G3\n", warnings))
        self.assertEqual(self.run_raw("goal", "set", "G1", "--name", "renamed"), (0, "G1 name renamed\n", warnings))
        self.assertEqual(self.run_raw("goal", "set", "G1", "--state", "paused"), (0, "G1 paused\n", warnings))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "kind", "goal"), (b"chore", b"G9"))

    def test_goal_add_prints_the_new_id_appends_one_open_row_and_logs_one_goal_row(self):
        self.open()
        self.assertEqual(self.goal("activity view", "--share", "2"), "G1")
        self.assertEqual(self.goal("current with upstream"), "G2")
        rows = self.rows_of("goals.tsv")
        self.assertEqual([row[:1] + row[2:] for row in rows],
                         [["G1", "open", "activity view", "One page of activity", "The page ships", "2", ""],
                          ["G2", "open", "current with upstream", "One page of activity", "The page ships", "", ""]])
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"], ["G2", "open", "current with upstream"]])

    def test_goal_add_refuses_a_name_a_purpose_or_a_done_test_that_is_only_whitespace_and_changes_no_file(self):
        self.open()
        before = self.store_files()
        for flag in ("--name", "--purpose", "--done"):
            words = {"--name": "n", "--purpose": "p", "--done": "d", flag: " \t\n"}
            with self.subTest(flag=flag):
                self.assertEqual(self.brigade("goal", "add", *[word for pair in words.items() for word in pair], ok=False),
                                 "brigade: goal add needs a nonempty --name, --purpose, and --done")
        self.assertEqual(self.store_files(), before)

    def test_goal_add_refuses_the_name_of_an_open_goal_and_of_a_paused_goal_and_adds_no_row(self):
        self.open()
        self.goal("Activity view")
        before = self.store_files()
        self.assertEqual(self.goal("activity view", ok=False), "brigade: same name as G1 (open); nothing added")
        self.assertEqual(self.goal("  activity\tVIEW ", ok=False), "brigade: same name as G1 (open); nothing added")
        self.assertEqual(self.store_files(), before)
        self.brigade("goal", "set", "G1", "--state", "paused")
        self.assertEqual(self.goal("activity view", ok=False), "brigade: same name as G1 (paused); nothing added")
        self.assertEqual(len(self.rows_of("goals.tsv")), 1)

    def test_goal_add_takes_the_name_of_a_done_goal_and_of_a_dropped_goal(self):
        self.open()
        self.goal("activity view")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.assertEqual(self.goal("activity view"), "G2")
        self.brigade("goal", "set", "G2", "--state", "dropped", "--evidence", "not wanted")
        self.assertEqual(self.goal("activity view"), "G3")

    def test_goal_list_in_a_store_with_no_goals_table_creates_none_and_the_first_goal_add_writes_the_table_with_its_header(self):
        self.open()
        (self.at / "goals.tsv").unlink()
        self.assertEqual(self.brigade("goal", "list"), f"no open or paused goals\n{self.NO_UNASSIGNED_WORK}")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "paused", ok=False),
                         "brigade: no goal G1; run goal list --all to see every goal")
        self.assertFalse((self.at / "goals.tsv").exists())
        self.assertEqual(self.goal(), "G1")
        header, row = (self.at / "goals.tsv").read_text().splitlines()
        self.assertEqual(header, "id\tat\tstate\tname\tpurpose\tdone\tshare\tclosed")
        self.assertEqual(row.split("\t")[2:], ["open", "activity view", "One page of activity", "The page ships", "", ""])
