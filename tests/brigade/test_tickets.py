from .support import (
    setUpModule,
    BULLETS,
    BrigadeTest,
    CLAUDE,
    CODEX,
    Path,
    ROOT,
    ProcessReason,
    SCRIPT,
    STILL_OPEN,
    TRACKED,
    _TicketsRace,
    _wait_for_path,
    call,
    datetime,
    json,
    os,
    real_process,
    runpy,
    shlex,
    timezone,
)


class TicketsTest(BrigadeTest, _TicketsRace):
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

    def test_ticket_add_names_a_waiting_ticket_of_another_source_with_the_same_summary(self):
        self.open()
        self.brigade("set", "--intake", "github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--source", "github", "--ref", "#12")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", ok=False),
                         self.same_text_refusal("T1", "waiting"))

    def test_ticket_add_with_a_request_id_still_refuses_the_summary_of_a_waiting_ticket(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--request", "A2", ok=False),
                         self.same_text_refusal("T1", "waiting"))

    def test_ticket_add_with_a_ref_takes_the_summary_of_a_waiting_ticket(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--ref", "#12"), "T2")

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

    def test_queueing_or_merging_needs_a_pass_at_the_current_sha(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", "--sha", "def", ok=False),
                         "brigade: only reviewed work lands: D1 has no review verdict for def")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued"), "D1 queued")
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract, waiting to land: 1\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)\nkinds: fix 1 in flight")
        self.assertEqual(self.brigade("dish", "D1", "--state", "merged"), "D1 merged")
        self.assertEqual(self.brigade("ticket", "list", "--state", "done"), "T1 done normal fix [user] s")

    def test_from_report_files_one_waiting_ticket_per_bullet_and_a_rerun_adds_nothing(self):
        self.fired_bug_fix()
        first = ("A no-ref `--source user` add still has no CLI dedupe. The new Refusals rule tells the coordinator to read "
                 "`ticket list` before refiling. A `brigade.py` guard would make that structural.")
        second = ("`docs/guide.md` still describes intake as `gh issue list` and `gh pr list` when the standing orders name "
                  "them. That file is outside this lease.")
        third = "PR #87 was not commented on, approved, or closed."
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines(), [
            f"T2 added from perf/reports/D1.md#1: {first}",
            f"T3 added from perf/reports/D1.md#2: {second}",
            f"T4 added from perf/reports/D1.md#3: {third}",
        ])
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1],
                         f"T4 waiting low polish [report] {third} perf/reports/D1.md#3")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["summary"], first)
        self.assertIn(f"\tticket\tT4\twaiting\t{third}\n", (self.at / "log.tsv").read_text())
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines(), [
            f"skipped perf/reports/D1.md#1, same text as T2 (waiting): {first}",
            f"skipped perf/reports/D1.md#2, same text as T3 (waiting): {second}",
            f"skipped perf/reports/D1.md#3, same text as T4 (waiting): {third}",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)
        self.brigade("ticket", "set", "T4", "--state", "dropped")
        self.brigade("ticket", "set", "T3", "--state", "done")
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines()[1:], [
            f"skipped perf/reports/D1.md#2, same text as T3 (done): {second}",
            f"skipped perf/reports/D1.md#3, same text as T4 (dropped): {third}",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_skips_text_a_ticket_already_holds_whatever_its_spacing_and_case(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "pr #87   was NOT commented on, approved, or closed.")
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines()[2],
                         "skipped perf/reports/D1.md#3, same text as T2 (waiting): "
                         "PR #87 was not commented on, approved, or closed.")
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1].split(" ", 1)[0], "T4")

    def test_from_report_refuses_the_flags_of_a_hand_written_ticket(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "## Follow-ups\n\n- One thing.\n")
        before = (self.at / "rail.tsv").read_bytes()
        add = ("ticket", "add", "--from-report", "reports/D1.md")
        for extra in (("--source", "github"), ("--ref", "x"), ("--request", "A1"), ("--again",)):
            self.assertEqual(self.brigade(*add, *extra, ok=False),
                             "brigade: --from-report takes no --source, --ref, --request, or --again")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "x", "--dry-run", ok=False),
                         "brigade: --dry-run needs --from-report")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "x", "--source", "report", ok=False),
                         "brigade: no coordinator owns intake from report; the one that reads it runs set --intake report")
        for flags, message in ((("--summary", "x", "--from-report", "reports/D1.md"),
                                "argument --from-report: not allowed with argument --summary"),
                               ((), "one of the arguments --summary --from-report is required")):
            # process exit status
            result = real_process(
                ProcessReason.EXIT_STATUS, SCRIPT,
                ["--store", str(self.store), "--at", str(self.at), "ticket", "add", *flags],
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_a_state_is_logged_only_when_it_changes(self):
        self.open()
        self.fire_one()
        self.brigade("dish", "D1", "--state", "in-progress", "--task", "t-1")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", "grok/grok-4.7", "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("ticket", "set", "T1", "--state", "done")
        log = (self.at / "log.tsv").read_text()
        self.assertEqual(log.count("\tdish\tD1\tin-progress\t"), 1)
        self.assertEqual(log.count("\tticket\tT1\tdone\t"), 1)

    def test_tabs_and_newlines_in_input_cannot_break_a_table(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "line one\nline\ttwo")
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] line one line two")

    def test_dropping_an_item_leaves_its_other_tickets_alone(self):
        self.open()
        for summary in ("one", "two"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.assertEqual(self.brigade("dish", "D2", "--state", "dropped"), "D2 dropped")
        self.assertEqual(self.brigade("ticket", "list"), "T1 assigned normal fix [user] one\nT2 done normal fix [user] two")

    def test_blocked_counts_follow_the_block_that_holds_now(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate",
                         "--paths", "src", ok=False)
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate blocked: lease")
            self.assertIn("Perf (reports milestones): waiting tickets: 1 (1 blocked)", self.brigade("walk"))
            self.land("lease", "release", "L1")
            self.assertTrue(self.brigade("watch").splitlines()[1].startswith("T1: unblocked; run fire"))
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate")
            self.assertIn("Perf (reports milestones): waiting tickets: 1\n", self.brigade("walk") + "\n")
            self.land("lease", "claim", "--holder", "engine/D2", "--paths", "src")
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate blocked: lease")

    def test_fire_and_watch_name_the_worker_cap_first_when_both_caps_hold(self):
        from unittest import mock
        self.init_landing("--cap", "1")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one", "--paths", "src")
            refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "two",
                                   "--paths", "docs", ok=False)
            self.assertEqual(refused, "brigade: nothing fired: 1 of 1 workers running")
            self.assertEqual(self.brigade("watch").splitlines()[-1], "T2: waiting for a worker (1 of 1 running)")
            self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] two blocked: workers")
            self.brigade("set", "--workers", "2")
            self.assertEqual(self.brigade("watch").splitlines()[-1],
                             "T2: waiting for room in the repository (1 of 1 changes in flight)")
            self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"),
                             "T2 waiting normal fix [user] two blocked: repository")

    def test_a_losing_open_keeps_the_owners_ticket_and_menu(self):
        case = Path(self.temporary.name) / "race"
        case.mkdir()
        project = Path(self.temporary.name) / "app"
        project.mkdir()
        directory = self.store / "app" / "docs"
        args = ["--store", str(self.store), "open", "--project-root", str(project), "--name", "Docs"]
        owner = loser = None
        try:
            owner = self._spawn_race("owner", case, args)
            _wait_for_path(case / "metadata-ready")
            loser = self._spawn_race("loser", case, args)
            _wait_for_path(case / "loser-saw-meta")
            self.assertIsNone(owner.poll())
            self.assertIsNone(loser.poll())
            if not (directory / "rail.tsv").exists():
                (case / "loser-after-meta").touch()
                _wait_for_path(case / "loser-at-scaffold")
                (case / "owner-proceed").touch()
                owner_code, owner_out, owner_err = self._finish_race(owner)
                owner = None
            else:
                owner_code = owner_out = owner_err = None
            # inherited environment
            added = real_process(
                ProcessReason.INHERITED_ENV, SCRIPT,
                ["--at", str(directory), "ticket", "add", "--kind", "fix", "--summary", "Keep this ticket"],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
            self.assertEqual((added.returncode, added.stdout), (0, "T1\n"), added.stderr)
            menu = directory / "menu.md"
            menu.write_text(menu.read_text() + "Keep the menu sentence.\n")
            if (case / "loser-at-scaffold").exists():
                (case / "loser-scaffold-proceed").touch()
            else:
                (case / "loser-after-meta").touch()
            loser_code, loser_out, loser_err = self._finish_race(loser)
            loser = None
            if owner is not None:
                (case / "owner-proceed").touch()
                owner_code, owner_out, owner_err = self._finish_race(owner)
                owner = None
        finally:
            for proc in (owner, loser):
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        self.assertEqual((owner_code, owner_err), (0, ""), owner_out)
        self.assertEqual(owner_out, f"opened {directory}")
        self.assertEqual((loser_code, loser_err), (0, ""), loser_out)
        self.assertEqual(loser_out, f"exists {directory}")
        self.assertIn("Keep this ticket", (directory / "rail.tsv").read_text())
        self.assertIn("Keep the menu sentence.", (directory / "menu.md").read_text())

    def test_a_refused_fire_stays_blocked_until_the_lease_is_free(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate",
                                   "--paths", "src", "--timebox", "60", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate blocked: lease")
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on L1 (engine/D1)")
            self.land("lease", "release", "L1")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on L2 (engine/D1)")
            self.land("lease", "release", "L2")
            line = "T1: unblocked; run fire --tickets=T1 --station=bug-fix '--summary=Fix the gate' --paths=src --timebox=60"
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + line)
            started = self.brigade(*shlex.split(line.split("run ", 1)[1]))
            self.assertEqual(started, "D1 (lease L3 held by perf/D1)")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], "src,changes/perf%2Fd1.md")
            self.assertEqual(self.brigade("ticket", "list"), "T1 assigned normal fix [user] Fix the gate")

    def test_the_worker_cap_defaults_to_two_and_a_later_fire_clears_the_block(self):
        self.open()
        self.assertNotIn("workers", json.loads((self.at / "restaurant.json").read_text()))
        for summary in ("one", "two", "three"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a"), "D1")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b"), "D2")
        refused = self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "c", ok=False)
        self.assertIn("nothing fired: 2 of 2 workers running", refused)
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"),
                         "T3 waiting normal fix [user] three blocked: workers")
        self.assertIn("T3: waiting for a worker (2 of 2 running)", self.brigade("watch"))
        self.brigade("set", "--workers", "3")
        self.assertEqual(self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "c"), "D3")
        self.assertNotIn("blocked:", self.brigade("ticket", "list"))

    def test_a_replayed_keep_parked_relay_logs_inbox_done_once(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.open_admin()
        before = (self.at / "86.tsv").read_bytes()
        self.admin("request", "--to", "perf", "answer perf Q1: keep parked")
        self.assertEqual(self.brigade("inbox", "take"), "A1: answer perf Q1: keep parked")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.brigade("inbox", "take"), "A1: answer perf Q1: keep parked")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.brigade("inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.brigade("inbox", "take"), "nothing handed to you")
        self.assertEqual(self.brigade("inbox", "done", "A1"), "A1 done")
        self.assertEqual((self.at / "86.tsv").read_bytes(), before)
        done = [row for row in self.log_rows() if row["kind"] == "inbox-done"]
        self.assertEqual([(row["id"], row["state"], row["note"]) for row in done],
                         [("A1", "done", "answer perf Q1: keep parked")])

    def test_a_store_without_a_generation_works_until_set_thread_records_one(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["thread"] = "t1"
        (self.at / "restaurant.json").write_text(json.dumps(meta))
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "legacy"), "T1")
        self.brigade("set", "--thread", "t1")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "unowned", ok=False, owner=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.brigade("--owner", "t1@1", "ticket", "add", "--kind", "fix", "--summary", "owned"), "T2")

    def test_item_mode_flags_refuse_light_and_a_missing_reason_with_exit_1(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        before = (self.at / "log.tsv").read_bytes()
        refusals = [
            (("dish", "D1", "--mode", "light", "--reason", "r"),
             "brigade: an item's mode only moves to full; change the coordinator with set --mode light"),
            (("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "t", "--mode", "light", "--reason", "r"),
             "brigade: an item's mode only moves to full; change the coordinator with set --mode light"),
            (("dish", "D1", "--mode", "full"), 'brigade: --mode full needs --reason "<one line>"'),
            (("dish", "D1", "--mode", "full", "--reason", " "), 'brigade: --mode full needs --reason "<one line>"'),
            (("dish", "D1", "--reason", "r"), "brigade: --reason goes with --mode full"),
            (("dish", "D1", "--mode", "fast", "--reason", "r"), "brigade: --mode takes full, got 'fast'"),
            (("dish", "D1", "--mode", "full", "--reason", "r", "--state", "dropped"),
             "brigade: --mode full needs open work; run --state dropped without it"),
            (("dish", "D1", "--mode", "full", "--reason", "r", "--state", "queued"),
             "brigade: --mode full needs open work; run --state queued without it"),
        ]
        for args, message in refusals:
            outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at), *args])
            self.assertEqual((outcome.code, outcome.stderr_text().strip()), (1, message), args)
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] t")

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

    def test_ticket_add_and_ticket_set_refuse_a_priority_outside_urgent_normal_and_low(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a")
        choices = ("urgent", "normal", "low")
        self.assert_refused_choice(
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--priority", "high", ok=False), "--priority", "high", choices)
        self.assert_refused_choice(
            self.brigade("ticket", "set", "T1", "--priority", "high", ok=False), "--priority", "high", choices)
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] a")

    def test_a_ticket_with_an_empty_priority_cell_reads_urgent_from_upstream_low_from_report_and_normal_from_other_sources(self):
        self.open()
        self.brigade("set", "--intake", "upstream,report,github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--source", "upstream")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--source", "report")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "c", "--source", "github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "d")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "e", "--source", "upstream", "--request", "A1")
        self.assertEqual(self.brigade("ticket", "list").splitlines(), [
            "T1 waiting urgent fix [upstream] a",
            "T2 waiting low fix [report] b",
            "T3 waiting normal fix [github] c",
            "T4 waiting normal fix [user] d",
            "T5 waiting urgent fix [upstream (request A1)] e",
        ])
        self.assertEqual([self.cells(f"T{number}") for number in range(1, 6)], [("", "", "")] * 5)

    def test_a_recorded_priority_wins_over_the_default_of_the_source(self):
        self.open()
        self.brigade("set", "--intake", "upstream")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--source", "upstream", "--priority", "low")
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting low fix [upstream] a")

    def test_ticket_list_reads_rail_rows_written_before_the_priority_paths_and_decision_cells(self):
        self.open()
        self.old_rail()
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.brigade("ticket", "list").splitlines(), [
            "T1 waiting normal [user] Fix login",
            "T2 waiting low [report] Tidy the guide perf/reports/D1.md#1",
            "T3 waiting urgent [upstream] Sync upstream abc123",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "New", "--priority", "urgent", "--paths", "a.txt"), "T4")
        self.assertEqual((self.at / "rail.tsv").read_bytes()[:len(before)], before)
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1], "T4 waiting urgent fix [user] New")
        self.assertEqual((self.at / "rail.tsv").read_text().splitlines()[-1].split("\t")[5:],
                         ["", "New", "urgent", "a.txt", "", "fix", ""])

    def test_a_rail_row_with_six_fields_or_thirteen_is_malformed(self):
        self.open()
        stamp = "2026-10-01T00:00:00+00:00"
        for fields in (["T1", stamp, "waiting", "user", "", ""], ["T1", stamp, "waiting", "user", "", "", "s", *[""] * 6]):
            (self.at / "rail.tsv").write_text("id\n" + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("ticket", "list", ok=False),
                             "brigade: bridge-kit/perf/rail.tsv line 2 is malformed; fix or remove it")

    def test_a_rail_row_with_seven_ten_eleven_or_twelve_fields_is_read(self):
        self.open()
        stamp = "2026-10-01T00:00:00+00:00"
        for extra in ([], ["low", "a.txt", ""], ["low", "a.txt", "", ""], ["low", "a.txt", "", "", ""]):
            (self.at / "rail.tsv").write_text("id\n" + "\t".join(["T1", stamp, "waiting", "user", "", "", "s", *extra]) + "\n")
            self.assertEqual(self.brigade("ticket", "list"), f"T1 waiting {'low' if extra else 'normal'} [user] s", extra)

    def test_a_rewrite_of_an_old_table_keeps_each_cell_and_adds_an_empty_kind_and_goal_cell_to_each_row(self):
        self.open()
        self.old_item()
        rail, dishes = ((self.at / name).read_text().splitlines()[1].split("\t") for name in ("rail.tsv", "dishes.tsv"))
        self.assertEqual(self.brigade("dish", "D1", "--timebox", "90"), "D1 in-review")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")
        dishes[11], rail[7] = "90", "low"
        self.assertEqual((self.at / "dishes.tsv").read_text().splitlines()[1].split("\t"), [*dishes, "", ""])
        self.assertEqual((self.at / "rail.tsv").read_text().splitlines()[1].split("\t"), [*rail, "", ""])
        self.assertEqual((self.at / "dishes.tsv").read_text().splitlines()[0].split("\t")[-3:], ["reported", "kind", "goal"])
        self.assertEqual((self.at / "rail.tsv").read_text().splitlines()[0].split("\t")[-3:], ["decision", "kind", "goal"])

    def test_a_store_with_no_goals_table_runs_status_watch_startable_ticket_list_and_close_and_gains_no_table(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "next")
        (self.at / "goals.tsv").unlink()
        files = sorted(self.store_files())
        self.assertEqual(self.brigade("status").splitlines()[0], "thread not recorded")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")
        self.assertEqual(self.brigade("startable").splitlines()[:2],
                         ["workers: 1 of 2 running, 1 idle, auto-start: normal", "waiting tickets: 1, unknown: 1"])
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] next")
        code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "# Perf report")
        self.assertEqual(sorted(self.store_files()), files)
        self.assertFalse((self.at / "goals.tsv").exists())

    def test_a_priority_paths_or_decision_change_adds_no_log_row_and_keeps_a_blocked_ticket_blocked(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "two", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] two blocked: workers")
        before = ((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes())
        self.assertEqual(self.brigade("ticket", "set", "T2", "--priority", "urgent"), "T2 priority urgent")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--paths", "a.txt"), "T2 paths a.txt")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--decision"), "T2 decision")
        self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes()), before)
        self.assertEqual(self.cells("T2"), ("urgent", "a.txt", "yes"))
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting urgent fix [user] two blocked: workers")

    def test_ticket_set_prints_each_given_change_in_one_line_and_refuses_a_call_with_none(self):
        self.open()
        for summary in ("a", "b", "c"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--state", "dropped"), "T1 dropped")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--state", "dropped", "--priority", "normal"),
                         "T2 dropped; priority normal")
        self.assertEqual(self.brigade("ticket", "set", "T3", "--decision", "--paths", "b,a", "--priority", "low",
                                      "--state", "waiting"), "T3 waiting; priority low; paths a,b; decision")
        self.assertEqual(self.cells("T3"), ("low", "a,b", "yes"))
        self.assertEqual(self.brigade("ticket", "set", "T3", "--paths", "", "--no-decision"), "T3 no paths; no decision")
        self.assertEqual(self.cells("T3"), ("low", "", ""))
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "set", "T3", ok=False),
                         "brigade: ticket set needs --state, --priority, --paths, --decision, --no-decision, --kind, --goal, or --no-goal")
        self.assertEqual(self.brigade("ticket", "set", "T9", ok=False), "brigade: no T9 in rail.tsv")
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_and_ticket_set_record_paths_sorted_normalized_and_without_repeats(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "src/b.py, docs/,src/b.py,./a.txt")
        self.assertEqual(self.cells("T1"), ("", "a.txt,docs,src/b.py", ""))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--paths", "x\\y,/abs/z,new/file.py"),
                         "T1 paths abs/z,new/file.py,x/y")
        self.assertEqual(self.cells("T1"), ("", "abs/z,new/file.py,x/y", ""))

    def test_ticket_set_with_empty_paths_clears_the_recorded_paths(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "a.txt")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--paths", ""), "T1 no paths")
        self.assertEqual(self.cells("T1"), ("", "", ""))

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

    def test_ticket_add_and_ticket_set_record_and_clear_the_decision_cell(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--decision")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b")
        self.assertEqual([self.cells("T1"), self.cells("T2")], [("", "", "yes"), ("", "", "")])
        self.assertEqual(self.brigade("ticket", "set", "T1", "--no-decision"), "T1 no decision")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--decision"), "T2 decision")
        self.assertEqual([self.cells("T1"), self.cells("T2")], [("", "", ""), ("", "", "yes")])

    def test_ticket_paths_records_quoted_paths_on_waiting_tickets_that_record_none_and_logs_nothing(self):
        self.unrecorded_tickets()
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths"),
                         "T1 paths docs/guide.md,tests/test_install.py\nT2 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": "docs/guide.md,tests/test_install.py", "T2": "", "T3": "t3/runtime.md", "T4": ""})
        after = self.store_files()
        self.assertEqual(sorted(name for name in after if after[name] != before[name]), ["rail.tsv"])

    def test_a_second_ticket_paths_run_changes_no_store_file(self):
        self.unrecorded_tickets()
        self.brigade("ticket", "paths")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths"), "T2 quotes no tracked path")
        self.assertEqual(self.store_files(), before)

    def test_ticket_paths_dry_run_prints_what_it_would_record_and_changes_no_store_file(self):
        self.unrecorded_tickets()
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths", "--dry-run", owner=False),
                         "T1 would record docs/guide.md,tests/test_install.py\nT2 quotes no tracked path")
        self.assertEqual(self.store_files(), before)

    def test_ticket_paths_says_so_when_every_waiting_ticket_records_paths(self):
        self.tracked_project(*TRACKED)
        self.open()
        self.assertEqual(self.brigade("ticket", "paths"), "every waiting ticket records paths")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Reword `README.md`", "--paths", "t3/runtime.md")
        self.assertEqual(self.brigade("ticket", "paths"), "every waiting ticket records paths")

    def test_ticket_paths_refuses_a_project_that_is_not_a_git_repository_and_changes_no_store_file(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `docs/guide.md`")
        before = self.store_files()
        for flags in ((), ("--dry-run",)):
            self.assertEqual(self.brigade("ticket", "paths", *flags, ok=False),
                             f"brigade: cannot list tracked files in {self.project.resolve()}")
        self.assertEqual(self.store_files(), before)

    def test_ticket_paths_records_the_one_tracked_file_that_has_a_quoted_file_name_with_no_directory(self):
        self.tracked_project("src/app/tool.py", "docs/guide.md")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths src/app/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "src/app/tool.py"})

    def test_ticket_paths_records_nothing_for_a_quoted_file_name_that_no_tracked_file_has(self):
        self.tracked_project("src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `nope.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_nothing_for_a_quoted_file_name_that_two_tracked_files_outside_skills_have(self):
        self.tracked_project("src/app/tool.py", "lib/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_the_t3_file_for_a_quoted_file_name_that_a_file_at_the_same_path_under_skills_also_has(self):
        self.tracked_project("t3/added/x/scripts/tool.py", "skills/x/scripts/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths t3/added/x/scripts/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "t3/added/x/scripts/tool.py"})

    def test_ticket_paths_records_the_t3_file_for_a_quoted_file_name_that_a_file_at_another_path_under_skills_also_has(self):
        self.tracked_project("t3/scripts/roles.py", "skills/pstack-runtime/scripts/roles.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `roles.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths t3/scripts/roles.py")
        self.assertEqual(self.paths_cells(), {"T1": "t3/scripts/roles.py"})

    def test_ticket_paths_records_nothing_for_a_quoted_file_name_that_files_under_skills_under_t3_and_elsewhere_have(self):
        self.tracked_project("skills/x/tool.py", "t3/added/x/tool.py", "vendor/x/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_a_file_under_skills_for_a_quoted_file_name_that_no_other_tracked_file_has(self):
        self.tracked_project("skills/x/scripts/tool.py", "t3/added/x/scripts/other.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths skills/x/scripts/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "skills/x/scripts/tool.py"})

    def test_ticket_paths_records_the_one_tracked_file_that_has_a_quoted_file_name_with_a_line_suffix(self):
        self.tracked_project("src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py:12` and `tool.py:30-34`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths src/app/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "src/app/tool.py"})

    def test_ticket_paths_records_a_quoted_file_name_that_is_a_tracked_file_at_the_root_when_another_directory_tracks_that_name(self):
        self.tracked_project("tool.py", "src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "tool.py"})

    def test_ticket_paths_dry_run_prints_the_one_tracked_file_that_has_a_quoted_file_name_and_changes_no_store_file(self):
        self.tracked_project("src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths", "--dry-run", owner=False), "T1 would record src/app/tool.py")
        self.assertEqual(self.store_files(), before)

    def test_ticket_paths_records_nothing_for_a_file_name_only_one_nested_file_has_quoted_with_a_dot_slash_before_it_a_slash_after_it_or_spaces_around_it(self):
        self.tracked_project("src/app/tool.py", "tool.py:12")
        self.open()
        for quote in ("./tool.py", "tool.py/", "./tool.py:12", " tool.py "):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Fix `{quote}`")
        self.assertEqual(self.brigade("ticket", "paths"), "\n".join(f"T{number} quotes no tracked path" for number in (1, 2, 3, 4)))
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "", "T3": "", "T4": ""})

    def test_ticket_paths_records_nothing_for_a_quote_that_is_a_tracked_file_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12", "src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py:12`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_nothing_for_a_quote_that_is_a_tracked_directory_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12/notes.md", "src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py:12`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_from_report_prints_a_list_item_with_no_text_as_not_filed_and_gives_it_no_number(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Fix a.\n- \n*   \n1. \n- Fix b.\n").splitlines(), [
            "T2 added from perf/reports/D1.md#1: Fix a.",
            "T3 added from perf/reports/D1.md#2: Fix b.",
            "not filed, list item with no text",
            "not filed, list item with no text",
            "not filed, list item with no text",
        ])
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), [
            "T2 waiting low polish [report] Fix a. perf/reports/D1.md#1",
            "T3 waiting low polish [report] Fix b. perf/reports/D1.md#2",
        ])

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

    def test_ticket_add_goal_records_an_open_goal_and_a_paused_goal(self):
        self.open()
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "b", "--kind", "upkeep", "--goal", "G2"), "T2")
        self.assertEqual((self.label("rail.tsv", "T1"), self.label("rail.tsv", "T2")), (("fix", "G1"), ("upkeep", "G2")))

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

    def test_ticket_add_and_ticket_set_refuse_a_done_goal_and_change_no_file(self):
        self.assert_closed_goal_takes_no_ticket("done")

    def test_ticket_add_and_ticket_set_refuse_a_dropped_goal_and_change_no_file(self):
        self.assert_closed_goal_takes_no_ticket("dropped")

    def test_ticket_set_kind_goal_and_no_goal_change_the_two_cells_of_a_waiting_ticket_and_add_no_log_row(self):
        self.open()
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        before = ((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes())
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "G1", "--kind", "polish"), "T1 kind polish; goal G1")
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", "G1"))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "G2"), "T1 goal G2")
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", "G2"))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--no-goal"), "T1 unassigned")
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", ""))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--kind", "study", "--priority", "low", "--no-goal", "--state", "dropped"),
                         "T1 dropped; priority low; kind study; unassigned")
        self.assertEqual(self.label("rail.tsv", "T1"), ("study", ""))
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "dropped")
        self.assertEqual((self.at / "log.tsv").read_bytes().count(b"\n"), before[0].count(b"\n") + 1)

    def test_ticket_set_with_goal_and_no_goal_together_exits_2_and_changes_no_file(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        before = self.store_files()
        code, out, _ = self.run_raw("ticket", "set", "T1", "--goal", "G1", "--no-goal")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(self.store_files(), before)

    def test_ticket_set_kind_goal_and_no_goal_are_refused_on_a_done_ticket_and_on_a_dropped_ticket_and_change_no_file(self):
        self.open()
        self.goal()
        for number, state in enumerate(("done", "dropped"), 1):
            self.brigade("ticket", "add", "--summary", f"work {number}", "--kind", "fix", "--goal", "G1")
            self.brigade("ticket", "set", f"T{number}", "--state", state)
        before = self.store_files()
        for number, state in enumerate(("done", "dropped"), 1):
            for flags in (("--kind", "polish"), ("--goal", "G1"), ("--no-goal",), ("--priority", "low", "--kind", "polish"),
                          ("--state", "waiting", "--no-goal")):
                with self.subTest(state=state, flags=flags):
                    self.assertEqual(self.brigade("ticket", "set", f"T{number}", *flags, ok=False),
                                     f"brigade: T{number} is {state}; --kind, --goal, and --no-goal change a waiting ticket only")
        self.assertEqual(self.store_files(), before)

    def test_ticket_set_kind_goal_and_no_goal_are_refused_on_an_assigned_ticket_with_its_items_id_and_change_no_file(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        before = self.store_files()
        for flags in (("--kind", "polish"), ("--goal", "G1"), ("--no-goal",), ("--priority", "low", "--kind", "polish")):
            with self.subTest(flags=flags):
                self.assertEqual(self.brigade("ticket", "set", "T1", *flags, ok=False),
                                 "brigade: T1 is assigned; --kind, --goal, and --no-goal change a waiting ticket only; "
                                 "D1 keeps the kind and goal it took when it started")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")

    def test_ticket_set_kind_goal_and_no_goal_are_refused_on_a_waiting_ticket_that_an_item_in_flight_names_and_change_no_file_and_priority_alone_is_accepted(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2", "--priority", "low")
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "feature", "--summary", "Build a")
        for ticket in ("T1", "T2"):
            self.assertEqual(self.brigade("ticket", "set", ticket, "--state", "waiting"), f"{ticket} waiting")
        for state in ("in-progress", "in-review", "passed", "sent-back", "blocked", "queued"):
            self.hand_edit("dishes.tsv", "D1", state=state)
            before = self.store_files()
            for ticket, flags in (("T1", ("--kind", "polish")), ("T1", ("--goal", "G2")), ("T1", ("--no-goal",)),
                                  ("T1", ("--priority", "urgent", "--kind", "polish")), ("T2", ("--goal", "G1"))):
                with self.subTest(state=state, ticket=ticket, flags=flags):
                    self.assertEqual(self.brigade("ticket", "set", ticket, *flags, ok=False),
                                     f"brigade: {ticket} is a ticket of D1, which is {state}; --kind, --goal, and --no-goal change "
                                     "a waiting ticket that no item in flight names; D1 keeps the kind and goal it took when it "
                                     f"started; drop D1, and every other item in flight that names {ticket}, first, or leave {ticket} as it is")
            self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")

    def test_ticket_set_kind_and_goal_change_a_ticket_set_back_to_waiting_whose_item_is_merged_or_dropped_and_leave_the_items_cells(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.brigade("ticket", "set", "T1", "--state", "waiting")
        self.hand_edit("dishes.tsv", "D1", state="merged")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--kind", "polish", "--goal", "G2"), "T1 kind polish; goal G2")
        self.assertEqual((self.label("rail.tsv", "T1"), self.label("dishes.tsv", "D1")), (("polish", "G2"), ("fix", "G1")))
        self.hand_edit("dishes.tsv", "D1", state="dropped")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--kind", "upkeep", "--no-goal"), "T1 kind upkeep; unassigned")
        self.assertEqual((self.label("rail.tsv", "T1"), self.label("dishes.tsv", "D1")), (("upkeep", ""), ("fix", "G1")))

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

    def test_a_dropped_ticket_and_a_done_ticket_of_a_goal_do_not_keep_the_goal_open(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "set", "T1", "--state", "dropped")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "dropped", "--evidence", "why"), "G1 dropped")

    def test_a_goal_change_while_fire_claims_its_lease_is_caught_before_the_write_and_the_lease_is_released(self):
        case = self.fake_land()
        self.two_goals()
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        proc = self.fire_with_fake_land(case, "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", "--paths", "src")
        _wait_for_path(case / "claiming")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--goal", "G2"), "T2 goal G2")
        before = self.store_files()
        (case / "claim-proceed").touch()
        _, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()),
                         (1, "brigade: nothing fired: T2 is under G2 and the lead ticket T1 is under G1; "
                             "name T2 in --ride-along, which takes a low ticket that is not a feature, or start it on its own"))
        self.assertEqual((case / "land-calls").read_text().splitlines(),
                         ["lease claim --holder perf/D1 --paths src,changes/perf%2Fd1.md", "lease release L7"])
        self.assertEqual(self.store_files(), before)

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

    def test_follow_up_kind_is_empty_for_an_item_with_no_kind_and_else_fix_at_urgent_or_normal_and_polish_at_low(self):
        follow_up_kind = runpy.run_path(str(SCRIPT))["follow_up_kind"]
        self.assertEqual([follow_up_kind("", priority) for priority in ("urgent", "normal", "low")], ["", "", ""])
        self.assertEqual([follow_up_kind("feature", priority) for priority in ("urgent", "normal", "low")], ["fix", "fix", "polish"])

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

    def test_goal_set_refuses_a_name_a_purpose_or_a_done_test_that_is_only_whitespace_and_changes_no_file(self):
        self.open()
        self.goal()
        before = self.store_files()
        for flag in ("--name", "--purpose", "--done"):
            with self.subTest(flag=flag):
                self.assertEqual(self.brigade("goal", "set", "G1", flag, " ", "--state", "paused", ok=False),
                                 f"brigade: goal set {flag} takes text; a goal keeps a name, a purpose, and a done test")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_refuses_evidence_beside_state_open_state_paused_or_no_state_and_changes_no_file(self):
        self.open()
        self.goal()
        before = self.store_files()
        for flags in (("--state", "paused"), ("--state", "open"), ("--name", "other")):
            with self.subTest(flags=flags):
                self.assertEqual(self.brigade("goal", "set", "G1", *flags, "--evidence", "x", ok=False),
                                 "brigade: --evidence goes only with --state done or --state dropped")
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

    def test_goal_set_moves_an_open_goal_to_paused_and_a_paused_goal_to_open_and_logs_each_move(self):
        self.open()
        self.goal()
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "paused"), "G1 paused")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "paused")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "open"), "G1 open")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "open")
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"], ["G1", "paused", "activity view"],
                                           ["G1", "open", "activity view"]])

    def test_goal_set_closes_a_paused_goal_with_evidence(self):
        self.open()
        self.goal()
        self.brigade("goal", "set", "G1", "--state", "paused")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "dropped", "--evidence", "not wanted"), "G1 dropped")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "dropped")

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

    def test_goal_set_that_changes_no_state_adds_no_log_row(self):
        self.open()
        self.goal()
        before = ((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes())
        self.assertEqual(self.brigade("goal", "set", "G1", "--name", "other", "--share", "4"), "G1 name other; share 4")
        self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes()), before)

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

    def test_goal_set_state_refuses_a_state_that_is_none_of_the_four(self):
        self.open()
        self.goal()
        self.assert_refused_choice(self.brigade("goal", "set", "G1", "--state", "finished", ok=False), "--state", "finished",
                                   ("open", "paused", "done", "dropped"))

    def test_the_worked_store_holds_the_kind_and_goal_cells_its_tests_count(self):
        self.worked_store()
        self.assertEqual([(row[0], row[2], row[5], *row[10:]) for row in self.rows_of("rail.tsv")], [
            ("T1", "done", "D1", "feature", "G1"), ("T2", "assigned", "D2", "fix", "G1"), ("T3", "assigned", "D2", "polish", "G2"),
            ("T4", "waiting", "", "fix", "G1"), ("T5", "waiting", "", "polish", ""), ("T6", "assigned", "D3", "upkeep", "G2"),
            ("T7", "assigned", "D4", "study", ""), ("T8", "waiting", "", "", ""), ("T9", "waiting", "", "feature", "G1"),
            ("T10", "done", "D5", "polish", "")])
        self.assertEqual([(row[0], row[2], row[4], *row[15:]) for row in self.rows_of("dishes.tsv")], [
            ("D1", "merged", "T1", "feature", "G1"), ("D2", "in-review", "T2,T3", "fix", "G1"), ("D3", "sent-back", "T6", "upkeep", "G2"),
            ("D4", "in-progress", "T7", "study", ""), ("D5", "merged", "T10", "polish", "")])

    def test_ticket_list_prints_the_kind_then_the_goal_after_the_priority_and_neither_for_a_ticket_with_no_kind_and_no_goal(self):
        self.worked_store()
        self.hand_edit("rail.tsv", "T9", kind="")
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), [
            "T4 waiting normal fix G1 [user] Activity page drops a row",
            "T5 waiting low polish [user] Tidy the lease docs",
            "T8 waiting normal [user] Installer keeps a relative config home",
            "T9 waiting normal G1 [user] Activity page filters by coordinator",
        ])
        self.assertEqual(self.brigade("ticket", "list", "--state", "assigned").splitlines()[:2], [
            "T2 assigned normal fix G1 [user] Lease check arms a reservation",
            "T3 assigned low polish G2 [user] Tidy the lease help",
        ])

    def test_a_waiting_ticket_whose_kind_cell_holds_no_kind_is_warned_of_by_each_printing_command_and_counts_as_untagged(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="bugfix")
        out = self.assert_each_printing_command_warns(f"T1 has kind 'bugfix', {self.NOT_A_KIND}; run ticket set T1 --kind <kind>")
        self.assertEqual(out["status"].splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "untagged: 1"])
        self.assertEqual(out["ticket list"], "T1 waiting normal [user] a")
        self.assertEqual(out["startable"].splitlines()[2], "T1 normal, 0m: unknown, no paths recorded")
        self.assertEqual(out["close --dry-run"].splitlines()[4:], ["## New tickets, not started", "", "- T1: a",
                                                                  "", "## Spend", "", "none"])
        self.assertNotIn("bugfix", "".join(out.values()))

    def test_a_waiting_ticket_that_an_item_in_flight_names_is_warned_of_for_a_bad_kind_and_a_bad_goal_with_that_items_id_and_state(self):
        self.fired_bug_fix()
        self.brigade("ticket", "set", "T1", "--state", "waiting")
        self.hand_edit("rail.tsv", "T1", kind="chore", goal="G9")
        held = f"{self.NOT_WHILE} T1 is a ticket of D1, which is in-progress; correct it in rail.tsv by hand"
        self.assert_each_printing_command_warns(f"T1 has kind 'chore', {self.NOT_A_KIND}; {held}",
                                                f"T1 names goal 'G9', {self.NO_GOAL_ROW}; {held}")

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

    def test_goal_set_refuses_an_empty_id_and_goal_add_gives_the_next_id_when_goals_tsv_has_a_row_with_no_id(self):
        self.two_goals()
        self.copy_goal_row("", "no id")
        before = self.store_files()
        self.assertEqual(self.brigade("goal", "set", "", "--name", "x", ok=False),
                         "brigade: warning: goals.tsv has a row with no id; it is not a goal; remove it from goals.tsv by hand\n"
                         "brigade: no goal ; run goal list --all to see every goal")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("goal", "add", "--name", "third", "--purpose", "p", "--done", "d"), "G3")

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

    def test_fire_ride_along_of_a_low_ticket_whose_kind_cell_is_a_padded_feature_warns_and_leaves_the_cell_as_it_was(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--priority", "low")
        self.hand_edit("rail.tsv", "T2", kind=" feature ")
        has = f"brigade: warning: T2 has kind ' feature ', {self.NOT_A_KIND}; "
        self.assertEqual(self.run_raw("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", "--ride-along", "T2"),
                         (0, "D1\n", has + "run ticket set T2 --kind <kind>\n"))
        self.assertEqual(self.raw_cells("rail.tsv", "T2", "state", "kind", "goal"), (b"assigned", b" feature ", b""))
        code, out, err = self.run_raw("ticket", "list")
        self.assertEqual((code, err), (0, has + f"{self.NOT_WHILE} T2 is assigned; correct it in rail.tsv by hand\n"))
        self.assertEqual(out, "T1 assigned normal fix [user] a\nT2 assigned low [user] b\n")

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

    def test_goal_set_state_done_warns_of_a_waiting_ticket_whose_goal_cell_is_that_goals_id_padded(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("rail.tsv", "T1", goal=" G1 ")
        self.assertEqual(self.run_raw("goal", "set", "G2", "--state", "paused"), (0, "G2 paused\n", ""))
        self.assertEqual(self.run_raw("goal", "set", "G1", "--state", "done", "--evidence", "shipped"),
                         (0, "G1 done\n", f"brigade: warning: T1 names goal ' G1 ', {self.NO_GOAL_ROW}; "
                                          "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "state", "goal"), (b"waiting", b" G1 "))

    def test_a_ticket_added_to_a_store_from_before_kinds_leaves_each_older_row_of_the_ticket_table_byte_identical(self):
        self.store_from_before_kinds()
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "New", "--kind", "fix"), "T14")
        after = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(after[len(before):].decode().rstrip("\n").split("\t")[5:], ["", "New", "", "", "", "fix", ""])
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1], "T14 waiting normal fix [user] New")

    def test_next_asks_about_a_blocked_ticket_only_while_it_waits(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "One")
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Two", ok=False)
        self.assertEqual(refused, "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("set", "--reports-to", "admin-thread")
        young = self.next_sheet("liveness")
        self.assertNotIn("watch still shows T2 blocked", young)
        stamp = self.backdate_blocked("T2", 2)
        waiting = self.next_sheet("liveness")
        self.assertIn(
            "if watch still shows T2 blocked: t3_thread_send admin-thread mode queue "
            f"clientRequestId blocked:bridge-kit/perf/T2: blocked perf T2: waiting since {stamp}. "
            "Lease ids are not in the store.",
            waiting)
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Two")
        self.assertIn("T2 assigned", self.brigade("ticket", "list"))
        assigned = self.next_sheet("liveness")
        self.assertNotIn("watch still shows T2 blocked", assigned)
        self.assertNotIn("clientRequestId blocked:", assigned)

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

    def test_fire_directory_holding_a_skill_file_names_that_file(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.seed_doc_settings()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long skill work")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Skill",
                                      "--paths", "t3/added/brigade", "--timebox", "120", ok=False),
                         self.doc_line(("t3/added/brigade/SKILL.md",), timebox=120))
        self.assertEqual(self.store_files(), before)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Scripts work")
        proc = self.fire_with_fake_land(case, "--tickets", "T2", "--station", "bug-fix", "--summary", "Scripts",
                                        "--paths", "t3/added/brigade/scripts", "--timebox", "120")
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()), (0, ""))
        self.assertTrue(out.strip().startswith("D1"), out)

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

    def test_ticket_help_names_paths(self):
        # -h output
        result = real_process(ProcessReason.HELP, SCRIPT, ["--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("add, list, update, move, take, or record paths on tickets on the rail", " ".join(result.stdout.split()))
