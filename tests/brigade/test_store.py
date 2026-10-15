from .support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    Path,
    ProcessReason,
    ROOT,
    SCRIPT,
    _wait_for_path,
    real_popen,
    real_process,
    json,
    os,
    runpy,
    shlex,
    subprocess,
    sys,
    time,
)


class StoreTest(BrigadeTest):
    def test_open_creates_the_store_once_and_keeps_edits(self):
        self.assertEqual(self.open(), f"opened {self.at}")
        (self.at / "menu.md").write_text("# Menu: Perf\n\nKeep startup under 400 ms.\n")
        self.assertEqual(self.open(), f"exists {self.at}")
        self.assertEqual((self.at / "menu.md").read_text(), "# Menu: Perf\n\nKeep startup under 400 ms.\n")
        self.assertIn("only through the repository's landing queue", (self.at / "house-rules.md").read_text())
        self.assertIn("Never use them in replies", (self.at / "house-rules.md").read_text())

    def test_set_landing_exits_2(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        # process exit status
        result = real_process(
            ProcessReason.EXIT_STATUS, SCRIPT,
            ["--store", str(self.store), "--at", str(self.at), "set", "--landing", "merge"],
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_set_schedule_drops_a_recorded_name_and_a_missing_one(self):
        self.open()
        self.brigade("set", "--schedule", "liveness=sched-4", "--schedule", "drain=sched-5")
        self.brigade("set", "--schedule", "liveness=")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"], {"drain": "sched-5"})
        self.brigade("set", "--schedule", "evening=")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"], {"drain": "sched-5"})
        self.brigade("set", "--schedule", "morning=sched-1")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"],
                         {"drain": "sched-5", "morning": "sched-1"})
        error = self.brigade("set", "--schedule", "drain", ok=False)
        self.assertIn("NAME=ID", error)
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"],
                         {"drain": "sched-5", "morning": "sched-1"})

    def test_set_reporting_changes_the_level_and_rejects_an_unknown_one(self):
        self.open()
        self.brigade("set", "--reporting", "every-turn")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["reporting"], "every-turn")
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: every-turn, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal")
        self.brigade("set", "--reporting", "digest")
        before = (self.at / "restaurant.json").read_text()
        error = self.brigade("set", "--reporting", "hourly", ok=False)
        for level in ("every-turn", "milestones", "digest"):
            self.assertIn(level, error)
        self.assertEqual((self.at / "restaurant.json").read_text(), before)
        opened = self.brigade("open", "--project-root", str(self.project), "--name", "Loud",
                              "--reporting", "hourly", ok=False)
        for level in ("every-turn", "milestones", "digest"):
            self.assertIn(level, opened)
        self.assertFalse((self.store / "bridge-kit" / "loud").exists())

    def test_set_scratch_dir_reads_back_stores_the_resolved_path_and_a_clear_removes_the_key(self):
        self.open()
        parent = str(Path.home().resolve())
        meta = json.loads(self.brigade("set", "--scratch-dir", f"{parent}/./x/.."))
        self.assertEqual(meta["scratchDir"], parent)
        meta = json.loads(self.brigade("set", "--scratch-dir", ""))
        self.assertNotIn("scratchDir", meta)

    def test_set_scratch_dir_refuses_a_relative_tilde_or_tmp_value_and_changes_nothing(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        refusal = "brigade: --scratch-dir takes an absolute directory outside /tmp, or empty string to clear it"
        for bad in ("scratch", "~/scratch", "/tmp", "/tmp/scratch", "/home\n"):
            with self.subTest(bad=bad):
                self.assertEqual(self.brigade("set", "--scratch-dir", bad, ok=False), refusal)
                self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_set_cap_and_review_repair_a_string_int_and_list_stored_map_and_write_a_clean_one(self):
        # A hand edit leaves a non-object here, and set merges into an existing value only when it is a dictionary.
        self.open()
        cases = (("caps", '"oops"', ("--cap", "polish=1"), {"polish": 1}),
                 ("caps", "2", ("--cap", "polish=1"), {"polish": 1}),
                 ("caps", '["polish"]', ("--cap", "polish=1"), {"polish": 1}),
                 ("review", '"oops"', ("--review", "fix=light"), {"fix": "light"}),
                 ("review", "2", ("--review", "fix=light"), {"fix": "light"}),
                 ("review", '["fix"]', ("--review", "fix=light"), {"fix": "light"}))
        for key, stored, flag, clean in cases:
            with self.subTest(key=key, stored=stored):
                meta = json.loads((self.at / "restaurant.json").read_text())
                meta[key] = json.loads(stored)
                (self.at / "restaurant.json").write_text(json.dumps(meta))
                self.assertEqual(json.loads(self.brigade("set", *flag))[key], clean)
                self.assertEqual(json.loads((self.at / "restaurant.json").read_text())[key], clean)

    def test_dropping_an_item_returns_its_tickets_to_waiting(self):
        from unittest import mock
        self.init_landing()
        ref = "https://github.com/o/r/issues/1"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("set", "--intake", "github")
            self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "bug", "--source", "github", "--ref", ref), "T1")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "fix",
                                          "--paths", "a.py"), "D1 (lease L1 held by perf/D1)")
            self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
            self.assertEqual(self.brigade("ticket", "list"), f"T1 waiting normal fix [github] bug {ref}")
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["dish"], "")
            self.assertEqual([(row["kind"], row["id"], row["state"], row["note"]) for row in self.log_rows()][-2:],
                             [("dish", "D1", "dropped", "fix (released L1)"),
                              ("ticket", "T1", "waiting", "bug (back from dropped D1)")])
            self.assertIn("waiting tickets: 1", self.brigade("status"))
            self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "bug again", "--source", "github", "--ref", ref,
                                          ok=False), f"brigade: {ref} is already T1 (waiting); nothing added")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "fix",
                                          "--paths", "a.py"), "D2 (lease L2 held by perf/D2)")
            self.assertEqual(self.brigade("dish", "D2", "--state", "dropped"), "D2 dropped; T1 waiting again")
            self.brigade("ticket", "set", "T1", "--state", "dropped")
            self.brigade("set", "--intake", "")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_open_prints_the_sibling_on_the_same_project_root(self):
        self.open()
        menu = (self.at / "menu.md").read_text()
        menu = menu.replace(
            "What this restaurant exists to achieve, in one or two sentences.",
            "Keep startup under 400 ms.")
        menu = menu.replace(
            "Work this restaurant does not take, even when asked.",
            "Docs and release notes\nVendor upgrades")
        (self.at / "menu.md").write_text(menu)
        self.brigade("set", "--thread", "thread-perf")
        docs_dir = self.store / "bridge-kit" / "docs"
        self.assertEqual(
            self.brigade("open", "--project-root", str(self.project), "--name", "Docs"),
            "\n".join([
                f"opened {docs_dir}",
                f"sibling Perf ({self.at}), thread thread-perf",
                "  purpose: Keep startup under 400 ms.",
                "  off the menu: Docs and release notes; Vendor upgrades",
            ]))
        self.assertEqual(
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf"),
            "\n".join([
                f"exists {self.at}",
                "thread thread-perf already recorded",
                f"sibling Docs ({docs_dir}), thread not recorded",
                "  purpose: not written yet",
                "  off the menu: not written yet",
            ]))

    def test_open_refuses_a_directory_that_holds_another_project_root(self):
        first = (Path(self.temporary.name) / "left" / "app").resolve()
        second = (Path(self.temporary.name) / "right" / "app").resolve()
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        directory = self.store / "app" / "docs"
        self.assertEqual(
            self.brigade("open", "--project-root", str(first), "--name", "Docs"),
            f"opened {directory}")
        before = (directory / "restaurant.json").read_bytes()
        error = self.brigade("open", "--project-root", str(second), "--name", "Docs", ok=False)
        self.assertEqual(
            error,
            f"brigade: {directory} already holds a coordinator for {first}; pick another --name")
        self.assertEqual((directory / "restaurant.json").read_bytes(), before)

    def test_two_processes_opening_one_name_on_different_roots_leave_one_store(self):
        roots = []
        for label in ("left", "right"):
            path = (Path(self.temporary.name) / label / "app").resolve()
            path.mkdir(parents=True)
            roots.append(path)
        directory = self.store / "app" / "docs"
        procs = []
        for root in roots:
            # race between two processes
            procs.append(real_popen(
                ProcessReason.RACE,
                [sys.executable, str(SCRIPT), "--store", str(self.store),
                 "open", "--project-root", str(root), "--name", "Docs"],
            ))
        finished = []
        for proc in procs:
            out, err = proc.communicate(timeout=15)
            finished.append((proc.returncode, out, err))
        self.assertEqual(sorted(code for code, _, _ in finished), [0, 1])
        self.assertEqual(len(list((directory.parent).glob("*/restaurant.json"))), 1)
        meta = json.loads((directory / "restaurant.json").read_text())
        winner = next(item for item in finished if item[0] == 0)
        loser = next(item for item in finished if item[0] == 1)
        self.assertIn(meta["projectRoot"], [str(root) for root in roots])
        self.assertTrue(winner[1].startswith(f"opened {directory}"))
        self.assertIn(f"already holds a coordinator for {meta['projectRoot']}", loser[2])
        self.assertIn("pick another --name", loser[2])

    def test_open_rejects_a_restaurant_json_that_is_not_valid_json(self):
        self.at.mkdir(parents=True)
        (self.at / "restaurant.json").write_text('{"restaurant":')
        started = time.monotonic()
        error = self.brigade("open", "--project-root", str(self.project), "--name", "Perf", ok=False)
        elapsed = time.monotonic() - started
        self.assertEqual(error, f"brigade: {self.at / 'restaurant.json'} is not valid JSON")
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 3)
        self.assertEqual((self.at / "restaurant.json").read_text(), '{"restaurant":')
        self.assertFalse((self.at / "menu.md").exists())
        self.assertFalse((self.at / "rail.tsv").exists())

    def test_open_accepts_restaurant_json_that_becomes_valid_within_one_second(self):
        self.at.mkdir(parents=True)
        path = self.at / "restaurant.json"
        path.write_text('{"restaurant":')
        # race between two processes
        proc = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(SCRIPT), "--store", str(self.store), "open",
             "--project-root", str(self.project), "--name", "Perf"],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        try:
            time.sleep(0.2)
            self.assertIsNone(proc.poll())
            path.write_text(json.dumps({
                "restaurant": "Perf",
                "projectRoot": str(self.project.resolve()),
                "landing": "merge",
                "thread": None,
                "schedules": {},
            }))
            out, err = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            self.fail(f"timed out\nstdout:\n{out}\nstderr:\n{err}")
        self.assertEqual((proc.returncode, err.strip()), (0, ""), out)
        self.assertTrue(out.startswith(f"exists {self.at}"))

    def test_workers_one_refuses_fire_and_a_dish_that_is_not_already_counted(self):
        opened = self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                              "--workers", "1")
        self.assertEqual(opened, f"opened {self.at}")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["workers"], 1)
        for summary in ("one", "two", "three"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a"), "D1")
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b", ok=False)
        self.assertIn("nothing fired: 1 of 1 workers running", refused)
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b"), "D2")
        self.brigade("dish", "D2", "--state", "blocked")
        self.assertEqual(self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "c"), "D3")
        sent = self.brigade("dish", "D1", "--state", "in-progress", ok=False)
        review = self.brigade("dish", "D2", "--state", "in-review", ok=False)
        self.assertIn("1 of 1 workers running", sent)
        self.assertIn("1 of 1 workers running", review)
        self.assertEqual(self.brigade("dish", "D3", "--state", "in-review"), "D3 in-review")
        self.assertEqual(self.brigade("dish", "D3", "--state", "in-progress", "--thread", "worker-2"), "D3 in-progress")
        before = (self.at / "restaurant.json").read_text()
        error = self.brigade("set", "--workers", "0", ok=False)
        self.assertIn("workers must be 1 or more", error)
        self.assertEqual((self.at / "restaurant.json").read_text(), before)
        missing = self.brigade("open", "--project-root", str(self.project), "--name", "Nope",
                               "--workers", "0", ok=False)
        self.assertIn("workers must be 1 or more", missing)
        self.assertFalse((self.store / "bridge-kit" / "nope").exists())

    def test_open_warns_when_workers_meet_the_repository_cap_and_a_sibling_exists(self):
        from unittest import mock
        self.init_landing("--cap", "2")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            first = self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                                 "--workers", "4")
            self.assertNotIn("warning:", first)
            second = self.brigade("open", "--project-root", str(self.project), "--name", "Docs",
                                  "--workers", "2")
            self.assertIn("warning: workers 2 is at or above the repository cap of 2 while a sibling exists", second)
            third = self.brigade("open", "--project-root", str(self.project), "--name", "Quiet",
                                 "--workers", "1")
            self.assertNotIn("warning:", third)

    def test_open_mode_records_light_and_set_mode_clears_it(self):
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "light"),
                         f"opened {self.at}")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["mode"], "light")
        self.assertIn("## Budget\n\nAny cap on parallel workers, and whether a provider's usage limit switches this restaurant "
                      "to light mode.\n", (self.at / "menu.md").read_text())
        self.brigade("set", "--thread", "c1")
        self.assertEqual(self.brigade("status"),
                         "thread c1\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nmode light\nowner c1@1")
        self.assertIn("  Perf (reports milestones, mode light): nothing on record", self.brigade("walk"))
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "full"),
                         f"exists {self.at}\nthread c1 already recorded\nmode stays light; change it with set --mode")
        self.assertEqual(json.loads(self.brigade("set", "--mode", "full"))["mode"], "full")
        self.assertEqual(self.brigade("set", "--mode", "fast", ok=False), 'brigade: --mode takes full, light, or ""')
        self.assertNotIn("mode", json.loads(self.brigade("set", "--mode", "")))
        self.assertEqual(self.brigade("status"), "thread c1\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nowner c1@1")
        self.assertIn("  Perf (reports milestones): nothing on record", self.brigade("walk"))
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "full"),
                         f"exists {self.at}\nthread c1 already recorded\nmode stays unset; change it with set --mode")

    def test_a_mode_set_while_roles_runs_is_in_the_brief(self):
        self.coordinator()
        case = Path(self.temporary.name) / "race"
        first = self.paused_brief(case)
        _wait_for_path(case / "paused", timeout=20)
        self.brigade("set", "--mode", "full")
        (case / "proceed").touch()
        code, out, err = self._finish_race(first)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Mode: full\nMode source: restaurant.json\n", out)

    def test_open_writes_goals_tsv_with_its_header_and_no_row_into_a_new_store_and_into_the_admins_store(self):
        self.open()
        self.open_admin()
        for directory in (self.at, self.store / "bridge-kit" / ".admin"):
            self.assertEqual((directory / "goals.tsv").read_text(), "id\tat\tstate\tname\tpurpose\tdone\tshare\tclosed\n")

    def test_set_autofire_refuses_a_value_outside_off_urgent_normal_and_low(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        self.assert_refused_choice(self.brigade("set", "--autofire", "high", ok=False), "--autofire", "high",
                                   ("off", "urgent", "normal", "low"))
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_the_executive_admin_status_has_no_workers_line(self):
        self.open_admin()
        self.assertEqual(self.admin("status"), "thread not recorded\nreporting: milestones, no landing contract")

    def test_startable_is_refused_in_the_executive_admin_store(self):
        self.open_admin()
        self.assertEqual(self.admin("startable", ok=False), "brigade: the executive admin routes work and never runs it")

    def test_set_stores_the_build_and_check_commands_and_an_empty_value_removes_one(self):
        self.open()
        meta = json.loads(self.brigade("set", "--build-command", " make  gen ", "--check-command", "none"))
        self.assertEqual((meta["buildCommand"], meta["checkCommand"]), ("make  gen", "none"))
        meta = json.loads(self.brigade("set", "--build-command", ""))
        self.assertNotIn("buildCommand", meta)
        self.assertEqual(meta["checkCommand"], "none")
        refusal = self.brigade("set", "--check-command", "make 'gen", ok=False)
        self.assertTrue(refusal.startswith("brigade: \"make 'gen\" is not a command line: "), refusal)
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["checkCommand"], "none")

    def test_set_refuses_a_command_that_is_not_a_command_line_once_the_whitespace_at_its_two_ends_is_removed(self):
        self.open()
        refusal = self.brigade("set", "--build-command", "make\\ ", ok=False)
        self.assertTrue(refusal.startswith("brigade: 'make\\\\' is not a command line: "), refusal)
        self.assertNotIn("buildCommand", json.loads((self.at / "restaurant.json").read_text()))

    def test_edits_verify_refuses_a_store_with_no_build_or_check_command_and_takes_none_for_both(self):
        reviewed = self.edits_store(build=None)
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED, out=self.SOURCE.upper())
        self.assertEqual(self.verify(reviewed, head), (1, "", self.NEEDS_COMMANDS))
        self.brigade("set", "--build-command", "none")
        self.assertEqual(self.verify(reviewed, head), (1, "", self.NEEDS_COMMANDS))
        self.brigade("set", "--build-command", "", "--check-command", "none")
        self.assertEqual(self.verify(reviewed, head), (1, "", self.NEEDS_COMMANDS))
        self.brigade("set", "--build-command", "none")
        self.assertEqual(self.verify(reviewed, head), (0, f"edited src/greeting.txt\n{self.verified(reviewed, head)}", ""))
        rebuilt = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, rebuilt),
                         (1, f"FAIL out/greeting.txt: differs from {reviewed} with the edits applied\nedited src/greeting.txt",
                          self.not_verified(reviewed, rebuilt)))

    def test_next_reply(self):
        self.open()
        pause = ("if the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed: "
                 "run next --failure stuck")
        self.assertEqual(self.next_sheet("morning"), "\n".join([
            "wake: morning", "reporting: milestones",
            "see No intake source is recorded. File a user request with ticket add when the message asks for work.",
            "see Morning service is not a report.",
            "reply: no (routine: morning service)"]))
        self.assertEqual(self.next_sheet("evening"), "\n".join([
            "wake: evening", "reporting: milestones",
            "see Write the evening report for this reporting level. At digest, plain sentences and the closeout path. "
            "Otherwise at most three sentences and then the close output.",
            "run close",
            "reply: yes (evening: report)"]))
        self.assertEqual(self.next_sheet("user"), "\n".join([
            "wake: user", "reporting: milestones",
            "see Answer the user first.",
            "see File the work the message asks for with ticket add.",
            "run close",
            "reply: yes (user: message)"]))
        self.assertEqual(self.next_sheet("user", "--question").splitlines()[-1], "reply: yes (user: question)")
        self.assertEqual(self.next_sheet("liveness"), "\n".join([
            "wake: liveness", "reporting: milestones", "see watch", pause,
            "if watch printed no work in progress: run set --schedule liveness=",
            "reply: no (routine: liveness)"]))
        self.brigade("set", "--reporting", "every-turn")
        every = self.next_sheet()
        self.assertEqual(every.splitlines()[-1], "reply: yes (every-turn: wake)")
        self.assertNotIn("run close", every)
        self.assertEqual(self.next_sheet("--unsent", "misrouted").splitlines()[-1], "reply: yes (every-turn: wake)")
        self.brigade("set", "--reporting", "milestones")
        self.assertEqual(self.next_sheet("--unsent", "misrouted").splitlines()[-1], "reply: no (routine: unspecified)")
        report = self.next_sheet("--unsent", "report")
        self.assertEqual(report.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assertNotIn("run close", report)
        self.brigade("set", "--intake", "github")
        intake = self.next_sheet("intake")
        self.assertIn("see Intake sources are github. File tickets from those sources with ticket add.", intake)
        self.assertEqual(intake.splitlines()[-1], "reply: no (routine: intake)")
        self.assertNotIn("run close", intake)
        self.fresh()
        self.park_item()
        self.brigade("close")
        for level, last in (("milestones", "reply: no (routine: unspecified)"),
                            ("digest", "reply: no (routine: unspecified)"),
                            ("every-turn", "reply: yes (every-turn: wake)")):
            self.brigade("set", "--reporting", level)
            self.assertEqual(self.next_sheet().splitlines()[-1], last)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.next_sheet("review", "D1", "--verdict", "send-back").splitlines()[-1], "reply: no (sent-back: D1)")
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        crossed = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertEqual(crossed.splitlines()[-1], "reply: yes (decision: D1)")
        self.assertIn("run close --to-file", crossed)
        blocked = self.next_sheet("review", "D1", "--verdict", "blocked")
        self.assertEqual(blocked.splitlines()[-1], "reply: yes (decision: D1)")
        self.assertIn("run close --to-file", blocked)
        self.assertIn("if only the user can unblock D1: ask with 86 add --dish D1 --question --options --default", blocked)
        self.assertNotIn("if only the user can unblock D1: run 86 add --dish D1", blocked)
        fixable = self.next_sheet("review", "D1", "--verdict", "blocked", "--failure", "fixable")
        self.assertEqual(fixable.splitlines()[-1], "reply: no (failure: fixable)")
        self.assertNotIn("run close", fixable)
        self.assertNotIn("86 add --dish D1", fixable)
        self.assertEqual(self.next_sheet("checks", "D1", "--result", "fail").splitlines()[-1], "reply: no (failure: fail)")
        stuck = self.next_sheet("checks", "D1", "--result", "fail", "--failure", "stuck")
        self.assertEqual(stuck.splitlines()[-1], "reply: yes (stuck: stuck)")
        self.assertIn("run close --to-file", stuck)
        self.assertEqual(self.next_sheet("--unsent", "blocked").splitlines()[-1], "reply: no (failure: unsent blocked)")
        self.assertEqual(self.next_sheet("--unsent", "blocked", "--failure", "stuck").splitlines()[-1], "reply: yes (stuck: stuck)")
        digest_report = self.next_sheet("--unsent", "report")
        self.assertEqual(digest_report.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assertIn("run close --to-file", digest_report)
        refused = self.next_sheet("merge", "D1")
        self.assertEqual(refused.splitlines()[-1], "reply: no (routine: merge)")
        self.assertNotIn("run dish D1 --state merged", refused)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        drained = self.next_sheet("merge", "D1")
        self.assertEqual(drained.splitlines()[-1], "reply: yes (drained: batch)")
        self.assertIn("run dish D1 --state merged", drained)
        self.assertIn("run close --to-file", drained)
        for line in drained.splitlines():
            if line.startswith("run "):
                self.brigade(*shlex.split(line[4:]))
        self.assertEqual(self.next_sheet().splitlines()[-1], "reply: no (routine: unspecified)")
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        merged = self.next_sheet("merge", "D1")
        self.assertEqual(merged.splitlines()[-1], "reply: yes (merged: D1)")
        self.assertNotIn("run close", merged)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        held = self.next_sheet("merge", "D1")
        self.assertEqual(held.splitlines()[-1], "reply: no (merged: D1)")
        self.assertNotIn("run close", held)
        self.fresh()
        same = self.run_raw("next", "--failure", "stuck", "merge", "D1")
        other = self.run_raw("next", "merge", "D1", "--failure", "stuck")
        self.assertEqual(same, other)
        self.assertEqual(same[0], 0)
        self.assertEqual(same[1].splitlines()[-1], "reply: yes (stuck: stuck)")
        self.fresh()
        self.brigade("set", "--reports-to", "admin-thread")
        evening = self.next_sheet("evening")
        self.assertEqual(evening.splitlines()[-1], "reply: no (reports to the executive admin: evening report)")
        self.assertIn("run close --to-file", evening)
        lines = evening.splitlines()
        report_line = "see t3_thread_send admin-thread mode queue: report perf: the path close --to-file prints"
        self.assertLess(lines.index("run close --to-file"), lines.index(report_line))
        user = self.next_sheet("user")
        self.assertEqual(user.splitlines()[-1], "reply: yes (user: message)")
        self.assertNotIn("run close", user)
        self.assertEqual(self.next_sheet("user", "--question").splitlines()[-1], "reply: yes (user: question)")
        failed = self.next_sheet("--unsent", "failed")
        self.assertEqual(failed.splitlines()[-1], "reply: yes (failed: unsent failed)")
        self.open_admin()
        published = self.admin("request", "--to", "perf", "from-user perf: please fix the gate")
        ident = published.split()[0]
        routed = self.next_sheet("admin", ident)
        self.assertEqual(routed.splitlines()[-1], "reply: no (reports to the executive admin: admin request)")
        self.assertIn(f"see t3_thread_send admin-thread mode queue: reply perf: sent after the answer", routed)
        self.brigade("set", "--reports-to", "")
        direct = self.next_sheet("admin", ident)
        self.assertEqual(direct.splitlines()[-1], "reply: yes (user: message)")
        self.assertIn("run close\n", direct + "\n")
        self.assertNotIn("run close --to-file", direct)

    def test_next_admin_lines_match_the_events_table(self):
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        rows = []
        started = False
        for line in text.splitlines():
            if line.startswith("| Line |"):
                started = True
                continue
            if not started:
                continue
            if not line.startswith("|"):
                break
            if set(line.replace("|", "").replace("-", "").strip()) == set():
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            kind = cells[0].replace("`", "").split()[0]
            store = " ".join(cells[2].replace("`", "").split())
            rows.append((kind, store))
        recorded = [(kind, store) for kind, store, _only in runpy.run_path(str(SCRIPT))["ADMIN_LINES"]]
        self.assertEqual(recorded, rows)
