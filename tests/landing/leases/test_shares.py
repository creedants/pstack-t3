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
    def test_two_coordinators_claiming_reserved_paths_at_once_leave_only_the_winners_lease(self):
        self.init()
        for n in range(1, 5):
            self.reserve("docs/", f"p{n}.txt", f"R{n}")
            results = self.concurrent(["lease", "claim", "--holder", f"engine/D{n}", "--paths", f"p{n}.txt"],
                                      ["lease", "claim", "--holder", f"docs/D{n}", "--paths", f"p{n}.txt"])
            (engine_code, _, engine_err), (docs_code, docs_out, _) = results
            self.assertEqual((engine_code, docs_code, docs_out), (1, 0, f"L{n}"), results)
            self.assertTrue(engine_err.startswith("land: paths "), engine_err)
        self.assertEqual(self.listed(), [f"L{n} active docs/D{n}: p{n}.txt" for n in range(1, 5)])

    def test_a_reservation_rerun_returns_it_and_an_ended_one_says_how_it_ended(self):
        self.init()
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.assertEqual(self.reserve("engine/", "a.txt,b.txt", "R5", ok=False), "land: paths overlap S1 reserved for docs/ by ruling R4 on a.txt")
        self.assertEqual(self.land("lease", "unreserve", "S1", *ADMIN), "S1 lifted")
        self.assertEqual(self.land("lease", "unreserve", "S1", *ADMIN), "S1 lifted")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4", ok=False), "land: S1 for ruling R4 was lifted")
        self.assertEqual(self.claim("engine/D3", "a.txt"), "L1")
        self.assertEqual(self.reserve("docs/", "b.txt", "R6", "--ttl-hours", "0"), "S2")
        self.assertEqual(self.reserve("docs/", "b.txt", "R6", ok=False), "land: S2 for ruling R6 expired")
        self.assertEqual(self.claim("engine/D4", "b.txt"), "L2")
        self.assertRegex(self.land("lease", "unreserve", "S2", *ADMIN), r"^S2 expired at \d{4}-\d\d-\d\dT\d\d:\d\d$")
        self.assertEqual(self.land("lease", "unreserve", "S9", *ADMIN, ok=False), "land: no S9")
        self.assertEqual(self.reserve("docs", "c.txt", "R7", ok=False), "land: 'docs' is not a holder prefix such as docs/")
        self.assertEqual(self.reserve("docs/", "c.txt", "R8"), "S3")
        self.assertEqual(self.claim("docs/D7", "c.txt"), "L3")
        self.assertEqual(self.reserve("docs/", "c.txt", "R8"), "S3 was claimed in full")

    def test_a_reservation_refuses_the_whole_repository(self):
        self.init()
        refusal = "land: a reservation names the contested paths; it cannot hold the whole repository"
        self.assertEqual(self.reserve("docs/", "", "R4", ok=False), refusal)
        self.assertEqual(self.reserve("docs/", "a.txt,.", "R4", ok=False), refusal)
        self.assertEqual(self.claim("engine/D3", "lib"), "L1")
        self.assertEqual(self.listed(), ["L1 active engine/D3: lib"])

    def test_shares_refuse_a_sum_over_the_cap_and_admission_refuses_a_claim_over_a_share(self):
        self.init()
        self.land("cap", "4")
        self.assertEqual(self.share("docs/", 2), "docs/ share is 2")
        self.assertEqual(self.share("engine/", 3, ok=False), "land: shares would add up to 5, over the cap of 4")
        self.assertEqual(self.share("engine/", 2), "engine/ share is 2")
        self.assertEqual(self.share("engine/", 2), "engine/ share is 2")
        self.assertEqual(self.claim("docs/D1", "a.txt"), "L1")
        self.assertEqual(self.claim("docs/D2", "b.txt"), "L2")
        self.assertEqual(self.claim("docs/D3", "lib", ok=False), "land: docs/ is at its share: 2 of 2")
        self.assertEqual(self.lease_check("docs/D3", "lib"), (1, "docs/ is at its share: 2 of 2"))
        self.assertEqual(self.claim("ops/D1", "lib"), "L3")
        self.assertEqual(self.share("docs/", 1, "--clear", ok=False), "land: --clear takes 0")
        self.assertEqual(self.share("docs/", 0, "--clear"), "docs/ share cleared")
        self.assertEqual(self.claim("docs/D3", "c.txt"), "L4")
        self.assertEqual([(row["prefix"], row["count"]) for row in self.ruling_rows("share")], [("engine/", 2)])

    def test_a_cap_below_the_share_sum_is_refused_and_zero_still_clears(self):
        self.init()
        self.land("cap", "4")
        self.share("docs/", 2)
        self.share("engine/", 2)

        def snapshot():
            with land.Store.for_repo(self.work) as store:
                shares = [(row["prefix"], row["count"]) for row in store.db.execute(
                    "SELECT prefix, count FROM share ORDER BY prefix")]
                log = [(row["kind"], row["id"], row["state"], row["note"]) for row in store.db.execute(
                    "SELECT kind, id, state, note FROM log ORDER BY at")]
                return store.contract.get("cap"), shares, log

        before = snapshot()
        refusal = "land: shares add up to 4 (docs/ 2, engine/ 2), over the cap of 3"
        self.assertEqual(self.land("cap", "3", ok=False), refusal)
        self.assertEqual(snapshot(), before)
        self.assertIn("changes in flight: 0 of 4", self.land("status"))
        self.assertEqual(self.land("cap", "4"), "repository cap is now 4 changes in flight")
        self.assertEqual(self.land("cap", "0"), "repository cap cleared")
        cap, shares, _log = snapshot()
        self.assertIsNone(cap)
        self.assertEqual(shares, [("docs/", 2), ("engine/", 2)])
        self.assertNotIn("of ", self.land("status"))
        self.assertEqual(self.land("cap", "3", ok=False), refusal)
        self.assertEqual(self.land("cap", "4"), "repository cap is now 4 changes in flight")

    def test_an_armed_reservation_counts_toward_its_prefixs_share_until_the_winner_claims(self):
        self.init()
        self.share("docs/", 1)
        self.reserve("docs/", "a.txt", "R4")
        self.assertEqual(self.claim("docs/D9", "b.txt", ok=False), "land: docs/ is at its share: 1 of 1")
        self.assertEqual(self.claim("docs/D9", "a.txt"), "L1")
        self.assertEqual(self.claim("docs/D10", "b.txt", ok=False), "land: docs/ is at its share: 1 of 1")

    def test_a_reservation_counts_toward_the_cap_again_after_its_taken_lease_is_released(self):
        self.reservation_counts_again_after_its_lease_ends(
            "release", lambda: self.land("cap", "1"), "repository at its cap: 1 of 1 changes in flight (S1 for docs/)", "engine/D3")
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1, changes in flight: 1 of 1. author policy: any")

    def test_a_reservation_counts_toward_the_cap_again_after_its_taken_lease_expires(self):
        self.reservation_counts_again_after_its_lease_ends(
            "expire", lambda: self.land("cap", "1"), "repository at its cap: 1 of 1 changes in flight (S1 for docs/)", "engine/D3")

    def test_a_reservation_counts_toward_its_share_again_after_its_taken_lease_is_released(self):
        self.reservation_counts_again_after_its_lease_ends(
            "release", lambda: self.share("docs/", 1), "docs/ is at its share: 1 of 1", "docs/D8")

    def test_a_reservation_counts_toward_its_share_again_after_its_taken_lease_expires(self):
        self.reservation_counts_again_after_its_lease_ends(
            "expire", lambda: self.share("docs/", 1), "docs/ is at its share: 1 of 1", "docs/D8")

    def test_a_reservation_stays_out_of_the_count_while_any_lease_taken_from_it_is_live(self):
        self.init()
        self.land("cap", "2")
        self.reserve("docs/", "a.txt,b.txt,c.txt", "R4")
        self.assertEqual(self.claim("docs/D7", "a.txt"), "L1")
        self.assertEqual(self.claim("docs/D7", "b.txt"), "L2")
        self.land("lease", "release", "L1")
        self.assertEqual(self.claim("engine/D3", "lib"), "L3")
        self.land("lease", "release", "L2")
        self.assertEqual(self.claim("ops/D1", "d.txt", ok=False),
                         "land: repository at its cap: 2 of 2 changes in flight (engine/D3, S1 for docs/)")
        self.assertEqual(self.claim("docs/D7", "c.txt"), "L4")
        self.assertEqual(self.listed(), ["L3 active engine/D3: lib", "L4 active docs/D7: c.txt"])

    def test_the_winner_renews_its_expired_lease_taken_from_a_reservation_at_a_full_cap(self):
        self.init()
        self.land("cap", "1")
        self.reserve("docs/", "a.txt,b.txt", "R4")
        self.assertEqual(self.claim("docs/D7", "a.txt", "--ttl-hours", "0"), "L1")
        self.assertEqual(self.land("lease", "renew", "L1"), "L1 renewed")
        self.assertEqual(self.claim("engine/D3", "lib", ok=False), "land: repository at its cap: 1 of 1 changes in flight (docs/D7)")
        self.assertEqual(self.listed(), ["L1 active docs/D7: a.txt", "S1 armed docs/ by R4: b.txt"])
