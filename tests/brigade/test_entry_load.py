from .support import (
    setUpModule,
    Path,
    ROOT,
    SCRIPT,
    mock,
    os,
    runpy,
    tempfile,
    unittest,
)


class EntryLoadTest(unittest.TestCase):
    """Two loadings of the entry in one process, as a caller that loads it by path makes."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Path(self.temporary.name) / "store"
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.at = self.store / "project" / "core"
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.temporary.name) / "config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def globals_of(self, load):
        """The live module globals one load's functions read. run_path hands back a copy of them."""
        return load["run"].__globals__

    def open_store(self):
        return runpy.run_path(str(SCRIPT))["run"](["--store", str(self.store), "--at", str(self.at), "open",
                                                   "--project-root", str(self.project), "--name", "Core"])

    def add_ticket(self, load, summary):
        return load["run"](["--store", str(self.store), "--at", str(self.at), "ticket", "add",
                            "--summary", summary, "--kind", "fix"])

    def rail(self):
        lines = (self.at / "rail.tsv").read_text().splitlines()
        keys = lines[0].split("\t")
        return [dict(zip(keys, line.split("\t"))) for line in lines[1:]]

    def test_a_second_load_does_not_reset_the_first_loads_read_only_guard(self):
        self.open_store()
        first = runpy.run_path(str(SCRIPT))
        self.globals_of(first)["READ_ONLY"] = True
        second = runpy.run_path(str(SCRIPT))
        self.globals_of(second)["now"] = lambda: "2001-01-01T00:00:00Z"
        with self.assertRaises(first["BrigadeError"]) as raised:
            self.add_ticket(first, "Guarded")
        self.assertEqual(str(raised.exception), "brigade: next is read-only")
        self.assertEqual(self.rail(), [])
        self.assertEqual(self.add_ticket(second, "Unguarded"), "T1")

    def test_each_load_keeps_its_own_patched_clock(self):
        self.open_store()
        first = runpy.run_path(str(SCRIPT))
        self.globals_of(first)["now"] = lambda: "2000-01-01T00:00:00Z"
        second = runpy.run_path(str(SCRIPT))
        self.globals_of(second)["now"] = lambda: "2001-01-01T00:00:00Z"
        self.assertEqual(self.add_ticket(first, "First"), "T1")
        self.assertEqual(self.add_ticket(second, "Second"), "T2")
        self.assertEqual([(row["id"], row["summary"], row["at"]) for row in self.rail()],
                         [("T1", "First", "2000-01-01T00:00:00Z"), ("T2", "Second", "2001-01-01T00:00:00Z")])

    def test_two_loads_do_not_share_held_locks(self):
        first = runpy.run_path(str(SCRIPT))
        first["HELD_LOCKS"].append(Path("/store/held"))
        second = runpy.run_path(str(SCRIPT))
        self.assertEqual(first["HELD_LOCKS"], [Path("/store/held")])
        self.assertEqual(second["HELD_LOCKS"], [])

    def test_a_restaurant_from_the_first_load_still_writes_after_a_second_load(self):
        self.open_store()
        first = runpy.run_path(str(SCRIPT))
        restaurant = first["Restaurant"](str(self.at))
        second = runpy.run_path(str(SCRIPT))
        self.globals_of(second)["READ_ONLY"] = True
        restaurant.write("notes.txt", "written by the first load\n")
        self.assertEqual((self.at / "notes.txt").read_text(), "written by the first load\n")
        with self.assertRaises(second["BrigadeError"]) as raised:
            second["Restaurant"](str(self.at)).write("notes.txt", "written by the second load\n")
        self.assertEqual(str(raised.exception), "brigade: next is read-only")
        self.assertEqual((self.at / "notes.txt").read_text(), "written by the first load\n")

    def test_two_entry_paths_load_independent_area_modules(self):
        self.open_store()
        first = runpy.run_path(str(SCRIPT))
        second = runpy.run_path(str(ROOT / "skills/brigade/scripts/brigade.py"))
        first["HELD_LOCKS"].append(Path("/store/held"))
        self.assertEqual(first["HELD_LOCKS"], [Path("/store/held")])
        self.assertEqual(second["HELD_LOCKS"], [])
        self.globals_of(second)["now"] = lambda: "2001-01-01T00:00:00Z"
        restaurant = first["Restaurant"](str(self.at))
        restaurant.write("notes.txt", "written by the source load\n")
        self.assertEqual((self.at / "notes.txt").read_text(), "written by the source load\n")
        self.assertEqual(self.add_ticket(second, "From the built copy"), "T1")
        self.assertEqual([row["at"] for row in self.rail()], ["2001-01-01T00:00:00Z"])
        self.assertIsNot(first["Restaurant"], second["Restaurant"])
        self.assertIsNot(first["seam"], second["seam"])
