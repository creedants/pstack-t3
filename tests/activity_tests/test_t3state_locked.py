import sqlite3
import tempfile
import unittest
from pathlib import Path

from .support import MOD, setUpModule

TABLES = "create table orchestration_v2_projection_metadata (projection_name text, schema_version integer)"
COLUMNS = "select count(*) from orchestration_v2_projection_metadata"


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

    def test_a_lock_taken_after_open_t3_returns_does_not_interrupt_the_read_that_follows_it(self):
        connection = MOD["open_t3"](self.path)
        self.addCleanup(connection.close)
        self.assertFalse(self.lock_is_free(), "the lock was granted, so the open held no read lock")
        self.assertEqual(connection.execute(COLUMNS).fetchone(), (0,))


if __name__ == "__main__":
    unittest.main()
