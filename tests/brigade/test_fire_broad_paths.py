import subprocess

from .support import (
    setUpModule,
    BrigadeTest,
    SCRIPT,
    _owner_words,
    call,
)


class FireBroadPathsTest(BrigadeTest):
    def repo(self, names):
        run = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        for name in names:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name + "\n")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.init_landing()
        self.landing_env()
        self.open()

    def fire(self, summary, paths):
        self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at),
                                      "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                      "--paths", paths])
        self.assertEqual(outcome.code, 0, outcome.stderr_text())
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], paths)
        self.assertEqual(outcome.stdout_text().splitlines()[0], "D1 (lease L1 held by perf/D1)")
        return outcome.stderr_text().strip()

    @staticmethod
    def names(count):
        return [f"src/{letter}.py" for letter in "abcdefghijkl"[:count]]

    def test_a_broad_directory_lists_the_quoted_files_in_sorted_order(self):
        self.repo(self.names(8))
        stderr = self.fire("touches `src/h.py` and `src/a.py`", "src")
        self.assertEqual(stderr, "fire: src holds 8 tracked files; suggest src/a.py, src/h.py")

    def test_a_broad_directory_with_no_quoted_file_prints_the_file_level_advice(self):
        self.repo(self.names(8))
        stderr = self.fire("touches nothing", "src")
        self.assertEqual(stderr, "fire: src holds 8 tracked files; name a file under it in the ticket summary to lease at file level")

    def test_a_directory_with_exactly_five_tracked_files_prints_no_line(self):
        self.repo(self.names(5))
        self.assertEqual(self.fire("touches `src/a.py`", "src"), "")

    def test_a_file_path_whose_parent_directory_is_broad_prints_no_line(self):
        self.repo(self.names(8))
        self.assertEqual(self.fire("touches `src/a.py`", "src/a.py"), "")

    def test_a_broad_directory_lists_five_of_six_quoted_files_in_sorted_order(self):
        self.repo(self.names(12))
        stderr = self.fire("touches `src/f.py` `src/e.py` `src/d.py` `src/c.py` `src/b.py` `src/a.py`", "src")
        self.assertEqual(stderr, "fire: src holds 12 tracked files; suggest src/a.py, src/b.py, "
                                 "src/c.py, src/d.py, src/e.py")
