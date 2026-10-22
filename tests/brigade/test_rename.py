from .support import BrigadeTest, Path, SCRIPT, call_in_process, datetime, json, mock, run_globals, subprocess, timezone, setUpModule


class RenameTest(BrigadeTest):
    """A rename moves the store and records the new name, and is refused while a landing hold, a dish, or the target path blocks it."""

    def rename(self, new="Speed", *extra, **kwargs):
        return self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                            "--rename", new, *extra, **kwargs)

    def tables_at(self, directory):
        return {path.name: path.read_bytes() for path in sorted(Path(directory).glob("*.tsv"))}

    def test_rename_moves_the_store_and_new_name_reaches_meta_and_both_headers(self):
        self.open()
        tables = self.tables()
        menu = (self.at / "menu.md").read_bytes()
        rules = (self.at / "house-rules.md").read_bytes()
        new = self.at.parent / "speed"
        self.rename()
        self.assertFalse(self.at.exists())
        self.assertEqual(json.loads((new / "restaurant.json").read_text())["restaurant"], "Speed")
        for name, heading, before in (("menu.md", "# Menu: Speed", menu),
                                      ("house-rules.md", "# House rules: Speed", rules)):
            after = (new / name).read_bytes()
            self.assertEqual(after.split(b"\n", 1)[0], heading.encode())
            self.assertEqual(after.split(b"\n", 1)[1], before.split(b"\n", 1)[1])
        self.assertEqual(self.tables_at(new), tables)
        self.run_at(new, "set", "--reporting", "digest")
        self.assertEqual(json.loads((new / "restaurant.json").read_text())["reporting"], "digest")

    def test_rename_refuses_a_lease_held_under_the_old_prefix(self):
        self.started()
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn("L1", error)
        self.assertEqual(self.store_files(), store)
        self.assertTrue(self.at.exists())

    def test_rename_refuses_a_queue_entry_held_under_the_old_prefix(self):
        """The CLI exits 1 on a queue entry its status read prints and the store keeps its bytes."""
        self.open()
        store = self.store_files()
        readings = {("lease", "list"): "no leases held",
                    ("status", "--holder", "perf/"): "E3 queued (perf/D1, ab12cd34ef56)"}

        def land_result(project_root, *args):
            return subprocess.CompletedProcess([], 0, stdout=readings[args], stderr="")

        with mock.patch.dict(run_globals(self, SCRIPT), {"land_result": land_result}):
            outcome = call_in_process(self, SCRIPT, [
                "--store", str(self.store), "--at", str(self.at), "open",
                "--project-root", str(self.project), "--name", "Perf", "--rename", "Speed"])
        self.assertEqual(outcome.code, 1, outcome.stderr_text())
        self.assertIn("E3", outcome.stderr_text())
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_dish_that_is_not_merged_or_dropped(self):
        self.open()
        path = self.at / "dishes.tsv"
        header = path.read_text()
        for state in ("in-progress", "in-review", "queued", "passed", "sent-back", "blocked"):
            with self.subTest(state=state):
                row = "\t".join(["D1", datetime.now(timezone.utc).isoformat(), state] + [""] * 14)
                path.write_text(f"{header}{row}\n")
                store = self.store_files()
                error = self.rename(code=1)
                self.assertIn(f"D1 is {state}", error)
                self.assertEqual(self.store_files(), store)
                self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_an_existing_target_and_keeps_its_file(self):
        self.open()
        target = self.at.parent / "speed"
        target.mkdir()
        (target / "keep.txt").write_text("keep\n")
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn(str(target), error)
        self.assertEqual((target / "keep.txt").read_text(), "keep\n")
        self.assertEqual(self.store_files(), store)

    def test_rename_refuses_a_stale_owner_although_open_needs_no_store(self):
        self.open()
        self.brigade("set", "--thread", "coordinator")
        store = self.store_files()
        error = self.run_at(self.at, "--owner", "other@1", "open", "--project-root", str(self.project),
                            "--name", "Perf", "--rename", "Speed", owner=False, code=1)
        self.assertIn("coordinator@1", error)
        self.assertEqual(self.store_files(), store)

    def test_rename_refuses_workers_which_it_would_ignore(self):
        self.open()
        store = self.store_files()
        error = self.rename("Speed", "--workers", "2", code=1)
        self.assertIn("--workers", error)
        self.assertEqual(self.store_files(), store)
