if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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
    "python": "/usr/local/bin/python3.10",
    "importRoots": ["scripts", "t3/scripts"],
    "env": {"PSTACK_TEST_REAL_PROCESS": "1"},
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
    cur = parent
    while cur and cur not in (".", "/"):
        cur = posixpath.dirname(cur)
        if not cur or cur in (".", "/"):
            break
        for tail in rels:
            cands.append(cur + "/" + tail)
    for tail in rels:
        for prefix in ("scripts", "t3/scripts", "t3/added/brigade/scripts/brigade_lib"):
            cands.append(prefix + "/" + tail)
    return module._found_files(root, cands)




class AffectedBase(BrigadeTest):
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
        "t3/runtime.md=docs/skills.md!^",
        "t3/setup.md=docs/skills.md!^",
        "t3/removed.txt=docs/skills.md!^",
        "t3=docs/skills.md!^",
        "t3/overrides=docs/skills.md!^",
        "t3/added=docs/skills.md!^",
        "vendor/pstack=docs/skills.md!^",
        "vendor/pstack/skills=docs/skills.md!^",
        "t3/overrides/*=docs/skills.md!^",
        "t3/added/*=docs/skills.md!^",
        "vendor/pstack/skills/*=docs/skills.md!^",
        "t3/overrides/*/SKILL.md=docs/skills.md!^",
        "t3/added/*/SKILL.md=docs/skills.md!^",
        "vendor/pstack/skills/*/SKILL.md=docs/skills.md!^",
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
            subprocess.run(["git", "-C", str(archive), "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                            "add", "-A"], check=True,
                           capture_output=True, text=True)
            subprocess.run(["git", "-C", str(archive), "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                            "-c", "user.name=t", "-c", "user.email=t@t",
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
        # Disable detached maintenance in this archive.
        subprocess.run(["git", "config", "maintenance.auto", "false"], cwd=dest, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "gc.auto", "0"], cwd=dest, capture_output=True, text=True, check=True)
        subprocess.run(["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "add", "-A"],
                       cwd=dest, capture_output=True, text=True, check=True)
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
            "t3/runtime.md=docs/skills.md!^",
            "t3/setup.md=docs/skills.md!^",
            "t3/removed.txt=docs/skills.md!^",
            "t3=docs/skills.md!^",
            "t3/overrides=docs/skills.md!^",
            "t3/added=docs/skills.md!^",
            "vendor/pstack=docs/skills.md!^",
            "vendor/pstack/skills=docs/skills.md!^",
            "t3/overrides/*=docs/skills.md!^",
            "t3/added/*=docs/skills.md!^",
            "vendor/pstack/skills/*=docs/skills.md!^",
            "t3/overrides/*/SKILL.md=docs/skills.md!^",
            "t3/added/*/SKILL.md=docs/skills.md!^",
            "vendor/pstack/skills/*/SKILL.md=docs/skills.md!^",
        ]))


class AffectedTest(AffectedBase):
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
