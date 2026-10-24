if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import sqlite3

from .support import setUpModule, LandingTest as LandingTestBase

from landing_lib import foundation, seam, store

import landing_lib.times as times


def bind_seam():
    """Bind the seam to the foundation and store names."""
    seam.bind({name: value for module in (foundation, store) for name, value in vars(module).items()
               if not name.startswith("__")})


bind_seam()


class TimesLockedTest(LandingTestBase):
    """A times read against a database locked past the busy timeout reports the queue's controlled error."""

    def database(self):
        """The scratch repository's real land.db, in its write-ahead log mode."""
        self.init()
        return next((self.base / "state").glob("pstack-t3/landing/*/land.db"))

    def test_a_database_locked_past_the_busy_timeout_raises_land_error_naming_the_database_and_its_reason(self):
        path = self.database()
        holder = sqlite3.connect(str(path), isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("INSERT INTO log (at, kind, id, state, note) VALUES ('2026-10-01T00:11:30+00:00', 'entry', 1, 'landed', '')")
        holder.execute("PRAGMA locking_mode=EXCLUSIVE")
        holder.execute("BEGIN EXCLUSIVE")
        with self.assertRaises(foundation.LandError) as caught:
            times.landing_times(str(self.work), 10, None, False)
        message = str(caught.exception)
        self.assertIn(str(path), message)
        self.assertIn("locked", message)
