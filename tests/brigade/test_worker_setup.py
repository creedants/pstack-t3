if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import BrigadeTest, setUpModule


class WorkerSetupTest(BrigadeTest):
    BULLET = ("- Before your first build or test, run the project's setup command through "
              "`land.py slot --` unless the worktree already has it run: make deps.")

    def verify_section(self, text):
        return self.brief_body(text).split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]

    def setup_line(self, verify):
        return [line for line in verify.splitlines() if "setup command through" in line]

    def test_setup_command_keeps_the_verify_command_first_fails_when_setup_displaces_it(self):
        self.started("--setup", "make deps")
        self.brigade("set", "--thread", "thread-coord")
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a",
                            "--verify", "v", "--paths", "README.md", "--lease", "L1", "--base", "origin/main")
        verify = self.verify_section(text)
        self.assertIn(self.BULLET, verify)
        self.assertTrue(verify.startswith("- v\n" + self.BULLET + "\n"))

    def test_missing_setup_adds_no_bullet_until_the_contract_gains_one_fails_when_the_next_brief_stays_without_it(self):
        self.started()
        self.brigade("set", "--thread", "thread-coord")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "s", "--paths", "src/b.py")
        first = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a",
                             "--verify", "v", "--paths", "README.md", "--lease", "L1", "--base", "origin/main")
        self.assertEqual(self.setup_line(self.verify_section(first)), [])
        self.land("init", "--trunk", "lane", "--mode", "local", "--base", "main", "--setup", "make deps")
        second = self.brigade("brief", "D2", "--contract", "none", "--goal", "g", "--acceptance", "a",
                              "--verify", "v", "--base", "origin/main")
        verify = self.verify_section(second)
        self.assertIn(self.BULLET, verify)
        self.assertTrue(verify.startswith("- v\n" + self.BULLET + "\n"))

    def test_setup_bullet_reads_byte_identical_across_two_briefs_fails_when_a_second_brief_differs(self):
        self.started("--setup", "make deps")
        self.brigade("set", "--thread", "thread-coord")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "s", "--paths", "src/b.py")
        first = self.verify_section(self.brigade("brief", "D1", "--contract", "none", "--goal", "g",
                                                 "--acceptance", "a", "--verify", "v", "--base", "origin/main"))
        second = self.verify_section(self.brigade("brief", "D2", "--contract", "none", "--goal", "g",
                                                  "--acceptance", "a", "--verify", "v", "--base", "origin/main"))
        self.assertEqual(self.setup_line(first), [self.BULLET])
        self.assertEqual(self.setup_line(second), [self.BULLET])
