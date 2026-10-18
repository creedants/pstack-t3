import json
import os
import posixpath
import tempfile
from unittest import mock

from .support import (
    setUpModule,
    BrigadeTest,
    Path,
    ROOT,
)


ALWAYS = "tests.test_pstack_t3"
WORKFLOW = "tests.test_run_tests"
# The exact layout pstack-t3 records, which reproduces the paragraph the picker printed before a store could
# turn it off.
LAYOUT = {
    "always": ALWAYS,
    "workflowModule": WORKFLOW,
    "entryPoints": ["t3/added/brigade/scripts/brigade.py", "t3/added/landing/scripts/land.py"],
    "helpers": ["tests/brigade/support.py", "tests/landing/support.py"],
    "suite": "python3 scripts/run_tests.py -j 8",
    "python": "/home/marcus/.local/share/mise/installs/python/3.10.22/bin/python3.10",
    "importRoots": ["scripts", "t3/scripts"],
}


def reference_close(table, edges):
    closed = {}
    for key, value in table.items():
        closed[key] = set(value)
    changed = True
    while changed:
        changed = False
        for members in closed.values():
            for importer, imported in edges.items():
                if importer in members:
                    continue
                if imported & members:
                    members.add(importer)
                    changed = True
    return closed


def _base_resolve_import(edge, loader_path, root, roots=None):
    import sys
    module = sys.modules["brigade_lib.affected"]
    level, name, symbol = edge
    if symbol == "*":
        symbol = ""
    parts = name.split(".") if name else []
    if level and level > 0:
        parent = posixpath.dirname(module._norm(loader_path) if loader_path else "")
        bits = parent.split("/") if parent else []
        if level - 1 > len(bits):
            return ()
        base = "/".join(bits[:len(bits) - (level - 1)]) if level - 1 else parent
        cands = []
        if parts:
            rel = "/".join(parts)
            if base:
                cands.append(base + "/" + rel + ".py")
                cands.append(base + "/" + rel + "/__init__.py")
            else:
                cands.append(rel + ".py")
                cands.append(rel + "/__init__.py")
        if symbol:
            rel = "/".join(parts + [symbol]) if parts else symbol
            if base:
                cands.append(base + "/" + rel + ".py")
                cands.append(base + "/" + rel + "/__init__.py")
            else:
                cands.append(rel + ".py")
                cands.append(rel + "/__init__.py")
        return module._found_files(root, cands)
    parent = posixpath.dirname(module._norm(loader_path) if loader_path else "")
    cands = []
    rels = []
    if parts:
        rel = "/".join(parts)
        rels.append(rel + ".py")
        rels.append(rel + "/__init__.py")
    if symbol:
        rel = "/".join(parts + [symbol]) if parts else symbol
        rels.append(rel + ".py")
        rels.append(rel + "/__init__.py")
    if not rels and parts:
        rel = "/".join(parts)
        rels = [rel + ".py", rel + "/__init__.py"]
    for tail in rels:
        if parent:
            cands.append(parent + "/" + tail)
        cands.append(tail)
    for tail in rels:
        for prefix in ("scripts", "t3/scripts", "t3/added/brigade/scripts/brigade_lib"):
            cands.append(prefix + "/" + tail)
    return module._found_files(root, cands)


