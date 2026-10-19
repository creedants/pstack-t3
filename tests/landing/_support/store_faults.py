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
from .paths import ADMIN, REVIEWER, ROOT, SCRIPT, repository_state, stamp_in


class StoreFaultsBase:

    def rebuild_entry_without_autoincrement(self):
        """Rewrite entry as an older database: the same rows, and no AUTOINCREMENT."""
        with land.Store.for_repo(self.work) as store:
            rows = [dict(row) for row in store.db.execute("SELECT * FROM entry")]
            store.db.execute("DROP TABLE entry")
            store.db.executescript(land.SCHEMA.replace(" AUTOINCREMENT", ""))
            columns = [info["name"] for info in store.db.execute("PRAGMA table_info(entry)")]
            for row in rows:
                present = [name for name in columns if name in row]
                store.db.execute(
                    f"INSERT INTO entry ({', '.join(present)}) VALUES ({', '.join('?' * len(present))})",
                    [row[name] for name in present])
            if store.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
                store.db.execute("DELETE FROM sqlite_sequence WHERE name = 'entry'")


    def subprocesses_during_land(self):
        """Commands this land run starts while a write transaction is open."""
        with land.Store.for_repo(self.work) as store:
            depth = {"n": 0}
            hits = []
            original_tx = land.Store.tx
            original_run = land.subprocess.run
            original_popen = land.subprocess.Popen

            @contextlib.contextmanager
            def watching_tx(store_self):
                depth["n"] += 1
                try:
                    with original_tx(store_self) as db:
                        yield db
                finally:
                    depth["n"] -= 1

            def record(command):
                if isinstance(command, (list, tuple)):
                    hits.append(" ".join(str(part) for part in command))
                else:
                    hits.append(str(command))

            def watching_run(*args, **kwargs):
                if depth["n"]:
                    record(args[0] if args else kwargs.get("args"))
                return original_run(*args, **kwargs)

            def watching_popen(*args, **kwargs):
                # subprocess.run calls Popen for the same command. Record a direct Popen only.
                if depth["n"] and inspect.stack()[1].function != "run":
                    record(args[0] if args else kwargs.get("args"))
                return original_popen(*args, **kwargs)

            with mock.patch.object(land.Store, "tx", watching_tx), \
                 mock.patch.object(land.subprocess, "run", watching_run), \
                 mock.patch.object(land.subprocess, "Popen", watching_popen):
                land.land(store)
            return hits


    def fail_state_reads_after_the_first(self):
        """The next PR state read answers. Every one after it fails with API unavailable."""
        real = os.environ["LAND_GH"]
        wrapper = self.base / "gh-state-fails"
        wrapper.write_text(
            f"""#!{sys.executable}
import os, sys
from pathlib import Path
base = Path({str(self.base)!r})
args = sys.argv[1:]
if args[:2] == ["pr", "view"] and "state,headRefOid,mergeCommit" in args:
    if (base / "state-read-answered").exists():
        (base / "state-query-fails").touch()
    (base / "state-read-answered").touch()
os.execv({real!r}, [{real!r}, *args])
"""
        )
        wrapper.chmod(0o755)
        os.environ["LAND_GH"] = str(wrapper)


    @contextlib.contextmanager
    def git_fails_for_the_merge_commit_of_pr_9(self, names, code=1):
        """Once the fake gh has merged PR 9, exit <code> from every git command with an argument <names> accepts.

        <names> is an expression over arg and merged, the merge commit."""
        bindir = self.base / "unreadable-bin"
        bindir.mkdir()
        real = shutil.which("git")
        (bindir / "git").write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            f"table = Path({str(self.base / 'prs.json')!r})\n"
            "merged = json.loads(table.read_text()).get('9', {}).get('merge', '') if table.exists() else ''\n"
            f"if merged and any({names} for arg in sys.argv[1:]):\n"
            f"    sys.exit({code})\n"
            f"os.execv({real!r}, [{real!r}, *sys.argv[1:]])\n")
        (bindir / "git").chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]}):
            yield


    def killed_land(self):
        result = real_process(
            ProcessReason.KILLED_CALLER, SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stdout + result.stderr)


    @contextlib.contextmanager
    def land_dies_after_its_push_to(self, branch):
        bindir = self.base / "kill-bin"
        bindir.mkdir()
        real = shutil.which("git")
        marker = bindir / "armed"
        marker.write_text("")
        (bindir / "git").write_text(
            "#!/bin/sh\n"
            f"if [ \"$1\" = push ] && [ -e '{marker}' ]; then\n"
            f"  case \"$*\" in *:refs/heads/{branch}) '{real}' \"$@\"; rm '{marker}'; kill -9 $PPID; exit 0;; esac\n"
            "fi\n"
            f"exec '{real}' \"$@\"\n")
        (bindir / "git").chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]}):
            yield


    def install_racing_branch_delete(self):
        """While git push --delete runs, another deleter holds the ref lock and removes the ref.

        git 2.55 then rejects the push with "File exists" and "reference already exists".
        That stderr does not contain "does not exist", and ls-remote shows the ref gone.
        """
        hook = self.base / "origin.git" / "hooks" / "pre-receive"
        marker = self.base / "race-deleted"
        hook.write_text(f"""#!/bin/sh
marker={marker}
while read old new ref; do
  if [ "$new" = "0000000000000000000000000000000000000000" ]; then
    path=$(git rev-parse --git-path "$ref")
    echo x > "$path.lock"
    rm -f "$path"
    echo "$ref" >> "$marker"
  fi
done
exit 0
""")
        hook.chmod(0o755)


    def record_pushes(self):
        """Log each git push while later commands run. The real git stays behind the wrapper."""
        log = self.base / "git-pushes"
        bindir = self.base / "bin"
        bindir.mkdir(exist_ok=True)
        real = subprocess.run(["which", "git"], capture_output=True, text=True, check=True).stdout.strip()
        script = bindir / "git"
        script.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = push ]; then\n"
            f"  printf '%s\\n' \"$*\" >> '{log}'\n"
            "fi\n"
            f"exec '{real}' \"$@\"\n"
        )
        script.chmod(0o755)
        self._push_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + self._push_path
        return log


    def stop_recording_pushes(self):
        os.environ["PATH"] = self._push_path


    def assert_pushes_stay_on_their_refs(self, log):
        lines = log.read_text().splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(line.startswith("push --no-follow-tags "), line)
            self.assertNotIn("--dry-run", line)
            self.assertFalse(any(part.startswith(":") for part in line.split()), line)
