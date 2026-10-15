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

    def layout_fire(self, paths):
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        return self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                            "--paths", paths)

    def dish_paths(self):
        return self.table_row(self.at, "dishes.tsv", "D1")["paths"]

    def fresh_fire(self, given, with_layout):
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
        out = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                           "--paths", given)
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
