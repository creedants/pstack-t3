from ..support import Path, ProcessReason, SCRIPT, _wait_for_path, json, os, real_popen, subprocess, sys, time

class OpenMixin:
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

    def test_open_creates_the_store_once_and_keeps_edits(self):
        self.assertEqual(self.open(), f"opened {self.at}")
        (self.at / "menu.md").write_text("# Menu: Perf\n\nKeep startup under 400 ms.\n")
        self.assertEqual(self.open(), f"exists {self.at}")
        self.assertEqual((self.at / "menu.md").read_text(), "# Menu: Perf\n\nKeep startup under 400 ms.\n")
        self.assertIn("only through the repository's landing queue", (self.at / "house-rules.md").read_text())
        self.assertIn("Never use them in replies", (self.at / "house-rules.md").read_text())

    def test_open_mode_records_light_and_set_mode_clears_it(self):
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "light"),
                         f"opened {self.at}")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["mode"], "light")
        self.assertIn("## Budget\n\nAny cap on parallel workers, and whether a provider's usage limit switches this restaurant "
                      "to light mode.\n", (self.at / "menu.md").read_text())
        self.brigade("set", "--thread", "c1")
        self.assertEqual(self.brigade("status"),
                         "thread c1\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)\nmode light\nowner c1@1")
        self.assertIn("  Perf (reports milestones, mode light): nothing on record", self.brigade("walk"))
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "full"),
                         f"exists {self.at}\nthread c1 already recorded\nmode stays light; change it with set --mode")
        self.assertEqual(json.loads(self.brigade("set", "--mode", "full"))["mode"], "full")
        self.assertEqual(self.brigade("set", "--mode", "fast", ok=False), 'brigade: --mode takes full, light, or ""')
        self.assertNotIn("mode", json.loads(self.brigade("set", "--mode", "")))
        self.assertEqual(self.brigade("status"), "thread c1\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)\nowner c1@1")
        self.assertIn("  Perf (reports milestones): nothing on record", self.brigade("walk"))
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "full"),
                         f"exists {self.at}\nthread c1 already recorded\nmode stays unset; change it with set --mode")

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

    def test_open_writes_goals_tsv_with_its_header_and_no_row_into_a_new_store_and_into_the_admins_store(self):
        self.open()
        self.open_admin()
        for directory in (self.at, self.store / "bridge-kit" / ".admin"):
            self.assertEqual((directory / "goals.tsv").read_text(), "id\tat\tstate\tname\tpurpose\tdone\tshare\tclosed\n")

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
