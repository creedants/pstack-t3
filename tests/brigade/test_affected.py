from .support import (
    setUpModule,
    BrigadeTest,
    Path,
    ROOT,
)


ALWAYS = "tests.test_pstack_t3"
WORKFLOW = "tests.test_run_tests"


class AffectedTest(BrigadeTest):
    def git(self, *args):
        import subprocess
        return subprocess.run(["git", *args], cwd=self.project, capture_output=True, text=True, check=True)

    def write(self, rel, text):
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def track(self):
        self.git("add", "-A")

    def affected(self, *args, ok=True, code=None):
        return self.brigade("affected", "--root", str(self.project), *args, ok=ok, code=code)

    def vendor_layout(self):
        target = self.project / "scripts"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("build.py", "cli_reference.py"):
            (target / name).write_bytes((ROOT / "scripts" / name).read_bytes())

    def test_brigade_script_load_maps_fails_when_run_path_ignored(self):
        self.git_project()
        self.write("t3/added/brigade/scripts/brigade.py", "VALUE = 1\n")
        self.write("tests/test_example.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "SCRIPT = ROOT / \"t3/added/brigade/scripts/brigade.py\"\n"
                   "def test_x():\n    runpy.run_path(str(SCRIPT))\n")
        self.track()
        out = self.affected("--paths", "t3/added/brigade/scripts/brigade.py")
        self.assertIn("tests.test_example", out)
        self.assertIn(ALWAYS, out)
        self.assertNotIn("No affected mapping", out)

    def test_landing_script_subprocess_maps_fails_when_argv_ignored(self):
        self.git_project()
        self.write("t3/added/landing/scripts/land.py", "VALUE = 1\n")
        self.write("tests/test_land.py",
                   "import subprocess\nimport sys\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "LAND = ROOT / \"t3/added/landing/scripts/land.py\"\n"
                   "def test_y():\n    subprocess.run([sys.executable, str(LAND)], capture_output=True)\n")
        self.track()
        out = self.affected("--paths", "t3/added/landing/scripts/land.py")
        self.assertIn("tests.test_land", out)
        self.assertIn(ALWAYS, out)

    def test_installer_read_maps_fails_when_reader_ignored(self):
        self.git_project()
        self.write("scripts/install.py", "MARK = 1\n")
        self.write("tests/test_inst.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_z():\n    (ROOT / \"scripts/install.py\").read_text()\n")
        self.track()
        out = self.affected("--paths", "scripts/install.py")
        self.assertIn("tests.test_inst", out)
        self.assertIn(ALWAYS, out)

    def test_workflow_declared_owner_maps_fails_when_owner_missing(self):
        self.git_project()
        self.write(".github/workflows/ci.yml", "name: ci\n")
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        out = self.affected("--paths", ".github/workflows/ci.yml")
        self.assertIn(WORKFLOW, out)
        self.assertIn(ALWAYS, out)

    def test_changes_fragment_maps_to_always_fails_when_changes_owner_missing(self):
        self.git_project()
        self.write("changes/note.md", "- First.\n")
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        out = self.affected("--paths", "changes/note.md")
        self.assertIn(ALWAYS, out)
        self.assertNotIn("No affected mapping", out)

    def test_unmapped_names_itself_and_falls_back_fails_when_full_missing(self):
        self.git_project()
        self.write("src/unknown.py", "x = 1\n")
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        out = self.affected("--paths", "src/unknown.py")
        self.assertIn("src/unknown.py", out)
        self.assertIn("full suite", out)

    def test_mixed_expansion_and_mapping_failures_name_both_fails_when_union_missing(self):
        self.git_project()
        self.write("src/unknown.py", "x = 1\n")
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        out = self.affected("--paths", "no-such-file.txt", "--paths", "src/unknown.py")
        self.assertIn("no-such-file.txt", out)
        self.assertIn("src/unknown.py", out)
        self.assertIn("full suite", out)

    def test_generated_copy_same_as_source_fails_when_twins_ignored(self):
        self.git_project()
        self.vendor_layout()
        self.write("t3/runtime.md", "# Runtime\n")
        self.write("skills/pstack-runtime/SKILL.md", "# Runtime\n")
        self.write("tests/test_twin.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_t():\n    (ROOT / \"t3/runtime.md\").read_text()\n")
        self.track()
        one = self.affected("--paths", "t3/runtime.md")
        other = self.affected("--paths", "skills/pstack-runtime/SKILL.md")
        self.assertIn("tests.test_twin", one)
        self.assertIn("tests.test_twin", other)
        self.assertIn(ALWAYS, one)
        self.assertIn(ALWAYS, other)

    def test_docs_page_not_twin_fails_when_docs_included(self):
        self.git_project()
        self.vendor_layout()
        self.write("t3/added/brigade/scripts/brigade.py", "x = 1\n")
        self.write("docs/cli/brigade.md", "# Brigade\n")
        self.write("tests/test_src.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "SCRIPT = ROOT / \"t3/added/brigade/scripts/brigade.py\"\n"
                   "def test_s():\n    runpy.run_path(str(SCRIPT))\n")
        self.track()
        out = self.affected("--paths", "docs/cli/brigade.md")
        self.assertIn("docs/cli/brigade.md", out)
        self.assertIn("full suite", out)
        self.assertNotIn("tests.test_src", out)

    def test_directory_expands_fails_when_dir_not_expanded(self):
        self.git_project()
        self.write("src/only/a.py", "x = 1\n")
        self.write("tests/test_dir.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_d():\n    (ROOT / \"src/only/a.py\").read_text()\n")
        self.track()
        by_file = self.affected("--paths", "src/only/a.py")
        by_dir = self.affected("--paths", "src/only")
        self.assertIn("tests.test_dir", by_file)
        self.assertIn("tests.test_dir", by_dir)

    def test_dot_is_unmapped_fails_when_dot_treated_as_all(self):
        self.git_project()
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        out = self.affected("--paths", ".")
        self.assertIn("full suite", out)

    def test_empty_diff_names_only_always_fails_when_empty_not_always(self):
        self.git_project()
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        out = self.affected("--diff", "HEAD")
        self.assertIn(ALWAYS, out)
        self.assertNotIn("No affected mapping", out)

    def test_diff_names_changed_file_fails_when_diff_not_read(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_diff.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_f():\n    (ROOT / \"src/a.py\").read_text()\n")
        self.track()
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.write("src/a.py", "x = 2\n")
        self.git("add", "-A")
        out = self.affected("--diff", "HEAD")
        self.assertIn("tests.test_diff", out)
        self.assertIn(ALWAYS, out)

    def test_git_nonzero_is_error_fails_when_error_swallowed(self):
        self.git_project()
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        err = self.affected("--diff", "not-a-ref...also-bad", ok=False)
        self.assertIn("git diff failed", err)

    def test_paths_and_diff_together_exit_1_fails_when_exclusivity_missing(self):
        self.git_project()
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        err = self.affected("--paths", "src/a.py", "--diff", "HEAD", ok=False, code=1)
        self.assertIn("use either --paths or --diff", err)

    def test_neither_paths_nor_diff_exits_1_fails_when_neither_allowed(self):
        self.git_project()
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        err = self.affected(ok=False, code=1)
        self.assertIn("affected needs --paths or --diff", err)

    def test_import_one_hop_maps_fails_when_import_not_followed(self):
        self.git_project()
        self.write("src/a.py", "import b\n")
        self.write("src/b.py", "y = 1\n")
        self.write("tests/test_hop.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "A = ROOT / \"src/a.py\"\n"
                   "def test_h():\n    runpy.run_path(str(A))\n")
        self.track()
        out = self.affected("--paths", "src/b.py")
        self.assertIn("tests.test_hop", out)

    def test_import_two_hops_not_mapped_fails_when_hops_not_limited(self):
        self.git_project()
        self.write("src/a.py", "import b\n")
        self.write("src/b.py", "import c\n")
        self.write("src/c.py", "z = 1\n")
        self.write("tests/test_hop2.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "A = ROOT / \"src/a.py\"\n"
                   "def test_h():\n    runpy.run_path(str(A))\n")
        self.track()
        out = self.affected("--paths", "src/c.py")
        self.assertIn("src/c.py", out)
        self.assertIn("full suite", out)

    def test_pin_maps_fails_when_pin_ignored(self):
        self.git_project()
        pin = "pin-" + "x" * 36
        self.assertEqual(len(pin), 40)
        self.write("src/pinned.py", f"MARK = \"{pin}\"\n")
        self.write("tests/test_pin.py", f"MARK = \"{pin}\"\n\ndef test_p():\n    assert MARK\n")
        self.track()
        out = self.affected("--paths", "src/pinned.py")
        self.assertIn("tests.test_pin", out)
        self.assertIn(ALWAYS, out)

    def test_brigade_source_and_twin_pick_briefs_and_times_fails_when_brigade_entry_not_carried(self):
        for rel in ("t3/added/brigade/scripts/brigade.py", "skills/brigade/scripts/brigade.py"):
            out = self.brigade("affected", "--root", str(ROOT), "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)

    def test_landing_source_and_twin_pick_briefs_and_times_fails_when_landing_entry_not_carried(self):
        for rel in ("t3/added/landing/scripts/land.py", "skills/landing/scripts/land.py"):
            out = self.brigade("affected", "--root", str(ROOT), "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)
