if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    _run,
    ADMIN,
    os,
    Path,
    ProcessReason,
    repository_state,
    REVIEWER,
    sh,
    subprocess,
    sys,
    tempfile,
    unittest,
)
from .base import IdentityBase

class LandingTest(IdentityBase, LandingTestBase):
    def test_a_core_worktree_that_names_a_missing_directory_stops_status_land_resume_lease_init_and_submit(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        config = self.work / ".git" / "config"
        before = config.read_bytes()
        commands = (["status"], ["land"], ["resume"], ["lease", "list"],
                    ["init", "--trunk", "main", "--mode", "auto", "--check", "./check.sh"],
                    ["submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER])
        for repo in (self.work, self.base / "w1"):
            for command in commands:
                with self.subTest(repo=repo.name, command=command[0]):
                    self.assertEqual(self.run_land(repo, *command), (1, "", self.stray_worktree_line(gone)))
        self.assertEqual(config.read_bytes(), before)

    def test_a_core_worktree_that_names_a_directory_outside_the_repository_stops_status(self):
        self.init()
        sh("git", "config", "core.worktree", str(self.base), cwd=self.work)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(self.base)))

    def test_the_printed_command_quotes_a_config_path_that_has_a_space(self):
        self.work = self.base / "spaced work"
        sh("git", "clone", "-q", "origin.git", "spaced work", cwd=self.base)
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        quoted = "'" + str((self.work / ".git").resolve() / "config") + "'"
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(gone, quoted)))

    def test_a_core_worktree_that_names_the_checkout_does_not_stop_status(self):
        self.init(author_policy=None)
        for value in (str(self.work), ".."):
            with self.subTest(value=value):
                sh("git", "config", "core.worktree", value, cwd=self.work)
                self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty. author policy: any\n", ""))

    def test_a_relative_core_worktree_that_names_a_missing_directory_stops_status(self):
        self.init()
        sh("git", "config", "core.worktree", "../gone", cwd=self.work)
        for repo in (self.work, self.base):
            with self.subTest(cwd=repo.name):
                self.assertEqual(self.run_land(self.work, "status", cwd=repo), (1, "", self.stray_worktree_line("../gone")))

    def test_the_printed_command_run_in_the_checkout_removes_a_relative_core_worktree(self):
        self.init(author_policy=None)
        sh("git", "config", "core.worktree", "../gone", cwd=self.work)
        command = self.run_land(self.work, "status")[2].rstrip("\n").split("remove it with ", 1)[1]
        subprocess.run(command, shell=True, cwd=self.work, check=True, env=os.environ.copy())
        self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty. author policy: any\n", ""))

    def test_the_last_core_worktree_value_decides(self):
        self.init(author_policy=None)
        gone = self.base / "gone"
        sh("git", "config", "--add", "core.worktree", str(gone), cwd=self.work)
        sh("git", "config", "--file", str(self.work / ".git" / "config"), "--add", "core.worktree", str(self.work), cwd=self.base)
        self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty. author policy: any\n", ""))
        sh("git", "config", "--file", str(self.work / ".git" / "config"), "--add", "core.worktree", str(gone), cwd=self.base)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(gone)))

    def test_a_core_worktree_that_is_the_checkout_path_and_a_newline_stops_status_and_lease_claim(self):
        self.init()
        sh("git", "config", "core.worktree", str(self.work) + "\n", cwd=self.work)
        before = self.files_under(self.work / ".git"), self.files_under(self.base / "state")
        for command in (["status"], ["lease", "claim", "--holder", "r/D1", "--paths", "a.txt"]):
            with self.subTest(command=command[0]):
                self.assertEqual(self.run_land(self.work, *command), (1, "", self.stray_worktree_line(str(self.work) + "\\n")))
        self.assertEqual((self.files_under(self.work / ".git"), self.files_under(self.base / "state")), before)
        with self.raw_db() as db:
            self.assertEqual(db.execute("SELECT id FROM lease").fetchall(), [])

    def test_the_printed_command_removes_a_core_worktree_that_is_the_checkout_path_and_a_newline(self):
        self.init(author_policy=None)
        sh("git", "config", "core.worktree", str(self.work) + "\n", cwd=self.work)
        command = self.run_land(self.work, "status")[2].rstrip("\n").split("remove it with ", 1)[1]
        subprocess.run(command, shell=True, cwd=self.work, check=True, env=os.environ.copy())
        self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty. author policy: any\n", ""))

    def test_a_core_worktree_with_a_carriage_return_is_not_judged_as_the_directory_with_a_newline_there(self):
        self.init()
        (self.work / "a\nb").mkdir()
        sh("git", "config", "core.worktree", str(self.work / "a\rb"), cwd=self.work)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(str(self.work / "a") + "\\rb")))

    def test_the_core_worktree_line_is_one_line_when_the_value_holds_line_breaks_and_a_backslash(self):
        self.init()
        gone = str(self.base / "gone")
        sh("git", "config", "core.worktree", gone + "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\\", cwd=self.work)
        status, printed, complaint = self.run_land(self.work, "status")
        self.assertEqual((status, printed), (1, ""))
        self.assertEqual(complaint, self.stray_worktree_line(gone + r"\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\\"))
        self.assertEqual(len(complaint.splitlines()), 1)

    def test_a_core_worktree_of_300_letters_stops_status(self):
        self.init()
        sh("git", "config", "core.worktree", "x" * 300, cwd=self.work)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line("x" * 300)))

    def test_the_printed_command_is_one_line_and_runs_when_the_config_path_holds_a_newline_and_a_backslash(self):
        self.work = self.base / "new\nline\\work"
        sh("git", "clone", "-q", "origin.git", str(self.work), cwd=self.base)
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        top = str(self.base.resolve())
        shown = top + r"/new\nline\\work/.git/config"
        quoted = "\"$(printf %b '" + top + r"/new\0012line\0134work/.git/config" + "')\""
        status, printed, complaint = self.run_land(self.work, "status")
        self.assertEqual((status, printed, complaint), (1, "", self.stray_worktree_line(gone, quoted, shown)))
        self.assertEqual(len(complaint.splitlines()), 1)
        subprocess.run(complaint.rstrip("\n").split("remove it with ", 1)[1], shell=True, cwd=self.work, check=True, env=os.environ.copy())
        self.assertEqual(self.git_raw("config", "--get-all", "core.worktree", cwd=self.work).returncode, 1)

    def test_a_git_directory_whose_name_ends_in_a_space_is_read_under_that_name(self):
        self.work = self.base / "apart"
        store = self.base / "store "
        sh("git", "clone", "-q", "--separate-git-dir", str(store), "origin.git", "apart", cwd=self.base)
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        config = str(store.resolve() / "config")
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(gone, "'" + config + "'", config)))
