import json
import os
import posixpath
import tempfile
from unittest import mock

from ..support import Path, ROOT, setUpModule
from tests.brigade.test_affected import AffectedBase, ALWAYS, LAYOUT, WORKFLOW, _base_resolve_import, reference_close


class AffectedSplit3Test(AffectedBase):
    def test_a_store_with_no_build_command_drops_the_build_sentence_fails_when_the_default_is_printed(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_a.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_a():\n    (ROOT / \"src/a.py\").read_text()\n")
        self.track()
        self.restaurant(self.project, build=None)
        out = self.affected("--paths", "src/a.py")
        self.assertNotIn("no regeneration diff", out)
        self.assertNotIn("scripts/build.py", out)
        self.assertIn("slot -- python3 scripts/check.py`.", out)
        self.assertIn("Keep each build and test in a foreground slot.", out)

    def test_a_build_command_of_none_drops_the_build_sentence_fails_when_the_default_is_printed(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_a.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_a():\n    (ROOT / \"src/a.py\").read_text()\n")
        self.track()
        self.restaurant(self.project, build="none")
        out = self.affected("--paths", "src/a.py")
        self.assertNotIn("no regeneration diff", out)
        self.assertNotIn("slot -- none", out)
        self.assertIn("slot -- python3 scripts/check.py`.", out)

    def test_a_store_with_no_check_command_drops_the_check_sentence_fails_when_the_default_is_printed(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_a.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_a():\n    (ROOT / \"src/a.py\").read_text()\n")
        self.track()
        self.restaurant(self.project, check=None)
        out = self.affected("--paths", "src/a.py")
        self.assertIn("slot -- python3 scripts/build.py` and leave no regeneration diff", out)
        self.assertNotIn("scripts/check.py", out)
        self.assertIn("Keep each build and test in a foreground slot.", out)

    def test_a_python_with_a_suite_that_is_not_python3_is_refused_fails_when_the_pair_is_run(self):
        self.git_project()
        self.write("tests/test_a.py", "def test_a():\n    pass\n")
        self.track()
        self.restaurant(self.project, layout=dict(LAYOUT, suite="npm test", python="/usr/bin/python3.11"))
        err = self.affected("--paths", "tests/test_a.py", ok=False)
        self.assertIn("restaurant.json", err)
        self.assertIn("affectedTests", err)
        self.assertIn("must start with python3", err)

    def test_a_python_whose_file_name_names_no_version_is_refused_fails_when_any_path_is_taken(self):
        self.git_project()
        self.write("tests/test_a.py", "def test_a():\n    pass\n")
        self.track()
        self.restaurant(self.project, layout=dict(LAYOUT, python="/usr/bin/pypy3"))
        err = self.affected("--paths", "tests/test_a.py", ok=False)
        self.assertIn("restaurant.json", err)
        self.assertIn("affectedTests", err)
        self.assertIn("/usr/bin/pypy3", err)

    def test_a_second_interpreter_is_named_by_its_own_version_fails_when_a_3_10_label_is_printed(self):
        self.git_project()
        self.write("tests/test_a.py", "def test_a():\n    pass\n")
        self.track()
        self.restaurant(self.project, layout=dict(LAYOUT, suite="python3 -m pytest -q", python="/usr/bin/python3.11"))
        out = self.affected("--paths", "README.md")
        self.assertIn("slot --slots 2 -- PSTACK_TEST_REAL_PROCESS=1 /usr/bin/python3.11 -m pytest -q", out)
        self.assertIn("Python 3.11", out)
        self.assertNotIn("Python 3.10", out)

    def test_close_matches_reference_on_seeded_random_tables_fails_when_worklist_misses_an_importer(self):
        import random
        close = self.close_function()
        rng = random.Random(20261017)
        for _ in range(300):
            names = [f"m{n}" for n in range(rng.randint(2, 14))]
            table = {}
            for k in range(rng.randint(1, 6)):
                table[f"src/{k}.py"] = set(rng.sample(names, rng.randint(0, len(names))))
            edges = {}
            for name in rng.sample(names, rng.randint(0, len(names))):
                others = [other for other in names if other != name]
                imported = rng.sample(others, rng.randint(0, len(others)))
                if imported:
                    edges[name] = frozenset(imported)
            want = reference_close({k: set(v) for k, v in table.items()}, edges)
            got = {k: set(v) for k, v in close(table, edges).items()}
            self.assertEqual(got, want)

    def test_close_matches_reference_on_a_deep_chain_fails_when_worklist_stops_early(self):
        close = self.close_function()
        names = [f"t{n}" for n in range(14)]
        edges = {names[n]: frozenset([names[n + 1]]) for n in range(len(names) - 1)}
        table = {"src/a.py": {"t13"}}
        got = close(table, edges)
        self.assertEqual(got["src/a.py"], set(names))
        self.assertEqual({k: set(v) for k, v in got.items()},
                         reference_close({k: set(v) for k, v in table.items()}, edges))

    def test_absent_import_roots_searches_entry_and_helper_dirs_fails_when_default_ignored(self):
        self.git_project()
        self.write("tool/run.py", "VALUE = 1\n")
        self.write("tool/libx.py", "X = 1\n")
        self.write("other/help.py", "VALUE = 1\n")
        self.write("other/liby.py", "Y = 2\n")
        self.write("tests/test_entry.py", "from libx import X\nfrom liby import Y\n\ndef test_e():\n assert X and Y\n")
        self.track()
        layout = dict(LAYOUT)
        layout.pop("importRoots")
        layout["entryPoints"] = ["tool/run.py"]
        layout["helpers"] = ["other/help.py"]
        self.restaurant(self.project, layout=layout)
        for rel in ("tool/libx.py", "other/liby.py"):
            out = self.affected("--paths", rel)
            self.assertIn("tests.test_entry", out)
            self.assertNotIn("No affected mapping", out)

    def test_configured_import_roots_resolve_fails_when_list_ignored(self):
        self.git_project()
        self.write("tool/run.py", "VALUE = 1\n")
        self.write("pkg/mod.py", "VALUE = 1\n")
        self.write("tests/test_cfg.py", "import mod\n\ndef test_c():\n assert mod\n")
        self.track()
        self.restaurant(self.project, layout=dict(LAYOUT, entryPoints=["tool/run.py"], helpers=[],
                                                  importRoots=["pkg"]))
        out = self.affected("--paths", "pkg/mod.py")
        self.assertIn("tests.test_cfg", out)
        self.assertNotIn("No affected mapping", out)
        self.restaurant(self.project, layout=dict(LAYOUT, entryPoints=["tool/run.py"], helpers=[], importRoots=[]))
        out = self.affected("--paths", "pkg/mod.py")
        self.assertIn("No affected mapping", out)
        self.assertIn("pkg/mod.py", out)

    def test_malformed_import_roots_refused_fails_when_accepted(self):
        self.git_project()
        self.write("tests/test_a.py", "def test_a():\n pass\n")
        self.track()
        for bad in ("scripts", None, 123, {"roots": ["scripts"]}, ["/abs"], ["../up"], ["a b"],
                    [None], [123], ["scripts", None]):
            with self.subTest(bad=bad):
                self.restaurant(self.project, layout=dict(LAYOUT, importRoots=bad))
                err = self.affected("--paths", "tests/test_a.py", ok=False)
                self.assertIn("restaurant.json", err)
                self.assertIn("affectedTests", err)
                self.assertIn("importRoots", err)
                before = (self.at / "restaurant.json").read_bytes()
                before_store = self.store_files()
                err = self.brigade("set", "--check-command", "echo NEW", code=1)
                self.assertIn("restaurant.json", err)
                self.assertIn("affectedTests", err)
                self.assertIn("importRoots", err)
                self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
                self.assertEqual(self.store_files(), before_store)

    def test_malformed_env_refused_fails_when_accepted(self):
        self.git_project()
        self.write("tests/test_a.py", "def test_a():\n pass\n")
        self.track()
        for bad in (None, 123, ["A=1"], {"A": ""}, {"A": "x y"}, {"1A": "1"}, {"A": 1}):
            with self.subTest(bad=bad):
                self.restaurant(self.project, layout=dict(LAYOUT, env=bad))
                err = self.affected("--paths", "tests/test_a.py", ok=False)
                self.assertIn("restaurant.json", err)
                self.assertIn("affectedTests", err)
                self.assertIn("env", err)
                before = (self.at / "restaurant.json").read_bytes()
                before_store = self.store_files()
                err = self.brigade("set", "--check-command", "echo NEW", code=1)
                self.assertIn("restaurant.json", err)
                self.assertIn("affectedTests", err)
                self.assertIn("env", err)
                self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
                self.assertEqual(self.store_files(), before_store)

    def test_d411_pair_keeps_every_old_key_fails_when_a_gained_key_is_outside_an_underscore_directory(self):
        self.close_function()
        import sys
        module = sys.modules["brigade_lib.affected"]
        archive = self.archive_root()
        entries = tuple(LAYOUT["entryPoints"])
        helpers = tuple(LAYOUT["helpers"])
        old = module.Layout(ALWAYS, WORKFLOW, entries, helpers, LAYOUT["suite"], LAYOUT["python"],
                            ("scripts", "t3/scripts", "t3/added/brigade/scripts/brigade_lib"))
        new = module.Layout(ALWAYS, WORKFLOW, entries, helpers, LAYOUT["suite"], LAYOUT["python"],
                            ("scripts", "t3/scripts"))
        current = module._resolve_import
        module._resolve_import = _base_resolve_import
        try:
            old_index = module._index(archive, old)
        finally:
            module._resolve_import = current
        new_index = module._index(archive, new)
        self.assertIsInstance(old_index, module._Closed)
        self.assertIsInstance(new_index, module._Closed)
        lost = set(old_index.table) - set(new_index.table)
        gained = set(new_index.table) - set(old_index.table)
        self.assertEqual(lost, set())
        self.assertTrue(all(posixpath.basename(posixpath.dirname(key)).startswith("_") for key in gained))
