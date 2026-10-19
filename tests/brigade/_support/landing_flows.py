import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from .names import CLAUDE, CODEX, ROOT, SCRIPT, _owner_words
from .inprocess import call, run_globals


class LandingFlows:
    def renamed(self, hook=None):
        """Fire D1 on perf/d1, then run dish D1 --branch perf/d1-r2 in this process, calling hook at its lease list."""
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("set", "--thread", "coordinator")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                     "--branch", "perf/d1", "--paths", "src")
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        original = glob["land_result"]

        def hooked(project_root, *args):
            if hook and args[:2] == ("lease", "list"):
                hook()
            return original(project_root, *args)

        glob["land_result"] = hooked
        argv = ["--store", str(self.store), "--at", str(self.at), "--owner", "coordinator@1",
                "dish", "D1", "--branch", "perf/d1-r2"]
        try:
            return glob["run"](argv)
        except glob["BrigadeError"] as error:
            return f"brigade: {error}"


    def lease_lines(self):
        return [line.split(" until ")[0] + ": " + line.rsplit(": ", 1)[1]
                for line in self.land("lease", "list").splitlines()]


    def git_project(self):
        if (self.project / ".git").exists():
            return
        run = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        (self.project / "a.txt").write_text("a\n")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")


    def land_env(self):
        return dict(os.environ, XDG_STATE_HOME=str(Path(self.temporary.name) / "state"))


    def land_repo(self, repo, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / "t3/added/landing/scripts/land.py"),
                                 "--repo", str(repo), *args],
                                capture_output=True, text=True, env=self.land_env())
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if ok else result.stderr).strip()


    def land(self, *args, ok=True):
        return self.land_repo(self.project, *args, ok=ok)


    def init_landing(self, *args):
        self.git_project()
        return self.land("init", "--trunk", "lane", "--mode", "local", "--base", "main", *args)


    def landing_env(self):
        from unittest import mock
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]})
        patcher.start()
        self.addCleanup(patcher.stop)


    def started(self, *init, paths="README.md"):
        """A local landing contract, the Perf coordinator, and D1 holding L1."""
        self.init_landing(*init)
        self.landing_env()
        self.open()
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", paths),
                         "D1 (lease L1 held by perf/D1)")


    def backdate_attempt(self, minutes, ident="D1"):
        start = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        log = self.at / "log.tsv"
        lines = log.read_text().splitlines()
        rewritten = [lines[0]]
        found = False
        for line in lines[1:]:
            fields = line.split("\t")
            if fields[1:4] == ["dish", ident, "in-progress"]:
                fields[0] = start
                line = "\t".join(fields)
                found = True
            rewritten.append(line)
        self.assertTrue(found, ident)
        log.write_text("\n".join(rewritten) + "\n")


    def landing_db(self):
        import sqlite3
        database = next((Path(self.temporary.name) / "state").glob("pstack-t3/landing/*/land.db"))
        db = sqlite3.connect(database)
        db.row_factory = sqlite3.Row
        return db


    def lease_row(self, number):
        from contextlib import closing
        with closing(self.landing_db()) as db:
            return dict(db.execute("SELECT * FROM lease WHERE id = ?", (number,)).fetchone())


    def seed_entry(self, sha, state, note):
        """A queue entry for perf/D1 at another commit whose first 12 characters match, copied from E1."""
        from contextlib import closing
        with closing(self.landing_db()) as db, db:
            db.execute("INSERT INTO entry (at, holder, branch, sha, base, fingerprint, lease, reviewer, state, note) "
                       "SELECT at, holder, branch, ?, base, fingerprint, lease, reviewer, ?, ? FROM entry WHERE id = 1",
                       (sha, state, note))


    @staticmethod
    def same_prefix(sha):
        return sha[:12] + ("1" if sha[12] == "0" else "0") + sha[13:]


    def worker_commit(self, branch, files):
        path = Path(self.temporary.name) / branch.replace("/", "-")
        git = lambda *command, cwd=self.project: subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                                                check=True).stdout.strip()
        if not path.exists():
            git("git", "worktree", "add", "-q", "-b", branch, str(path), "main")
        for name, text in files.items():
            if text is None:
                (path / name).unlink()
            else:
                full = path / name
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(text)
        git("git", "add", "-A", cwd=path)
        git("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", branch, cwd=path)
        return git("git", "rev-parse", "HEAD", cwd=path)


    def passed(self, sha):
        self.brigade("dish", "D1", "--state", "in-review", "--sha", sha)
        self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "pass", "--author", CODEX, "--verifier", CLAUDE)


    def submit(self, sha):
        return self.land("submit", "--holder", "perf/D1", "--branch", "perf/d1", "--sha", sha, "--lease", "L1",
                         "--reviewer", CLAUDE)


    def landing_rows(self):
        from contextlib import closing
        with closing(self.landing_db()) as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
                    for table in ("lease", "log", "reservation", "owner", "entry")}
