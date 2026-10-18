from .support import (
    setUpModule,
    BrigadeTest,
    _ItemsRace,
    DOC_SETTINGS,
    Path,
    ROOT,
    SCRIPT,
    json,
    mock,
    os,
    re,
    runpy,
    subprocess,
)


class ItemsPathsTest(BrigadeTest, _ItemsRace):
    def test_fire_shared_doc_with_two_tickets_is_refused(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "50")
        self.seed_doc_settings()
        for doc in self.SHARED_DOCS:
            with self.subTest(doc=doc):
                self.brigade("ticket", "add", "--kind", "fix", "--summary", f"First {doc}")
                self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Second {doc}")
                ids = f"T{2 * self.SHARED_DOCS.index(doc) + 1},T{2 * self.SHARED_DOCS.index(doc) + 2}"
                before = self.store_files()
                self.assertEqual(self.brigade("fire", "--tickets", ids, "--station", "bug-fix", "--summary", f"Fix {doc}",
                                              "--paths", doc, "--timebox", "60", ok=False),
                                 self.doc_line((doc,), count=2))
                self.assertEqual(self.store_files(), before)

    def test_fire_shared_doc_with_ride_along_counts_every_ticket(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "50")
        self.seed_doc_settings()
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("ticket", "add", "--summary", "Lead work", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Ride work", "--kind", "polish", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix",
                                      "--summary", "Both", "--paths", "docs/guide.md", "--timebox", "60", ok=False),
                         self.doc_line(("docs/guide.md",), count=2))
        self.assertEqual(self.store_files(), before)

    def test_fire_shared_doc_refusal_order(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.seed_doc_settings()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "First")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Second")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Third")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "First"), "D1")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Again",
                                      "--paths", "docs/guide.md", "--timebox", "120", ok=False),
                         "brigade: T1 is assigned, not waiting")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second",
                                      "--paths", "docs/guide.md", "--timebox", "120", ok=False),
                         self.doc_line(("docs/guide.md",), timebox=120))
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second",
                                      "--paths", "docs/guide.md", "--timebox", "120", "--kind", "feature", ok=False),
                         self.doc_line(("docs/guide.md",), timebox=120))
        self.assertEqual(self.store_files(), before)

    def test_fire_shared_doc_refusal_precedes_no_kind_before_a_lease(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.seed_doc_settings()
        self.rail_before_kinds("a")
        before = self.store_files()
        proc = self.fire_with_fake_land(case, "--tickets", "T1", "--station", "feature", "--summary", "Do a",
                                        "--paths", "docs/guide.md", "--timebox", "120")
        _, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()),
                         (1, self.doc_line(("docs/guide.md",), timebox=120)))
        self.assertFalse((case / "land-calls").exists())
        self.assertEqual(self.store_files(), before)

    def test_fire_shared_doc_wins_over_a_lease_clash(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            self.land("lease", "claim", "--holder", "engine/D9", "--paths", "docs/guide.md")
            leases = self.land("lease", "list")
            before = self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                          "--paths", "docs/guide.md", "--timebox", "120", ok=False),
                             self.doc_line(("docs/guide.md",), timebox=120))
            self.assertEqual(self.land("lease", "list"), leases)
            self.assertEqual(self.store_files(), before)

    def test_fire_doc_item_kind_mismatch_still_refuses(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a",
                                      "--paths", "docs/guide.md", "--timebox", "120", "--doc-item", "--kind", "feature",
                                      ok=False),
                         "brigade: T1 has kind fix, and --kind feature does not match it; "
                         "drop --kind, or run ticket set T1 --kind feature first when no item in flight names T1")
        self.assertEqual(self.store_files(), before)

    def test_fire_leading_slash_parent_path_refuses_and_changes_no_file(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            leases = self.land("lease", "list")
            before = self.store_files()
            log = (self.at / "log.tsv").read_bytes()
            rail = (self.at / "rail.tsv").read_bytes()
            dishes = (self.at / "dishes.tsv").read_bytes()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                          "--paths", "/../docs/guide.md", "--timebox", "120", ok=False),
                             self.doc_line(("docs/guide.md",), timebox=120))
            self.assertEqual(self.land("lease", "list"), leases)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "rail.tsv").read_bytes(),
                              (self.at / "dishes.tsv").read_bytes()), (log, rail, dishes))

    def test_lease_parts_match_landing_canonical(self):
        brigade = runpy.run_path(str(SCRIPT))
        land = runpy.run_path(str(ROOT / "t3/added/landing/scripts/land.py"))
        lease_parts = brigade["_lease_parts"]
        canonical = land["canonical"]
        inside = (
            ("/docs/guide.md", "docs/guide.md"),
            ("//docs//guide.md", "docs/guide.md"),
            ("docs/./guide.md", "docs/guide.md"),
            ("docs/a/../guide.md", "docs/guide.md"),
            ("\\docs\\guide.md", "docs/guide.md"),
            ("/../docs/guide.md", "docs/guide.md"),
            ("/t3/added/../added/brigade", "t3/added/brigade"),
            ("t3/added//brigade/", "t3/added/brigade"),
            ("/", ""),
            (".", ""),
            ("/.", ""),
            ("/./docs/guide.md", "docs/guide.md"),
            ("docs//guide.md", "docs/guide.md"),
        )
        for raw, want in inside:
            with self.subTest(raw=raw):
                self.assertEqual(lease_parts(raw), [want])
                self.assertEqual(canonical(raw), want)
        outside = ("../docs/guide.md", "a/../../b", "..", "../")
        for raw in outside:
            with self.subTest(raw=raw):
                self.assertEqual(lease_parts(raw), [])
                with self.assertRaises(land["LandError"]):
                    canonical(raw)

    def test_fire_untracked_skill_file_path_refuses(self):
        self.git_project()
        self.open()
        self.seed_doc_settings()
        for path in ("t3/added/new-skill/SKILL.md", "skills/new-skill/SKILL.md"):
            with self.subTest(path=path):
                (self.project / path).parent.mkdir(parents=True, exist_ok=True)
                (self.project / path).write_text("# Skill\n")
                ident = self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Long work on {path}")
                before = self.store_files()
                self.assertEqual(self.brigade("fire", "--tickets", ident, "--station", "bug-fix", "--summary", "Long",
                                              "--paths", path, "--timebox", "120", ok=False),
                                 self.doc_line((path,), timebox=120))
                self.assertEqual(self.store_files(), before)

    def test_fire_untracked_skill_directory_refuses(self):
        self.git_project()
        self.open()
        self.seed_doc_settings()
        for directory, doc in (("t3/added/new-skill", "t3/added/new-skill/SKILL.md"),
                               ("skills/new-skill", "skills/new-skill/SKILL.md")):
            with self.subTest(directory=directory):
                (self.project / doc).parent.mkdir(parents=True, exist_ok=True)
                (self.project / doc).write_text("# Skill\n")
                ident = self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Long work on {directory}")
                before = self.store_files()
                self.assertEqual(self.brigade("fire", "--tickets", ident, "--station", "bug-fix", "--summary", "Long",
                                              "--paths", directory, "--timebox", "120", ok=False),
                                 self.doc_line((doc,), timebox=120))
                self.assertEqual(self.store_files(), before)

    def test_fire_untracked_nested_repository_directory_refuses(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            nested = self.project / "t3/added/new-skill"
            (nested / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
            (nested / "SKILL.md").write_text("# Skill\n")
            subprocess.run(["git", "init", "-q"], cwd=nested, capture_output=True, check=True)
            leases = self.land("lease", "list")
            before = self.store_files()
            log = (self.at / "log.tsv").read_bytes()
            rail = (self.at / "rail.tsv").read_bytes()
            dishes = (self.at / "dishes.tsv").read_bytes()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                          "--paths", "t3/added/new-skill", "--timebox", "120", ok=False),
                             self.doc_line(("t3/added/new-skill/SKILL.md",), timebox=120))
            self.assertEqual(self.land("lease", "list"), leases)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "rail.tsv").read_bytes(),
                              (self.at / "dishes.tsv").read_bytes()), (log, rail, dishes))

    def test_fire_nested_repository_symlink_is_not_followed(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            nested = self.project / "t3/added/new-skill"
            (nested / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
            (nested / "SKILL.md").write_text("# Skill\n")
            subprocess.run(["git", "init", "-q"], cwd=nested, capture_output=True, check=True)
            outside = Path(self.temporary.name) / "outside"
            outside.mkdir()
            (outside / "SKILL.md").write_text("# Outside\n")
            os.symlink(outside, nested / "link")
            before = self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                          "--paths", "t3/added/new-skill", "--timebox", "120", ok=False),
                             self.doc_line(("t3/added/new-skill/SKILL.md",), timebox=120))
            self.assertEqual(self.store_files(), before)

    def test_fire_with_no_shared_docs_set_fires_on_a_shared_path(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "First")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Second")
        proc = self.fire_with_fake_land(case, "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Both",
                                        "--paths", "README.md", "--timebox", "120")
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()), (0, ""))
        self.assertTrue(out.strip().startswith("D1"), out)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Third")
        proc = self.fire_with_fake_land(case, "--tickets", "T3", "--station", "bug-fix", "--summary", "Doc",
                                        "--paths", "README.md", "--doc-item")
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()), (0, ""))
        self.assertEqual(self.doc_log_note(self.at, out.strip().split()[0]), "doc-item")

    def test_fire_reads_the_catalog_from_the_store_only(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "First")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Second")
            self.seed_doc_settings(sharedDocs=[{"path": "src/app.py", "kind": "file"}])
            self.assertIn("D1", self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Both",
                                             "--paths", "README.md", "--timebox", "120"))
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Third")
            before = self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "Third",
                                          "--paths", "src/app.py", "--timebox", "120", ok=False),
                             self.doc_line(("src/app.py",), timebox=120))
            self.assertEqual(self.store_files(), before)

    def test_fire_reads_a_file_directory_prefix_and_a_skill_prefix_from_the_store(self):
        self.tracked_project("README.md", "docs/guide.md", "docs/skills.md", "lib/thing/SKILL.md")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long work")
        cases = (
            ([{"path": "docs/", "kind": "file"}], "docs", ("docs/guide.md", "docs/skills.md")),
            ([{"path": "lib/", "kind": "skill"}], "lib/thing", ("lib/thing/SKILL.md",)),
            ([{"path": "lib", "kind": "skill"}], "lib/thing", ("lib/thing/SKILL.md",)),
        )
        for shared, given, held in cases:
            with self.subTest(shared=shared, given=given):
                self.seed_doc_settings(sharedDocs=shared)
                before = self.store_files()
                self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                              "--paths", given, "--timebox", "120", ok=False),
                                 self.doc_line(held, timebox=120))
                self.assertEqual(self.store_files(), before)

    def test_fire_reads_the_doc_timebox_from_the_store(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings(sharedDocs=[{"path": "docs/guide.md", "kind": "file"}], docTimebox=30)
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "At the limit")
            self.assertIn("D1", self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "At",
                                             "--paths", "docs/guide.md", "--timebox", "30"))
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Over the limit")
            before = self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Over",
                                          "--paths", "docs/guide.md", "--timebox", "31", ok=False),
                             self.doc_line(("docs/guide.md",), timebox=31, minutes=30))
            self.assertEqual(self.store_files(), before)

    def test_a_malformed_shared_docs_refuses_before_any_write(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            cases = (
                ("not a list", "it is not a list"),
                (["not an object"], "an entry is not an object"),
                ([{"path": "a.md", "kind": "file", "extra": 1}], "an entry has unknown key 'extra'"),
                ([{"kind": "file"}], "an entry's 'path' is not a repository path: None"),
                ([{"path": "../a.md", "kind": "file"}], "an entry's 'path' is not a repository path: '../a.md'"),
                ([{"path": "a.md", "kind": "doc"}], "an entry's 'kind' is not file or skill: 'doc'"),
            )
            for bad, why in cases:
                with self.subTest(bad=bad):
                    self.seed_doc_settings(sharedDocs=bad)
                    before = self.store_files()
                    leases = self.land("lease", "list")
                    sentence = f"restaurant.json sharedDocs {bad!r} is invalid: {why}"
                    refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                           "--paths", "docs/guide.md", "--timebox", "120", ok=False)
                    self.assertIn(f"brigade: warning: {sentence}", refused)
                    self.assertIn(f"brigade: {sentence}", refused)
                    self.assertEqual(self.store_files(), before)
                    self.assertEqual(self.land("lease", "list"), leases)

    def test_a_malformed_doc_timebox_refuses_before_any_write(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            for bad in (True, 0, "90", 2.5):
                with self.subTest(bad=bad):
                    self.seed_doc_settings(docTimebox=bad)
                    before = self.store_files()
                    leases = self.land("lease", "list")
                    sentence = (f"restaurant.json docTimebox {bad!r} is invalid: "
                                "it is not a whole number of minutes above zero")
                    refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                           "--paths", "docs/guide.md", "--timebox", "120", ok=False)
                    self.assertIn(f"brigade: warning: {sentence}", refused)
                    self.assertIn(f"brigade: {sentence}", refused)
                    self.assertEqual(self.store_files(), before)
                    self.assertEqual(self.land("lease", "list"), leases)

    def test_set_refuses_a_malformed_shared_docs_and_leaves_restaurant_json_unchanged(self):
        self.open()
        self.seed_doc_settings(sharedDocs="not a list")
        before = (self.at / "restaurant.json").read_bytes()
        refused = self.brigade("set", "--reporting", "digest", ok=False)
        self.assertIn("sharedDocs", refused)
        self.assertIn("it is not a list", refused)
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_set_refuses_a_malformed_doc_timebox_and_leaves_restaurant_json_unchanged(self):
        self.open()
        self.seed_doc_settings(docTimebox=0)
        before = (self.at / "restaurant.json").read_bytes()
        refused = self.brigade("set", "--reporting", "digest", ok=False)
        self.assertIn("docTimebox", refused)
        self.assertIn("it is not a whole number of minutes above zero", refused)
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_set_writes_with_valid_shared_docs_and_doc_timebox(self):
        self.open()
        self.seed_doc_settings(sharedDocs=[{"path": "docs/guide.md", "kind": "file"}], docTimebox=30)
        self.brigade("set", "--reporting", "digest")
        meta = json.loads((self.at / "restaurant.json").read_text())
        self.assertEqual(meta["reporting"], "digest")
        self.assertEqual(meta["sharedDocs"], [{"path": "docs/guide.md", "kind": "file"}])
        self.assertEqual(meta["docTimebox"], 30)

    def test_store_config_documents_the_shared_docs_value_this_suite_seeds(self):
        text = (ROOT / "t3/added/brigade/store-config.md").read_text()
        blocks = [block for block in re.findall(r"```json\n(.*?)```", text, re.S) if "sharedDocs" in block]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(json.loads(blocks[0]), DOC_SETTINGS)

    def test_a_refused_fire_that_records_a_block_records_paths_on_a_ticket_that_has_none(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate",
                                   "--paths", "src", ok=False)
            self.assertEqual(refused, "brigade: nothing fired: paths overlap L1 held by engine/D1 on src")
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "src")
            self.assertEqual(len((self.at / "dishes.tsv").read_text().splitlines()), 1)
            self.assertIn("engine/D1", self.land("lease", "list"))
            self.assertEqual(self.brigade("startable"), "\n".join([
                "workers: 0 of 2 running, 2 idle, auto-start: normal",
                "waiting tickets: 1, blocked: 1",
                "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src",
            ]))
            self.assertEqual(self.brigade("ticket", "list"),
                             "T1 waiting normal fix [user] Fix the gate blocked: lease")
            self.assertEqual(self.brigade("watch"), "\n".join([
                "workers: 0 of 2 running, 2 idle, auto-start: normal",
                "T1: waiting on L1 (engine/D1)",
            ]))

    def test_a_fire_keeps_paths_a_ticket_already_records(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Keep mine", "--paths", "lib")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Keep mine",
                                   "--paths", "src", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "lib")
            self.assertEqual(self.blocked_note(self.at, "T1")["paths"], "src")
            self.land("lease", "release", "L1")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Keep mine",
                                          "--paths", "src"), "D1 (lease L2 held by perf/D1)")
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "lib")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], "src,changes/perf%2Fd1.md")

    def test_a_dropped_item_keeps_the_paths_its_fire_recorded(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate",
                                          "--paths", "src"), "D1 (lease L1 held by perf/D1)")
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "src")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], "src,changes/perf%2Fd1.md")
            self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "src")
            self.assertEqual(self.brigade("startable"), "\n".join([
                "workers: 0 of 2 running, 2 idle, auto-start: normal",
                "waiting tickets: 1, startable: 1",
                "T1 normal fix, 0m: startable; src",
                "  Fix the gate",
            ]))
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate")
            self.assertEqual(self.brigade("watch"), "\n".join([
                "workers: 0 of 2 running, 2 idle, auto-start: normal",
                "no work in progress",
            ]))

    def test_a_fire_with_no_paths_leaves_an_empty_paths_cell_empty(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "")
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Wait")
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Wait", "--paths", "",
                               ok=False)
        self.assertIn("nothing fired: 1 of 1 workers running", refused)
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["paths"], "")
        self.assertEqual(self.blocked_note(self.at, "T2")["paths"], "")
