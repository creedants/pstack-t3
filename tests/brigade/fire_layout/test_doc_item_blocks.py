if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

class DocItemBlocksMixin:
    def test_startable_doc_item_lease_omits_catalog_for_other_holder(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("README.md,t3/runtime.md,t3/setup.md,docs/skills.md")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "docs/skills.md")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "t3/runtime.md")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "filler",
                     "--paths", "README.md")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b",
                                      "--paths", "t3/setup.md", "--doc-item", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("dish", "D1", "--state", "sent-back")
        text = self.brigade("startable")
        self.assertIn("T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); t3/runtime.md", text)
        self.assertIn("T2 normal fix, 0m: startable; t3/setup.md --doc-item", text)

    def test_startable_doc_item_expansion_omits_catalog_for_overlap(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("README.md,t3/runtime.md,t3/setup.md")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "t3/runtime.md")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "filler",
                     "--paths", "README.md")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b",
                                      "--paths", "t3/setup.md", "--doc-item", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("set", "--workers", "2")
        text = self.brigade("startable")
        self.assertIn("T1 normal fix, 0m: startable; t3/runtime.md", text)
        self.assertIn("T2 normal fix, 0m: startable; t3/setup.md --doc-item", text)

    def test_blocked_doc_item_recheck_unblocks_with_other_catalog_holder(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("README.md,t3/runtime.md,docs/skills.md")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "filler",
                     "--paths", "README.md")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "docs/skills.md")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b",
                                      "--paths", "t3/runtime.md", "--doc-item", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.assertEqual(self.blocked_note(self.at, "T2"),
                         {"kind": "workers", "tickets": ["T2"], "station": "bug-fix", "summary": "b",
                          "paths": "t3/runtime.md", "timebox": 60, "doc_item": True})
        self.brigade("dish", "D1", "--state", "sent-back")
        line = next(part for part in self.brigade("watch").splitlines() if "unblocked; run" in part)
        self.assertIn("--doc-item", line)
        ran = self.bash_unblocked(self.at, line)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        dish = self.table_row(self.at, "dishes.tsv", "D2")["paths"]
        self.assertEqual(dish, "t3/runtime.md,skills/pstack-runtime/SKILL.md,changes/perf%2Fd2.md")
        leases = [entry for entry in self.lease_lines() if "perf/D2" in entry]
        self.assertEqual(len(leases), 1)
        self.assertNotIn("docs/skills.md", leases[0])

    def test_blocked_legacy_note_without_doc_item_stays_blocked_on_catalog(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("README.md,t3/runtime.md,docs/skills.md")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "filler",
                     "--paths", "README.md")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "docs/skills.md")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b",
                                      "--paths", "t3/runtime.md", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        note = self.blocked_note(self.at, "T2")
        self.assertNotIn("doc_item", note)
        self.brigade("dish", "D1", "--state", "sent-back")
        lines = self.brigade("watch").splitlines()
        self.assertFalse(any(part.startswith("T2: unblocked") for part in lines))
        self.assertTrue(any(part.startswith("T2: waiting on") for part in lines))

    def test_blocked_doc_item_with_explicit_catalog_path_stays_blocked(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("README.md,docs/skills.md")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "filler",
                     "--paths", "README.md")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "docs/skills.md")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b",
                                      "--paths", "docs/skills.md", "--doc-item", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.assertEqual(self.blocked_note(self.at, "T2")["doc_item"], True)
        self.brigade("dish", "D1", "--state", "sent-back")
        lines = self.brigade("watch").splitlines()
        self.assertFalse(any(part.startswith("T2: unblocked") for part in lines))
        self.assertTrue(any(part.startswith("T2: waiting on") for part in lines))