class AffectedTest(BrigadeTest):
    GENERATED = "skills,docs/cli,docs/skills.md"
    MAP = ",".join([
        "t3/added/**=skills/**",
        "t3/overrides/**=skills/**",
        "t3/agents/**=skills/pstack-runtime/agents/**",
        "t3/runtime.md=skills/pstack-runtime/SKILL.md",
        "t3/scripts/roles.py=skills/pstack-runtime/scripts/roles.py",
        "t3/setup.md=skills/setup-pstack/SKILL.md",
        "t3/added/brigade/scripts/activity.py=docs/cli/activity.md!",
        "t3/added/brigade/scripts/brigade.py=docs/cli/brigade.md!",
        "t3/added/landing/scripts/land.py=docs/cli/land.md!",
        "t3/scripts/roles.py=docs/cli/roles.md!",
        "t3/runtime.md=docs/skills.md!",
        "t3/setup.md=docs/skills.md!",
        "t3/removed.txt=docs/skills.md!",
        "t3=docs/skills.md!",
        "t3/overrides=docs/skills.md!",
        "t3/added=docs/skills.md!",
        "vendor/pstack=docs/skills.md!",
        "vendor/pstack/skills=docs/skills.md!",
        "t3/overrides/*=docs/skills.md!",
        "t3/added/*=docs/skills.md!",
        "vendor/pstack/skills/*=docs/skills.md!",
        "t3/overrides/*/SKILL.md=docs/skills.md!",
        "t3/added/*/SKILL.md=docs/skills.md!",
        "vendor/pstack/skills/*/SKILL.md=docs/skills.md!",
    ])

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ, {"BRIGADE_STORE": str(self.store)})
        patch.start()
        self.addCleanup(patch.stop)
        self.restaurant(self.project, self.at)
        self.restaurant(self.archive_root(), self.store / "archive" / "perf")
        self.landing_env()
        import subprocess
        archive = self.archive_root()
        exists = subprocess.run(["git", "-C", str(archive), "rev-parse", "--verify", "--quiet", "main"],
                                capture_output=True)
        if exists.returncode != 0:
            subprocess.run(["git", "-C", str(archive), "add", "-A"], check=True,
                           capture_output=True, text=True)
            subprocess.run(["git", "-C", str(archive), "-c", "user.name=t", "-c", "user.email=t@t",
                            "commit", "-qm", "archive"], check=True, capture_output=True, text=True)
        self.land_repo(archive, "init", "--trunk", "main", "--mode", "local", "--base", "main",
                       "--generated", self.GENERATED, "--generated-map", self.MAP)

    def restaurant(self, root, directory=None, layout=LAYOUT, build="python3 scripts/build.py",
                   check="python3 scripts/check.py", note=None):
        """Write a restaurant.json whose projectRoot is root, with or without an affectedTests layout."""
        directory = self.at if directory is None else directory
        directory.mkdir(parents=True, exist_ok=True)
        meta = {"projectRoot": str(Path(root).resolve())}
        if layout is not None:
            meta["affectedTests"] = layout
        if build is not None:
            meta["buildCommand"] = build
        if check is not None:
            meta["checkCommand"] = check
        if note is not None:
            meta["changeNoteDir"] = note
        (directory / "restaurant.json").write_text(json.dumps(meta))
        return directory / "restaurant.json"

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

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._archive_temporary = tempfile.TemporaryDirectory()
        dest = Path(cls._archive_temporary.name).resolve() / "archive"
        cls._archive_dir = cls._make_archive(dest)

    @classmethod
    def tearDownClass(cls):
        cls._archive_temporary.cleanup()
        super().tearDownClass()

    @classmethod
    def _make_archive(cls, dest):
        import shutil
        import subprocess
        dest.mkdir(parents=True, exist_ok=True)
        for child in ROOT.iterdir():
            if child.name == ".git" or child.name == "__pycache__":
                continue
            target = dest / child.name
            if child.is_dir():
                shutil.copytree(child, target, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            else:
                shutil.copy2(child, target)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=dest, capture_output=True, text=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=dest, capture_output=True, text=True, check=True)
        return dest

    def archive_root(self):
        return type(self)._archive_dir

    def close_function(self):
        import importlib
        import sys
        scripts = str(ROOT / "t3/added/brigade/scripts")
        # Another test module in this worker can leave the path behind, so add it only when it is absent.
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
            self.addCleanup(sys.path.remove, scripts)
        for name in [n for n in list(sys.modules) if n == "brigade_lib" or n.startswith("brigade_lib.")]:
            del sys.modules[name]
        module = importlib.import_module("brigade_lib.affected")
        self.assertEqual(Path(module.__file__).resolve(), (Path(scripts) / "brigade_lib" / "affected.py").resolve())
        return module._close

    def vendor_layout(self):
        self.landing_env()
        self.init_landing("--generated", "skills,docs/cli,docs/skills.md", "--generated-map", ",".join([
            "t3/added/**=skills/**",
            "t3/overrides/**=skills/**",
            "t3/agents/**=skills/pstack-runtime/agents/**",
            "t3/runtime.md=skills/pstack-runtime/SKILL.md",
            "t3/scripts/roles.py=skills/pstack-runtime/scripts/roles.py",
            "t3/setup.md=skills/setup-pstack/SKILL.md",
            "t3/added/brigade/scripts/activity.py=docs/cli/activity.md!",
            "t3/added/brigade/scripts/brigade.py=docs/cli/brigade.md!",
            "t3/added/landing/scripts/land.py=docs/cli/land.md!",
            "t3/scripts/roles.py=docs/cli/roles.md!",
            "t3/runtime.md=docs/skills.md!",
            "t3/setup.md=docs/skills.md!",
            "t3/removed.txt=docs/skills.md!",
            "t3=docs/skills.md!",
            "t3/overrides=docs/skills.md!",
            "t3/added=docs/skills.md!",
            "vendor/pstack=docs/skills.md!",
            "vendor/pstack/skills=docs/skills.md!",
            "t3/overrides/*=docs/skills.md!",
            "t3/added/*=docs/skills.md!",
            "vendor/pstack/skills/*=docs/skills.md!",
            "t3/overrides/*/SKILL.md=docs/skills.md!",
            "t3/added/*/SKILL.md=docs/skills.md!",
            "vendor/pstack/skills/*/SKILL.md=docs/skills.md!",
        ]))

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
        self.restaurant(self.project, note="changes")
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

    def test_brigade_source_and_twin_pick_briefs_without_times_fails_when_helper_union_not_narrowed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/brigade/scripts/brigade.py", "skills/brigade/scripts/brigade.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertNotIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)

    def test_landing_source_and_twin_pick_briefs_and_times_fails_when_landing_entry_not_carried(self):
        root = str(self.archive_root())
        for rel in ("t3/added/landing/scripts/land.py", "skills/landing/scripts/land.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)

    def test_briefs_lib_one_hop_maps_fails_when_helper_carried_entry_import_not_followed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/brigade/scripts/brigade_lib/briefs.py", "skills/brigade/scripts/brigade_lib/briefs.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertNotIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)

    def test_governor_one_hop_maps_fails_when_helper_carried_entry_import_not_followed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/landing/scripts/landing_lib/governor.py", "skills/landing/scripts/landing_lib/governor.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.landing.test_slot", out)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertNotIn("No affected mapping", out)

    def test_activity_source_and_twin_pick_activity_and_render_fails_when_transitive_import_not_closed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/brigade/scripts/activity.py", "skills/brigade/scripts/activity.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.test_activity", out)
            self.assertIn("tests.test_activity_render", out)
            self.assertNotIn("No affected mapping", out)

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
        self.assertIn("slot -- python3 -m unittest", out)
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
        self.assertEqual(
            self.affected("--paths", "src/a.py"),
            "Affected test modules are `tests.test_a`, `tests.test_pstack_t3`. "
            f"Run them in the foreground as `python3 {land} slot -- python3 -m unittest tests.test_a tests.test_pstack_t3`. "
            f"Run the same modules on Python 3.10 in the foreground as `python3 {land} slot -- {interpreter} -m unittest tests.test_a tests.test_pstack_t3`. "
            f"Run `python3 {land} slot -- python3 scripts/build.py` and leave no regeneration diff. "
            f"Run `python3 {land} slot -- python3 scripts/check.py`. "
            "Keep each build and test in a foreground slot.")
        self.assertEqual(
            self.affected("--paths", "README.md"),
            "No affected mapping for `README.md`. "
            f"Run the full suite in the foreground as `python3 {land} slot --slots 2 -- python3 scripts/run_tests.py -j 8`. "
            f"Run the same suite on Python 3.10 in the foreground as `python3 {land} slot --slots 2 -- {interpreter} scripts/run_tests.py -j 8`. "
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
        self.assertIn("slot --slots 2 -- /usr/bin/python3.11 -m pytest -q", out)
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

    def test_d411_pair_maps_same_as_old_literals_fails_when_the_archive_tables_differ(self):
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
        self.assertEqual(old_index.table, new_index.table)
        lost = set(old_index.table) - set(new_index.table)
        gained = set(new_index.table) - set(old_index.table)
        self.assertEqual(lost, set())
        self.assertEqual(gained, set())
