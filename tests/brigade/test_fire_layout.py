from .support import (
    setUpModule,
    BrigadeTest,
    Path,
    ROOT,
)


class FireLayoutTest(BrigadeTest):
    def vendor_layout(self):
        target = self.project / "scripts"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("build.py", "cli_reference.py"):
            (target / name).write_bytes((ROOT / "scripts" / name).read_bytes())

    def layout_fire(self, paths, doc_item=False):
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        args = ["fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                "--paths", paths]
        if doc_item:
            args.append("--doc-item")
        return self.brigade(*args)

    def dish_paths(self):
        return self.table_row(self.at, "dishes.tsv", "D1")["paths"]

    def fresh_fire(self, given, with_layout, doc_item=False):
        base = Path(self.temporary.name) / f"case-{self.case_no}"
        self.case_no += 1
        store = base / "store"
        project = base / "Bridge Kit"
        project.mkdir(parents=True, exist_ok=True)
        self.store, self.project = store, project
        self.at = store / "bridge-kit" / "perf"
        self.git_project()
        if with_layout:
            self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        args = ["fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                "--paths", given]
        if doc_item:
            args.append("--doc-item")
        out = self.brigade(*args)
        return out, self.dish_paths(), self.lease_lines()

    def test_source_under_each_mapped_root_adds_its_copy(self):
        given = ("t3/runtime.md,t3/setup.md,t3/agents/a.md,"
                 "t3/added/brigade/scripts/brigade_lib/store.py,"
                 "t3/overrides/teach/SKILL.md,t3/scripts/roles.py")
        copies = ("skills/pstack-runtime/SKILL.md", "docs/skills.md", "skills/setup-pstack/SKILL.md",
                  "skills/pstack-runtime/agents/a.md", "skills/brigade/scripts/brigade_lib/store.py",
                  "skills/teach/SKILL.md", "skills/pstack-runtime/scripts/roles.py", "docs/cli/roles.md")
        added = ", ".join(copies)
        frag = "changes/perf%2Fd1.md"
        out = self.layout_fire(given)
        self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {added}")
        self.assertEqual(self.dish_paths(), f"{given},{','.join(copies)},{frag}")
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/roles.md, "
                                              "docs/skills.md, skills/brigade/scripts/brigade_lib/store.py, "
                                              "skills/pstack-runtime/SKILL.md, skills/pstack-runtime/agents/a.md, "
                                              "skills/pstack-runtime/scripts/roles.py, skills/setup-pstack/SKILL.md, "
                                              "skills/teach/SKILL.md, t3/added/brigade/scripts/brigade_lib/store.py, "
                                              "t3/agents/a.md, t3/overrides/teach/SKILL.md, t3/runtime.md, "
                                              "t3/scripts/roles.py, t3/setup.md"])

    def test_tool_script_with_docs_page_adds_copy_plus_page(self):
        given = "t3/added/brigade/scripts/brigade.py"
        copy = "skills/brigade/scripts/brigade.py"
        page = "docs/cli/brigade.md"
        frag = "changes/perf%2Fd1.md"
        out = self.layout_fire(given)
        self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {copy}, {page}")
        self.assertEqual(self.dish_paths(), f"{given},{copy},{page},{frag}")
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/brigade.md, skills/brigade/scripts/brigade.py, t3/added/brigade/scripts/brigade.py"])

    def test_skill_source_adds_docs_skills_md_and_unrelated_omits_it(self):
        self.case_no = 0
        rows = [
            {"name": "vendor skill file", "given": "vendor/pstack/skills/teach/SKILL.md", "with_layout": True,
             "added": "docs/skills.md",
             "dish": "vendor/pstack/skills/teach/SKILL.md,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, vendor/pstack/skills/teach/SKILL.md"]},
            {"name": "overrides skill file", "given": "t3/overrides/teach/SKILL.md", "with_layout": True,
             "added": "skills/teach/SKILL.md, docs/skills.md",
             "dish": "t3/overrides/teach/SKILL.md,skills/teach/SKILL.md,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, skills/teach/SKILL.md, t3/overrides/teach/SKILL.md"]},
            {"name": "added skill file", "given": "t3/added/teach/SKILL.md", "with_layout": True,
             "added": "skills/teach/SKILL.md, docs/skills.md",
             "dish": "t3/added/teach/SKILL.md,skills/teach/SKILL.md,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, skills/teach/SKILL.md, t3/added/teach/SKILL.md"]},
            {"name": "runtime file", "given": "t3/runtime.md", "with_layout": True,
             "added": "skills/pstack-runtime/SKILL.md, docs/skills.md",
             "dish": "t3/runtime.md,skills/pstack-runtime/SKILL.md,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, skills/pstack-runtime/SKILL.md, t3/runtime.md"]},
            {"name": "setup file", "given": "t3/setup.md", "with_layout": True,
             "added": "skills/setup-pstack/SKILL.md, docs/skills.md",
             "dish": "t3/setup.md,skills/setup-pstack/SKILL.md,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, skills/setup-pstack/SKILL.md, t3/setup.md"]},
            {"name": "overrides skill dir", "given": "t3/overrides/teach", "with_layout": True,
             "added": "skills/teach, docs/skills.md",
             "dish": "t3/overrides/teach,skills/teach,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, skills/teach, t3/overrides/teach"]},
            {"name": "added skill dir", "given": "t3/added/teach", "with_layout": True,
             "added": "skills/teach, docs/skills.md",
             "dish": "t3/added/teach,skills/teach,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, skills/teach, t3/added/teach"]},
            {"name": "vendor dir", "given": "vendor/pstack", "with_layout": True,
             "added": "docs/skills.md",
             "dish": "vendor/pstack,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, vendor/pstack"]},
            {"name": "removed list", "given": "t3/removed.txt", "with_layout": True,
             "added": "docs/skills.md",
             "dish": "t3/removed.txt,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, t3/removed.txt"]},
            {"name": "tool script omits catalog", "given": "t3/scripts/roles.py", "with_layout": True,
             "added": "skills/pstack-runtime/scripts/roles.py, docs/cli/roles.md",
             "dish": "t3/scripts/roles.py,skills/pstack-runtime/scripts/roles.py,docs/cli/roles.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/roles.md, "
                       "skills/pstack-runtime/scripts/roles.py, t3/scripts/roles.py"]},
        ]
        for row in rows:
            with self.subTest(row["name"]):
                out, dish, lease = self.fresh_fire(row["given"], row["with_layout"])
                if row["added"] is None:
                    self.assertEqual(out, "D1 (lease L1 held by perf/D1)")
                else:
                    self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {row['added']}")
                self.assertEqual(dish, row["dish"])
                self.assertEqual(lease, row["lease"])

    def test_fire_table_covers_files_dirs_and_guards(self):
        self.case_no = 0
        rows = [
            {"name": "readme no copy", "given": "README.md", "with_layout": True,
             "added": None, "dish": "README.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: README.md, changes/perf%2Fd1.md"]},
            {"name": "unmapped dir", "given": "tests/brigade", "with_layout": True,
             "added": None, "dish": "tests/brigade,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, tests/brigade"]},
            {"name": "no layout", "given": "t3/added/brigade/scripts/brigade.py", "with_layout": False,
             "added": None, "dish": "t3/added/brigade/scripts/brigade.py,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, t3/added/brigade/scripts/brigade.py"]},
            {"name": "agents dir", "given": "t3/agents", "with_layout": True,
             "added": "skills/pstack-runtime/agents",
             "dish": "t3/agents,skills/pstack-runtime/agents,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, skills/pstack-runtime/agents, t3/agents"]},
            {"name": "scripts dir", "given": "t3/scripts", "with_layout": True,
             "added": "skills/pstack-runtime/scripts/roles.py, docs/cli/roles.md",
             "dish": "t3/scripts,skills/pstack-runtime/scripts/roles.py,docs/cli/roles.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/roles.md, "
                       "skills/pstack-runtime/scripts/roles.py, t3/scripts"]},
            {"name": "brigade scripts dir", "given": "t3/added/brigade/scripts", "with_layout": True,
             "added": "skills/brigade/scripts, docs/cli/activity.md, docs/cli/brigade.md",
             "dish": ("t3/added/brigade/scripts,skills/brigade/scripts,"
                      "docs/cli/activity.md,docs/cli/brigade.md,changes/perf%2Fd1.md"),
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/activity.md, docs/cli/brigade.md, "
                       "skills/brigade/scripts, t3/added/brigade/scripts"]},
        ]
        for row in rows:
            with self.subTest(row["name"]):
                out, dish, lease = self.fresh_fire(row["given"], row["with_layout"])
                if row["added"] is None:
                    self.assertEqual(out, "D1 (lease L1 held by perf/D1)")
                else:
                    self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {row['added']}")
                self.assertEqual(dish, row["dish"])
                self.assertEqual(lease, row["lease"])

    def test_cli_reference_top_level_never_runs_but_expansion_works(self):
        self.git_project()
        self.vendor_layout()
        cli = self.project / "scripts" / "cli_reference.py"
        marker = self.project / "project-code-ran"
        text = cli.read_text()
        cli.write_text(text + f'\nPath(__file__).with_name("project-code-ran").write_text("executed")\nraise SystemExit(99)\n')
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        out = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                           "--paths", "t3/runtime.md")
        self.assertFalse(marker.exists())
        self.assertEqual(out, "D1 (lease L1 held by perf/D1)\nadded skills/pstack-runtime/SKILL.md, docs/skills.md")
        self.assertEqual(self.dish_paths(), "t3/runtime.md,skills/pstack-runtime/SKILL.md,docs/skills.md,changes/perf%2Fd1.md")

    def test_same_branch_dish_over_unexpanded_lease_prints_only_in_progress(self):
        self.git_project()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                     "--branch", "perf/d1", "--paths", "t3/runtime.md")
        self.assertEqual(self.dish_paths(), "t3/runtime.md,changes/perf%2Fd1.md")
        self.vendor_layout()
        self.assertEqual(self.brigade("dish", "D1", "--branch", "perf/d1"), "D1 in-progress")
        self.assertEqual(self.dish_paths(), "t3/runtime.md,changes/perf%2Fd1.md")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        out = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "t",
                           "--paths", "t3/setup.md")
        self.assertEqual(out, "D2 (lease L2 held by perf/D2)\nadded skills/setup-pstack/SKILL.md, docs/skills.md")

    def test_startable_blocks_overlapping_generated_dirs(self):
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "t3/added/teach")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--paths", "t3/overrides/teach")
        text = self.brigade("startable")
        self.assertIn("waiting tickets: 2, startable: 1, blocked: 1", text)
        self.assertIn("T1 normal fix, 0m: startable; t3/added/teach", text)
        self.assertIn("T2 normal fix, 0m: blocked, starts after T1; t3/overrides/teach", text)

    def test_doc_item_omits_docs_skills_md_for_runtime_and_skill(self):
        self.case_no = 0
        rows = [
            {"name": "runtime without flag", "given": "t3/runtime.md", "doc_item": False,
             "added": "skills/pstack-runtime/SKILL.md, docs/skills.md",
             "dish": "t3/runtime.md,skills/pstack-runtime/SKILL.md,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, skills/pstack-runtime/SKILL.md, t3/runtime.md"]},
            {"name": "runtime with flag", "given": "t3/runtime.md", "doc_item": True,
             "added": "skills/pstack-runtime/SKILL.md",
             "dish": "t3/runtime.md,skills/pstack-runtime/SKILL.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, skills/pstack-runtime/SKILL.md, t3/runtime.md"]},
            {"name": "added skill without flag", "given": "t3/added/teach/SKILL.md", "doc_item": False,
             "added": "skills/teach/SKILL.md, docs/skills.md",
             "dish": "t3/added/teach/SKILL.md,skills/teach/SKILL.md,docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, skills/teach/SKILL.md, t3/added/teach/SKILL.md"]},
            {"name": "added skill with flag", "given": "t3/added/teach/SKILL.md", "doc_item": True,
             "added": "skills/teach/SKILL.md",
             "dish": "t3/added/teach/SKILL.md,skills/teach/SKILL.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, skills/teach/SKILL.md, t3/added/teach/SKILL.md"]},
            {"name": "overrides skill with flag", "given": "t3/overrides/teach/SKILL.md", "doc_item": True,
             "added": "skills/teach/SKILL.md",
             "dish": "t3/overrides/teach/SKILL.md,skills/teach/SKILL.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, skills/teach/SKILL.md, t3/overrides/teach/SKILL.md"]},
        ]
        for row in rows:
            with self.subTest(row["name"]):
                out, dish, lease = self.fresh_fire(row["given"], True, doc_item=row["doc_item"])
                self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {row['added']}")
                self.assertEqual(dish, row["dish"])
                self.assertEqual(lease, row["lease"])

    def test_doc_item_keeps_explicit_docs_skills_md(self):
        self.case_no = 0
        rows = [
            {"name": "explicit without flag", "given": "docs/skills.md", "doc_item": False,
             "out": "D1 (lease L1 held by perf/D1)",
             "dish": "docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md"]},
            {"name": "explicit with flag", "given": "docs/skills.md", "doc_item": True,
             "out": "D1 (lease L1 held by perf/D1)",
             "dish": "docs/skills.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md"]},
            {"name": "runtime plus explicit with flag", "given": "t3/runtime.md,docs/skills.md", "doc_item": True,
             "out": "D1 (lease L1 held by perf/D1)\nadded skills/pstack-runtime/SKILL.md",
             "dish": "t3/runtime.md,docs/skills.md,skills/pstack-runtime/SKILL.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/skills.md, skills/pstack-runtime/SKILL.md, t3/runtime.md"]},
        ]
        for row in rows:
            with self.subTest(row["name"]):
                out, dish, lease = self.fresh_fire(row["given"], True, doc_item=row["doc_item"])
                self.assertEqual(out, row["out"])
                self.assertEqual(dish, row["dish"])
                self.assertEqual(lease, row["lease"])

    def test_startable_doc_item_lease_omits_catalog_for_other_holder(self):
        self.git_project()
        self.vendor_layout()
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

    def test_startable_doc_item_flag_feeds_derived_fire_omits_catalog(self):
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
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

    def test_startable_empty_blocked_note_falls_back_to_rail_and_ticket_set_repairs_it(self):
        self.git_project()
        self.vendor_layout()
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
