import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .support import MOD, setUpModule

TABLES = "create table orchestration_v2_projection_metadata (projection_name text, schema_version integer)"
COLUMNS = "select count(*) from orchestration_v2_projection_metadata"
HOLDER = """import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("create table if not exists probe (value integer)")
connection.execute("insert into probe values (1)")
connection.execute("PRAGMA locking_mode=EXCLUSIVE")
connection.execute("BEGIN EXCLUSIVE")
connection.execute("select count(*) from probe").fetchone()
print("held", flush=True)
sys.stdin.readline()
"""


class T3StateLockedTest(unittest.TestCase):
    """A database locked past the busy timeout surfaces as SourceError, never a raw sqlite3 error from a later query."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "statev2.sqlite"
        writer = sqlite3.connect(str(self.path), isolation_level=None)
        writer.execute("PRAGMA journal_mode=DELETE")
        writer.execute(TABLES)
        writer.close()

    def hold_lock(self):
        """A second connection that holds this database exclusively until the test ends."""
        connection = sqlite3.connect(str(self.path), isolation_level=None, timeout=0)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        connection.execute("BEGIN EXCLUSIVE")
        self.assertEqual(connection.execute(COLUMNS).fetchone(), (0,))
        return connection

    def lock_is_free(self):
        """True when a second connection takes the exclusive lock, which it cannot while a read lock is held."""
        connection = sqlite3.connect(str(self.path), isolation_level=None, timeout=0)
        try:
            connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            connection.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            return False
        finally:
            connection.close()
        return True

    def test_open_t3_with_the_database_locked_exclusively_raises_source_error_and_no_sqlite_error(self):
        self.hold_lock()
        with self.assertRaises(MOD["SourceError"]) as raised:
            MOD["open_t3"](self.path)
        self.assertNotIsInstance(raised.exception, sqlite3.Error)
        self.assertEqual(
            str(raised.exception),
            "cannot read T3's database (database is locked); check that T3 Code is running and try again")

    def test_the_read_after_open_t3_returns_is_not_interrupted_by_an_exclusive_lock_attempt(self):
        connection = MOD["open_t3"](self.path)
        self.addCleanup(connection.close)
        self.assertFalse(self.lock_is_free(), "the lock was granted, so the open held no read lock")
        self.assertEqual(connection.execute(COLUMNS).fetchone(), (0,))


class T3StateWalWithoutIndexTest(unittest.TestCase):
    """A log with no index is refused, and the refusal names the held lock when one process holds the file."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "statev2.sqlite"
        writer = sqlite3.connect(str(self.path), isolation_level=None)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("create table probe (value integer)")
        writer.close()

    def test_a_log_with_no_index_and_no_holder_keeps_the_refusal_that_names_the_missing_index(self):
        """A read-only open leaves an empty log, the test then removes the index, and nobody holds the file."""
        reader = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, isolation_level=None)
        reader.execute("select 1 from sqlite_master limit 1")
        reader.close()
        Path(f"{self.path}-shm").unlink()
        self.assertTrue(Path(f"{self.path}-wal").exists())
        self.assertFalse(Path(f"{self.path}-shm").exists())
        with self.assertRaises(MOD["SourceError"]) as raised:
            MOD["open_t3"](self.path)
        self.assertEqual(
            str(raised.exception),
            "T3's database has a write-ahead log with no index beside it; start T3 Code and run this again")
        self.assertFalse(Path(f"{self.path}-shm").exists(), "the refusal created the index")

    def test_a_database_held_in_exclusive_locking_mode_is_refused_as_locked_not_as_a_missing_index(self):
        """The holder runs in a child process, because POSIX record locks never conflict within one process, so a lock taken by the test process would not block the probe."""
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(self.path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(holder.stderr.close)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.stdin.close)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        ready = holder.stdout.readline().strip()
        self.assertEqual(ready, "held", holder.stderr.read() if holder.poll() is not None else "the holder did not take its lock")
        Path(f"{self.path}-shm").unlink()
        self.assertTrue(Path(f"{self.path}-wal").exists())
        self.assertFalse(Path(f"{self.path}-shm").exists())
        with self.assertRaises(MOD["SourceError"]) as raised:
            MOD["open_t3"](self.path)
        self.assertEqual(
            str(raised.exception),
            "another process holds T3's database exclusively; wait for it to finish and run this again")
        self.assertFalse(Path(f"{self.path}-shm").exists(), "the refusal created the index")


if __name__ == "__main__":
    unittest.main()
