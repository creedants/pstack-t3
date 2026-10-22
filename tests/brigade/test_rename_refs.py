from .support import BrigadeTest, Path, SCRIPT, call_in_process, json, mock, run_globals, setUpModule


class RenameReferenceTest(BrigadeTest):
    """A rename is refused while it reports to the executive admin, while an executive admin request, a handoff, or another project root names the store."""

    def rename(self, new="Speed", project_root=None, **kwargs):
        return self.brigade("open", "--project-root", str(project_root or self.project), "--name", "Perf",
                            "--rename", new, **kwargs)

    def files(self, directory):
        return {str(path.relative_to(directory)): path.read_bytes() for path in sorted(Path(directory).rglob("*"))
                if path.is_file() and path.name != "restaurant.lock"}

    def open_sibling(self, name):
        return self.run_at(self.at.parent / name, "open", "--project-root", str(self.project), "--name", name)

    def open_admin(self):
        return self.run_at(self.at.parent / ".admin", "open", "--admin", "--project-root", str(self.project))

    def admin(self, *args):
        return self.run_at(self.at.parent / ".admin", *args)

    def test_rename_refuses_an_executive_admin_request_to_it(self):
        self.open()
        self.open_admin()
        self.admin("request", "--to", "perf", "do one thing")
        self.assertTrue((self.at / "inbox" / "A1.line").is_file())
        store = self.store_files()
        admin = self.files(self.at.parent / ".admin")
        error = self.rename(code=1)
        self.assertIn("the executive admin recorded A1 for perf", error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.files(self.at.parent / ".admin"), admin)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_an_admin_request_file_in_its_inbox(self):
        self.open()
        (self.at / "inbox").mkdir()
        (self.at / "inbox" / "A1.line").write_text("do one thing\n")
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn("inbox/A1.line is an executive admin request", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_coordinator_that_reports_to_the_executive_admin(self):
        self.open()
        self.brigade("set", "--reports-to", "admin-thread-1")
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn("reports to admin-thread-1", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_ticket_it_handed_to_a_sibling(self):
        self.open()
        self.open_sibling("docs")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "move", "T1", "--to", "docs")
        store = self.store_files()
        sibling = self.files(self.at.parent / "docs")
        error = self.rename(code=1)
        self.assertIn("T1 is moved to docs", error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.files(self.at.parent / "docs"), sibling)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_ticket_a_sibling_handed_to_it(self):
        self.open()
        self.open_sibling("docs")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "move", "T1", "--to", "docs")
        self.run_at(self.at.parent / "docs", "ticket", "take")
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn("T1 in docs came from bridge-kit/perf", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_ticket_a_sibling_moved_to_it(self):
        self.open()
        self.open_sibling("docs")
        self.run_at(self.at.parent / "docs", "ticket", "add", "--kind", "fix", "--summary", "two")
        self.run_at(self.at.parent / "docs", "ticket", "move", "T1", "--to", "perf")
        store = self.store_files()
        error = self.rename(code=1)
        self.assertIn("T1 in docs is moved to perf", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_ticket_it_took_from_a_sibling(self):
        self.open()
        self.open_sibling("docs")
        self.run_at(self.at.parent / "docs", "ticket", "add", "--kind", "fix", "--summary", "two")
        self.run_at(self.at.parent / "docs", "ticket", "move", "T1", "--to", "perf")
        self.brigade("ticket", "take")
        store = self.store_files()
        sibling = self.files(self.at.parent / "docs")
        error = self.rename(code=1)
        self.assertIn("came from bridge-kit/docs/T1", error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.files(self.at.parent / "docs"), sibling)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_refuses_a_project_root_the_store_does_not_record(self):
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--thread", "coordinator")
        self.land("lease", "claim", "--holder", "perf/D55", "--paths", "a.txt", "--owner", "perf/@1")
        other = self.root / "other" / "Bridge Kit"
        other.mkdir(parents=True)
        store = self.store_files()
        error = self.rename(project_root=other, code=1)
        self.assertIn(f"{self.at} records the project root {self.project}", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())
        self.assertIn("L1", self.land("lease", "list"))

    def test_a_failed_name_rewrite_leaves_the_old_store_and_no_target(self):
        self.open()
        store = self.store_files()
        restaurant = run_globals(self, SCRIPT)["Restaurant"]
        original = restaurant.write

        def write(store, relative, text):
            if relative == "restaurant.json":
                raise OSError("simulated write failure")
            return original(store, relative, text)

        with mock.patch.object(restaurant, "write", write):
            outcome = call_in_process(self, SCRIPT, [
                "--store", str(self.store), "--at", str(self.at), "open",
                "--project-root", str(self.project), "--name", "Perf", "--rename", "Speed"])
        self.assertEqual(outcome.code, 1, outcome.stderr_text())
        self.assertIn("simulated write failure", outcome.stderr_text())
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_a_failed_heading_rewrite_leaves_the_old_store_and_no_target(self):
        self.open()
        store = self.store_files()
        restaurant = run_globals(self, SCRIPT)["Restaurant"]
        original = restaurant.write

        def write(store, relative, text):
            if relative == "menu.md":
                raise OSError("simulated heading write failure")
            return original(store, relative, text)

        with mock.patch.object(restaurant, "write", write):
            outcome = call_in_process(self, SCRIPT, [
                "--store", str(self.store), "--at", str(self.at), "open",
                "--project-root", str(self.project), "--name", "Perf", "--rename", "Speed"])
        self.assertEqual(outcome.code, 1, outcome.stderr_text())
        self.assertIn("simulated heading write failure", outcome.stderr_text())
        self.assertEqual(self.store_files(), store)
        self.assertFalse((self.at.parent / "speed").exists())

    def test_rename_moves_a_store_that_already_records_the_new_name(self):
        """The state an interruption after the name rewrite leaves: the old path, with the new name stored."""
        self.open()
        menu = (self.at / "menu.md").read_bytes()
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["restaurant"] = "Speed"
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.rename()
        new = self.at.parent / "speed"
        self.assertFalse(self.at.exists())
        self.assertEqual(json.loads((new / "restaurant.json").read_text())["restaurant"], "Speed")
        after = (new / "menu.md").read_bytes()
        self.assertEqual(after.split(b"\n", 1)[0], b"# Menu: Speed")
        self.assertEqual(after.split(b"\n", 1)[1], menu.split(b"\n", 1)[1])
        self.run_at(new, "set", "--reporting", "digest")
        self.assertEqual(json.loads((new / "restaurant.json").read_text())["reporting"], "digest")
