from .support import setUpModule, LandingTest as LandingTestBase


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
