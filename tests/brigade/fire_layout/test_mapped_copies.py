if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

class MappedCopiesMixin:
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

    def test_unset_key_leases_no_fragment(self):
        given = "t3/added/brigade/scripts/brigade.py"
        copy = "skills/brigade/scripts/brigade.py"
        page = "docs/cli/brigade.md"
        out = self.layout_fire(given, note=None)
        self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {copy}, {page}")
        self.assertEqual(self.dish_paths(), f"{given},{copy},{page}")
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: docs/cli/brigade.md, skills/brigade/scripts/brigade.py, t3/added/brigade/scripts/brigade.py"])

    def test_configured_subdirectory_leases_its_fragment(self):
        given = "t3/added/brigade/scripts/brigade.py"
        copy = "skills/brigade/scripts/brigade.py"
        page = "docs/cli/brigade.md"
        frag = "notes/sub/perf%2Fd1.md"
        out = self.layout_fire(given, note="notes/sub")
        self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {copy}, {page}")
        self.assertEqual(self.dish_paths(), f"{given},{copy},{page},{frag}")
        self.assertEqual(self.lease_lines(), [f"L1 active perf/D1: docs/cli/brigade.md, {frag}, {copy}, {given}"])

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
        self.commit_paths("t3/runtime.md")
        cli = self.project / "scripts" / "cli_reference.py"
        marker = self.project / "project-code-ran"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text('from pathlib import Path\nPath(__file__).with_name("project-code-ran").write_text("executed")\nraise SystemExit(99)\n')
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        out = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                           "--paths", "t3/runtime.md")
        self.assertFalse(marker.exists())
        self.assertEqual(out, "D1 (lease L1 held by perf/D1)\nadded skills/pstack-runtime/SKILL.md, docs/skills.md")
        self.assertEqual(self.dish_paths(), "t3/runtime.md,skills/pstack-runtime/SKILL.md,docs/skills.md,changes/perf%2Fd1.md")
