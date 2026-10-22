import json
import os
import posixpath
import tempfile
from unittest import mock

from ..support import Path, ROOT, setUpModule
from tests.brigade.test_affected import AffectedBase, ALWAYS, LAYOUT, WORKFLOW, _base_resolve_import, reference_close


class AffectedSplit2Test(AffectedBase):
    def test_chain_outer_render_page_maps_fails_when_transitive_import_not_closed(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_page.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "A = ROOT / \"src/a.py\"\n"
                   "def test_p():\n    runpy.run_path(str(A))\n")
        self.write("tests/test_render.py",
                   "from tests import test_page\n"
                   "def test_r():\n    assert test_page\n")
        self.write("tests/test_outer.py",
                   "from tests import test_render\n"
                   "def test_o():\n    assert test_render\n")
        self.track()
        out = self.affected("--paths", "src/a.py")
        self.assertIn("tests.test_page", out)
        self.assertIn("tests.test_render", out)
        self.assertIn("tests.test_outer", out)
        self.assertIn(ALWAYS, out)
        self.assertNotIn("No affected mapping", out)

    def test_unresolved_test_import_falls_back_fails_when_open_index_ignored(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_page.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "A = ROOT / \"src/a.py\"\n"
                   "def test_p():\n    runpy.run_path(str(A))\n")
        self.write("tests/test_bad.py",
                   "import test_missing_zz\n"
                   "def test_b():\n    assert test_missing_zz\n")
        self.track()
        out = self.affected("--paths", "src/a.py")
        self.assertIn("full suite", out)
        self.assertIn("test_missing_zz", out)

    def test_no_layout_prints_the_check_command_fails_when_the_picker_runs_without_one(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_a.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_a():\n    (ROOT / \"src/a.py\").read_text()\n")
        self.track()
        self.restaurant(self.project, layout=None)
        out = self.affected("--paths", "src/a.py")
        self.assertIn("slot -- python3 scripts/check.py", out)
        self.assertIn("picker is off", out)
        self.assertNotIn(ALWAYS, out)
        self.assertNotIn("tests.test_a", out)

    def test_two_layouts_that_differ_refuse_and_name_both_files_fails_when_the_first_wins(self):
        self.git_project()
        self.write("tests/test_a.py", "def test_a():\n    pass\n")
        self.track()
        first = self.restaurant(self.project, self.at)
        other = self.restaurant(self.project, self.store / "other" / "perf",
                                layout=dict(LAYOUT, always="tests.test_other"))
        err = self.affected("--paths", "tests/test_a.py", ok=False)
        self.assertIn("affectedTests differs across", err)
        self.assertIn(str(first), err)
        self.assertIn(str(other), err)

    def test_a_malformed_layout_refuses_with_the_file_key_and_value_fails_when_it_is_ignored(self):
        self.git_project()
        self.write("tests/test_a.py", "def test_a():\n    pass\n")
        self.track()
        self.restaurant(self.project, layout="tests.test_pstack_t3")
        err = self.affected("--paths", "tests/test_a.py", ok=False)
        self.assertIn("restaurant.json", err)
        self.assertIn("affectedTests", err)
        self.assertIn("'tests.test_pstack_t3'", err)

    def test_set_refuses_a_malformed_affected_tests_and_leaves_restaurant_json_unchanged_fails_when_validate_settings_skips_the_layout(self):
        self.restaurant(self.project, layout="tests.test_pstack_t3")
        before = (self.at / "restaurant.json").read_bytes()
        err = self.brigade("set", "--check-command", "echo NEW", code=1)
        self.assertIn(
            "brigade: warning: restaurant.json affectedTests 'tests.test_pstack_t3' is invalid: it is not an object",
            err)
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_set_accepts_the_documented_affected_tests_template_fails_when_validate_settings_refuses_a_valid_layout(self):
        self.restaurant(self.project)
        self.brigade("set", "--check-command", "echo NEW")
        meta = json.loads((self.at / "restaurant.json").read_text())
        self.assertEqual(meta["checkCommand"], "echo NEW")
        self.assertEqual(meta["affectedTests"], LAYOUT)

    def test_a_layout_without_python_omits_the_second_interpreter_fails_when_a_3_10_path_is_printed(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_a.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_a():\n    (ROOT / \"src/a.py\").read_text()\n")
        self.track()
        self.restaurant(self.project, layout={key: value for key, value in LAYOUT.items() if key != "python"})
        out = self.affected("--paths", "src/a.py")
        self.assertIn("tests.test_a", out)
        self.assertIn("slot -- env PSTACK_TEST_REAL_PROCESS=1 python3 -m unittest", out)
        self.assertNotIn("3.10", out)

    def test_the_pstack_layout_renders_the_paragraph_the_old_picker_printed_fails_when_a_sentence_changes(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_a.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_a():\n    (ROOT / \"src/a.py\").read_text()\n")
        self.track()
        land = ROOT / "t3/added/landing/scripts/land.py"
        interpreter = LAYOUT["python"]
        env = "env PSTACK_TEST_REAL_PROCESS=1 "
        self.assertEqual(
            self.affected("--paths", "src/a.py"),
            "Affected test modules are `tests.test_a`, `tests.test_pstack_t3`. "
            f"Run them in the foreground as `python3 {land} slot -- {env}python3 -m unittest tests.test_a tests.test_pstack_t3`. "
            f"Run the same modules on Python 3.10 in the foreground as `python3 {land} slot -- {env}{interpreter} -m unittest tests.test_a tests.test_pstack_t3`. "
            f"Run `python3 {land} slot -- python3 scripts/build.py` and leave no regeneration diff. "
            f"Run `python3 {land} slot -- python3 scripts/check.py`. "
            "Keep each build and test in a foreground slot.")
        self.assertEqual(
            self.affected("--paths", "README.md"),
            "No affected mapping for `README.md`. "
            f"Run the full suite in the foreground as `python3 {land} slot --slots 2 -- {env}python3 scripts/run_tests.py -j 8`. "
            f"Run the same suite on Python 3.10 in the foreground as `python3 {land} slot --slots 2 -- {env}{interpreter} scripts/run_tests.py -j 8`. "
            f"Run `python3 {land} slot -- python3 scripts/build.py` and leave no regeneration diff. "
            f"Run `python3 {land} slot -- python3 scripts/check.py`. "
            "Keep each build and test in a foreground slot.")

    def test_an_explicit_store_root_keeps_its_layout_fails_when_the_environment_store_wins(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_a.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_a():\n    (ROOT / \"src/a.py\").read_text()\n")
        self.track()
        empty = self.root / "empty-store"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"BRIGADE_STORE": str(empty)}):
            out = self.affected("--paths", "src/a.py")
        self.assertIn("tests.test_a", out)
        self.assertIn(ALWAYS, out)
        self.assertNotIn("No verify command is set", out)

    def test_a_check_command_of_none_is_no_check_command_fails_when_the_sentinel_is_run(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_a.py", "def test_a():\n    pass\n")
        self.track()
        self.restaurant(self.project, layout=None, check="none")
        out = self.affected("--paths", "src/a.py")
        self.assertIn("No verify command is set", out)
        self.assertNotIn("slot -- none", out)

    def test_other_build_and_check_commands_name_both_fails_when_either_is_hard_coded(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_a.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_a():\n    (ROOT / \"src/a.py\").read_text()\n")
        self.track()
        self.restaurant(self.project, build="make gen", check="make check")
        out = self.affected("--paths", "src/a.py")
        self.assertIn("slot -- make gen` and leave no regeneration diff", out)
        self.assertIn("slot -- make check`.", out)
        self.assertNotIn("scripts/build.py", out)
        self.assertNotIn("scripts/check.py", out)
