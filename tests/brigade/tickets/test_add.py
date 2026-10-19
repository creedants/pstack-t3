from ..support import ROOT

class AddMixin:
    def test_a_ticket_added_to_a_store_from_before_kinds_leaves_each_older_row_of_the_ticket_table_byte_identical(self):
        self.store_from_before_kinds()
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "New", "--kind", "fix"), "T14")
        after = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(after[len(before):].decode().rstrip("\n").split("\t")[5:], ["", "New", "", "", "", "fix", ""])
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1], "T14 waiting normal fix [user] New")

    def test_again_lifts_neither_the_ref_refusal_nor_the_request_refusal_nor_the_source_refusal(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--ref", "#12", "--request", "A2")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--ref", "#12", "--again", ok=False),
                         "brigade: #12 is already T1 (waiting); nothing added")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--request", "A2", "--again", ok=False),
                         "brigade: request A2 is already T1; nothing added")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--source", "github", "--again", ok=False),
                         "brigade: no coordinator owns intake from github; the one that reads it runs set --intake github")
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user (request A2)] Fix the login page #12")

    def test_the_skill_quotes_the_same_summary_refusal_ticket_add_prints(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        line = self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", ok=False)
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        section = text.split("## Filing tracker work", 1)[1].split("\n## ", 1)[0]
        step = next(row for row in section.splitlines() if row.startswith("4. **Refusals.**"))
        self.assertEqual(line, "brigade: same text as T1 (waiting); nothing added; pass --again to file a second ticket")
        self.assertIn("`" + line.replace("T1 (waiting)", "T<n> (<state>)") + "`", step)
        self.assertNotIn("never refused", step)

    def test_ticket_add_and_ticket_set_record_and_clear_the_decision_cell(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--decision")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b")
        self.assertEqual([self.cells("T1"), self.cells("T2")], [("", "", "yes"), ("", "", "")])
        self.assertEqual(self.brigade("ticket", "set", "T1", "--no-decision"), "T1 no decision")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--decision"), "T2 decision")
        self.assertEqual([self.cells("T1"), self.cells("T2")], [("", "", ""), ("", "", "yes")])

    def test_ticket_add_and_ticket_set_record_paths_sorted_normalized_and_without_repeats(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "src/b.py, docs/,src/b.py,./a.txt")
        self.assertEqual(self.cells("T1"), ("", "a.txt,docs,src/b.py", ""))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--paths", "x\\y,/abs/z,new/file.py"),
                         "T1 paths abs/z,new/file.py,x/y")
        self.assertEqual(self.cells("T1"), ("", "abs/z,new/file.py,x/y", ""))

    def test_ticket_add_and_ticket_set_refuse_a_done_goal_and_change_no_file(self):
        self.assert_closed_goal_takes_no_ticket("done")

    def test_ticket_add_and_ticket_set_refuse_a_dropped_goal_and_change_no_file(self):
        self.assert_closed_goal_takes_no_ticket("dropped")

    def test_ticket_add_and_ticket_set_refuse_a_goal_id_with_no_row_and_an_empty_goal_and_change_no_file(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G7", ok=False),
                         "brigade: no goal G7; run goal list --all to see every goal")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "G7", "--priority", "low", ok=False),
                         "brigade: no goal G7; run goal list --all to see every goal")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "", ok=False),
                         'brigade: --goal "" names no goal; run goal list --all to see every goal')
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "", "--priority", "low", ok=False),
                         'brigade: --goal "" names no goal; run goal list --all to see every goal')
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_and_ticket_set_refuse_a_path_that_is_empty_or_the_repository_or_outside_it(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "a.txt")
        before = self.store_files()
        for value, part in (("..", ".."), ("src,../x", "../x"), ("a,,b", ""), (".", "."), ("/", "/"), ("a/../..", "a/../.."),
                            (" ", " "), ("a, ./", " ./")):
            refusal = f"brigade: --paths takes files or directories inside the repository, got {part!r}"
            self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--paths", value, ok=False), refusal, value)
            self.assertEqual(self.brigade("ticket", "set", "T1", "--paths", value, ok=False), refusal, value)
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_and_ticket_set_refuse_a_priority_outside_urgent_normal_and_low(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a")
        choices = ("urgent", "normal", "low")
        self.assert_refused_choice(
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--priority", "high", ok=False), "--priority", "high", choices)
        self.assert_refused_choice(
            self.brigade("ticket", "set", "T1", "--priority", "high", ok=False), "--priority", "high", choices)
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] a")

    def test_ticket_add_goal_and_ticket_set_goal_refuse_an_id_whose_goals_tsv_row_has_a_space_at_each_end_warn_of_the_row_and_change_no_file(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("goals.tsv", "G1", id=" G1 ")
        before = self.store_files()
        warning = f"brigade: warning: {self.PADDED_GOAL_ROW}\n"
        for goal in (" G1 ", "G1"):
            with self.subTest(goal=goal):
                refusal = warning + f"brigade: no goal {goal}; run goal list --all to see every goal\n"
                self.assertEqual(self.run_raw("ticket", "add", "--summary", "Fix bug", "--kind", "fix", "--goal", goal), (1, "", refusal))
                self.assertEqual(self.run_raw("ticket", "set", "T1", "--goal", goal), (1, "", refusal))
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_goal_records_an_open_goal_and_a_paused_goal(self):
        self.open()
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "b", "--kind", "upkeep", "--goal", "G2"), "T2")
        self.assertEqual((self.label("rail.tsv", "T1"), self.label("rail.tsv", "T2")), (("fix", "G1"), ("upkeep", "G2")))

    def test_ticket_add_names_a_waiting_ticket_of_another_source_with_the_same_summary(self):
        self.open()
        self.brigade("set", "--intake", "github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--source", "github", "--ref", "#12")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", ok=False),
                         self.same_text_refusal("T1", "waiting"))

    def test_ticket_add_records_each_of_the_five_kinds_and_refuses_a_word_that_is_none_of_them(self):
        self.open()
        for number, kind in enumerate(("feature", "fix", "polish", "upkeep", "study"), 1):
            self.assertEqual(self.brigade("ticket", "add", "--summary", f"work {number}", "--kind", kind), f"T{number}")
            self.assertEqual(self.label("rail.tsv", f"T{number}"), (kind, ""))
        before = self.store_files()
        self.assert_refused_choice(self.brigade("ticket", "add", "--summary", "other", "--kind", "chore", ok=False), "--kind", "chore",
                                   ("feature", "fix", "polish", "upkeep", "study"))
        self.assert_refused_choice(self.brigade("ticket", "set", "T1", "--kind", "chore", ok=False), "--kind", "chore",
                                   ("feature", "fix", "polish", "upkeep", "study"))
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_refuses_a_user_request_with_the_summary_of_a_waiting_ticket(self):
        self.open()
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "fix  the LOGIN page", ok=False),
                         self.same_text_refusal("T1", "waiting"))
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "  Fix the\tlogin\npage  ", ok=False),
                         self.same_text_refusal("T1", "waiting"))
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the login page")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the loginpage"), "T2")
        self.assertEqual(self.brigade("ticket", "list"),
                         "T1 waiting normal fix [user] Fix the login page\nT2 waiting normal fix [user] Fix the loginpage")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "fix  the LOGIN page", "--again"), "T3")

    def test_ticket_add_refuses_a_user_request_with_the_summary_of_an_assigned_ticket(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix login")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", ok=False),
                         self.same_text_refusal("T1", "assigned"))

    def test_ticket_add_takes_a_user_request_with_the_summary_of_a_done_or_dropped_ticket(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page"), "T2")
        self.brigade("ticket", "set", "T2", "--state", "dropped")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page"), "T3")

    def test_ticket_add_with_a_ref_takes_the_summary_of_a_waiting_ticket(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--ref", "#12"), "T2")

    def test_ticket_add_with_a_request_id_still_refuses_the_summary_of_a_waiting_ticket(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--request", "A2", ok=False),
                         self.same_text_refusal("T1", "waiting"))

    def test_ticket_add_with_a_summary_and_no_kind_is_refused_and_changes_no_file(self):
        self.open()
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "Tidy the lease docs", ok=False), self.NEEDS_KIND)
        self.assertEqual(self.brigade("ticket", "add", "--summary", "Tidy the lease docs", "--priority", "urgent", "--paths", "docs",
                                      "--decision", ok=False), self.NEEDS_KIND)
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_with_no_kind_and_a_stale_owner_or_no_owner_prints_the_owner_refusal_first(self):
        self.open()
        self.brigade("set", "--thread", "t1")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "a", ok=False, owner=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.brigade("--owner", "t0@1", "ticket", "add", "--summary", "a", ok=False),
                         "brigade: owner t0@1 is stale; this store is owned by t1@1")
        self.assertEqual(self.brigade("--owner", "t1@1", "ticket", "add", "--summary", "a", ok=False), self.NEEDS_KIND)
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_with_no_kind_prints_the_request_source_ref_and_same_text_refusals_first(self):
        self.open()
        self.brigade("set", "--intake", "github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--request", "A2")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--source", "github", "--ref", "#12")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "other", "--request", "A2", ok=False),
                         "brigade: request A2 is already T1; nothing added")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "other", "--source", "linear", ok=False),
                         "brigade: no coordinator owns intake from linear; the one that reads it runs set --intake linear")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "other", "--source", "github", "--ref", "#12", ok=False),
                         "brigade: #12 is already T2 (waiting); nothing added")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "fix the login page", ok=False),
                         self.same_text_refusal("T1", "waiting"))
        self.assertEqual(self.store_files(), before)

    def test_ticket_list_prints_the_priority_that_ticket_add_or_ticket_set_recorded(self):
        self.open()
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--priority", "urgent"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--priority", "low"), "T2")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "c", "--priority", "normal"), "T3")
        self.assertEqual(self.brigade("ticket", "list"),
                         "T1 waiting urgent fix [user] a\nT2 waiting low fix [user] b\nT3 waiting normal fix [user] c")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--priority", "urgent"), "T2 priority urgent")
        self.assertEqual(self.brigade("ticket", "list"),
                         "T1 waiting low fix [user] a\nT2 waiting urgent fix [user] b\nT3 waiting normal fix [user] c")
        self.assertEqual([self.cells(ident)[0] for ident in ("T1", "T2", "T3")], ["low", "urgent", "normal"])
