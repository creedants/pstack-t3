from .support import (
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


class LandingTest(LandingTestBase):
    def test_leases_refuse_overlap_including_aliases_and_the_whole_repo(self):
        self.init()
        self.assertEqual(self.land("lease", "claim", "--holder", "perf/D1", "--paths", "lib"), "L1")
        self.assertIn("L1 held by perf/D1 on lib", self.land("lease", "claim", "--holder", "bugs/D1", "--paths", "a.txt/../lib/x.py", ok=False))
        self.assertIn("L1 held by perf/D1", self.land("lease", "claim", "--holder", "bugs/D1", "--paths", ".", ok=False))
        self.assertIn("leaves the repository", self.land("lease", "claim", "--holder", "bugs/D1", "--paths", "../etc", ok=False))
        self.assertEqual(self.land("lease", "claim", "--holder", "bugs/D1", "--paths", "lib2,a.txt"), "L2")

    def test_the_cap_refuses_a_claim_naming_the_holders_and_frees_on_release(self):
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--cap", "2")
        self.assertEqual(self.claim("a/D1", "a.txt"), "L1")
        self.assertEqual(self.claim("b/D1", "b.txt"), "L2")
        self.assertEqual(self.claim("c/D1", "lib", ok=False), "land: repository at its cap: 2 of 2 changes in flight (a/D1, b/D1)")
        self.assertEqual(self.land("lease", "release", "L1"), "L1 released")
        self.assertEqual(self.claim("c/D1", "lib"), "L3")

    def test_the_cap_is_set_and_cleared_on_its_own_and_shown_in_status(self):
        self.init()
        self.assertNotIn("changes in flight", self.land("status"))
        self.assertEqual(self.land("cap", "4"), "repository cap is now 4 changes in flight")
        self.claim("a/D1", "a.txt")
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1, changes in flight: 1 of 4. author policy: any")
        self.assertEqual(self.land("cap", "0"), "repository cap cleared")
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1. author policy: any")
        self.assertIn("negative", self.land("cap", "-1", ok=False))

    def test_ten_concurrent_claims_under_a_cap_of_three_make_exactly_three_leases(self):
        self.init()
        self.land("cap", "3")
        claims = [real_popen(
            ProcessReason.RACE,
            [sys.executable, str(SCRIPT), "--repo", str(self.work), "lease", "claim",
             "--holder", f"r/D{n}", "--paths", f"p{n}.txt"],
            env=os.environ.copy())
            for n in range(10)]
        results = [(claim.wait(), *claim.communicate()) for claim in claims]
        self.assertEqual(sorted(out.strip() for code, out, _ in results if code == 0), ["L1", "L2", "L3"])
        refused = [err for code, _, err in results if code != 0]
        self.assertEqual(len(refused), 7)
        self.assertTrue(all("repository at its cap: 3 of 3 changes in flight" in err for err in refused), refused)
        self.assertEqual(len(self.land("lease", "list").splitlines()), 3)

    def test_a_submitted_lease_counts_toward_the_cap_and_a_bounce_keeps_the_count(self):
        self.init()
        self.land("cap", "2")
        self.assertEqual(self.queue_one(text="BROKEN\n"), "E1")
        self.assertEqual(self.claim("r/D2", "b.txt"), "L2")
        self.assertIn("changes in flight: 2 of 2", self.land("status"))
        self.assertEqual(self.claim("r/D3", "lib", ok=False), "land: repository at its cap: 2 of 2 changes in flight (r/D1, r/D2)")
        self.assertIn("bounced E1 (r/D1)", self.land("land"))
        self.assertIn("changes in flight: 2 of 2", self.land("status"))
        self.assertIn("at its cap", self.claim("r/D3", "lib", ok=False))

    def test_an_expired_lease_does_not_count_toward_the_cap(self):
        self.init()
        self.land("cap", "1")
        self.assertEqual(self.claim("a/D1", "a.txt", "--ttl-hours", "0"), "L1")
        self.assertEqual(self.claim("b/D1", "b.txt"), "L2")

    def test_renewing_an_expired_lease_refuses_an_overlap_another_holder_now_has(self):
        self.init()
        self.claim("docs/D9", "a.txt", "--ttl-hours", "0")
        self.assertEqual(self.claim("engine/D9", "a.txt"), "L2")
        self.assertEqual(self.land("lease", "renew", "L1", ok=False),
                         "land: L1 expired and L2 held by engine/D9 now covers a.txt; claim again after it is released")
        listed = self.land("lease", "list").splitlines()
        self.assertEqual([line.split(" until ")[0] for line in listed], ["L2 active engine/D9"])

    def test_renewing_an_expired_lease_refuses_when_the_cap_filled(self):
        self.init()
        self.land("cap", "2")
        self.claim("a/D1", "a.txt", "--ttl-hours", "0")
        self.claim("b/D1", "b.txt")
        self.claim("c/D1", "lib")
        self.assertEqual(self.land("lease", "renew", "L1", ok=False), "land: repository at its cap: 2 of 2 changes in flight (b/D1, c/D1)")
        self.land("lease", "release", "L3")
        self.assertEqual(self.land("lease", "renew", "L1"), "L1 renewed")
        self.assertEqual([line.split(" until ")[0] for line in self.land("lease", "list").splitlines()], ["L1 active a/D1", "L2 active b/D1"])

    def test_renew_if_live_refuses_an_expired_lease_and_renews_a_live_one(self):
        self.init()
        self.claim("a/D1", "a.txt", "--ttl-hours", "0")
        self.claim("b/D1", "b.txt")
        self.assertRegex(self.land("lease", "renew", "L1", "--if-live", ok=False), r"^land: L1 expired at \d{4}-\d\d-\d\dT\d\d:\d\d$")
        self.assertEqual(self.land("lease", "renew", "L2", "--if-live", "--ttl-hours", "48"), "L2 renewed")
        self.assertEqual(self.land("lease", "list").split(" until ")[1][:10], stamp_in(48)[:10])

    def test_renew_names_a_submitted_a_released_and_a_missing_lease(self):
        self.init()
        self.queue_one()
        self.claim("r/D2", "b.txt")
        self.land("lease", "release", "L2")
        self.assertEqual(self.land("lease", "renew", "L1", ok=False), "land: L1 is submitted; the queue holds it")
        self.assertEqual(self.land("lease", "renew", "L2", ok=False), "land: L2 is released")
        self.assertEqual(self.land("lease", "renew", "L9", ok=False), "land: no L9")

    def test_an_owner_floor_rises_and_never_lowers(self):
        self.init()
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "2"), "docs/ at generation 2")
        before = self.owner_state()
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "2"), "docs/ at generation 2")
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "1", ok=False),
                         "land: docs/ is at generation 2; a floor never lowers")
        self.assertEqual(self.owner_state(), before)
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "3"), "docs/ at generation 3")

    def test_lease_writes_refuse_an_owner_below_the_floor_and_change_nothing(self):
        self.init()
        self.assertEqual(self.claim("docs/D3", "a.txt", "--owner", "docs/@1"), "L1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0", "--owner", "docs/@1")
        self.land("owner", "--prefix", "docs/", "--generation", "2")
        before = self.lease_row(1)
        stale = "land: owner docs/@1 is stale; docs/ is at generation 2"
        self.assertEqual(self.land("lease", "release", "L1", "--owner", "docs/@1", ok=False), stale)
        self.assertEqual(self.land("lease", "renew", "L1", "--owner", "docs/@1", ok=False), stale)
        self.assertEqual(self.claim("docs/D4", "b.txt", "--owner", "docs/@1", ok=False), stale)
        self.assertEqual(self.lease_row(1), before)
        self.assertEqual(self.land("lease", "renew", "L1", "--owner", "docs/@2"), "L1 renewed")
        self.assertEqual(self.land("lease", "release", "L1", "--owner", "docs/@2"), "L1 released")

    def test_a_floored_prefix_needs_an_owner_that_covers_the_holder(self):
        self.init()
        self.land("owner", "--prefix", "docs/", "--generation", "2")
        self.assertEqual(self.claim("docs/D1", "a.txt", ok=False), "land: docs/D1 is under docs/ at generation 2; pass --owner docs/@2")
        self.assertEqual(self.claim("docs/D1", "a.txt", "--owner", "engine/@2", ok=False),
                         "land: owner engine/@2 does not cover holder docs/D1")
        self.assertEqual(self.claim("docs/D1", "a.txt", "--owner", "docs/@2"), "L1")
        self.assertEqual(self.claim("engine/D1", "b.txt"), "L2")
        self.assertEqual(self.land("lease", "release", "L2"), "L2 released")

    def test_submit_refuses_a_stale_owner_and_queues_for_the_current_one(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        lease = self.claim("docs/D1", "a.txt", "--owner", "docs/@1")
        self.land("owner", "--prefix", "docs/", "--generation", "2")
        submit = ("submit", "--holder", "docs/D1", "--branch", "w1", "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)
        self.assertEqual(self.land(*submit, "--owner", "docs/@1", ok=False), "land: owner docs/@1 is stale; docs/ is at generation 2")
        self.assertEqual(self.land("status", "--holder", "docs/D1"), "no entries held by docs/D1")
        self.assertEqual(self.land(*submit, "--owner", "docs/@2"), "E1")

    def test_lease_check_runs_admission_without_claiming(self):
        self.init()
        self.claim("a/D1", "a.txt")
        self.land("lease", "release", "L1")
        self.assertEqual(self.land("lease", "check", "--holder", "b/D1", "--paths", "a.txt"), "free")
        self.claim("c/D1", "a.txt,b.txt")
        self.assertEqual(self.lease_check("b/D1", "a.txt"), (1, "L2 held by c/D1 on a.txt, b.txt"))
        self.land("cap", "1")
        self.assertEqual(self.lease_check("b/D1", "lib"), (1, "repository at its cap: 1 of 1 changes in flight (c/D1)"))
        self.assertEqual(len(self.land("lease", "list").splitlines()), 1)

    def test_status_holder_lists_one_holder_or_a_prefix(self):
        self.init()
        sha1 = self.worker("w1", {"a.txt": "one\n"})
        sha2 = self.worker("w2", {"b.txt": "two\n"})
        sha3 = self.worker("w3", {"lib/x.py": "three\n"})
        for holder, branch, sha, path in [("engine/D1", "w1", sha1, "a.txt"), ("engine/D2", "w2", sha2, "b.txt"), ("enginex/D1", "w3", sha3, "lib")]:
            lease = self.claim(holder, path)
            self.land("submit", "--holder", holder, "--branch", branch, "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)
        self.assertEqual(self.land("status", "--holder", "engine/D2"), f"E2 queued (engine/D2, {sha2[:12]})")
        self.land("land")
        landed = sh("git", "rev-parse", "main~2", cwd=self.base / "origin.git")
        self.assertEqual(self.land("status", "--holder", "engine/").splitlines(),
                         [f"E1 landed (engine/D1, {sha1[:12]}) as {landed[:12]}",
                          f"E2 landed (engine/D2, {sha2[:12]}) as {sh('git', 'rev-parse', 'main~1', cwd=self.base / 'origin.git')[:12]}"])
        self.assertEqual(self.land("status", "--holder", "docs/D1"), "no entries held by docs/D1")

    def test_status_holder_sha_matches_the_whole_commit(self):
        import sqlite3
        from contextlib import closing
        self.init()
        sha = self.worker("w1", {"a.txt": "one\n"})
        lease = self.claim("engine/D1", "a.txt")
        self.land("submit", "--holder", "engine/D1", "--branch", "w1", "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)
        other = sha[:12] + ("1" if sha[12] == "0" else "0") + sha[13:]
        database = next((self.base / "state").glob("pstack-t3/landing/*/land.db"))
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("INSERT INTO entry (at, holder, branch, sha, base, fingerprint, lease, reviewer, state, note) "
                       "SELECT at, holder, branch, ?, base, fingerprint, lease, reviewer, 'bounced', 'other' FROM entry",
                       (other,))
        self.assertEqual(self.land("status", "--holder", "engine/D1").splitlines(),
                         [f"E1 queued (engine/D1, {sha[:12]})", f"E2 bounced (engine/D1, {sha[:12]}): other"])
        self.assertEqual(self.land("status", "--holder", "engine/D1", "--sha", sha), f"E1 queued (engine/D1, {sha[:12]})")
        self.assertEqual(self.land("status", "--holder", "engine/D1", "--sha", sha.upper()),
                         f"E1 queued (engine/D1, {sha[:12]})")
        self.assertEqual(self.land("status", "--holder", "engine/D1", "--sha", other),
                         f"E2 bounced (engine/D1, {sha[:12]}): other")
        self.assertEqual(self.land("status", "--holder", "engine/D2", "--sha", sha), f"no entries held by engine/D2 at {sha}")

    def test_status_holder_shows_a_bounce_reason(self):
        self.init()
        self.queue_one(text="BROKEN\n")
        self.land("land")
        line = self.land("status", "--holder", "r/D1")
        self.assertRegex(line, r"^E1 bounced \(r/D1, [0-9a-f]{12}\): checks failed: `./check.sh` exited 1")

    def test_the_mode_line_names_the_holders_of_unreleased_leases(self):
        with self.fake_gh():
            self.init(mode="human")
            self.claim("engine/D3", "a.txt")
            self.claim("docs/D1", "b.txt")
            self.claim("old/D1", "lib", "--ttl-hours", "0")
            self.assertEqual(self.land("mode", "merge"), "landing mode is now merge; leases held by docs/D1, engine/D3")
            self.land("lease", "release", "L1")
            self.land("lease", "release", "L2")
            self.assertEqual(self.land("mode", "push"), "landing mode is now push")

    def test_submit_requires_the_holders_own_lease_covering_every_changed_path(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "a2\n", "b.txt": "b2\n"})
        self.land("lease", "claim", "--holder", "perf/D1", "--paths", "a.txt")
        self.land("lease", "claim", "--holder", "perf/D2", "--paths", "b.txt")
        self.assertIn("not an active lease held by bugs/D9",
                      self.land("submit", "--holder", "bugs/D9", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, ok=False))
        self.assertIn("outside L1: b.txt",
                      self.land("submit", "--holder", "perf/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, ok=False))

    def test_non_ascii_file_names_match_their_lease(self):
        self.init()
        self.assertEqual(self.queue_one(path="café.txt"), "E1")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_a_reservation_refuses_another_holders_claim_and_admits_the_winner(self):
        self.init()
        self.assertEqual(self.reserve("docs/", "a.txt,b.txt", "R4"), "S1")
        refusal = self.claim("engine/D3", "a.txt", ok=False)
        self.assertEqual(refusal, f"land: paths reserved for docs/ by ruling R4 until {self.reserved_until(1)}")
        self.assertEqual(self.lease_check("engine/D3", "b.txt"), (1, refusal.removeprefix("land: ")))
        self.assertEqual(self.claim("engine/D3", "lib"), "L1")
        self.assertEqual(self.claim("docs/D7", "a.txt,b.txt"), "L2")
        self.assertEqual(self.listed(), ["L1 active engine/D3: lib", "L2 active docs/D7: a.txt, b.txt"])
        self.assertEqual(self.ruling_rows("reservation")[0]["state"], "claimed")

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
