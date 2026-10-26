if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import BrigadeTest, setUpModule
from . import test_apply


class ApplyIgnoredTest(BrigadeTest):
    # Helpers come from ApplyTest by reference. Subclassing it would run its tests a second time here.
    git = test_apply.ApplyTest.git
    contract = test_apply.ApplyTest.contract
    apply = test_apply.ApplyTest.apply
    one_edit = test_apply.ApplyTest.one_edit
    edit_args = test_apply.ApplyTest.edit_args

    def test_apply_skips_git_ignored_build_output_fails_when_the_build_writes_an_ignored_file(self):
        # Processes: git and land build the fixture project and its contract; edits apply reads the contract and runs the
        # build script, which writes ignored files.
        build = ('import pathlib\ntext = pathlib.Path("src/greeting.txt").read_text()\n'
                 'pathlib.Path("out").mkdir(exist_ok=True)\n'
                 'pathlib.Path("out/greeting.txt").write_text(text.upper())\n'
                 'pathlib.Path("__pycache__").mkdir(exist_ok=True)\n'
                 'pathlib.Path("__pycache__/build.cpython-312.pyc").write_text("bytecode\\n")\n'
                 'pathlib.Path("build.log").write_text("built\\n")\n')
        self.contract()
        reviewed = self.edits_store(**{"tools/build.py": build, ".gitignore": "*.log\n__pycache__/\n"})
        self.one_edit()
        target = self.root / "wt-ignored"
        code, out, err = self.apply(*self.edit_args(reviewed, "d1-ignored", target))
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        new_head = out.strip().splitlines()[1].split(" ", 1)[1]
        names = self.git("diff", "--name-only", reviewed, new_head).splitlines()
        self.assertEqual(sorted(names), ["out/greeting.txt", "src/greeting.txt"])
        self.assertEqual(self.git("show", f"{new_head}:src/greeting.txt", cwd=target), "hello wrold\nsecond line")
        tracked = self.git("ls-tree", "-r", "--name-only", new_head, cwd=target).splitlines()
        self.assertNotIn("build.log", tracked)
        self.assertNotIn("__pycache__/build.cpython-312.pyc", tracked)
        self.assertEqual((target / "build.log").read_text(), "built\n")
        self.assertEqual((target / "__pycache__" / "build.cpython-312.pyc").read_text(), "bytecode\n")
