class DerivedFireMixin:
    def test_missing_map_refuses_fire_affected_startable_and_claim(self):
        self.git_project()
        self.commit_paths("t3/runtime.md,t3/setup.md")
        self.init_landing("--generated", self.GENERATED)
        self.landing_env()
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        want = "land.py init --generated-map"
        fire_err = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                "--paths", "t3/runtime.md", ok=False)
        self.assertIn(want, fire_err)
        import json
        meta_path = self.at / "restaurant.json"
        meta = json.loads(meta_path.read_text())
        meta["affectedTests"] = {
            "always": "tests.test_pstack_t3",
            "entryPoints": ["t3/added/brigade/scripts/brigade.py"],
            "helpers": [],
            "suite": "python3 -m unittest discover -s tests",
        }
        meta_path.write_text(json.dumps(meta))
        affected_err = self.brigade("affected", "--root", str(self.project), "--paths", "t3/runtime.md", ok=False)
        self.assertIn(want, affected_err)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        startable_text = self.brigade("startable", ok=False)
        self.assertIn(want, startable_text)
        from ..support import run_globals, SCRIPT
        claimed = run_globals(self, SCRIPT)["claim_paths"]
        with self.assertRaises(Exception) as ctx:
            claimed({"projectRoot": str(self.project), "restaurant": "perf"}, "t3/runtime.md", "perf/d1", False)
        self.assertIn(want, str(ctx.exception))

    def test_startable_doc_item_flag_feeds_derived_fire_omits_catalog(self):
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
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b",
                                      "--paths", "t3/runtime.md", "--doc-item", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "docs/skills.md")
        text = self.brigade("startable")
        self.assertIn("T2 normal fix, 0m: startable; t3/runtime.md --doc-item", text)
        line = next(part for part in text.splitlines() if part.startswith("T2 "))
        raw = line.split("; ", 1)[1]
        self.assertTrue(raw.endswith(" --doc-item"))
        paths = raw[:-len(" --doc-item")]
        self.assertEqual(paths, "t3/runtime.md")
        out = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b",
                           "--paths", paths, "--doc-item")
        self.assertIn("D2 (lease L3 held by perf/D2)", out)
        self.assertIn("added skills/pstack-runtime/SKILL.md", out)
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D2")["paths"],
                         "t3/runtime.md,skills/pstack-runtime/SKILL.md,changes/perf%2Fd2.md")
        leases = [entry for entry in self.lease_lines() if "perf/D2" in entry]
        self.assertEqual(len(leases), 1)
        self.assertNotIn("docs/skills.md", leases[0])

    def test_startable_blocked_note_paths_agree_with_watch_for_derived_fire(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("README.md,t3/runtime.md,docs/skills.md")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--paths", "t3/runtime.md")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "filler",
                     "--paths", "README.md")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b",
                                      "--paths", "t3/runtime.md,docs/skills.md", "--doc-item", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "docs/skills.md")
        text = self.brigade("startable")
        self.assertIn("T2 normal fix, 0m: blocked, waiting on L2 (engine/D1); t3/runtime.md,docs/skills.md --doc-item", text)
        lines = self.brigade("watch").splitlines()
        self.assertFalse(any(part.startswith("T2: unblocked") for part in lines))
        self.assertTrue(any(part.startswith("T2: waiting on L2 (engine/D1)") for part in lines))
        line = next(part for part in text.splitlines() if part.startswith("T2 "))
        raw = line.split("; ", 1)[1]
        self.assertTrue(raw.endswith(" --doc-item"))
        saved = raw[:-len(" --doc-item")]
        self.assertEqual(saved, "t3/runtime.md,docs/skills.md")
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b",
                               "--paths", saved, "--doc-item", ok=False)
        self.assertIn("paths overlap L2 held by engine/D1 on docs/skills.md", refused)

    def test_startable_blocked_dot_path_normalizes_for_generated_overlap_and_derived_fire(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("README.md,t3/runtime.md,skills/pstack-runtime/SKILL.md,./t3/runtime.md")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "first", "--paths", "skills/pstack-runtime/SKILL.md")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second", "--paths", "t3/runtime.md")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "filler",
                     "--paths", "README.md")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "second",
                                      "--paths", "./t3/runtime.md", "--doc-item", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("set", "--workers", "2")
        text = self.brigade("startable")
        self.assertIn("T1 normal fix, 0m: startable; skills/pstack-runtime/SKILL.md", text)
        self.assertIn("T2 normal fix, 0m: blocked, starts after T1; ./t3/runtime.md --doc-item", text)
        line = next(part for part in text.splitlines() if part.startswith("T2 "))
        raw = line.split("; ", 1)[1]
        self.assertTrue(raw.endswith(" --doc-item"))
        saved = raw[:-len(" --doc-item")]
        self.assertEqual(saved, "./t3/runtime.md")
        self.assertIn("D2 (lease", self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "first",
                                                "--paths", "skills/pstack-runtime/SKILL.md"))
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "second",
                               "--paths", saved, "--doc-item", ok=False)
        self.assertIn("paths overlap", refused)
        self.assertIn("skills/pstack-runtime/SKILL.md", refused)

    def test_startable_empty_blocked_note_falls_back_to_rail_and_ticket_set_updates_rail_preview(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("README.md,t3/runtime.md,t3/setup.md")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second", "--paths", "t3/runtime.md")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "filler",
                     "--paths", "README.md")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "second",
                                      "--doc-item", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.assertEqual(self.blocked_note(self.at, "T2")["paths"], "")
        self.assertEqual(self.blocked_note(self.at, "T2")["doc_item"], True)
        self.brigade("dish", "D1", "--state", "sent-back")
        text = self.brigade("startable")
        self.assertIn("T2 normal fix, 0m: startable; t3/runtime.md", text)
        t2_line = next(part for part in text.splitlines() if part.startswith("T2 "))
        self.assertFalse(t2_line.endswith(" --doc-item"))
        self.brigade("ticket", "set", "T2", "--paths", "t3/setup.md")
        text = self.brigade("startable")
        self.assertIn("T2 normal fix, 0m: startable; t3/setup.md", text)
        t2_line = next(part for part in text.splitlines() if part.startswith("T2 "))
        self.assertFalse(t2_line.endswith(" --doc-item"))
