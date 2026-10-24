import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from .names import CLAUDE, CODEX, ROOT, SCRIPT, SUPPORT, _owner_words
from .inprocess import ProcessReason, _drop_entry, call, real_popen
from .race_child import _race_args
from tests.landing._support.host_gh_guard import HostGhGuard


class CaseBase:
    def setUp(self):
        self.addCleanup(_drop_entry, self)
        self.temporary = tempfile.TemporaryDirectory()
        # Cleanups run last registered first, so this one runs after every holder a test leaves running is stopped.
        self.addCleanup(self.temporary.cleanup)
        # The product resolves the project root, so the fixture paths below come from one resolved root.
        self.root = Path(self.temporary.name).resolve()
        self.store = self.root / "store"
        self.project = self.root / "Bridge Kit"
        self.project.mkdir()
        self.at = self.store / "bridge-kit" / "perf"
        self.isolate_roles_config()
        self.isolate_t3_home()
        self.gh_guard = HostGhGuard(self.temporary.name, label="brigade")
        self.gh_guard.install()
        self.addCleanup(self.gh_guard.assert_unused)
        self.addCleanup(self.gh_guard.assert_answered)


    def isolate_roles_config(self):
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.temporary.name) / "config")})
        patcher.start()
        self.addCleanup(patcher.stop)


    def isolate_t3_home(self):
        t3home = Path(self.temporary.name) / "t3home-empty"
        t3home.mkdir(exist_ok=True)
        patcher = mock.patch.dict(os.environ, {"T3CODE_HOME": str(t3home)})
        patcher.start()
        self.addCleanup(patcher.stop)


    def brigade(self, *args, ok=True, stdin=None, owner=True, code=None):
        return self.run_at(self.at, *args, ok=ok, stdin=stdin, owner=owner, code=code)


    def run_at(self, directory, *args, ok=True, stdin=None, owner=True, code=None):
        """Run brigade at directory.

        The call must exit 0 when ok and must not exit 0 otherwise, unless code
        names the one exit status it must return.
        """
        if owner and "--owner" not in args:
            args = (*_owner_words(directory), *args)
        outcome = call(
            self, SCRIPT,
            ["--store", str(self.store), "--at", str(directory), *(str(arg) for arg in args)],
            stdin=stdin,
        )
        if code is None:
            self.assertEqual(outcome.code == 0, ok, outcome.stdout_text() + outcome.stderr_text())
            return (outcome.stdout_text() if ok else outcome.stderr_text()).strip()
        self.assertEqual(outcome.code, code, outcome.stdout_text() + outcome.stderr_text())
        return (outcome.stdout_text() if code == 0 else outcome.stderr_text()).strip()


    def assert_refused_choice(self, output, flag, value, choices):
        refusal = f"argument {flag}: invalid choice: '{value}'"
        self.assertIn(refusal, output)
        after = output[output.index(refusal) + len(refusal):]
        for choice in choices:
            self.assertIn(choice, after)


    def table_row(self, directory, table, ident):
        lines = [line for line in (directory / table).read_text().splitlines() if line]
        keys = lines[0].split("\t")
        for line in lines[1:]:
            row = dict(zip(keys, line.split("\t")))
            if row["id"] == ident:
                return row
        self.fail(f"no {ident} in {directory / table}")


    def blocked_note(self, directory, ident):
        lines = [line for line in (directory / "log.tsv").read_text().splitlines() if line]
        keys = lines[0].split("\t")
        note = None
        for line in lines[1:]:
            row = dict(zip(keys, line.split("\t")))
            if row["kind"] == "ticket" and row["id"] == ident and row["state"] == "blocked":
                note = json.loads(row["note"])
        self.assertIsNotNone(note, ident)
        return note


    def bash_unblocked(self, directory, line):
        # The assertion is the shell round trip.
        command = line.split("run ", 1)[1]
        invocation = " ".join([shlex.quote(sys.executable), shlex.quote(str(SCRIPT)),
                               "--store", shlex.quote(str(self.store)), "--at", shlex.quote(str(directory)),
                               command])
        return subprocess.run(["bash", "-c", invocation], capture_output=True, text=True, env=self.land_env())


    def open(self):
        return self.brigade("open", "--project-root", str(self.project), "--name", "Perf")


    def same_text_refusal(self, ticket, state):
        return f"brigade: same text as {ticket} ({state}); nothing added; pass --again to file a second ticket"


    def check_json(self, sha):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                       "pass", "check", "D1", "--sha", sha, "--json"])
        return outcome.code, outcome.stdout_text(), outcome.stderr_text().strip()


    def record(self, sha, verdict, *extra, author=CLAUDE, verifier=CODEX):
        reason = ("--reason", "other") if verdict == "send-back" and "--reason" not in extra else ()
        self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", verdict, "--author", author, "--verifier", verifier,
                     *reason, *extra)


    def pass_rows(self):
        return [line.split("\t") for line in (self.at / "pass.tsv").read_text().splitlines()[1:]]


    def review_file(self, name, body="findings\n"):
        path = self.at / "reports" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(body)
        return path


    def attempt_report(self, body, dish="D1"):
        """Write reports/<dish>.md and stamp its mtime at or after that dish's last attempt start.

        Watch counts the file for this attempt only when that mtime is at or after the last attempt start.
        The stamp is the clock after the write, or the start when the clock is earlier.
        """
        path = self.at / "reports" / f"{dish}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(body)
        starts = [row["at"] for row in self.log_rows()
                  if row["kind"] == "dish" and row["id"] == dish and row["state"] == "in-progress"]
        if not starts:
            starts = [self.table_row(self.at, "dishes.tsv", dish)["at"]]
        start = datetime.fromisoformat(starts[-1])
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        moment = datetime.now(timezone.utc)
        if moment < start:
            moment = start
        stamp = moment.timestamp()
        os.utime(path, (stamp, stamp))
        return path


    def dish_fields(self, *keys):
        row = self.table_row(self.at, "dishes.tsv", "D1")
        return tuple(row[key] for key in keys)


    def store_files(self):
        """Every file of the store but restaurant.lock, which is empty and is not store data."""
        return {str(path.relative_to(self.at)): path.read_bytes() for path in sorted(self.at.rglob("*"))
                if path.is_file() and path.name != "restaurant.lock"}


    def tables(self):
        return {path.name: path.read_bytes() for path in sorted(self.at.glob("*.tsv"))}


    def thread_rows(self):
        """Every row of threads.tsv, with each timestamp replaced by `stamp` and an empty cell left empty."""
        lines = [line for line in (self.at / "threads.tsv").read_text().splitlines() if line]
        keys = lines[0].split("\t")
        return [["stamp" if key in ("at", "start", "end") and value else value
                 for key, value in zip(keys, line.split("\t"))] for line in lines[1:]]


    def raw_stdout(self, *args):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                       *_owner_words(self.at), *(str(arg) for arg in args)])
        self.assertEqual(outcome.code, 0, outcome.stderr)
        return outcome.stdout


    def log_rows(self):
        lines = (self.at / "log.tsv").read_text().splitlines()
        header = lines[0].split("\t")
        return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line]


    def _spawn_race(self, mode, case, args):
        """Start one side of the owner and loser pair."""
        if mode not in ("owner", "loser"):
            raise SystemExit(f"_spawn_race starts owner or loser, not {mode}")
        support = SUPPORT
        return real_popen(
            ProcessReason.RACE,
            [sys.executable, str(support), *_race_args(mode, case, args)],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)


    def _finish_race(self, proc):
        try:
            out, err = proc.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            self.fail(f"timed out\nstdout:\n{out}\nstderr:\n{err}")
        return proc.returncode, out.strip(), err.strip()


    def store_bytes(self):
        return tuple((self.at / name).read_bytes() for name in ("86.tsv", "log.tsv", "restaurant.json"))


    def directory_files(self, directory):
        return {str(path.relative_to(directory)): path.read_bytes()
                for path in sorted(Path(directory).rglob("*")) if path.is_file() and path.name != "restaurant.lock"}


    def unchanged(self, before=None):
        now = tuple((self.at / name).read_bytes() for name in ("dishes.tsv", "rail.tsv", "log.tsv", "86.tsv"))
        if before is not None:
            self.assertEqual(now, before)
        return now
