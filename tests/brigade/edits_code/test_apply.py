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
import shlex
import subprocess
import sys
from unittest import mock

from ..support import SCRIPT, BrigadeTest, _owner_words, call_in_process, run_globals, setUpModule


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

    def contract(self):
        self.init_landing()
        self.landing_env()

    def apply(self, *args):
        return self.run_raw("edits", "apply", *args)

    def test_clean_apply_writes_only_the_edit_and_build_output_fails_when_apply_does_not_exist(self):
        # Processes: git builds the fixture project; edits apply runs the build script.
        self.contract()
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
        self.contract()
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
        self.contract()
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
        # Processes: git and land build the fixture project and its contract; edits apply commits without running a build.
        self.init_landing()
        self.landing_env()
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

    def assert_refused_leaving_nothing(self, target, branch, code, out, err, before):
        self.assertEqual(code, 1, err)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertEqual((self.branches(), self.worktrees()), before)

    def standing(self):
        return (self.branches(), self.worktrees())

    def edit_args(self, reviewed, branch, target):
        return ("D1", "--report", "D1-review-1.md", "--from", reviewed, "--branch", branch, "--worktree", str(target))

    def one_edit(self):
        self.review_file("D1-review-1.md", report(block("1", "src/greeting.txt", "helo", "hello")))

    def test_apply_refuses_when_the_contract_read_fails_fails_when_a_failed_read_means_no_generated_paths(self):
        # Processes: git builds the fixture project; edits apply reads the contract in this process with land.py faked.
        self.init_landing("--generated", "out")
        self.landing_env()
        reviewed = self.edits_store()
        self.review_file("D1-review-1.md", report(block("1", "out/greeting.txt", "HELO", "HELLO")))
        ok = '{"generated": ["out"]}'
        for label, result in (("nonzero", (1, ok)), ("not json", (0, "no json")), ("not an object", (0, "[]")),
                              ("no list", (0, '{"generated": "out"}')), ("bad item", (0, '{"generated": [1]}'))):
            with self.subTest(label):
                fake = lambda root, *args, result=result: subprocess.CompletedProcess([], result[0], stdout=result[1], stderr="")
                target = self.root / f"wt-{label.replace(' ', '-')}"
                before = self.standing()
                with mock.patch.dict(run_globals(self, SCRIPT), {"land_result": fake}):
                    outcome = call_in_process(self, SCRIPT, [
                        "--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), "edits", "apply",
                        *self.edit_args(reviewed, f"d1-{label.replace(' ', '-')}", target)])
                self.assertEqual(outcome.code, 1, outcome.stderr_text())
                self.assertEqual(len(outcome.stderr_text().strip().splitlines()), 1)
                self.assertIn("generated paths", outcome.stderr_text())
                self.assertEqual(self.standing(), before)
                self.assertFalse(target.exists())

    def test_apply_refuses_an_existing_worktree_path_fails_when_a_user_directory_is_removed(self):
        # Processes: git builds the fixture project; edits apply refuses before it creates anything.
        self.contract()
        reviewed = self.edits_store(**{"tools/build.py": "import sys\nsys.exit(2)\n"})
        self.one_edit()
        empty = self.root / "wt-empty"
        empty.mkdir()
        away = self.root / "wt-away"
        away.mkdir()
        link = self.root / "wt-link"
        link.symlink_to(away)
        line = self.root / "wt-line\nbreak"
        line.mkdir()
        for target in (empty, link, line):
            with self.subTest(target.name):
                before = self.standing()
                code, out, err = self.apply(*self.edit_args(reviewed, "d1-existing", target))
                self.assert_refused_leaving_nothing(target, "d1-existing", code, out, err, before)
                self.assertTrue(os.path.lexists(target))
        self.assertIn("wt-line\\nbreak", err)
        self.assertEqual(list(empty.iterdir()), [])
        self.assertTrue(away.is_dir())

    def test_apply_refuses_a_branch_name_with_a_newline_on_one_line_fails_when_the_name_prints_raw(self):
        # Processes: git builds the fixture project; edits apply refuses the branch name before it creates anything.
        self.contract()
        reviewed = self.edits_store()
        self.one_edit()
        before = self.standing()
        target = self.root / "wt-bad-branch"
        code, out, err = self.apply(*self.edit_args(reviewed, "d1-bad\nbranch", target))
        self.assert_refused_leaving_nothing(target, "d1-bad\nbranch", code, out, err, before)
        self.assertIn("d1-bad\\nbranch", err)
        self.assertFalse(target.exists())

    def test_apply_refuses_a_worktree_path_inside_the_repository_fails_when_it_writes_into_the_checkout(self):
        # Processes: git builds the fixture project and a second worktree; edits apply refuses before it creates anything.
        self.contract()
        reviewed = self.edits_store()
        self.one_edit()
        other, line = self.root / "wt-other", self.root / "wt-line\nbreak"
        for name, path in (("other", other), ("linebreak", line)):
            self.git("worktree", "add", "-q", "-b", name, path)
            self.addCleanup(subprocess.run, ["git", "-C", str(self.project), "worktree", "remove", "--force", str(path)],
                            capture_output=True)
        for target in (self.project / "nested", other / "nested", line / "nested", self.project / ".git" / "nested"):
            with self.subTest(str(target.parent.name)):
                before, status = self.standing(), self.status()
                code, out, err = self.apply(*self.edit_args(reviewed, "d1-inside", target))
                self.assert_refused_leaving_nothing(target, "d1-inside", code, out, err, before)
                self.assertFalse(target.exists())
                self.assertEqual(self.status(), status)

    def test_apply_commits_only_edited_and_built_paths_fails_when_setup_stages_a_file(self):
        # Processes: git and land build the fixture project and its contract; edits apply runs a setup that stages a file.
        setup = f"{shlex.quote(sys.executable)} tools/setup.py"
        self.init_landing("--setup", setup)
        self.landing_env()
        staging = ('import pathlib, subprocess\npathlib.Path("setup-out.txt").write_text("setup\\n")\n'
                   'subprocess.run(["git", "add", "setup-out.txt"], check=True)\n')
        reviewed = self.edits_store(**{"tools/setup.py": staging})
        self.one_edit()
        target = self.root / "wt-staged"
        code, out, err = self.apply(*self.edit_args(reviewed, "d1-staged", target))
        self.assertEqual(code, 0, err)
        new_head = out.strip().splitlines()[1].split(" ", 1)[1]
        names = self.git("diff", "--name-only", reviewed, new_head).splitlines()
        self.assertEqual(sorted(names), ["out/greeting.txt", "src/greeting.txt"])

    def test_apply_cleans_up_a_locked_worktree_fails_when_setup_locks_it_and_the_build_fails(self):
        # Processes: git and land build the fixture project and its contract; edits apply runs a setup that locks the worktree.
        self.init_landing("--setup", "git worktree lock .")
        self.landing_env()
        reviewed = self.edits_store(**{"tools/build.py": "import sys\nsys.exit(2)\n"})
        self.one_edit()
        stale = self.root / "wt-stale"
        self.git("worktree", "add", "-q", "-b", "stale", stale)
        subprocess.run(["rm", "-rf", str(stale)], check=True)
        before = self.standing()
        target = self.root / "wt-locked"
        code, out, err = self.apply(*self.edit_args(reviewed, "d1-locked", target))
        self.assertEqual(code, 1, err)
        self.assertNotIn("cleanup incomplete", err)
        self.assertEqual(self.standing(), before)
        self.assertFalse(target.exists())
        self.assertIn("prunable", self.worktrees())

    def test_apply_fails_on_a_rejecting_hook_fails_when_the_commit_skips_hooks(self):
        # Processes: git builds the fixture project; edits apply commits through a pre-commit hook that exits 44.
        self.contract()
        reviewed = self.edits_store()
        self.one_edit()
        hook = self.project / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho hook says no\nexit 44\n")
        hook.chmod(0o755)
        before = self.standing()
        target = self.root / "wt-hook"
        code, out, err = self.apply(*self.edit_args(reviewed, "d1-hook", target))
        self.assertEqual(code, 1, err)
        self.assertIn("cannot commit", err)
        self.assertEqual(self.standing(), before)
        self.assertFalse(target.exists())

    def test_apply_cleans_up_a_failed_add_fails_when_the_worktree_stays_registered(self):
        # Processes: git builds the fixture project; edits apply runs a post-checkout hook that rejects the new worktree.
        self.contract()
        reviewed = self.edits_store()
        self.one_edit()
        hook = self.project / ".git" / "hooks" / "post-checkout"
        commit = "git -c user.name=hook -c user.email=hook@localhost commit -q --allow-empty -m moved\n"
        for label, body in (("rejects", ""), ("commits then rejects", commit)):
            with self.subTest(label):
                hook.write_text("#!/bin/sh\necho hook says no\n" + body + "exit 44\n")
                hook.chmod(0o755)
                before = self.standing()
                target = self.root / f"wt-checkout-hook-{len(label)}"
                code, out, err = self.apply(*self.edit_args(reviewed, "d1-checkout-hook", target))
                self.assertEqual(code, 1, err)
                self.assertIn("cannot create worktree", err)
                self.assertNotIn("cleanup incomplete", err)
                self.assertEqual(self.standing(), before)
                self.assertFalse(target.exists())

    def test_apply_commits_only_edited_and_built_paths_fails_when_a_commit_hook_stages_a_file(self):
        # Processes: git and land build the fixture project and its contract; edits apply commits through a pre-commit hook that stages setup output.
        setup = f"{shlex.quote(sys.executable)} tools/setup.py"
        self.init_landing("--setup", setup)
        self.landing_env()
        reviewed = self.edits_store(**{"tools/setup.py": 'import pathlib\npathlib.Path("setup-out.txt").write_text("setup\\n")\n'})
        self.one_edit()
        hook = self.project / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\ngit add setup-out.txt\n")
        hook.chmod(0o755)
        before = self.standing()
        target = self.root / "wt-hook-stage"
        code, out, err = self.apply(*self.edit_args(reviewed, "d1-hook-stage", target))
        self.assertEqual(code, 1, err)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("setup-out.txt", err)
        self.assertEqual(self.standing(), before)
        self.assertFalse(target.exists())
