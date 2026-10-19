import os
import sys
import tempfile
import unittest
from pathlib import Path

from .names import SCRIPT, SUPPORT, _owner_words
from .inprocess import ProcessReason, _drop_entry, call, real_popen, real_process
from .race_child import _race_args, _wait_for_path


class StoresBase:
    """Two or three coordinators on one project root, as `app/<name>` under the store."""

    def setUp(self):
        self.addCleanup(_drop_entry, self)
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Path(self.temporary.name) / "store"
        self.project = (Path(self.temporary.name) / "app").resolve()
        self.project.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def dir(self, name):
        return self.store / "app" / name

    def run_brigade(self, name, *args):
        if "--owner" not in args:
            args = (*_owner_words(self.dir(name)), *args)
        argv = ["--store", str(self.store), "--at", str(self.dir(name)), *(str(arg) for arg in args)]
        command = [sys.executable, str(SCRIPT), *argv]
        return call(self, SCRIPT, argv).completed(command)

    def brigade(self, name, *args, ok=True):
        result = self.run_brigade(name, *args)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if ok else result.stderr).strip()

    def open(self, name, *extra):
        return self.brigade(name, "open", "--project-root", str(self.project), "--name", name, *extra)

    def child(self, mode, name, *args):
        support = SUPPORT
        tail = _race_args(mode, self.temporary.name,
                          ["--store", str(self.store), "--at", str(self.dir(name)), *args])
        return real_process(ProcessReason.EXIT_STATUS, support, tail, timeout=30,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

    def paused(self, label, name, *args):
        """A command that has parsed its arguments and waits before it takes the store lock. Every caller starts two of these and lets both reach the wait before either resumes."""
        case = Path(self.temporary.name) / label
        case.mkdir()
        support = SUPPORT
        tail = _race_args("pause", case, ["--store", str(self.store), "--at", str(self.dir(name)), *args])
        proc = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(support), *tail],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        _wait_for_path(case / "paused", timeout=20)
        return case, proc

    def resume(self, case, proc):
        (case / "proceed").touch()
        _, err = proc.communicate(timeout=30)
        return proc.returncode, err.strip()

    def files(self, name):
        directory = self.dir(name)
        return {str(path.relative_to(directory)): path.read_bytes() for path in sorted(directory.rglob("*"))
                if path.is_file() and path.name != "restaurant.lock"}

    def rows(self, name, table):
        lines = (self.dir(name) / table).read_text().splitlines()
        return [line.split("\t") for line in lines[1:]]

    def inbox(self, name):
        return sorted(path.name for path in (self.dir(name) / "inbox").glob("*")) if (self.dir(name) / "inbox").exists() else []

    def handoff(self, source="core", target="engine", ref="https://github.com/o/r/issues/7"):
        """core owns github, files T1, and moves it to engine. Returns engine's inbox file."""
        self.open(source, "--intake", "github")
        self.open(target)
        self.brigade(source, "ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--source", "github", "--ref", ref)
        self.brigade(source, "ticket", "move", "T1", "--to", target)
        return self.dir(target) / "inbox" / f"app~{source}~T1.json"
