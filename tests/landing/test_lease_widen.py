if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from datetime import datetime, timedelta
from unittest import mock

from .support import setUpModule, LandingTest as LandingTestBase

import land


class LeaseWidenTest(LandingTestBase):
    def test_widen_keeps_the_id_and_holder_and_adds_paths(self):
        self.init()
        self.assertEqual(self.claim("perf/D1", "a.txt"), "L1")
        self.assertEqual(self.land("lease", "widen", "L1", "--paths", "b.txt"), "L1 widened")
        row = self.lease_row(1)
        self.assertEqual((row[2], row[3], row[4]), ("perf/D1", "a.txt\nb.txt", "active"))
        self.assertEqual(self.listed(), ["L1 active perf/D1: a.txt, b.txt"])

    def test_widen_refuses_another_holders_overlap_and_changes_nothing(self):
        self.init()
        self.assertEqual(self.claim("perf/D1", "a.txt"), "L1")
        self.assertEqual(self.claim("bugs/D1", "b.txt"), "L2")
        before = self.land("lease", "list")
        self.assertEqual(self.land("lease", "widen", "L1", "--paths", "b.txt", ok=False),
                         "land: paths overlap L2 held by bugs/D1 on b.txt")
        self.assertEqual(self.land("lease", "list"), before)

    def test_widen_at_a_full_cap_succeeds_because_it_adds_no_change_in_flight(self):
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--cap", "1")
        self.assertEqual(self.claim("perf/D1", "a.txt"), "L1")
        self.assertEqual(self.land("lease", "widen", "L1", "--paths", "b.txt"), "L1 widened")
        self.assertEqual(self.listed(), ["L1 active perf/D1: a.txt, b.txt"])

    def test_widen_folds_a_same_holder_orphan_into_the_recorded_lease(self):
        self.init()
        self.assertEqual(self.claim("perf/D1", "a.txt"), "L1")
        self.assertEqual(self.claim("perf/D1", "a.txt,b.txt"), "L2")
        self.assertEqual(self.land("lease", "widen", "L1", "--paths", "b.txt"), "L1 widened")
        self.assertEqual(self.listed(), ["L1 active perf/D1: a.txt, b.txt"])

    def test_widen_re_admits_an_expired_lease_and_keeps_its_id(self):
        self.init()
        self.assertEqual(self.claim("perf/D1", "a.txt", "--ttl-hours", "0"), "L1")
        self.assertEqual(self.land("lease", "list"), "no leases held")
        self.assertEqual(self.land("lease", "widen", "L1", "--paths", "b.txt"), "L1 widened")
        self.assertEqual(self.listed(), ["L1 active perf/D1: a.txt, b.txt"])

    def test_widen_zero_ttl_refuses_instead_of_releasing_the_folded_live_lease(self):
        self.init()
        self.assertEqual(self.claim("docs/D1", "a.txt", "--ttl-hours", "0"), "L1")
        self.assertEqual(self.claim("docs/D1", "a.txt,b.txt"), "L2")
        self.assertEqual(self.land("lease", "widen", "L1", "--paths", "c.txt", "--ttl-hours", "0", ok=False),
                         "land: L1 expired and a TTL of 0 hours keeps it expired")
        self.assertEqual(self.listed(), ["L2 active docs/D1: a.txt, b.txt"])

    def test_widen_re_admits_a_lease_that_expires_during_the_fold(self):
        self.init()
        self.assertEqual(self.claim("docs/D1", "a.txt"), "L1")
        self.assertEqual(self.claim("docs/D1", "a.txt,b.txt"), "L2")
        expires = datetime.fromisoformat(self.lease_row(1)[5])
        clock = {"now": expires - timedelta(microseconds=1)}
        module = land.lease_widen.__globals__
        seam = module["seam"]
        admit = module["admission"]

        def admit_then_expire(*args, **kwargs):
            taken = admit(*args, **kwargs)
            clock["now"] = expires + timedelta(microseconds=1)
            return taken

        with mock.patch.object(seam, "now", lambda: clock["now"]), \
                mock.patch.object(seam, "stamp", lambda moment=None: (moment or clock["now"]).isoformat(timespec="microseconds")), \
                mock.patch.dict(module, {"admission": admit_then_expire}):
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(land.lease_widen(store, 1, "c.txt"), "L1 widened")
                self.assertIn("L1 active docs/D1", land.lease_list(store))
                self.assertIn("a.txt, b.txt, c.txt", land.lease_list(store))

    def test_widen_keeps_folded_coverage_when_the_clock_crosses_the_kept_expiry_after_the_decision(self):
        self.init()
        self.assertEqual(self.claim("docs/D1", "a.txt", "--ttl-hours", "1"), "L1")
        self.assertEqual(self.claim("docs/D1", "a.txt,b.txt"), "L2")
        first = datetime.fromisoformat(self.lease_row(1)[5])
        clock = {"now": first - timedelta(microseconds=1)}
        module = land.lease_widen.__globals__
        seam = module["seam"]
        take = module["take"]

        def take_then_expire(*args, **kwargs):
            clock["now"] = first + timedelta(microseconds=1)
            return take(*args, **kwargs)

        with mock.patch.object(seam, "now", lambda: clock["now"]), \
                mock.patch.object(seam, "stamp", lambda moment=None: (moment or clock["now"]).isoformat(timespec="microseconds")), \
                mock.patch.dict(module, {"take": take_then_expire}):
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(land.lease_widen(store, 1, "c.txt"), "L1 widened")
                listed = land.lease_list(store)
                self.assertIn("L1 active docs/D1", listed)
                self.assertIn("a.txt, b.txt, c.txt", listed)
        self.assertEqual(self.lease_row(1)[5], self.lease_row(2)[5])

    def test_widen_tiny_positive_ttl_does_not_release_the_folded_leases_coverage(self):
        self.init()
        self.assertEqual(self.claim("docs/D1", "a.txt", "--ttl-hours", "0"), "L1")
        self.assertEqual(self.claim("docs/D1", "a.txt,b.txt"), "L2")
        self.assertEqual(self.land("lease", "widen", "L1", "--paths", "c.txt", "--ttl-hours", "0.000000001"), "L1 widened")
        self.assertEqual(self.listed(), ["L1 active docs/D1: a.txt, b.txt, c.txt"])
        self.assertEqual(self.claim("engine/D2", "a.txt,b.txt", ok=False),
                         "land: paths overlap L1 held by docs/D1 on a.txt, b.txt, c.txt")

    def test_widen_transfers_a_folded_leases_partial_reservation_to_the_kept_lease(self):
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--cap", "1")
        self.assertEqual(self.claim("docs/D1", "lib", "--ttl-hours", "0"), "L1")
        self.assertEqual(self.reserve("docs/", "a.txt,b.txt", "R1"), "S1")
        self.assertEqual(self.claim("docs/D1", "a.txt"), "L2")
        self.assertIn("changes in flight: 1 of 1", self.land("status"))
        self.assertEqual(self.land("lease", "widen", "L1", "--paths", "c.txt"), "L1 widened")
        self.assertIn("changes in flight: 1 of 1", self.land("status"))
        self.assertEqual(self.land("lease", "widen", "L1", "--paths", "c.txt"), "L1 widened")

    def test_widen_refuses_a_submitted_a_released_and_a_missing_lease(self):
        self.init()
        self.queue_one()
        before = self.dumped()
        self.assertEqual(self.land("lease", "widen", "L1", "--paths", "b.txt", ok=False),
                         "land: L1 is submitted; the queue holds it")
        self.assertEqual(self.dumped(), before)
        self.assertEqual(self.claim("r/D2", "b.txt"), "L2")
        self.assertEqual(self.land("lease", "release", "L2"), "L2 released")
        before = self.dumped()
        self.assertEqual(self.land("lease", "widen", "L2", "--paths", "lib", ok=False), "land: L2 is released")
        self.assertEqual(self.dumped(), before)
        self.assertEqual(self.land("lease", "widen", "L9", "--paths", "lib", ok=False), "land: no L9")
        self.assertEqual(self.dumped(), before)
