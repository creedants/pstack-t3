if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import DOC_SETTINGS, ROOT, json, mock, os, re

class DocSharedMixin:
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

    def test_set_refuses_a_malformed_doc_timebox_and_leaves_restaurant_json_unchanged(self):
        self.open()
        self.seed_doc_settings(docTimebox=0)
        before = (self.at / "restaurant.json").read_bytes()
        refused = self.brigade("set", "--reporting", "digest", ok=False)
        self.assertIn("docTimebox", refused)
        self.assertIn("it is not a whole number of minutes above zero", refused)
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_set_refuses_a_malformed_shared_docs_and_leaves_restaurant_json_unchanged(self):
        self.open()
        self.seed_doc_settings(sharedDocs="not a list")
        before = (self.at / "restaurant.json").read_bytes()
        refused = self.brigade("set", "--reporting", "digest", ok=False)
        self.assertIn("sharedDocs", refused)
        self.assertIn("it is not a list", refused)
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
