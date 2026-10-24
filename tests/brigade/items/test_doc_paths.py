if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import Path, ROOT, SCRIPT, mock, os, runpy, subprocess

class DocPathsMixin:
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

    def test_fire_of_a_ticket_with_no_kind_on_paths_another_lease_holds_is_refused_for_the_kind_and_logs_no_block(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.rail_before_kinds("a")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            leases, before = self.land("lease", "list"), self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--paths", "src",
                                          ok=False),
                             f"brigade: T1 has no kind and station feature gives none; pass --kind {self.KIND_CHOICE}")
            self.assertEqual((self.land("lease", "list"), self.store_files()), (leases, before))
            refused = self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--paths", "src",
                                   "--kind", "upkeep", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            self.assertEqual(self.blocked_note(self.at, "T1"),
                             {"kind": "lease", "tickets": ["T1"], "station": "feature", "summary": "Do a", "paths": "src",
                              "timebox": 60, "work": "upkeep"})

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

    def test_fire_with_paths_of_a_lead_ticket_with_no_kind_is_refused_before_it_claims_a_lease_and_changes_no_file(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.rail_before_kinds("a")
        before = self.store_files()
        proc = self.fire_with_fake_land(case, "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--paths", "src")
        _, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()),
                         (1, f"brigade: T1 has no kind and station feature gives none; pass --kind {self.KIND_CHOICE}"))
        calls = (case / "land-calls").read_text().splitlines() if (case / "land-calls").exists() else []
        self.assertFalse(any(line.startswith("lease claim") for line in calls))
        self.assertEqual(self.store_files(), before)

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
