import ast
import contextlib
import errno
import fcntl
import gc
import hashlib
import inspect
import io
import json
import os
import re
import shlex
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import land
from .shell import git_env, sh
from .inprocess import ProcessReason, _run, drive, real_popen, real_process
from . import paths
from .paths import ADMIN, REVIEWER, ROOT, SCRIPT, repository_state, stamp_in


class CaseBase:

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        # Cleanups run after tearDown, last registered first, so this one runs after every holder a test leaves running is stopped.
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.base / "state")})
        self.env.start()
        sh("git", "init", "-q", "--bare", "-b", "main", "origin.git", cwd=self.base)
        sh("git", "clone", "-q", "origin.git", "work", cwd=self.base)
        self.work = self.base / "work"
        sh("git", "checkout", "-q", "-b", "main", cwd=self.work)
        for name in ("a.txt", "b.txt", "lib/x.py"):
            (self.work / name).parent.mkdir(exist_ok=True)
            (self.work / name).write_text(f"{name}\n")
        (self.work / "check.sh").write_text("#!/bin/sh\n! grep -rq BROKEN --include=*.txt .\n")
        (self.work / "check.sh").chmod(0o755)
        self.commit("init")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.install_github_remote()


    def install_github_remote(self):
        """Point origin at https://github.com/o/r.git and put a git stand-in first on PATH.

        git remote get-url still prints the GitHub URL. When push, fetch, ls-remote, or pull is the
        stand-in's first argument and git remote get-url origin exits 0 with a URL that starts with
        https://github.com/, the stand-in runs git with that URL rewritten to origin.git.
        """
        git = shlex.quote(shutil.which("git"))
        bindir = self.base / "git-wrap"
        bindir.mkdir()
        rewrite = shlex.quote("url." + str(self.base / "origin.git") + "/.insteadOf=")
        script = bindir / "git"
        script.write_text(
            "#!/bin/sh\n"
            "case $1 in\n"
            "push|fetch|ls-remote|pull)\n"
            f"  if url=$({git} remote get-url origin 2>/dev/null); then\n"
            "    case $url in\n"
            f"    https://github.com/*) exec {git} -c {rewrite}\"$url\" \"$@\" ;;\n"
            "    esac\n"
            "  fi ;;\n"
            "esac\n"
            f"exec {git} \"$@\"\n"
        )
        script.chmod(0o755)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
        sh("git", "remote", "set-url", "origin", "https://github.com/o/r.git", cwd=self.work)


    def tearDown(self):
        self.env.stop()


    def commit(self, message, cwd=None):
        sh("git", "add", "-A", cwd=cwd or self.work)
        sh("git", "commit", "-qm", message, cwd=cwd or self.work)
        return sh("git", "rev-parse", "HEAD", cwd=cwd or self.work)


    def git_raw(self, *args, cwd):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=git_env())


    def land(self, *args, ok=True):
        code, out, err = drive(paths.SCRIPT, ["--repo", str(self.work), *args], env=os.environ.copy())
        self.assertEqual(code == 0, ok, out + err)
        return (out if ok else err).strip()


    def land_in_another_process(self, *args, ok=True):
        """Run land.py in a separate process. After the exit check, return stripped stdout when the exit code is 0 and stripped stderr otherwise."""
        result = real_process(
            ProcessReason.LOCK, paths.SCRIPT, ["--repo", str(self.work), *args], env=os.environ.copy())
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if result.returncode == 0 else result.stderr).strip()


    def init(self, mode="auto", batch=1, **extra):
        args = ["init", "--trunk", extra.get("trunk", "main"), "--mode", mode, "--check", "./check.sh", "--batch", str(batch)]
        if "merge_method" in extra:
            args += ["--merge-method", extra["merge_method"]]
        if "base" in extra:
            args += ["--base", extra["base"]]
        if "generated" in extra:
            args += ["--generated", extra["generated"]]
        if "generated_map" in extra:
            args += ["--generated-map", extra["generated_map"]]
        if "rebuild" in extra:
            args += ["--rebuild", extra["rebuild"]]
        return self.land(*args)


    def init_with_limit(self, maximum=5000, extra=()):
        args = ["init", "--trunk", "main", "--mode", "push", "--check", "./check.sh",
                "--max-file-tokens", str(maximum), *extra]
        return self.land(*args)

    def worker(self, name, edits, base="origin/main"):
        """A worker branch in its own worktree with one commit. Returns its SHA."""
        path = self.base / name
        sh("git", "worktree", "add", "-q", "-b", name, str(path), base, cwd=self.work)
        for file, text in edits.items():
            (path / file).parent.mkdir(parents=True, exist_ok=True)
            (path / file).write_text(text)
        return self.commit(name, cwd=path)


    def origin_log(self):
        return sh("git", "log", "--format=%s", "main", cwd=self.base / "origin.git").splitlines()


    def ref_exists(self, ref, cwd):
        return subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=cwd, capture_output=True).returncode == 0


    def run_land(self, repo, *args, cwd=None):
        """Run land.py with --repo set to repo. Returns its exit status, stdout, and stderr."""
        return drive(paths.SCRIPT, ["--repo", str(repo), *args], env=os.environ.copy(), cwd=cwd)


    def files_under(self, directory):
        """The SHA-256 of every file under a directory, by relative path."""
        return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.rglob("*")) if path.is_file()}


    def raw_db(self):
        """land.db opened without Store, which upgrades an older schema when it opens one."""
        return contextlib.closing(sqlite3.connect(next((self.base / "state").glob("pstack-t3/landing/*/land.db"))))


    def dumped(self):
        with self.raw_db() as db:
            return "\n".join(db.iterdump())


    def queue_rows(self):
        """Every row of every table in the store, by table name. The contract rows are sorted."""
        with self.raw_db() as db:
            tables = [name for name, in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            rows = {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}
        return {**rows, "contract": sorted(rows["contract"])}


    def entry_fields(self, ident, *names):
        with self.raw_db() as db:
            return db.execute(f"SELECT {', '.join(names)} FROM entry WHERE id = ?", (ident,)).fetchone()


    def entry_columns(self):
        with self.raw_db() as db:
            return [row[1] for row in db.execute("PRAGMA table_info(entry)")]


    def on_origin(self, *args):
        return sh("git", *args, cwd=self.base / "origin.git")


    def origin_files(self, ref):
        return [self.on_origin("show", f"{ref}:{path}") for path in ("a.txt", "b.txt", "lib/x.py")]
