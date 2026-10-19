import tempfile
import unittest
from .support import FOUR_PATHS, GitRepo, LIGHT_PROJECT, LIGHT_USER, Repo, TRACKED, escalated, run_mode, setUpModule


def open_tracked(directory, tracked=TRACKED, escalate=None):
    repo = GitRepo(directory, tracked)
    repo.put(repo.project, {"mode": "light", "escalate": list(FOUR_PATHS if escalate is None else escalate)})
    return repo


class ModeCommandTest(unittest.TestCase):
    def test_user_light_prints_roles_json(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = run_mode(directory)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, LIGHT_USER)


    def test_project_light_prints_project_label(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "light"})
            completed = run_mode(directory)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, LIGHT_PROJECT)


    def test_no_files_prints_default(self):
        with tempfile.TemporaryDirectory() as directory:
            Repo(directory)
            completed = run_mode(directory)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Mode: light\nMode source: default\n")


    def test_unset_mode_falls_back_to_light_not_full(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            unset = run_mode(directory)
            repo.put(repo.user, {"mode": "full"})
            user_full = run_mode(directory)
        self.assertEqual(unset.returncode, 0, unset.stderr)
        self.assertEqual(unset.stdout, "Mode: light\nMode source: default\n")
        self.assertEqual(user_full.returncode, 0, user_full.stderr)
        self.assertEqual(user_full.stdout, "Mode: full\nMode source: roles.json\n")


    def test_coordinator_light_over_project_full(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full"})
            completed = run_mode(directory, "--coordinator-mode", "light")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Mode: light\nMode source: restaurant.json\n")


    def test_brief_full_over_project_light(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "light"})
            completed = run_mode(directory, "--brief-mode", "full")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Mode: full\nMode source: brief\n")


    def test_session_light_over_project_full(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full"})
            completed = run_mode(directory, "--session-mode", "light")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Mode: light\nMode source: session\n")


    def test_brief_full_over_session_light(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full"})
            completed = run_mode(directory, "--brief-mode", "full", "--session-mode", "light")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Mode: full\nMode source: brief\n")


    def test_second_send_back_escalates(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = run_mode(directory, "--send-backs", "2")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Mode: full\nMode source: escalated: second send-back\n")


    def test_first_send_back_stays_light(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = run_mode(directory, "--send-backs", "1")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, LIGHT_USER)


    def test_durable_reason_escalates(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = run_mode(directory, "--escalated", "lock order")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Mode: full\nMode source: escalated: lock order\n")


    def test_durable_reason_wins_over_send_backs(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = run_mode(directory, "--escalated", "lock order", "--send-backs", "3")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Mode: full\nMode source: escalated: lock order\n")


    def test_escalation_overrides_brief_light(self):
        with tempfile.TemporaryDirectory() as directory:
            Repo(directory)
            completed = run_mode(directory, "--brief-mode", "light", "--send-backs", "2")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Mode: full\nMode source: escalated: second send-back\n")


    def test_empty_durable_reason_exits_1(self):
        with tempfile.TemporaryDirectory() as directory:
            Repo(directory)
            completed = run_mode(directory, "--escalated", "")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "error: --escalated needs a one-line reason\n")


    def test_durable_reason_with_a_break_exits_1(self):
        for text in ("\n", "lock\norder", "\r", "lock\rorder"):
            with self.subTest(text=text):
                with tempfile.TemporaryDirectory() as directory:
                    Repo(directory)
                    completed = run_mode(directory, "--escalated", text)
                self.assertEqual(completed.returncode, 1)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, "error: --escalated needs a one-line reason\n")


    def test_negative_send_backs_exits_2(self):
        with tempfile.TemporaryDirectory() as directory:
            Repo(directory)
            completed = run_mode(directory, "--send-backs", "-1")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")


    def test_file_lease_equal_to_a_pattern_escalates(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(directory)
            completed = repo.run("--paths", "scripts/install.py")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, escalated("scripts/install.py"))


    def test_directory_lease_escalates_on_a_tracked_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(directory)
            completed = repo.run("--paths", "t3/added/landing")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, escalated("t3/added/landing/scripts/land.py"))


    def test_sibling_file_lease_stays_light(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(directory)
            completed = repo.run("--paths", "t3/added/landing/SKILL.md")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, LIGHT_PROJECT)


    def test_root_lease_escalates_on_the_first_tracked_match(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(directory)
            completed = repo.run("--paths", ".")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, escalated("db/migrations/0001.sql"))


    def test_double_star_matches_a_migrations_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = open_tracked(directory)
            completed = repo.run("--paths", "db")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, escalated("db/migrations/0001.sql"))
