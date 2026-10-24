if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import (
    setUpModule,
    SCRIPT,
    StoresTest,
    runpy,
    tempfile,
)


class GoalWriteTest(StoresTest):
    """A goal command that is killed or that fails partway through its writes."""

    ADD = ("goal", "add", "--name", "activity view", "--purpose", "One page", "--done", "It ships")
    NO_UNASSIGNED_WORK = "unassigned: 0 waiting, 0 in flight, 0 merged, 0 send-backs"

    def goals(self):
        return [row[:1] + row[2:] for row in self.rows("core", "goals.tsv")]

    def goal_log(self):
        return [row[2:] for row in self.rows("core", "log.tsv") if row[1] == "goal"]

    def test_a_goal_add_killed_inside_its_append_leaves_a_tail_that_goal_list_skips_and_the_next_goal_add_drops(self):
        self.open("core")
        died = self.child("die-inside-goal-append", "core", *self.ADD)
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertRegex((self.dir("core") / "goals.tsv").read_text(), r"\tclosed\nG1\t[^\t\n]+\tope$")
        self.assertEqual(self.brigade("core", "goal", "list"), f"no open or paused goals\n{self.NO_UNASSIGNED_WORK}")
        self.assertEqual(self.brigade("core", *self.ADD), "G1")
        self.assertEqual(self.goals(), [["G1", "open", "activity view", "One page", "It ships", "", ""]])
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"]])

    def test_a_goal_add_killed_before_its_log_row_leaves_the_goal_and_the_same_command_again_is_refused_and_adds_no_row(self):
        self.open("core")
        died = self.child("die-before-log", "core", *self.ADD)
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.goals(), [["G1", "open", "activity view", "One page", "It ships", "", ""]])
        self.assertEqual(self.goal_log(), [])
        self.assertEqual(self.brigade("core", *self.ADD, ok=False), "brigade: same name as G1 (open); nothing added")
        self.assertEqual(len(self.goals()), 1)
        self.assertEqual(self.goal_log(), [])
        self.assertEqual(self.brigade("core", "goal", "list").splitlines()[0],
                         "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")

    def test_a_goal_set_killed_before_its_log_row_leaves_the_new_state_and_the_same_command_again_prints_already(self):
        self.open("core")
        self.brigade("core", *self.ADD)
        died = self.child("die-before-log", "core", "goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.assertEqual(died.returncode, 9, died.stderr)
        state, closed = self.goals()[0][1], self.goals()[0][6]
        self.assertEqual((state, closed.split(" ", 1)[1]), ("done", "shipped"))
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"]])
        self.assertEqual(self.brigade("core", "goal", "set", "G1", "--state", "done", "--evidence", "shipped"), "G1 already done")
        self.assertEqual(self.goals()[0][6], closed)
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"]])

    def test_a_goal_set_whose_temp_file_write_fails_leaves_goals_tsv_and_the_log_as_they_were_and_no_temp_file(self):
        self.open("core")
        self.brigade("core", *self.ADD)
        before = self.files("core")
        module = runpy.run_path(str(SCRIPT))
        glob = module["run"].__globals__

        class FailingTempfile:
            @staticmethod
            def NamedTemporaryFile(*args, **kwargs):
                handle = tempfile.NamedTemporaryFile(*args, **kwargs)
                write = handle.write

                def fail(text):
                    write(text[:10])
                    handle.flush()
                    raise OSError(28, "No space left on device")

                handle.write = fail
                return handle

        glob["tempfile"] = FailingTempfile
        with self.assertRaises(OSError):
            glob["run"](["--at", str(self.dir("core")), "goal", "set", "G1", "--state", "paused", "--share", "2"])
        glob["tempfile"] = tempfile
        self.assertEqual(self.files("core"), before)
        self.assertEqual(self.brigade("core", "goal", "set", "G1", "--state", "paused", "--share", "2"), "G1 paused; share 2")
        self.assertEqual(self.goals(), [["G1", "paused", "activity view", "One page", "It ships", "2", ""]])
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"], ["G1", "paused", "activity view"]])
