import sqlite3

from .support import setUpModule, LandingTest as LandingTestBase

from landing_lib import foundation, seam, store

import landing_lib.times as times


def bind_seam():
    """Bind the seam to the names land.py binds, so a landing_lib call resolves them in this process."""
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
