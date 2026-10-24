if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    ADMIN,
    datetime,
    land,
    os,
    ProcessReason,
    real_popen,
    REVIEWER,
    SCRIPT,
    sh,
    stamp_in,
    sys,
    timedelta,
    timezone,
)
from .base import LeasesBase

class LandingTest(LeasesBase, LandingTestBase):
    def test_a_reservation_refuses_readmitting_another_holders_expired_lease(self):
        self.init()
        self.assertEqual(self.claim("engine/D3", "a.txt", "--ttl-hours", "0"), "L1")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.assertEqual(self.land("lease", "renew", "L1", ok=False),
                         f"land: paths reserved for docs/ by ruling R4 until {self.reserved_until(1)}")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: a.txt"])
        self.assertEqual(self.claim("docs/D7", "a.txt"), "L2")

    def test_a_claim_covering_one_reserved_path_leaves_the_other_and_counts_once(self):
        self.init()
        self.land("cap", "2")
        self.reserve("docs/", "a.txt,b.txt", "R4")
        self.assertIn("changes in flight: 1 of 2", self.land("status"))
        self.assertEqual(self.claim("docs/D7", "a.txt"), "L1")
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1, changes in flight: 1 of 2. author policy: any")
        self.assertEqual(self.listed(), ["L1 active docs/D7: a.txt", "S1 armed docs/ by R4: b.txt"])
        self.assertIn("paths reserved for docs/ by ruling R4", self.claim("engine/D3", "b.txt", ok=False))
        self.assertEqual(self.claim("engine/D3", "lib"), "L2")
        self.assertEqual(self.claim("ops/D1", "c.txt", ok=False), "land: repository at its cap: 2 of 2 changes in flight (docs/D7, engine/D3)")

    def test_a_reservation_waits_to_arm_while_another_holders_live_lease_overlaps(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt,b.txt", "R4"), "S1")
        self.assertEqual(self.lease_check("ops/D1", "c.txt"), (0, "free"))
        self.assertEqual(self.listed(), ["L1 active engine/D1: a.txt", "S1 waiting docs/ by R4: a.txt, b.txt"])
        waiting = "paths reserved for docs/ by ruling R4 until 2 hours after it arms"
        self.assertEqual(self.claim("engine/D2", "b.txt", ok=False), f"land: {waiting}")
        self.assertEqual(self.lease_check("engine/D2", "b.txt"), (1, waiting))
        self.assertEqual(self.ruling_rows("reservation")[0]["armed"], "")
        self.land("lease", "release", "L1")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: a.txt, b.txt"])
        self.assertEqual(self.lease_check("ops/D1", "c.txt"), (0, "free"))
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: a.txt, b.txt"])

    def test_lease_check_leaves_a_reservation_waiting_that_a_claim_would_arm(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.listed(), ["S1 waiting docs/ by R4: a.txt"])
        for _ in range(2):
            self.assertEqual(self.check_with_dump_unchanged("ops/D2", "d.txt"), (0, "free"))
            code, refusal = self.check_with_dump_unchanged("engine/D9", "a.txt")
            self.assertEqual(code, 1)
            self.assert_reserved_for_two_hours(refusal)
            self.assertEqual(self.check_with_dump_unchanged("docs/D7", "a.txt"), (0, "free"))
        self.assertEqual(self.listed(), ["S1 waiting docs/ by R4: a.txt"])
        self.assertEqual(self.armed_log(), [])

    def test_a_reservations_hold_starts_at_the_first_claim_after_lease_checks(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        for holder, paths in [("ops/D2", "d.txt"), ("engine/D9", "a.txt"), ("docs/D7", "a.txt")] * 2:
            self.lease_check(holder, paths)
        claimed = datetime.now(timezone.utc)
        self.assertEqual(self.claim("ops/D2", "d.txt"), "L2")
        row = self.ruling_rows("reservation")[0]
        armed = datetime.fromisoformat(row["armed"])
        self.assertGreaterEqual(armed, claimed)
        self.assertEqual(datetime.fromisoformat(row["expires"]) - armed, timedelta(hours=2))
        self.assertEqual(self.listed(), ["L2 active ops/D2: d.txt", "S1 armed docs/ by R4: a.txt"])
        self.assertEqual(self.armed_log(), [1])
        self.assert_reserved_for_two_hours(self.claim("engine/D9", "a.txt", ok=False))

    def test_lease_check_naming_an_overlapping_lease_claims_no_lease_arms_no_reservation_starts_no_hold_and_adds_no_log_row(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.claim("ops/D1", "b.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.check_with_dump_unchanged("engine/D9", "b.txt"), (1, "L2 held by ops/D1 on b.txt"))
        self.assertEqual(self.listed(), ["L2 active ops/D1: b.txt", "S1 waiting docs/ by R4: a.txt"])

    def test_lease_check_at_the_cap_claims_no_lease_arms_no_reservation_starts_no_hold_and_adds_no_log_row_and_the_refused_claim_arms(self):
        self.init()
        self.land("cap", "1")
        self.claim("ops/D1", "lib")
        self.assertEqual(self.reserve("docs/", "a.txt", "R1"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        at_cap = "repository at its cap: 1 of 1 changes in flight (S1 for docs/)"
        for _ in range(2):
            self.assertEqual(self.check_with_dump_unchanged("ops/D2", "c.txt"), (1, at_cap))
        self.assertEqual(self.listed(), ["S1 waiting docs/ by R1: a.txt"])
        self.assertEqual(self.claim("ops/D2", "c.txt", ok=False), f"land: {at_cap}")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R1: a.txt"])
        self.assertEqual(self.armed_log(), [1])

    def test_lease_check_at_a_share_claims_no_lease_arms_no_reservation_starts_no_hold_and_adds_no_log_row(self):
        self.init()
        self.land("cap", "4")
        self.share("docs/", 2)
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("ops/", "a.txt", "R4"), "S1")
        self.claim("docs/D1", "b.txt")
        self.claim("docs/D2", "c.txt")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.check_with_dump_unchanged("docs/D3", "lib"), (1, "docs/ is at its share: 2 of 2"))
        self.assertEqual(self.listed(), ["L2 active docs/D1: b.txt", "L3 active docs/D2: c.txt", "S1 waiting ops/ by R4: a.txt"])
        self.assertEqual(self.claim("docs/D3", "lib", ok=False), "land: docs/ is at its share: 2 of 2")

    def test_lease_check_answers_free_past_a_reservation_whose_hold_is_zero_hours(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4", "--ttl-hours", "0"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.check_with_dump_unchanged("engine/D9", "a.txt"), (0, "free"))
        self.assertEqual(self.claim("engine/D9", "a.txt"), "L2")

    def test_lease_check_on_an_older_store_upgrades_the_schema_and_leaves_lease_reservation_and_log_rows_unchanged(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.rebuild_entry_without_autoincrement()

        def entry_sql():
            with self.raw_db() as db:
                return db.execute("SELECT sql FROM sqlite_master WHERE name = 'entry'").fetchone()[0]

        def rows():
            with self.raw_db() as db:
                return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                        for table in ("contract", "lease", "attempt", "log", "owner", "reservation", "share", "contest")}

        self.assertNotIn("AUTOINCREMENT", entry_sql())
        self.assertNotIn("title", entry_sql())
        before = rows()
        code, refusal = self.lease_check("engine/D9", "a.txt")
        self.assertEqual(code, 1)
        self.assert_reserved_for_two_hours(refusal)
        self.assertIn("AUTOINCREMENT", entry_sql())
        self.assertIn("title", entry_sql())
        self.assertEqual(rows(), before, "rows of every table except entry, which the schema upgrade rebuilds, and sqlite_sequence, which gains an entry row")
        with self.raw_db() as db:
            self.assertEqual(db.execute("SELECT id, holder FROM lease").fetchall(), [(1, "engine/D1")])
            self.assertEqual(db.execute("SELECT armed, expires FROM reservation").fetchall(), [("", "")])
            self.assertEqual(db.execute("SELECT count(*) FROM log WHERE state = 'armed'").fetchall(), [(0,)])

    def test_releasing_one_lease_leaves_a_reservation_waiting_on_another(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.claim("ops/D1", "b.txt")
        self.assertEqual(self.reserve("docs/", "a.txt,b.txt", "R4"), "S1")
        self.land("lease", "release", "L1")
        self.assertEqual(self.listed(), [
            "L2 active ops/D1: b.txt",
            "S1 waiting docs/ by R4: a.txt, b.txt",
        ])
        self.land("lease", "release", "L2")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: a.txt, b.txt"])

    def test_landing_an_entry_arms_a_reservation_that_was_waiting_on_its_lease(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "landed\n"})
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.land("submit", "--holder", "engine/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (engine/D1)")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: a.txt"])
        armed = next(row for row in self.ruling_rows("reservation") if row["id"] == 1)
        left = datetime.fromisoformat(armed["expires"]) - datetime.now(timezone.utc)
        self.assertLess(abs(left - timedelta(hours=2)), timedelta(minutes=1))

    def test_with_a_cap_of_one_two_waiting_reservations_arm_one_at_a_time_oldest_first(self):
        self.init()
        self.land("cap", "1")
        self.claim("ops/D1", "lib")
        self.assertEqual(self.reserve("docs/", "a.txt", "R1"), "S1")
        self.assertEqual(self.reserve("engine/", "b.txt", "R2"), "S2")
        self.assertEqual(self.listed(), ["L1 active ops/D1: lib", "S1 waiting docs/ by R1: a.txt", "S2 waiting engine/ by R2: b.txt"])
        self.land("lease", "release", "L1")
        self.assertEqual(self.lease_check("ops/D2", "c.txt"), (1, "repository at its cap: 1 of 1 changes in flight (S1 for docs/)"))
        self.assertEqual(self.listed(), ["S1 armed docs/ by R1: a.txt", "S2 waiting engine/ by R2: b.txt"])
        self.assertEqual(self.claim("docs/D7", "a.txt"), "L2")
        self.assertEqual(self.listed(), ["L2 active docs/D7: a.txt", "S2 waiting engine/ by R2: b.txt"])
        self.assertEqual(self.claim("engine/D3", "b.txt", ok=False), "land: repository at its cap: 1 of 1 changes in flight (docs/D7)")
        self.land("lease", "release", "L2")
        self.assertEqual(self.claim("engine/D3", "b.txt"), "L3")
        self.assertEqual(self.listed(), ["L3 active engine/D3: b.txt"])
