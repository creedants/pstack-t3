from ..support import Path, ProcessReason, SCRIPT, json, real_process

class SetMixin:
    def test_a_path_under_the_resolved_tmp_is_refused(self):
        # Uses call_in_process so the Path resolve patch reaches the command.
        from unittest import mock
        from ..support import call_in_process
        self.open()
        fake = Path.home().resolve() / "fake-tmp"
        real = Path.resolve

        def patched(self, *args, **kwargs):
            if self == Path("/tmp"):
                return fake
            return real(self, *args, **kwargs)

        bad = str(fake / "scratch")
        before = (self.at / "restaurant.json").read_bytes()
        refusal = "brigade: --scratch-dir takes an absolute directory path outside /tmp, or empty string to clear it\n"
        with mock.patch.object(Path, "resolve", patched):
            outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                      "set", "--scratch-dir", bad])
            self.assertEqual((outcome.code, outcome.stdout_text(), outcome.stderr_text()), (1, "", refusal))
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

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

    def test_set_autofire_refuses_a_value_outside_off_urgent_normal_and_low(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        self.assert_refused_choice(self.brigade("set", "--autofire", "high", ok=False), "--autofire", "high",
                                   ("off", "urgent", "normal", "low"))
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
                expected = dict(meta, **{key: clean})
                self.assertEqual(self.brigade("set", *flag), json.dumps(expected, indent=2))
                self.assertEqual((self.at / "restaurant.json").read_text(), json.dumps(expected, indent=2) + "\n")

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

    def test_set_refuses_a_command_that_is_not_a_command_line_once_the_whitespace_at_its_two_ends_is_removed(self):
        self.open()
        refusal = self.brigade("set", "--build-command", "make\\ ", ok=False)
        self.assertTrue(refusal.startswith("brigade: 'make\\\\' is not a command line: "), refusal)
        self.assertNotIn("buildCommand", json.loads((self.at / "restaurant.json").read_text()))

    def test_set_reporting_changes_the_level_and_rejects_an_unknown_one(self):
        self.open()
        self.brigade("set", "--reporting", "every-turn")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["reporting"], "every-turn")
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: every-turn, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for every-turn)")
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
        refusal = "brigade: --scratch-dir takes an absolute directory path outside /tmp, or empty string to clear it"
        for bad in ("scratch", "~/scratch", "/tmp", "/tmp/scratch", "/home\n"):
            with self.subTest(bad=bad):
                self.assertEqual(self.brigade("set", "--scratch-dir", bad, ok=False), refusal)
                self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

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

    def test_startable_is_refused_in_the_executive_admin_store(self):
        self.open_admin()
        self.assertEqual(self.admin("startable", ok=False), "brigade: the executive admin routes work and never runs it")

    def test_the_executive_admin_status_has_no_workers_line(self):
        self.open_admin()
        self.assertEqual(self.admin("status"), "thread not recorded\nreporting: milestones, no landing contract\nliveness: every 600000 ms (default for milestones)")

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
