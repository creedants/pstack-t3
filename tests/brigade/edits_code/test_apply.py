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
import subprocess
import sys

from ..support import BrigadeTest, setUpModule


def block(number, path, old, new):
    return (f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n```\n{old}\n```\n\n"
            f"New text:\n\n```\n{new}\n```\n")


def report(*blocks):
    return "Verdict: pass with edits\n\n## Edits\n\n" + "\n".join(blocks)


class ApplyTest(BrigadeTest):
    def git(self, *args, cwd=None):
        result = subprocess.run(["git", *(str(a) for a in args)], cwd=str(cwd or self.project),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def worktrees(self):
        return self.git("worktree", "list", "--porcelain")

    def branches(self):
        return self.git("branch", "--list").splitlines()

    def head(self):
        return self.git("rev-parse", "HEAD")

    def status(self):
        return self.git("status", "--porcelain")

    def apply(self, *args):
        return self.run_raw("edits", "apply", *args)

    def test_clean_apply_writes_only_the_edit_and_build_output_fails_when_apply_does_not_exist(self):
        # Processes: git builds the fixture project; edits apply runs the build script.
        reviewed = self.edits_store()
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello"),
                                                  block("2", "src/greeting.txt", "wrold", "world")))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        before_head, before_status = self.head(), self.status()
        before_pass = self.pass_rows()
        target = self.root / "wt-apply"
        code, out, err = self.apply("D1", "--report", "D1-review-1.md", "--from", reviewed,
                                    "--branch", "d1-applied", "--worktree", str(target))
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "D1: review edits applied on d1-applied")
        self.assertTrue(lines[1].startswith("head "))
        new_head = lines[1].split(" ", 1)[1]
        self.assertEqual(lines[2], f"worktree {target.resolve()}")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=target), new_head)
        names = self.git("diff", "--name-only", reviewed, new_head).splitlines()
        self.assertEqual(sorted(names), ["out/greeting.txt", "src/greeting.txt"])
        self.assertEqual(self.git("show", f"{new_head}:src/greeting.txt"), self.FIXED.strip())
        self.assertEqual(self.git("show", f"{new_head}:out/greeting.txt"), self.FIXED.upper().strip())
        self.assertEqual(self.head(), before_head)
        self.assertEqual((self.project / "src/greeting.txt").read_text(), self.SOURCE)
        self.assertEqual(self.status(), before_status)
        self.assertEqual(self.pass_rows(), before_pass)
        self.assertEqual(self.edits_rows(), [])
        message = self.git("log", "-1", "--format=%s%n%b", new_head, cwd=target)
        self.assertIn("D1: apply the review's edits", message)
        self.assertIn("reports/D1-review-1.md", message)
        self.assertIn(reviewed, message)

    def test_apply_refuses_a_doubled_old_text_fails_when_the_count_is_not_checked(self):
        # Processes: git builds the fixture project; edits apply refuses before creating anything.
        reviewed = self.edits_store()
        self.commit_files({"src/greeting.txt": "second and second\n"})
        doubled = self.head()
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "second", "SECOND")))
        target = self.root / "wt-doubled"
        code, out, err = self.apply("D1", "--report", "D1-review-1.md", "--from", doubled,
                                    "--branch", "d1-doubled", "--worktree", str(target))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("D1: edits not applied, ", err)
        self.assertNotIn("d1-doubled", "".join(self.branches()))
        self.assertNotIn(str(target), self.worktrees())
        self.assertFalse(target.exists())

    def test_apply_cleans_up_after_a_failing_build_fails_when_build_failures_leave_a_worktree(self):
        # Processes: git builds the fixture project; edits apply runs a failing build script.
        failing = ('import sys\nprint("build broke")\nsys.exit(2)\n')
        reviewed = self.edits_store(**{"tools/build.py": failing})
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello"),
                                                  block("2", "src/greeting.txt", "wrold", "world")))
        target = self.root / "wt-failing"
        code, out, err = self.apply("D1", "--report", "D1-review-1.md", "--from", reviewed,
                                    "--branch", "d1-failing", "--worktree", str(target))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("FAIL build: ", err.strip().splitlines()[-1])
        self.assertNotIn("d1-failing", "".join(self.branches()))
        self.assertNotIn(str(target), self.worktrees())
        self.assertFalse(target.exists())

    def test_apply_refuses_a_generated_path_fails_when_generated_output_is_edited(self):
        # Processes: git and land build the fixture project and its contract; edits apply reads the contract.
        self.git_project()
        self.init_landing("--generated", "out")
        self.landing_env()
        reviewed = self.edits_store()
        self.review_file("D1-review-1.md", report(block("1", "out/greeting.txt", "HELO", "HELLO")))
        target = self.root / "wt-generated"
        code, out, err = self.apply("D1", "--report", "D1-review-1.md", "--from", reviewed,
                                    "--branch", "d1-generated", "--worktree", str(target))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("out/greeting.txt", err)
        self.assertNotIn("d1-generated", "".join(self.branches()))
        self.assertNotIn(str(target), self.worktrees())
        self.assertFalse(target.exists())

    def test_apply_skips_a_missing_build_fails_when_a_missing_build_is_not_skipped(self):
        # Processes: git builds the fixture project; edits apply commits without running a build.
        self.git_project()
        reviewed = self.commit_files({"src/greeting.txt": self.SOURCE, "out/greeting.txt": self.SOURCE.upper(),
                                      "tools/build.py": self.BUILD, "tools/check.py": self.CHECK})
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello"),
                                                  block("2", "src/greeting.txt", "wrold", "world")))
        code, out, err = self.apply("D1", "--report", "D1-review-1.md", "--from", reviewed,
                                    "--branch", "d1-nobuild")
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0], "no build command recorded; the build was skipped")
        self.assertEqual(lines[1], "D1: review edits applied on d1-nobuild")
        new_head = lines[2].split(" ", 1)[1]
        worktree = lines[3].split(" ", 1)[1]
        try:
            names = self.git("diff", "--name-only", reviewed, new_head).splitlines()
            self.assertEqual(names, ["src/greeting.txt"])
        finally:
            subprocess.run(["git", "-C", str(self.project), "worktree", "remove", "--force", worktree],
                           capture_output=True)
            subprocess.run(["git", "-C", str(self.project), "branch", "-D", "d1-nobuild"], capture_output=True)
            subprocess.run(["git", "-C", str(self.project), "worktree", "prune"], capture_output=True)

    def test_apply_leaves_setup_output_uncommitted_fails_when_setup_output_is_committed(self):
        # Processes: git and land build the fixture project and its contract; edits apply runs its setup script.
        setup = f"{shlex.quote(sys.executable)} tools/setup.py"
        self.git_project()
        self.init_landing("--setup", setup)
        self.landing_env()
        reviewed = self.edits_store(**{"tools/setup.py": 'import pathlib\npathlib.Path("setup-out.txt").write_text("setup\\n")\n'})
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello"),
                                                  block("2", "src/greeting.txt", "wrold", "world")))
        target = self.root / "wt-setup"
        code, out, err = self.apply("D1", "--report", "D1-review-1.md", "--from", reviewed,
                                    "--branch", "d1-setup", "--worktree", str(target))
        self.assertEqual(code, 0, err)
        lines = out.strip().splitlines()
        new_head = lines[1].split(" ", 1)[1]
        names = self.git("diff", "--name-only", reviewed, new_head).splitlines()
        self.assertEqual(sorted(names), ["out/greeting.txt", "src/greeting.txt"])
        self.assertTrue((target / "setup-out.txt").is_file())
        tracked = self.git("ls-tree", "-r", "--name-only", new_head, cwd=target).splitlines()
        self.assertNotIn("setup-out.txt", tracked)
