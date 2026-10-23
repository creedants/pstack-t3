import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .support import setUpModule

from landing_lib import foundation

import landing_lib.times as times

OLD_PR = "https://github.com/o/r/pull/9" + "a" * 9000
NEW_PR = "https://github.com/o/r/pull/9" + "b" * 9000
SUBMITTED = "2026-10-01T00:00:00.000000+00:00"


class TimesReadRaceTest(unittest.TestCase):
    """A times read that a writer replaces mid-read returns one committed pull request, never a splice."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def database(self):
        """A land.db of two landed entries whose second pull request spans pages, closed so it leaves no log."""
        path = self.directory / "land.db"
        writer = sqlite3.connect(str(path), isolation_level=None)
        writer.executescript(foundation.SCHEMA)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        for ident, holder, pr in ((1, "r/D1", "https://github.com/o/r/pull/8"), (2, "r/D2", OLD_PR)):
            writer.execute(
                "INSERT INTO entry (id, at, holder, branch, sha, base, fingerprint, lease, reviewer, state, pr) "
                "VALUES (?, ?, ?, 'b', ?, 'base', 'f', 1, 'r', 'landed', ?)",
                (ident, SUBMITTED, holder, f"sha{ident}", pr))
        for ident, landed in ((1, "2026-10-01T00:10:30.000000+00:00"), (2, "2026-10-01T00:20:30.000000+00:00")):
            writer.execute("INSERT INTO log (at, kind, id, state, note) VALUES (?, 'entry', ?, 'landed', '')",
                           (landed, ident))
        writer.close()
        self.assertFalse(Path(f"{path}-wal").exists())
        return path

    def test_a_writer_that_replaces_a_multipage_pull_request_mid_read_never_leaves_a_spliced_value_fails_when_the_read_takes_no_lock(self):
        path = self.database()
        swapped = []

        def replace():
            writer = sqlite3.connect(str(path), isolation_level=None)
            writer.execute("UPDATE entry SET pr = ? WHERE id = 2", (NEW_PR,))
            writer.close()

        class Hook(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                cursor = super().execute(sql, *args, **kwargs)
                if "FROM entry" in sql and not swapped:
                    replace()
                    swapped.append(True)
                return cursor

        def connect(*args, **kwargs):
            return sqlite3.connect(*args, factory=Hook, **kwargs)

        shim = mock.Mock(wraps=sqlite3)
        shim.connect = connect
        with mock.patch.object(times, "sqlite3", shim):
            connection = times.open_read_only(path)
            rows, _ = times.landings(connection, None, 10)
            connection.close()
        self.assertEqual(len(swapped), 1)
        self.assertIn(rows[1].pr, (OLD_PR, NEW_PR),
                      f"the read returned a pull request that is neither committed one ({len(rows[1].pr)} characters)")
