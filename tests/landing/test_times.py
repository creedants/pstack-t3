import json

from .support import (
    setUpModule,
    LandingTest as LandingTestBase,
)

SUBMITTED = "2026-10-01T00:00:00.000000+00:00"
PR = "https://github.com/o/r/pull/9"
FOUR = (
    ("r/D1", SUBMITTED, "2026-10-01T00:12:30.000000+00:00"),
    ("r/D2", SUBMITTED, "2026-10-01T00:24:30.000000+00:00"),
    ("r/D3", SUBMITTED, "2026-10-01T00:33:30.000000+00:00"),
    ("r/D4", SUBMITTED, "2026-10-01T00:44:30.000000+00:00"),
)
THREE = (
    ("r/D1", SUBMITTED, "2026-10-01T00:12:30.000000+00:00"),
    ("r/D2", SUBMITTED, "2026-10-01T00:24:30.000000+00:00"),
    ("docs/D1", SUBMITTED, "2026-10-01T00:33:30.000000+00:00"),
)


class LandingTest(LandingTestBase):
    def database(self):
        return next((self.base / "state").glob("pstack-t3/landing/*/land.db"))

    def landed_entries(self, rows):
        """rows holds one (holder, submitted, landed) triple per entry. Land one entry each, then set those stamps."""
        self.init(batch=len(rows))
        for number, (holder, _, _) in enumerate(rows, 1):
            self.assertEqual(self.queue_one(path=f"{number}.txt", text=f"{number} file", name=f"w{number}", holder=holder), f"E{number}")
        self.land("land")
        with self.raw_db() as db, db:
            for number, (_, submitted, landed) in enumerate(rows, 1):
                db.execute("UPDATE entry SET at = ? WHERE id = ?", (submitted, number))
                db.execute("UPDATE log SET at = ? WHERE kind = 'entry' AND id = ? AND state = 'landed'", (landed, number))
            db.execute("UPDATE entry SET pr = ? WHERE id = ?", (PR, 1))

    def test_times_prints_one_line_per_entry_and_the_summary(self):
        self.landed_entries(FOUR)
        self.assertEqual(self.land("times"), "\n".join([
            "E1 r/D1 https://github.com/o/r/pull/9 12.5",
            "E2 r/D2 - 24.5",
            "E3 r/D3 - 33.5",
            "E4 r/D4 - 44.5",
            "n=4 median=24.5 p90=44.5 max=44.5 skipped=0",
        ]))

    def test_times_skips_an_entry_with_no_landed_log_row(self):
        self.landed_entries(FOUR)
        with self.raw_db() as db, db:
            db.execute("DELETE FROM log WHERE kind = 'entry' AND id = 2 AND state = 'landed'")
        self.assertEqual(self.land("times"), "\n".join([
            "E1 r/D1 https://github.com/o/r/pull/9 12.5",
            "E3 r/D3 - 33.5",
            "E4 r/D4 - 44.5",
            "n=3 median=33.5 p90=44.5 max=44.5 skipped=1",
        ]))

    def test_times_last_keeps_the_last_entries_by_landing_time(self):
        self.landed_entries(FOUR)
        self.assertEqual(self.land("times", "--last", "2"), "\n".join([
            "E3 r/D3 - 33.5",
            "E4 r/D4 - 44.5",
            "n=2 median=33.5 p90=44.5 max=44.5 skipped=0",
        ]))

    def test_times_holder_matches_one_holder_and_every_holder_under_a_prefix(self):
        self.landed_entries(THREE)
        self.assertEqual(self.land("times", "--holder", "r/"), "\n".join([
            "E1 r/D1 https://github.com/o/r/pull/9 12.5",
            "E2 r/D2 - 24.5",
            "n=2 median=12.5 p90=24.5 max=24.5 skipped=0",
        ]))
        self.assertEqual(self.land("times", "--holder", "docs/"), "\n".join([
            "E3 docs/D1 - 33.5",
            "n=1 median=33.5 p90=33.5 max=33.5 skipped=0",
        ]))

    def test_times_json_holds_the_entries_and_the_summary(self):
        self.landed_entries(FOUR)
        self.assertEqual(json.loads(self.land("times", "--json")), {
            "entries": [
                {"id": 1, "holder": "r/D1", "pr": "https://github.com/o/r/pull/9", "minutes": 12.5},
                {"id": 2, "holder": "r/D2", "pr": None, "minutes": 24.5},
                {"id": 3, "holder": "r/D3", "pr": None, "minutes": 33.5},
                {"id": 4, "holder": "r/D4", "pr": None, "minutes": 44.5},
            ],
            "n": 4,
            "median": 24.5,
            "p90": 44.5,
            "max": 44.5,
            "skipped": 0,
        })

    def test_times_with_nothing_landed_prints_n_zero(self):
        self.init()
        self.assertEqual(self.land("times"), "n=0 median=- p90=- max=- skipped=0")
        self.assertEqual(json.loads(self.land("times", "--json")), {
            "entries": [], "n": 0, "median": None, "p90": None, "max": None, "skipped": 0,
        })

    def test_times_writes_nothing_to_the_store(self):
        self.landed_entries(FOUR)
        before_names = sorted(p.name for p in self.database().parent.iterdir())
        before_bytes = self.database().read_bytes()
        self.land("times")
        self.land("times", "--json")
        self.assertEqual(sorted(p.name for p in self.database().parent.iterdir()), before_names)
        self.assertEqual(self.database().read_bytes(), before_bytes)

    def test_times_refuses_a_last_below_one(self):
        self.assertEqual(self.land("times", "--last=0", ok=False), "land: --last must be at least 1")
        self.assertEqual(self.land("times", "--last=-1", ok=False), "land: --last must be at least 1")
