if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import sys
import types

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

    def forget_planted_modules(self, unrelated):
        if sys.modules.get("brigade_lib.unrelated") is unrelated:
            sys.modules.pop("brigade_lib.unrelated", None)
        for name in [name for name in sys.modules if name.startswith("_brigade_ns_")]:
            del sys.modules[name]

    def test_a_load_puts_back_a_brigade_lib_entry_it_did_not_import(self):
        unrelated = types.ModuleType("brigade_lib.unrelated")
        unrelated.marker = "kept"
        sys.modules["brigade_lib.unrelated"] = unrelated
        self.addCleanup(self.forget_planted_modules, unrelated)
        runpy.run_path(str(SCRIPT))
        self.assertIs(sys.modules.get("brigade_lib.unrelated"), unrelated)
        self.assertEqual([name for name in sys.modules if name.startswith("_brigade_ns_")], [])

    def test_a_failed_seam_bind_drops_the_unique_package_and_restores_the_prior_entry(self):
        case = Path(self.temporary.name) / "load"
        case.mkdir()
        source = case / "brigade.py"
        source.write_text(Path(SCRIPT).read_text().replace("seam.bind(globals())", "raise RuntimeError('bind failed')", 1))
        (case / "brigade_lib").symlink_to(Path(SCRIPT).parent / "brigade_lib")
        unrelated = types.ModuleType("brigade_lib.unrelated")
        sys.modules["brigade_lib.unrelated"] = unrelated
        self.addCleanup(self.forget_planted_modules, unrelated)
        with self.assertRaises(RuntimeError):
            runpy.run_path(str(source))
        self.assertIs(sys.modules.get("brigade_lib.unrelated"), unrelated)
        self.assertEqual([name for name in sys.modules if name.startswith("_brigade_ns_")], [])

    def test_a_failed_import_drops_added_brigade_package_modules_and_restores_prior_modules_and_the_bytecode_flag(self):
        case = Path(self.temporary.name) / "load"
        case.mkdir()
        source = case / "brigade.py"
        source.write_text(Path(SCRIPT).read_text().replace(
            "from brigade_lib.spend import add_arguments, spend_text",
            "from brigade_lib.spend import add_arguments, spend_text, _not_a_real_export",
            1,
        ))
        (case / "brigade_lib").symlink_to(Path(SCRIPT).parent / "brigade_lib")
        unrelated = types.ModuleType("brigade_lib.unrelated")
        sys.modules["brigade_lib.unrelated"] = unrelated
        self.addCleanup(self.forget_planted_modules, unrelated)
        before = sys.dont_write_bytecode
        sys.dont_write_bytecode = False
        try:
            prior = sys.modules.copy()
            with self.assertRaises(ImportError):
                runpy.run_path(str(source))
            added = [
                name for name in sys.modules
                if name not in prior and (
                    name == "brigade_lib" or name.startswith("brigade_lib.") or name.startswith("_brigade_ns_")
                )
            ]
            self.assertEqual(added, [])
            self.assertEqual(
                [name for name in prior if sys.modules.get(name) is not prior[name]],
                [],
            )
            self.assertIs(sys.dont_write_bytecode, False)
        finally:
            sys.dont_write_bytecode = before
