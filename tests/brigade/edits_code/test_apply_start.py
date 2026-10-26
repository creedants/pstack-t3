if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from ..support import BrigadeTest, SCRIPT, _owner_words, call_in_process, setUpModule
from . import test_apply


class ApplyStartTest(BrigadeTest):
    # Helpers come from ApplyTest by reference. Subclassing it would run its tests a second time here.
    git = test_apply.ApplyTest.git
    worktrees = test_apply.ApplyTest.worktrees
    branches = test_apply.ApplyTest.branches
    standing = test_apply.ApplyTest.standing
    contract = test_apply.ApplyTest.contract
    one_edit = test_apply.ApplyTest.one_edit
    edit_args = test_apply.ApplyTest.edit_args

    def apply_with_failing_git(self, args, fails):
        """Run edits apply in this process. A git call that fails(argv) accepts cannot start, and the others run."""
        real_run = subprocess.run

        def run(argv, *rest, **kwargs):
            if fails(argv):
                raise OSError(12, "Cannot allocate memory")
            return real_run(argv, *rest, **kwargs)

        with mock.patch("subprocess.run", new=run):
            return call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                  *_owner_words(self.at), "edits", "apply", *args])

    def test_apply_cleans_up_after_a_worktree_add_that_cannot_start_fails_when_the_new_branch_is_left(self):
        # Processes: git builds the fixture project; edits apply runs in this process while starting git worktree add fails.
        self.contract()
        reviewed = self.edits_store()
        self.one_edit()
        adds = lambda argv: argv[0] == "git" and "worktree" in argv and "add" in argv
        temp_dirs = lambda: set(Path(tempfile.gettempdir()).glob("brigade-D1-edits-*"))
        for label, explicit in (("explicit", True), ("made", False)):
            with self.subTest(label):
                branch, target = f"d1-start-{label}", self.root / f"wt-start-{label}"
                args = ["D1", "--report", "D1-review-1.md", "--from", reviewed, "--branch", branch]
                if explicit:
                    args += ["--worktree", str(target)]
                before, before_temp = self.standing(), temp_dirs()
                outcome = self.apply_with_failing_git(args, adds)
                err = outcome.stderr_text()
                self.assertEqual(outcome.code, 1, err)
                self.assertEqual(len(err.strip().splitlines()), 1)
                self.assertIn("cannot run git", err)
                self.assertEqual(self.standing(), before)
                self.assertFalse(os.path.lexists(target))
                self.assertEqual(temp_dirs(), before_temp)

    def test_apply_names_a_branch_it_cannot_remove_fails_when_cleanup_is_silent_about_it(self):
        # Processes: git builds the fixture project; edits apply runs in this process and git cannot start after the branch exists.
        self.contract()
        reviewed = self.edits_store()
        self.one_edit()
        made = []

        def fails_after_the_branch(argv):
            if made:
                return True
            if "branch" in argv and "--" in argv:
                made.append(argv)
            return False

        branch = "d1-persist"
        args = ["D1", "--report", "D1-review-1.md", "--from", reviewed, "--branch", branch,
                "--worktree", str(self.root / "wt-persist")]
        outcome = self.apply_with_failing_git(args, fails_after_the_branch)
        err = outcome.stderr_text()
        self.assertEqual(outcome.code, 1, err)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn(f"cleanup incomplete: branch {branch}", err)
        self.assertIn(branch, "".join(self.branches()))
