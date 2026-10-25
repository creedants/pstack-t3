if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import shlex
import sys

from ..support import BrigadeTest, setUpModule


SETUP_OK = ('import pathlib\npathlib.Path("deps").mkdir(exist_ok=True)\n'
            'pathlib.Path("deps/ok").write_text("ok\\n")\n')

BUILD_NEEDS_SETUP = ('import pathlib, sys\nif not pathlib.Path("deps/ok").exists():\n'
                     '    print("deps is missing")\n    sys.exit(5)\n'
                     'pathlib.Path("out").mkdir(exist_ok=True)\n'
                     'pathlib.Path("out/greeting.txt").write_text(pathlib.Path("src/greeting.txt").read_text().upper())\n')

SETUP_FAIL = ('import pathlib, sys\npathlib.Path("setup-touched.txt").write_text("touched\\n")\n'
              'sys.exit(9)\n')

SETUP_REWRITES_TRACKED = 'import pathlib\npathlib.Path("src/greeting.txt").write_text("rewritten\\n")\n'


def block(number, path, old, new):
    return (f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n```\n{old}\n```\n\n"
            f"New text:\n\n```\n{new}\n```\n")


def report(*blocks):
    return "Verdict: pass with edits\n\n## Edits\n\n" + "\n".join(blocks)


class ReplaySetupTest(BrigadeTest):
    def contract(self, *setup):
        self.git_project()
        self.init_landing(*(("--setup", setup[0]) if setup else ()))
        self.landing_env()

    def setup_command(self):
        return f"{shlex.quote(sys.executable)} tools/setup.py"

    def build_command(self):
        return f"{shlex.quote(sys.executable)} tools/build.py"

    def test_setup_runs_before_the_build_in_the_replay_copy_fails_when_the_build_sees_no_setup_output(self):
        # Processes: git and land build the fixture project and its contract; edits verify runs its setup and build scripts.
        """Fails at the base, where the setup never runs and the build exits 5."""
        setup = self.setup_command()
        self.contract(setup)
        reviewed = self.edits_store(build=self.build_command(), **{"tools/setup.py": SETUP_OK,
                                                                   "tools/build.py": BUILD_NEEDS_SETUP})
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello"),
                                                  block("2", "src/greeting.txt", "wrold", "world")))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, "built out/greeting.txt\nedited src/greeting.txt\nok check\n"
                             f"{self.verified(reviewed, head)}", ""))
        self.assertFalse((self.project / "deps").exists())

    def test_a_failing_setup_stops_the_verify_with_one_line_fails_when_the_build_runs_after_a_failed_setup(self):
        # Processes: git and land build the fixture project and its contract; edits verify runs its setup script.
        """Fails at the base, where the setup never runs and the build and check pass."""
        setup = self.setup_command()
        self.contract(setup)
        reviewed = self.edits_store(build=self.build_command(), **{"tools/setup.py": SETUP_FAIL})
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello"),
                                                  block("2", "src/greeting.txt", "wrold", "world")))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL setup: {setup} exited 9", self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])
        self.assertFalse((self.project / "setup-touched.txt").exists())

    def test_a_setup_that_changes_a_tracked_path_stops_the_verify_fails_when_setup_output_is_left_in_the_copy(self):
        # Processes: git and land build the fixture project and its contract; edits verify runs its setup script.
        """Fails at the base, where the setup never runs and the build and check pass."""
        setup = self.setup_command()
        self.contract(setup)
        reviewed = self.edits_store(**{"tools/setup.py": SETUP_REWRITES_TRACKED})
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello"),
                                                  block("2", "src/greeting.txt", "wrold", "world")))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL setup: {setup} changed src/greeting.txt", self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_no_setup_command_keeps_the_lines_a_contract_without_setup_produced_before_this_change(self):
        # Processes: git and land build the fixture project and its contract; edits verify runs its tools' scripts.
        """Passes at the base by design, because a contract without setup keeps unchanged behavior."""
        self.contract()
        reviewed = self.edits_store()
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello"),
                                                  block("2", "src/greeting.txt", "wrold", "world")))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, "built out/greeting.txt\nedited src/greeting.txt\nok check\n"
                             f"{self.verified(reviewed, head)}", ""))
