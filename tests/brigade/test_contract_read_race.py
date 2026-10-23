import importlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest import mock

from .support import BrigadeTest, ROOT, setUpModule


class ContractReadRaceTest(BrigadeTest):
    """A contract lookup that a writer replaces mid-read returns one committed command, never a splice."""

    OLD_CHECK = "make lint " + "a" * 8000
    NEW_CHECK = "make test " + "b" * 8000

    def common_dir(self):
        """The project's git common directory, named the way the lookup names it.

        Processes: git, because the lookup itself runs git to name that directory.
        """
        result = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def queue(self, common, check):
        """A landing queue under this case's state directory holding one contract, closed so it leaves no log."""
        path = Path(self.temporary.name) / "state/pstack-t3/landing/one/land.db"
        path.parent.mkdir(parents=True)
        writer = sqlite3.connect(str(path), isolation_level=None)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE contract (key TEXT PRIMARY KEY, value TEXT)")
        writer.execute("INSERT INTO contract (key, value) VALUES (?, ?)", ("commonDir", json.dumps(common)))
        writer.execute("INSERT INTO contract (key, value) VALUES (?, ?)", ("checks", json.dumps([check])))
        writer.close()
        self.assertFalse(Path(f"{path}-wal").exists())
        return path

    def report_module(self):
        """The loaded contract reader, imported once from the source tree the tests run."""
        scripts = str(ROOT / "t3/added/brigade/scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
            self.addCleanup(sys.path.remove, scripts)
        for name in [name for name in list(sys.modules) if name == "brigade_lib" or name.startswith("brigade_lib.")]:
            del sys.modules[name]
        return importlib.import_module("brigade_lib._affected.report")

    def lookup(self, path, replace):
        """contract_check with its connection wrapped so replace runs after the first row is read and before the rest.

        A wrapped connection's execute steps once, which reads the first row and opens the read transaction, so the
        swap lands inside the lookup's read without any timing.
        """
        report = self.report_module()
        swapped = []

        class Hook:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, *args, **kwargs):
                cursor = self.connection.execute(*args, **kwargs)
                replace()
                swapped.append(True)
                return cursor

            def close(self):
                self.connection.close()

        def connect(*args, **kwargs):
            return Hook(sqlite3.connect(*args, **kwargs))

        shim = mock.Mock(wraps=sqlite3)
        shim.connect = connect
        with mock.patch.object(report, "sqlite3", shim):
            check = report.contract_check(str(self.project))
        return check, swapped

    def test_a_writer_that_replaces_a_multipage_check_mid_read_never_leaves_a_spliced_command_fails_when_the_read_takes_no_lock(self):
        # Processes: git names the project's common directory, as the lookup runs it too.
        self.git_project()
        self.landing_env()
        path = self.queue(self.common_dir(), self.OLD_CHECK)

        def replace():
            writer = sqlite3.connect(str(path), isolation_level=None)
            writer.execute("INSERT OR REPLACE INTO contract (key, value) VALUES (?, ?)",
                           ("checks", json.dumps([self.NEW_CHECK])))
            writer.close()

        check, swapped = self.lookup(path, replace)
        self.assertEqual(len(swapped), 1)
        self.assertTrue(check in (self.OLD_CHECK, self.NEW_CHECK),
                        f"the lookup returned a command that is neither committed one ({len(check)} characters)")
