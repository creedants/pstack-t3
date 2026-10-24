if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import tempfile
import unittest
from .support import FOUR_PATHS, GitRepo, LIGHT_PROJECT, LIGHT_USER, PLAYBOOK_NAMES, ROOT_ALIASES, Repo, escalated, run_mode, setUpModule
from .test_mode_command import open_tracked


class ModeCommandTest(unittest.TestCase):
    def test_double_star_skips_a_migrations_file_name(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(directory)
            completed = repo.run("--paths", "docs/migrations.md")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, LIGHT_PROJECT)


    def test_untracked_file_lease_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(directory)
            completed = repo.run("--paths", "t3/added/brigade/scripts/brigade.py")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, escalated("t3/added/brigade/scripts/brigade.py"))


    def test_star_does_not_cross_a_slash(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(
                directory,
                tracked=("scripts/tools/x.py", "scripts/install.py"),
                escalate=["scripts/*.py"],
            )
            nested = repo.run("--paths", "scripts/tools/x.py")
            direct = repo.run("--paths", "scripts/install.py")
        self.assertEqual(nested.returncode, 0, nested.stderr)
        self.assertEqual(nested.stdout, LIGHT_PROJECT)
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertEqual(direct.stdout, escalated("scripts/install.py"))


    def test_lease_leaving_the_repository_exits_1(self):
        with tempfile.TemporaryDirectory() as directory:
            Repo(directory)
            completed = run_mode(directory, "--paths", "../x")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "error: lease path '../x' leaves the repository\n")


    def test_lease_aliases_normalize(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(directory, tracked=(), escalate=["a/b", "b"])
            dotted = repo.run("--paths", "./a//b/")
            parent = repo.run("--paths", "a/../b")
        self.assertEqual(dotted.returncode, 0, dotted.stderr)
        self.assertEqual(dotted.stdout, escalated("a/b"))
        self.assertEqual(parent.returncode, 0, parent.stderr)
        self.assertEqual(parent.stdout, escalated("b"))


    def test_paths_without_an_escalate_list_skip_git(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = run_mode(directory, "--paths", "t3/added/landing")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, LIGHT_USER)


    def test_paths_outside_git_exit_2(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "light", "escalate": list(FOUR_PATHS)})
            completed = run_mode(directory, "--paths", "t3/added/landing")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            completed.stderr,
            f"error: --paths needs a git checkout to list tracked files, and {directory} is not one\n",
        )


    def test_escalate_pattern_leaving_the_repository_exits_1(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"escalate": ["../x"]})
            completed = run_mode(directory)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.project}: escalate pattern '../x' leaves the repository\n",
        )


    def test_root_normalized_escalate_patterns_keep_mode_resolution(self):
        for pattern in ROOT_ALIASES:
            with self.subTest(pattern=pattern):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    repo.put(repo.project, {"mode": "light", "escalate": [pattern]})
                    completed = run_mode(directory)
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, LIGHT_PROJECT)
                self.assertEqual(completed.stderr, "")


    def test_root_normalized_escalate_patterns_do_not_match_tracked_files(self):
        for pattern in ROOT_ALIASES:
            with self.subTest(pattern=pattern):
                with tempfile.TemporaryDirectory() as directory:
                    repo = GitRepo(directory, ("scripts/install.py",))
                    repo.put(repo.project, {"mode": "light", "escalate": [pattern]})
                    root = repo.run("--paths", ".")
                    tracked = repo.run("--paths", "scripts/install.py")
                for completed in (root, tracked):
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(completed.stdout, LIGHT_PROJECT)
                    self.assertEqual(completed.stderr, "")


    def test_escalate_pattern_aliases_normalize_before_segment_matching(self):
        for pattern in (" ./scripts//install.py/ ", "scripts/old/../install.py", "scripts\\install.py"):
            with self.subTest(pattern=pattern):
                with tempfile.TemporaryDirectory() as directory:
                    repo = GitRepo(directory, ("scripts/install.py", "scripts/tools/install.py"))
                    repo.put(repo.project, {"mode": "light", "escalate": [pattern]})
                    completed = repo.run("--paths", "scripts/install.py")
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(
                    completed.stdout,
                    "Mode: full\nMode source: escalated: lease covers scripts/install.py\n",
                )
                self.assertEqual(completed.stderr, "")
        with tempfile.TemporaryDirectory() as directory:
            repo = GitRepo(directory, ("scripts/install.py", "scripts/tools/install.py"))
            repo.put(repo.project, {"mode": "light", "escalate": [" ./scripts//*.py/ "]})
            completed = repo.run("--paths", "scripts/tools/install.py")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, LIGHT_PROJECT)
        self.assertEqual(completed.stderr, "")


    def test_rule1_reads_root_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(directory)
            completed = repo.run("--paths", "t3/added/landing", cwd=repo.directory / "docs")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, escalated("t3/added/landing/scripts/land.py"))


    def test_rule1_stable_first_hit(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(
                directory,
                tracked=("b/risk.py", "a/risk.py"),
                escalate=["b/*.py", "a/*.py"],
            )
            completed = repo.run("--paths", ".")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, escalated("a/risk.py"))


    def test_bug_fix_fix_under_light_prints_waivers(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = run_mode(directory, "--playbook", "bug-fix", "--attempt", "fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout,
            "Playbook: playbooks/bug-fix.md\n"
            "Mode: light\n"
            "Mode source: roles.json\n"
            "Attempt: fix\n"
            "Waived by mode: How, Why, Architect, Comment Sicko\n",
        )


    def test_bug_fix_fix_with_no_files_prints_default_waivers(self):
        with tempfile.TemporaryDirectory() as directory:
            Repo(directory)
            completed = run_mode(directory, "--playbook", "bug-fix", "--attempt", "fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout,
            "Playbook: playbooks/bug-fix.md\n"
            "Mode: light\n"
            "Mode source: default\n"
            "Attempt: fix\n"
            "Waived by mode: How, Why, Architect, Comment Sicko\n",
        )


    def test_feature_first_under_light(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = run_mode(directory, "--playbook", "feature", "--attempt", "first")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.splitlines()[-1],
            "Waived by mode: Arena, Interrogate, Comment Sicko",
        )


    def test_escalated_fix_prints_no_waivers(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = run_mode(directory, "--playbook", "bug-fix", "--attempt", "fix", "--send-backs", "2")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout,
            "Playbook: playbooks/bug-fix.md\n"
            "Mode: full\n"
            "Mode source: escalated: second send-back\n"
            "Attempt: fix\n",
        )


    def test_playbook_without_a_row_prints_no_waiver_line(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = run_mode(directory, "--playbook", "investigation", "--attempt", "first")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout,
            "Playbook: playbooks/investigation.md\nMode: light\nMode source: roles.json\nAttempt: first\n",
        )


    def test_playbook_without_attempt_exits_1(self):
        with tempfile.TemporaryDirectory() as directory:
            Repo(directory)
            completed = run_mode(directory, "--playbook", "bug-fix")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr, "error: --playbook and --attempt go together: pass both or neither\n")


    def test_attempt_without_playbook_exits_1(self):
        with tempfile.TemporaryDirectory() as directory:
            Repo(directory)
            completed = run_mode(directory, "--attempt", "fix")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr, "error: --playbook and --attempt go together: pass both or neither\n")


    def test_unknown_playbook_exits_1(self):
        with tempfile.TemporaryDirectory() as directory:
            Repo(directory)
            completed = run_mode(directory, "--playbook", "bugfix", "--attempt", "fix")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            completed.stderr,
            f"error: unknown playbook 'bugfix': expected one of: {PLAYBOOK_NAMES}\n",
        )


    def test_bad_attempt_exits_2(self):
        with tempfile.TemporaryDirectory() as directory:
            Repo(directory)
            completed = run_mode(directory, "--attempt", "retry")
        self.assertEqual(completed.returncode, 2)
