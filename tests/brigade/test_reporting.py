import sqlite3

from .support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    Path,
    ProcessReason,
    SCRIPT,
    call,
    call_in_process,
    real_process,
    run_globals,
    _ReportingRace,
    _owner_words,
    _shown_root,
    datetime,
    io,
    json,
    mock,
    os,
    runpy,
    subprocess,
    sys,
    timezone,
)


class ReportingTest(BrigadeTest, _ReportingRace):
    def test_close_warns_about_a_review_report_with_no_row_and_still_exits_0(self):
        self.fired_bug_fix()
        code, before, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.review_file("D1-review-2.md")
        self.assertEqual(self.close_run("--dry-run"), (0, before, self.no_row("D1-review-2.md")))
        code, out, err = self.close_run("--to-file")
        self.assertEqual((code, err), (0, self.no_row("D1-review-2.md")))
        self.assertEqual(Path(out).read_text().strip(), before)
        code, out, err = self.close_run()
        self.assertEqual((code, err), (0, self.no_row("D1-review-2.md")))
        self.assertEqual(out.splitlines()[0], "# Perf report")
        self.assertNotIn("warning", out)

    def test_close_warns_about_the_report_of_a_passed_item_that_has_no_claims_check_line(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n\nStatus: done\n")
        self.record("abc", "pass")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("passed")))

    def test_close_does_not_warn_about_a_report_a_row_names(self):
        self.fired_bug_fix()
        self.aged("D1-review-1.md", 2099)
        self.aged("D1-review-1-panel-2.md", 2099)
        self.record("abc", "send-back", "--report", "D1-review-1.md")
        self.record("abc", "pass", "--member", "--report", "D1-review-1-panel-2.md", verifier="grok/grok-4.7")
        self.assertEqual(self.close_run("--dry-run")[2], "")

    def test_close_matches_a_row_that_names_no_report_to_the_reports_written_before_it(self):
        self.fired_bug_fix()
        self.aged("D1-review.md", 2020)
        self.aged("D1-review-bunny.md", 2020)
        self.assertEqual(self.close_run("--dry-run")[2],
                         "\n".join([self.no_row("D1-review-bunny.md"), self.no_row("D1-review.md")]))
        self.record("abc", "send-back")
        self.assertEqual(self.close_run("--dry-run")[2], "")
        self.aged("D1-review-2.md", 2099)
        self.assertEqual(self.close_run("--dry-run")[2], self.no_row("D1-review-2.md"))

    def test_a_row_that_names_a_report_matches_no_other_report(self):
        self.fired_bug_fix()
        self.aged("D1-review-1.md", 2020)
        self.aged("D1-review-2.md", 2020)
        self.aged("D7-review.md", 2020)
        self.record("abc", "send-back", "--report", "D1-review-2.md")
        self.assertEqual(self.close_run("--dry-run")[2],
                         "\n".join([self.no_row("D1-review-1.md"), self.no_row("D7-review.md", "D7")]))

    def test_close_warns_about_a_dangling_link_named_like_a_review_report_and_still_exits_0(self):
        self.fired_bug_fix()
        code, before, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.linked("D1-review-7.md", "gone.md")
        warning = ("brigade: warning: reports/D1-review-7.md is a symbolic link and no review row of D1 names it; "
                   "remove the link and leave its target as it is")
        self.assertEqual(self.close_run("--dry-run"), (0, before, warning))
        self.assertEqual(self.close_run(), (0, before, warning))

    def test_close_warns_about_a_review_report_replaced_by_a_dangling_link_while_close_classifies_it(self):
        self.fired_bug_fix()
        entry = self.review_file("D1-review-7.md")
        pending = self.linked("pending.md", "gone.md")
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        replaced = []

        def replacing(real):
            def hooked(path):
                result = real(path)
                if not replaced and path.name == entry.name and path.parent.name == "reports":
                    replaced.append(path)
                    os.replace(pending, entry)
                return result
            return hooked

        argv = ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), "close", "--dry-run"]
        with mock.patch.object(Path, "lstat", replacing(Path.lstat)), \
                mock.patch.object(Path, "is_symlink", replacing(Path.is_symlink)), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            glob["run"](argv)
        self.assertEqual(len(replaced), 1)
        self.assertTrue(entry.is_symlink())
        self.assertFalse(entry.exists())
        self.assertEqual(stderr.getvalue().strip(), self.no_row("D1-review-7.md"))
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.a_link("D1-review-7.md")))

    def test_close_warns_about_a_live_link_and_a_directory_named_like_review_reports_and_following_its_advice_ends_each_warning(self):
        self.fired_bug_fix()
        target = Path(self.temporary.name) / "findings.md"
        target.write_text("findings\n")
        link = self.linked("D1-review-7.md", "findings.md")
        directory = self.at / "reports" / "D1-review-8.md"
        directory.mkdir()
        (directory / "notes.md").write_text("notes\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, "\n".join([
            self.a_link("D1-review-7.md"),
            self.not_a_file("D1-review-8.md", "is not a regular file", "move it out of reports/"),
        ])))
        link.unlink()
        directory.rename(self.at / "D1-review-8.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.assertEqual(target.read_text(), "findings\n")
        self.assertEqual((self.at / "D1-review-8.md" / "notes.md").read_text(), "notes\n")

    def test_close_does_not_warn_about_a_dangling_link_a_row_names(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md")
        self.record("abc", "send-back", "--report", "D1-review-1.md")
        (self.at / "reports" / "D1-review-1.md").unlink()
        self.linked("D1-review-1.md", "gone.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

    def test_a_row_that_names_no_report_accounts_for_no_link_to_a_file_written_before_it(self):
        self.fired_bug_fix()
        target = Path(self.temporary.name) / "findings.md"
        target.write_text("findings\n")
        stamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(target, (stamp, stamp))
        self.linked("D1-review-1.md", "findings.md")
        self.record("abc", "send-back")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.a_link("D1-review-1.md")))

    def test_close_advice_for_every_entry_under_a_linked_reports_leaves_the_files_outside_the_store_in_place(self):
        self.fired_bug_fix()
        outside = Path(self.temporary.name) / "outside" / "reports"
        outside.mkdir(parents=True)
        (outside / "D1-review-5.md").write_text("findings\n")
        (outside / "D1-review-6.md").symlink_to(outside / "D1-review-5.md")
        (outside / "D1-review-7.md").mkdir()
        before = sorted(path.name for path in outside.iterdir())
        reports = self.at / "reports"
        reports.symlink_to(outside)
        self.assertEqual(self.close_run("--dry-run")[::2], (0, "\n".join(
            self.under_a_linked_reports(f"D1-review-{number}.md") for number in (5, 6, 7))))
        reports.unlink()
        reports.mkdir()
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.assertEqual(sorted(path.name for path in outside.iterdir()), before)
        self.assertEqual((outside / "D1-review-5.md").read_text(), "findings\n")

    def test_close_ignores_a_file_that_is_not_a_review_report(self):
        self.fired_bug_fix()
        for name in ("D1.md", "notes.md", "D1-review.txt", "D1-reviewed.md", "xD1-review.md"):
            self.aged(name, 2099)
        self.assertEqual(self.close_run("--dry-run")[2], "")

    def test_report_lists_only_what_changed_since_the_last_report(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Startup is slow")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Splash hangs")
        self.brigade("fire", "--tickets", "T1", "--station", "perf-issue", "--summary", "Cut cold start time")
        self.brigade("86", "add", "--question", "Drop Windows 7 support?", "--options", "yes, no", "--default", "no")
        first = self.brigade("close")
        self.assertIn("## In progress\n\n- fix D1 (T1): Cut cold start time\n", first)
        self.assertIn("## New tickets, not started\n\n- fix T2: Splash hangs\n", first)
        self.assertIn("- Q1: Drop Windows 7 support? Options: yes, no. Default if no answer: no.", first)
        self.brigade("pass", "record", "D1", "--pr", "https://github.com/o/r/pull/7", "--sha", "abc", "--verdict", "pass",
                     "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("86", "answer", "Q1", "--answer", "no")
        second = self.brigade("close")
        self.assertIn("## Merged\n\n- fix D1 (T1): Cut cold start time https://github.com/o/r/pull/7", second)
        self.assertNotIn("In progress", second)
        self.assertNotIn("Passed review", second)
        self.assertNotIn("Decisions for you", second)
        self.assertIn("Nothing new.", self.brigade("close"))
        self.assertEqual(len(list((self.at / "closeouts").glob("*.md"))), 3)

    def test_a_dish_that_moves_through_every_state_in_one_report_appears_once(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "UI", "--source", "user")
        self.brigade("ticket", "set", "T2", "--state", "dropped")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", "grok/grok-4.7", "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("close"), "\n".join([
            "# Perf report", "",
            f"Since opening. merged: 1.", "",
            "## Merged", "", "- fix D1 (T1): Fix s", "",
            "## Dropped", "", "- T2: UI", "",
            "## Spend", "", "goal not recorded  wall 0m  rounds 1  send-backs 0  threads not recorded",
        ]))

    def test_close_to_file_prints_the_written_path_and_advances_the_report(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "UI", "--source", "user")
        self.brigade("ticket", "set", "T2", "--state", "dropped")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", "grok/grok-4.7", "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        written = self.brigade("close", "--to-file")
        files = list((self.at / "closeouts").glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertEqual(written, str(files[0].resolve()))
        self.assertIn("## Merged\n\n- fix D1 (T1): Fix s", files[0].read_text())
        self.assertIn("Nothing new.", self.brigade("close"))

    def test_close_to_file_with_dry_run_writes_no_closeout(self):
        self.open()
        self.brigade("close", "--to-file", "--dry-run", ok=False)
        self.assertEqual(list((self.at / "closeouts").glob("*.md")), [])

    def test_status_help_names_the_thread_line_then_counts(self):
        env = os.environ | {"COLUMNS": "300"}
        # -h output
        result = real_process(ProcessReason.HELP, SCRIPT, ["--help"], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status              the thread line first, then counts, then workers outside the executive admin's store, then a kinds line, a goals line, and an untagged line when each has something to count, then reports to, mode, and owner when present", result.stdout)
        self.assertNotIn("one line of counts", result.stdout)

    def test_status_and_walk_speak_plain_engineering_prose(self):
        self.open()
        self.brigade("set", "--thread", "thread-1", "--schedule", "report=s-1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no")
        self.assertEqual(self.brigade("status"),
                         "thread thread-1\nreporting: milestones, no landing contract, waiting tickets: 1, decisions for you: 1\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)\nkinds: fix 1 waiting\nowner thread-1@1")
        self.assertEqual(self.brigade("walk"), "\n".join([
            f"{_shown_root(self.project)}: no landing contract",
            "  Perf (reports milestones): waiting tickets: 1, decisions for you: 1",
            "    thread thread-1",
            "    Q1: Ship it?",
        ]))
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"], {"report": "s-1"})
        for word in ("86", "plate", "heard", "chef", "fire", "pass "):
            self.assertNotIn(word, self.brigade("status").lower())

    def test_status_without_a_contract_ignores_a_stored_landing_field(self):
        self.open()
        self.assertNotIn("landing", json.loads((self.at / "restaurant.json").read_text()))
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)")
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["landing"] = "merge"
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)")
        walked = self.brigade("walk")
        self.assertEqual(walked.splitlines()[0], f"{_shown_root(self.project)}: no landing contract")
        self.assertNotIn("lands by", walked)

    def test_walk_prints_a_home_directory_as_a_tilde(self):
        from unittest import mock
        project = Path.home() / "brigade-no-such-contract-d57"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            directory = self.store / "brigade-no-such-contract-d57" / "docs"
            self.assertEqual(self.brigade("open", "--project-root", str(project), "--name", "Docs"), f"opened {directory}")
            self.assertEqual(self.brigade("walk"), "\n".join([
                "~/brigade-no-such-contract-d57: no landing contract",
                "  Docs (reports milestones): nothing on record",
                "    thread not recorded",
            ]))

    def test_status_and_walk_follow_a_landing_mode_change(self):
        from unittest import mock
        self.git_project()
        remote = Path(self.temporary.name) / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=self.project, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=self.project, check=True)
        self.land("init", "--trunk", "main", "--mode", "human")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.land("mode", "merge")
            self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, lands by merge\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)")
            header = self.brigade("walk").splitlines()[0]
            self.assertEqual(header, f"{_shown_root(self.project)}: {self.land('status')}")
            self.assertTrue(header.startswith(f"{_shown_root(self.project)}: merge mode onto "))

    def test_walk_groups_repositories_and_repo_selects_one(self):
        from unittest import mock
        other = Path(self.temporary.name) / "other-app"
        other.mkdir()
        self.init_landing("--cap", "4")
        run = lambda *command: subprocess.run(command, cwd=other, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        (other / "a.txt").write_text("a\n")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.land_repo(other, "init", "--trunk", "lane", "--mode", "local", "--base", "main", "--cap", "4")
        docs = self.store / "bridge-kit" / "docs"
        engine = self.store / "bridge-kit" / "engine"
        core = self.store / "other-app" / "core"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.assertEqual(self.brigade("walk"), f"no restaurants under {self.store}")
            self.run_at(docs, "open", "--project-root", str(self.project), "--name", "Docs")
            self.run_at(engine, "open", "--project-root", str(self.project), "--name", "Engine")
            self.run_at(core, "open", "--project-root", str(other), "--name", "Core")
            self.run_at(docs, "set", "--thread", "thread-docs")
            self.run_at(engine, "set", "--thread", "thread-engine")
            self.run_at(core, "set", "--thread", "thread-core")
            self.run_at(docs, "ticket", "add", "--kind", "fix", "--summary", "Write the guide")
            self.assertEqual(self.run_at(docs, "fire", "--tickets", "T1", "--station", "bug-fix",
                                         "--summary", "Write the guide", "--paths", "a.txt"),
                             "D1 (lease L1 held by docs/D1)")
            self.run_at(engine, "ticket", "add", "--kind", "fix", "--summary", "Blocked edit")
            refused = self.run_at(engine, "fire", "--tickets", "T1", "--station", "bug-fix",
                                  "--summary", "Blocked edit", "--paths", "a.txt", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by docs/D1", refused)
            self.assertEqual(self.land("lease", "claim", "--holder", "engine/D12", "--paths", "src"), "L2")
            self.assertEqual(self.land("lease", "claim", "--holder", "engine/D13", "--paths", "lib"), "L3")
            self.assertEqual(self.run_at(docs, "86", "add", "--question", "Ship the guide?",
                                         "--options", "yes, no", "--default", "no"), "Q1")
            app = _shown_root(self.project)
            other_root = _shown_root(other)
            expected = "\n".join([
                f"{app}: local mode onto refs/landing/lane. leases held: 3, changes in flight: 3 of 4.",
                "  Docs (reports milestones): in progress: 1, decisions for you: 1",
                "    thread thread-docs, leases L1 (D1)",
                "    Q1: Ship the guide?",
                "  Engine (reports milestones): waiting tickets: 1 (1 blocked)",
                "    thread thread-engine, leases L2 (D12), L3 (D13)",
                f"{other_root}: local mode onto refs/landing/lane. changes in flight: 0 of 4.",
                "  Core (reports milestones): nothing on record",
                "    thread thread-core",
            ])
            self.assertEqual(self.brigade("walk"), expected)
            self.assertEqual(self.brigade("walk", "--repo", str(self.project)), "\n".join(expected.splitlines()[:6]))
            self.assertEqual(self.brigade("walk", "--repo", str(other)), "\n".join(expected.splitlines()[6:]))
            missing = Path(self.temporary.name) / "empty-repo"
            missing.mkdir()
            self.assertEqual(self.brigade("walk", "--repo", str(missing)), f"no restaurants for {missing.resolve()}")

    def test_commands_outside_a_restaurant_fail_with_the_fix(self):
        self.assertIn("run brigade.py open", self.brigade("status", ok=False))

    def test_open_records_reporting_and_defaults_to_milestones(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        self.assertEqual(meta["reporting"], "milestones")
        self.assertNotIn("landing", meta)
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)")
        self.assertIn("reports milestones", self.brigade("walk"))
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                                      "--reporting", "every-turn"), f"exists {self.at}")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["reporting"], "milestones")
        other = self.brigade("open", "--project-root", str(self.project), "--name", "Quiet",
                             "--reporting", "digest")
        quiet = self.store / "bridge-kit" / "quiet"
        self.assertEqual(other, "\n".join([
            f"opened {quiet}",
            f"sibling Perf ({self.at}), thread not recorded",
            "  purpose: not written yet",
            "  off the menu: not written yet",
        ]))
        self.assertEqual(json.loads((quiet / "restaurant.json").read_text())["reporting"], "digest")
        self.assertIn("reports digest", self.brigade("walk"))

    def test_a_restaurant_file_without_reporting_reads_as_milestones(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        del meta["reporting"]
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.assertNotIn("reporting", json.loads((self.at / "restaurant.json").read_text()))
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)")
        self.assertIn("reports milestones", self.brigade("walk"))
        self.assertNotIn("reporting", json.loads((self.at / "restaurant.json").read_text()))

    def test_close_lists_an_escalation_with_its_tickets(self):
        self.fired_bug_fix()
        self.brigade("close")
        self.brigade("dish", "D1", "--mode", "full", "--reason", "contested: two owners")
        text = self.brigade("close", "--dry-run")
        self.assertEqual(text.split("\n\n", 2)[2], "\n\n".join([
            "## Moved to full mode", "- D1 (T1): contested: two owners",
            "## Spend", "goal not recorded  wall open  rounds 0  send-backs 0  threads not recorded"]))

    def test_the_second_send_back_moves_the_item_to_full_once(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back")
        escalated = self.head("Playbook: playbooks/feature.md", "Mode: full", "Mode source: escalated: second send-back",
                              "Attempt: fix")
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], escalated)
        self.assertNotIn("Contested:", text)
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D1", "full", "second send-back"]])
        self.brigade(*self.BRIEF)
        self.assertEqual(len(self.mode_rows()), 1)
        self.brigade("set", "--mode", "light")
        self.assertEqual(self.brigade(*self.BRIEF).split("\n\n", 1)[0], escalated)
        self.assertEqual(self.brigade("close", "--dry-run").split("## Moved to full mode\n\n", 1)[1].split("\n")[0],
                         "- D1 (T1): second send-back")

    def test_status_prints_the_workers_line_third_before_reports_to_mode_and_owner(self):
        self.open()
        self.assertEqual(self.brigade("status"),
                         "thread not recorded\nreporting: milestones, no landing contract\n"
                         "workers: 0 of 2 running, 2 idle, auto-start: normal\n"
                         "liveness: every 600000 ms (default for milestones)")
        self.brigade("set", "--thread", "t1", "--reports-to", "admin-thread", "--mode", "light")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a")
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread t1",
            "reporting: milestones, no landing contract, waiting tickets: 1",
            "workers: 0 of 2 running, 2 idle, auto-start: normal",
            "liveness: every 600000 ms (default for milestones)",
            "kinds: fix 1 waiting",
            "reports to admin-thread",
            "mode light",
            "owner t1@1",
        ])

    def test_status_prints_the_stored_liveness_interval(self):
        self.open()
        for level in ("every-turn", "milestones", "digest"):
            with self.subTest(level=level):
                self.brigade("set", "--reporting", level)
                self.brigade("set", "--liveness-ms", "3600000")
                self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["livenessMs"], 3600000)
                self.assertIn("liveness: every 3600000 ms", self.brigade("status").splitlines())

    def test_status_marks_the_liveness_interval_as_the_default_for_the_reporting_level(self):
        self.open()
        defaults = (("every-turn", "liveness: every 600000 ms (default for every-turn)"),
                    ("milestones", "liveness: every 600000 ms (default for milestones)"),
                    ("digest", "liveness: every 1800000 ms (default for digest)"))
        for level, line in defaults:
            with self.subTest(level=level):
                self.brigade("set", "--reporting", level)
                self.assertNotIn("livenessMs", json.loads((self.at / "restaurant.json").read_text()))
                self.assertIn(line, self.brigade("status").splitlines())

    def test_status_names_the_milestones_liveness_default_when_the_reporting_level_is_not_one_it_knows(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["reporting"] = "weekly"
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.assertIn("liveness: every 600000 ms (default for milestones)", self.brigade("status").splitlines())

    def test_clearing_liveness_ms_removes_the_key_and_restores_the_level_default(self):
        self.open()
        self.brigade("set", "--liveness-ms", "1200000")
        self.assertIn("liveness: every 1200000 ms", self.brigade("status").splitlines())
        self.brigade("set", "--liveness-ms", "")
        self.assertNotIn("livenessMs", json.loads((self.at / "restaurant.json").read_text()))
        self.assertIn("liveness: every 600000 ms (default for milestones)", self.brigade("status").splitlines())

    def test_set_refuses_a_liveness_ms_that_is_not_a_whole_number_of_at_least_60000(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        for value in ("10m", "1.5", "59999", "-1"):
            with self.subTest(value=value):
                error = self.brigade("set", "--liveness-ms", value, code=1)
                self.assertIn("--liveness-ms", error)
                self.assertIn(value, error)
                self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_set_refuses_a_hand_edited_liveness_ms_before_writing_restaurant_json(self):
        self.open()
        for value in ("soon", 1000):
            with self.subTest(value=value):
                meta = json.loads((self.at / "restaurant.json").read_text())
                meta["livenessMs"] = value
                (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
                before = (self.at / "restaurant.json").read_bytes()
                error = self.brigade("set", "--workers", "3", code=1)
                self.assertIn("livenessMs", error)
                self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_status_prints_the_liveness_line_immediately_after_the_workers_line(self):
        self.open()
        self.brigade("set", "--thread", "thread-1")
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread thread-1",
            "reporting: milestones, no landing contract",
            "workers: 0 of 2 running, 2 idle, auto-start: normal",
            "liveness: every 600000 ms (default for milestones)",
            "owner thread-1@1",
        ])

    def test_set_autofire_records_each_level_and_status_prints_it_as_auto_start(self):
        self.open()
        for level in ("off", "urgent", "normal", "low"):
            self.assertEqual(json.loads(self.brigade("set", "--autofire", level))["autofire"], level)
            status = self.brigade("status")
            self.assertEqual(status.splitlines()[2], f"workers: 0 of 2 running, 2 idle, auto-start: {level}")
            self.assertNotIn("autofire", status)

    def test_a_missing_or_empty_autofire_key_reads_as_normal(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        self.assertNotIn("autofire", meta)
        line = "workers: 0 of 2 running, 2 idle, auto-start: normal"
        self.assertEqual(self.brigade("status").splitlines()[2], line)
        (self.at / "restaurant.json").write_text(json.dumps(meta | {"autofire": ""}, indent=2) + "\n")
        self.assertEqual(self.brigade("status").splitlines()[2], line)

    def test_close_prints_no_unrecorded_review_warning_for_the_report_a_pass_with_edits_row_names(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_row("D1-review-1.md")))
        self.record_edits("abc", "--edits", "D1-review-1.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

    def test_from_report_files_a_ticket_under_the_goal_of_its_item_when_that_goal_is_done_and_goal_list_prints_that_goal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "merged")
        self.follow_ups_in("## Follow-ups\n\n- Tidy the guide.\n")
        self.assertEqual(self.label("rail.tsv", "T2"), ("polish", "G1"))
        self.assertEqual(self.brigade("goal", "list").splitlines()[0],
                         "G1 done activity view: 1 waiting, 0 in flight, 1 merged, 0 send-backs")

    def test_goal_list_in_a_store_with_no_goal_prints_that_and_the_unassigned_line(self):
        self.open()
        before = self.store_files()
        self.assertEqual(self.run_raw("goal", "list"), (0, f"no open or paused goals\n{self.NO_UNASSIGNED_WORK}\n", ""))
        self.assertEqual(self.run_raw("goal", "list", "--all"), (0, f"no goals\n{self.NO_UNASSIGNED_WORK}\n", ""))
        self.assertEqual(self.store_files(), before)

    def test_goal_list_prints_each_open_and_paused_goal_in_file_order_with_its_share_and_all_adds_each_done_and_dropped_goal_with_its_closed_cell(self):
        self.open()
        self.goal("activity view", "--share", "2")
        self.goal("old lint")
        self.goal("current with upstream")
        self.goal("windows port")
        self.brigade("goal", "set", "G2", "--state", "done", "--evidence", "shipped in #12")
        self.brigade("goal", "set", "G3", "--state", "paused")
        self.brigade("goal", "set", "G4", "--state", "dropped", "--evidence", "no users")
        closed = {ident: self.table_row(self.at, "goals.tsv", ident)["closed"] for ident in ("G2", "G4")}
        self.assertEqual(closed["G2"].split(" ", 1)[1], "shipped in #12")
        counts = "0 waiting, 0 in flight, 0 merged, 0 send-backs"
        detail = ["  purpose: One page of activity", "  done when: The page ships"]
        self.assertEqual(self.brigade("goal", "list").splitlines(),
                         [f"G1 open activity view: {counts}, share 2", *detail,
                          f"G3 paused current with upstream: {counts}", *detail,
                          self.NO_UNASSIGNED_WORK])
        self.assertEqual(self.brigade("goal", "list", "--all").splitlines(),
                         [f"G1 open activity view: {counts}, share 2", *detail,
                          f"G2 done old lint: {counts}", *detail, f"  closed: {closed['G2']}",
                          f"G3 paused current with upstream: {counts}", *detail,
                          f"G4 dropped windows port: {counts}", *detail, f"  closed: {closed['G4']}",
                          self.NO_UNASSIGNED_WORK])

    def test_goal_list_with_only_done_and_dropped_goals_prints_no_open_or_paused_goals(self):
        self.open()
        self.goal()
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.assertEqual(self.brigade("goal", "list"), f"no open or paused goals\n{self.NO_UNASSIGNED_WORK}")

    def test_a_goal_share_that_is_not_a_whole_number_of_1_or_more_reads_as_no_limit_with_a_warning_and_goal_set_share_rewrites_the_cell(self):
        self.open()
        self.goal()
        for share in ("lots", "0", "-3"):
            with self.subTest(share=share):
                self.hand_edit("goals.tsv", "G1", share=share)
                code, out, err = self.run_raw("goal", "list")
                self.assertEqual((code, err), (0, f"brigade: warning: G1 has share {share!r}, which is not a whole number of 1 or more; "
                                                  "it reads as no limit; run goal set G1 --share <n>\n"))
                self.assertEqual(out.splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")
        self.assertEqual(self.brigade("goal", "set", "G1", "--share", "2"), "G1 share 2")
        code, out, err = self.run_raw("goal", "list")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs, share 2")

    def test_a_goal_share_of_five_thousand_digits_is_read_and_printed(self):
        self.open()
        self.goal()
        self.hand_edit("goals.tsv", "G1", share="7" * 5000)
        code, out, err = self.run_raw("goal", "list")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.splitlines()[0].endswith(", share " + "7" * 5000))

    def test_goal_add_and_goal_set_in_a_store_with_a_generation_need_the_owner_and_goal_list_does_not(self):
        self.open()
        self.brigade("set", "--thread", "t1")
        refusal = "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status"
        self.assertEqual(self.goal(owner=False, ok=False), refusal)
        self.assertEqual(self.goal(), "G1")
        before = self.store_files()
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "paused", ok=False, owner=False), refusal)
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("goal", "list", owner=False).splitlines()[0],
                         "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")

    def test_goal_list_prints_for_each_goal_the_counts_a_hand_join_of_the_three_tables_gives(self):
        self.worked_store()
        self.assertEqual(self.brigade("goal", "list").splitlines(), [
            # 2 waiting. T4 and T9 wait under G1. 1 in flight. D2 is in review under G1. 1 merged. D1.
            # 3 send-backs. Two on D1 and one on D2. The D2 row with member yes does not count.
            "G1 open activity view: 2 waiting, 1 in flight, 1 merged, 3 send-backs, share 2",
            "  purpose: One page of activity",
            "  done when: The page ships",
            # 0 waiting. T3 and T6 are under G2 and assigned. 1 in flight. D3 is sent back under G2. 0 merged.
            # 1 send-back. One on D3. Its pass-with-edits row does not count. 1 riding along. T3, assigned to D2, a G1 item.
            "G2 paused current with upstream: 0 waiting, 1 in flight, 0 merged, 1 send-back, 1 riding along",
            "  purpose: One page of activity",
            "  done when: The page ships",
            # 2 waiting. T5 and T8. 1 in flight. D4. 1 merged. D5. 1 send-back. One on D5.
            "unassigned: 2 waiting, 1 in flight, 1 merged, 1 send-back",
        ])

    def test_status_prints_the_waiting_and_in_flight_counts_by_kind_and_by_goal_a_hand_join_gives_after_its_workers_line(self):
        self.worked_store()
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread not recorded",
            "reporting: milestones, no landing contract, waiting tickets: 4, in progress: 1, in review: 1, sent back: 1, merged: 2",
            "workers: 2 of 9 running, 7 idle, auto-start: normal",
            "liveness: every 600000 ms (default for milestones)",
            # feature. T9 waits. fix. T4 waits and D2 is in flight. polish. T5 waits, and T3 is assigned so it is in no entry.
            # upkeep. D3 is in flight. study. D4 is in flight.
            "kinds: feature 1 waiting; fix 1 waiting, 1 in flight; polish 1 waiting; upkeep 1 in flight; study 1 in flight",
            # G1. T4 and T9 wait and D2 is in flight. G2. D3 is in flight. unassigned. T5 and T8 wait and D4 is in flight.
            "goals: G1 2 waiting, 1 in flight; G2 paused, 1 in flight; unassigned 2 waiting, 1 in flight",
            # T8 waits with no kind. The waiting counts by kind and this one sum to the 4 waiting tickets.
            "untagged: 1",
        ])

    def test_status_prints_no_kinds_line_no_goals_line_and_no_untagged_line_when_no_ticket_waits_and_no_item_is_in_flight(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "set", "T1", "--state", "dropped")
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread not recorded", "reporting: milestones, no landing contract",
            "workers: 0 of 9 running, 9 idle, auto-start: normal",
            "liveness: every 600000 ms (default for milestones)"])

    def test_status_prints_the_state_of_a_done_goal_that_has_a_waiting_ticket(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.hand_edit("dishes.tsv", "D1", state="merged")
        self.hand_edit("rail.tsv", "T1", state="done")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "merged")
        self.follow_ups_in("## Follow-ups\n\n- Tidy the guide.\n")
        self.assertEqual(self.brigade("status").splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: polish 1 waiting", "goals: G1 done, 1 waiting"])

    def test_status_prints_the_unassigned_entry_alone_when_the_store_has_a_goal_and_no_work_under_it(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.assertEqual(self.brigade("status").splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: fix 1 waiting", "goals: unassigned 1 waiting"])

    def test_fire_of_a_ticket_whose_goal_cell_is_a_padded_id_warns_starts_an_unassigned_item_and_leaves_the_cell_as_it_was(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("rail.tsv", "T1", goal=" G1 ")
        names = f"brigade: warning: T1 names goal ' G1 ', {self.NO_GOAL_ROW}; "
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"),
                         (0, "D1\n", names + "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.raw_cells("dishes.tsv", "D1", "kind", "goal"), (b"fix", b""))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "state", "kind", "goal"), (b"assigned", b"fix", b" G1 "))
        code, out, err = self.run_raw("status")
        self.assertEqual((code, err), (0, names + f"{self.NOT_WHILE} T1 is assigned; correct it in rail.tsv by hand\n"))
        self.assertEqual(out.splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: fix 1 in flight", "goals: unassigned 1 in flight"])

    def test_a_rewrite_of_its_table_leaves_each_kept_cell_byte_for_byte_and_the_warning_for_it_still_prints(self):
        self.two_goals()
        for summary in ("a", "b", "c", "d"):
            self.brigade("ticket", "add", "--summary", summary, "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "Do c")
        self.brigade("fire", "--tickets", "T4", "--station", "bug-fix", "--summary", "Do d")
        # Per table, the row whose cell is padded, and a command that rewrites the table to change another row.
        rewrites = {"rail.tsv": ("T1", lambda cell: ("ticket", "set", "T2", "--paths", f"docs/{cell}")),
                    "dishes.tsv": ("D1", lambda cell: ("dish", "D2", "--thread", f"thread-{cell}")),
                    "goals.tsv": ("G1", lambda cell: ("goal", "set", "G2", "--name", f"renamed {cell}"))}
        kept = runpy.run_path(str(SCRIPT))["KEPT_CELLS"]
        self.assertEqual(sorted(kept), sorted(rewrites))
        for table, cells in kept.items():
            ident, rewrite = rewrites[table]
            for cell in cells:
                with self.subTest(table=table, cell=cell):
                    legal, = self.raw_cells(table, ident, cell)
                    self.assertTrue(legal)
                    padded = b" " + legal + b" "
                    self.hand_edit(table, ident, **{cell: padded.decode()})
                    held = padded.decode() if cell == "id" else ident
                    warned = self.run_raw("status")
                    self.assertEqual(warned[0], 0)
                    self.assertIn(repr(padded.decode()), warned[2])
                    before = (self.at / table).read_bytes()
                    self.assertEqual(self.run_raw(*rewrite(cell))[0], 0)
                    self.assertNotEqual((self.at / table).read_bytes(), before)
                    self.assertEqual(self.raw_cells(table, held, cell), (padded,))
                    self.assertEqual(self.run_raw("status"), warned)
                    self.hand_edit(table, held, **{cell: legal.decode()})
                    self.assertEqual(self.run_raw("status")[2], "")

    def test_a_goal_cell_that_ends_in_a_carriage_return_still_reads_as_unassigned_after_another_ticket_is_rewritten(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix")
        rail = self.at / "rail.tsv"
        self.assertEqual(rail.read_bytes().count(b"\tG1\n"), 1)
        rail.write_bytes(rail.read_bytes().replace(b"\tG1\n", b"\tG1\r\n"))
        warning = (f"brigade: warning: T1 names goal 'G1\\r', {self.NO_GOAL_ROW}; "
                   "run ticket set T1 --goal G<n> or --no-goal\n")
        unassigned = ["liveness: every 600000 ms (default for milestones)", "kinds: fix 2 waiting", "goals: unassigned 2 waiting"]
        code, out, err = self.run_raw("status")
        self.assertEqual((code, err, out.splitlines()[3:]), (0, warning, unassigned))
        self.assertEqual(self.run_raw("ticket", "set", "T2", "--priority", "low"), (0, "T2 priority low\n", ""))
        self.assertEqual(self.raw_cells("rail.tsv", "T2", "priority"), (b"low",))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "kind", "goal"), (b"fix", b"G1\r"))
        code, out, err = self.run_raw("status")
        self.assertEqual((code, err, out.splitlines()[3:]), (0, warning, unassigned))

    def test_status_on_a_store_from_before_kinds_prints_its_three_lines_and_then_the_count_of_waiting_tickets_as_untagged(self):
        self.store_from_before_kinds()
        before = self.store_files()
        self.assertEqual(self.run_raw("status"), (0, "\n".join([
            "thread not recorded", f"reporting: milestones, lands by local, {self.OLD_COUNTS}", self.OLD_WORKERS,
            "liveness: every 600000 ms (default for milestones)", "untagged: 5"]) + "\n", ""))
        self.assertEqual(self.store_files(), before)

    def test_goal_list_on_a_store_from_before_kinds_counts_each_ticket_and_item_as_unassigned_and_writes_nothing(self):
        self.store_from_before_kinds()
        before = self.store_files()
        # Waiting. T8 to T12. In flight. D1 to D6. Merged. D7. Send-backs. The one row on D4.
        self.assertEqual(self.brigade("goal", "list"),
                         "no open or paused goals\nunassigned: 5 waiting, 6 in flight, 1 merged, 1 send-back")
        self.assertEqual(self.store_files(), before)

    def test_status_ticket_list_startable_watch_86_list_close_and_walk_print_for_an_empty_store_from_before_kinds_what_the_tool_printed_before_kinds_and_change_no_table(self):
        self.store_from_before_kinds(rows=False)
        before = self.tables()
        idle = "workers: 0 of 2 running, 2 idle, auto-start: normal"
        self.assertEqual(self.run_raw("status"), (0, f"thread not recorded\nreporting: milestones, lands by local\n{idle}\nliveness: every 600000 ms (default for milestones)\n", ""))
        self.assertEqual(self.brigade("ticket", "list"), "")
        self.assertEqual(self.brigade("startable"), f"{idle}\nno waiting tickets")
        self.assertEqual(self.brigade("watch"), f"{idle}\nno work in progress")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")
        self.assertEqual(self.close_run("--dry-run"),
                         (0, "# Perf report\n\nSince opening. nothing on record.\n\nNothing new.\n\n## Spend\n\nnone", ""))
        self.assertEqual(self.brigade("walk").splitlines(), [
            f"{_shown_root(self.project)}: local mode onto refs/landing/lane. empty.",
            "  Perf (reports milestones): nothing on record",
            "    thread not recorded"])
        self.assertEqual(self.tables(), before)

    def test_next_old_store(self):
        self.store_from_before_kinds()
        self.assertFalse((self.at / "goals.tsv").exists())
        code, out, err = self.run_raw("next")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("wake: unspecified\n"))
        lines = out.splitlines()
        self.assertEqual([line for line in lines if line.startswith("reply:")], [lines[-1]])
        self.assertFalse((self.at / "goals.tsv").exists())
        path = self.at / "dishes.tsv"
        rows = path.read_text().splitlines()
        fields = rows[1].split("\t")
        self.assertEqual((fields[0], len(fields)), ("D1", 15))
        rows[1] = "\t".join(fields + ["nope"])
        path.write_text("\n".join(rows) + "\n")
        code, out, err = self.run_raw("next")
        status = self.run_raw("status")
        self.assertEqual(code, 0)
        self.assertEqual(err, status[2])
        self.assertIn("D1 has kind 'nope'", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual([line for line in out.splitlines() if line.startswith("reply:")], [out.splitlines()[-1]])
        self.assertFalse((self.at / "goals.tsv").exists())


FIRE = "2026-10-01T00:00:00+00:00"
CLOSE_AT = "2026-10-01T02:00:00+00:00"


class _SpendFixture:
    """`fill`, `put`, and `dish` follow `SpendTest` in tests/brigade/test_spend.py, and `dish_close` renames its `close`. `goal_row`, `thread_row`, `token_database`, and `advance_report` are new."""

    def fill(self, dishes, passes=(), threads=(), logs=(), goals=()):
        self.open()
        self.put("dishes.tsv", dishes)
        self.put("pass.tsv", passes)
        self.put("threads.tsv", threads)
        self.put("log.tsv", logs)
        if goals:
            self.put("goals.tsv", goals)

    def put(self, table, rows):
        path = self.at / table
        header = path.read_text().splitlines()[0].split("\t")
        lines = ["\t".join(header)]
        for row in rows:
            lines.append("\t".join(str(row.get(column, "")) for column in header))
        path.write_text("\n".join(lines) + "\n")

    def dish(self, ident="D1", **cells):
        row = {
            "id": ident, "at": FIRE, "state": "merged", "kind": "fix", "goal": "G1",
            "thread": "dish-thread-sentinel", "summary": "summary sentinel",
        }
        row.update(cells)
        return row

    def dish_close(self, ident="D1", at=CLOSE_AT, state="merged"):
        return {"at": at, "kind": "dish", "id": ident, "state": state, "note": "note sentinel"}

    def goal_row(self, ident, name):
        return {"id": ident, "at": FIRE, "state": "open", "name": name, "purpose": "why", "done": "when"}

    def thread_row(self, dish, name):
        return {"at": FIRE, "dish": dish, "role": "worker", "thread": name, "seat": "codex/gpt-6.1-sol"}

    def token_database(self):
        """A database with spend's provider tables and indexes and no rows, so every thread reads as not recorded."""
        base = Path(self.temporary.name) / "t3"
        path = base / "userdata" / "statev2.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE orchestration_v2_projection_provider_threads "
                "(thread_id TEXT, provider_thread_id TEXT, driver TEXT, payload_json TEXT)")
            connection.execute(
                "CREATE TABLE orchestration_v2_projection_provider_turns "
                "(provider_thread_id TEXT, payload_json TEXT)")
            connection.execute(
                "CREATE INDEX provider_threads_thread "
                "ON orchestration_v2_projection_provider_threads(thread_id)")
            connection.execute(
                "CREATE INDEX provider_turns_thread "
                "ON orchestration_v2_projection_provider_turns(provider_thread_id)")
            connection.commit()
        finally:
            connection.close()
        return base

    def advance_report(self, stamp):
        path = self.at / "restaurant.json"
        meta = json.loads(path.read_text())
        meta["lastReportAt"] = stamp
        path.write_text(json.dumps(meta, indent=2) + "\n")


class SpendReportTest(BrigadeTest, _SpendFixture):
    def spend_block(self, text):
        return text[text.index("## Spend"):]

    def two_goal_store(self):
        self.fill(
            [self.dish("D1"), self.dish("D2", goal="G2")],
            threads=[self.thread_row("D1", "ledger-a"), self.thread_row("D2", "ledger-b")],
            logs=[self.dish_close("D1", CLOSE_AT), self.dish_close("D2", CLOSE_AT)],
            goals=[self.goal_row("G1", "one"), self.goal_row("G2", "two")],
        )

    def test_close_prints_a_spend_section_with_one_goal_row_per_goal(self):
        base = self.token_database()
        self.two_goal_store()
        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(base)}):
            code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.spend_block(out), "\n".join([
            "## Spend", "",
            "G1  wall 2h  rounds 0  send-backs 0  threads 1",
            "  not recorded",
            "G2  wall 2h  rounds 0  send-backs 0  threads 1",
            "  not recorded",
        ]))

    def test_close_prints_a_spend_none_for_a_window_with_no_items(self):
        base = self.token_database()
        self.two_goal_store()
        self.advance_report("2026-10-02T00:00:00+00:00")
        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(base)}):
            code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.spend_block(out), "## Spend\n\nnone")

    def test_close_keeps_the_goal_rows_and_says_tokens_unavailable_when_the_token_source_is_unreadable(self):
        empty = Path(self.temporary.name) / "no-t3-database"
        self.two_goal_store()
        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(empty)}):
            code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.spend_block(out), "\n".join([
            "## Spend", "",
            "G1  wall 2h  rounds 0  send-backs 0  threads 1",
            "G2  wall 2h  rounds 0  send-backs 0  threads 1",
            "tokens unavailable",
        ]))
        self.assertNotIn("  not recorded", out)

    GOAL_ROWS_NO_TOKENS = "\n".join([
        "## Spend", "",
        "G1  wall 2h  rounds 0  send-backs 0  threads 1",
        "G2  wall 2h  rounds 0  send-backs 0  threads 1",
        "tokens unavailable",
    ])

    def each_close_form(self, expected, in_process=False):
        """`close --dry-run`, `close`, and `close --to-file` each print this Spend block with empty stderr.

        A writing close advances the report window, so the window is reset to every item before each form.
        """
        runner = call_in_process if in_process else call
        for flags in (("--dry-run",), (), ("--to-file",)):
            with self.subTest(flags=flags):
                self.advance_report("")
                outcome = runner(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                *_owner_words(self.at), "close", *flags])
                self.assertEqual((outcome.code, outcome.stderr_text().strip()), (0, ""))
                text = outcome.stdout_text().strip()
                self.assertEqual(self.spend_block(Path(text).read_text().strip() if flags == ("--to-file",) else text), expected)

    def test_close_keeps_the_goal_rows_and_says_tokens_unavailable_when_the_prices_file_is_refused(self):
        base = self.token_database()
        self.two_goal_store()
        (self.at / "prices.json").write_text("{broken")
        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(base)}):
            self.each_close_form(self.GOAL_ROWS_NO_TOKENS)

    def test_close_keeps_the_goal_rows_and_says_tokens_unavailable_when_the_token_read_raises(self):
        base = self.token_database()
        self.two_goal_store()
        space = run_globals(self, SCRIPT)["report"].__globals__["spend"].__dict__

        def refused(question):
            raise PermissionError("the token read was refused")

        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(base)}), mock.patch.dict(space, {"_open_tokens": refused}):
            self.each_close_form(self.GOAL_ROWS_NO_TOKENS, in_process=True)
