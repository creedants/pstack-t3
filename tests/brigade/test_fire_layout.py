from .support import (
    setUpModule,
    BrigadeTest,
    Path,
    ROOT,
    SCRIPT,
    _owner_words,
    call,
)
import posixpath
import subprocess


class FireLayoutTest(BrigadeTest):
    GENERATED = "skills,docs/cli,docs/skills.md"
    MAP = ",".join([
        "t3/added/**=skills/**",
        "t3/overrides/**=skills/**",
        "t3/agents/**=skills/pstack-runtime/agents/**",
        "t3/runtime.md=skills/pstack-runtime/SKILL.md",
        "t3/scripts/roles.py=skills/pstack-runtime/scripts/roles.py",
        "t3/setup.md=skills/setup-pstack/SKILL.md",
        "t3/added/brigade/scripts/activity.py=docs/cli/activity.md!",
        "t3/added/brigade/scripts/brigade.py=docs/cli/brigade.md!",
        "t3/added/landing/scripts/land.py=docs/cli/land.md!",
        "t3/scripts/roles.py=docs/cli/roles.md!",
        "t3/runtime.md=docs/skills.md!^",
        "t3/setup.md=docs/skills.md!^",
        "t3/removed.txt=docs/skills.md!^",
        "t3=docs/skills.md!^",
        "t3/overrides=docs/skills.md!^",
        "t3/added=docs/skills.md!^",
        "vendor/pstack=docs/skills.md!^",
        "vendor/pstack/skills=docs/skills.md!^",
        "t3/overrides/*=docs/skills.md!^",
        "t3/added/*=docs/skills.md!^",
        "vendor/pstack/skills/*=docs/skills.md!^",
        "t3/overrides/*/SKILL.md=docs/skills.md!^",
        "t3/added/*/SKILL.md=docs/skills.md!^",
        "vendor/pstack/skills/*/SKILL.md=docs/skills.md!^",
    ])

    def vendor_layout(self):
        target = self.project / "scripts"
        target.mkdir(parents=True, exist_ok=True)
        self.init_landing("--generated", self.GENERATED, "--generated-map", self.MAP)

    def commit_paths(self, paths):
        for part in paths.split(","):
            token = part.strip()
            if not token:
                continue
            cleaned = token.replace("\\", "/").strip()
            if not cleaned:
                continue
            made = posixpath.normpath(cleaned).lstrip("/")
            if made in (".", ""):
                continue
            if made == ".." or made.startswith("../"):
                continue
            target = self.project / made
            if target.is_dir():
                continue
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x\n")
        subprocess.run(["git", "add", "-A"], cwd=self.project, check=True,
                       capture_output=True, text=True)
        listed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.project)
        if listed.returncode != 0:
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "add paths"],
                           cwd=self.project, check=True, capture_output=True, text=True)

    def layout_fire(self, paths, doc_item=False, note="changes"):
        self.git_project()
        self.vendor_layout()
        self.commit_paths(paths)
        self.init_landing()
        self.landing_env()
        self.open()
        if note is not None:
            self.brigade("set", "--change-note-dir", note)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        args = ["fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                "--paths", paths]
        if doc_item:
            args.append("--doc-item")
        return self.brigade(*args)

    def dish_paths(self):
        return self.table_row(self.at, "dishes.tsv", "D1")["paths"]

    def fresh_fire(self, given, with_layout, doc_item=False, note="changes"):
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
        self.commit_paths(given)
        self.init_landing()
        self.landing_env()
        self.open()
        if note is not None:
            self.brigade("set", "--change-note-dir", note)
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

    def test_same_branch_dish_over_unexpanded_lease_prints_only_in_progress(self):
        self.git_project()
        self.commit_paths("t3/runtime.md,t3/setup.md")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
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
        self.commit_paths("t3/added/teach,t3/overrides/teach")
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

    def test_doc_item_keeps_cli_pages_for_tool_sources(self):
        self.case_no = 0
        rows = [
            {"name": "activity with flag", "given": "t3/added/brigade/scripts/activity.py", "doc_item": True,
             "added": "skills/brigade/scripts/activity.py, docs/cli/activity.md",
             "dish": "t3/added/brigade/scripts/activity.py,skills/brigade/scripts/activity.py,docs/cli/activity.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/activity.md, skills/brigade/scripts/activity.py, t3/added/brigade/scripts/activity.py"]},
            {"name": "brigade with flag", "given": "t3/added/brigade/scripts/brigade.py", "doc_item": True,
             "added": "skills/brigade/scripts/brigade.py, docs/cli/brigade.md",
             "dish": "t3/added/brigade/scripts/brigade.py,skills/brigade/scripts/brigade.py,docs/cli/brigade.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/brigade.md, skills/brigade/scripts/brigade.py, t3/added/brigade/scripts/brigade.py"]},
            {"name": "land with flag", "given": "t3/added/landing/scripts/land.py", "doc_item": True,
             "added": "skills/landing/scripts/land.py, docs/cli/land.md",
             "dish": "t3/added/landing/scripts/land.py,skills/landing/scripts/land.py,docs/cli/land.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/land.md, skills/landing/scripts/land.py, t3/added/landing/scripts/land.py"]},
            {"name": "roles with flag", "given": "t3/scripts/roles.py", "doc_item": True,
             "added": "skills/pstack-runtime/scripts/roles.py, docs/cli/roles.md",
             "dish": "t3/scripts/roles.py,skills/pstack-runtime/scripts/roles.py,docs/cli/roles.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/roles.md, skills/pstack-runtime/scripts/roles.py, t3/scripts/roles.py"]},
        ]
        for row in rows:
            with self.subTest(row["name"]):
                out, dish, lease = self.fresh_fire(row["given"], True, doc_item=row["doc_item"])
                self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {row['added']}")
                self.assertEqual(dish, row["dish"])
                self.assertEqual(lease, row["lease"])
                self.assertNotIn("docs/skills.md", dish)
                self.assertNotIn("docs/skills.md", lease[0])

    def test_doc_item_keeps_cli_pages_for_ancestor_scopes(self):
        self.case_no = 0
        rows = [
            {"name": "brigade scripts dir with flag", "given": "t3/added/brigade/scripts", "doc_item": True,
             "added": "skills/brigade/scripts, docs/cli/activity.md, docs/cli/brigade.md",
             "dish": "t3/added/brigade/scripts,skills/brigade/scripts,docs/cli/activity.md,docs/cli/brigade.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/activity.md, docs/cli/brigade.md, skills/brigade/scripts, t3/added/brigade/scripts"]},
            {"name": "landing scripts dir with flag", "given": "t3/added/landing/scripts", "doc_item": True,
             "added": "skills/landing/scripts, docs/cli/land.md",
             "dish": "t3/added/landing/scripts,skills/landing/scripts,docs/cli/land.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/land.md, skills/landing/scripts, t3/added/landing/scripts"]},
            {"name": "scripts dir with flag", "given": "t3/scripts", "doc_item": True,
             "added": "skills/pstack-runtime/scripts/roles.py, docs/cli/roles.md",
             "dish": "t3/scripts,skills/pstack-runtime/scripts/roles.py,docs/cli/roles.md,changes/perf%2Fd1.md",
             "lease": ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/roles.md, skills/pstack-runtime/scripts/roles.py, t3/scripts"]},
        ]
        for row in rows:
            with self.subTest(row["name"]):
                out, dish, lease = self.fresh_fire(row["given"], True, doc_item=row["doc_item"])
                self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {row['added']}")
                self.assertEqual(dish, row["dish"])
                self.assertEqual(lease, row["lease"])
                self.assertNotIn("docs/skills.md", dish)

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
        from .support import run_globals, SCRIPT
        claimed = run_globals(self, SCRIPT)["claim_paths"]
        with self.assertRaises(Exception) as ctx:
            claimed({"projectRoot": str(self.project), "restaurant": "perf"}, "t3/runtime.md", "perf/d1", False)
        self.assertIn(want, str(ctx.exception))

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

    def test_startable_saved_scope_with_whole_repository_component_blocks_the_second_scope(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("README.md,t1.txt,t2.txt")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "filler")
        for ident in ("T1", "T2"):
            self.assertEqual(self.brigade("fire", "--tickets", ident, "--station", "bug-fix",
                                          "--summary", ident, "--paths", f".,{ident.lower()}.txt", "--doc-item",
                                          ok=False),
                             "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("set", "--workers", "2")
        text = self.brigade("startable")
        self.assertIn("waiting tickets: 2, startable: 1, blocked: 1", text)
        self.assertIn("T1 normal fix, 0m: startable; .,t1.txt --doc-item", text)
        self.assertIn("T2 normal fix, 0m: blocked, starts after T1; .,t2.txt --doc-item", text)
        saved = {line[:2]: line.split("; ", 1)[1][:-len(" --doc-item")]
                 for line in text.splitlines() if line.startswith(("T1 ", "T2 "))}
        self.assertEqual(saved, {"T1": ".,t1.txt", "T2": ".,t2.txt"})
        self.assertIn("D2 (lease", self.brigade("fire", "--tickets", "T1", "--station", "bug-fix",
                                                "--summary", "one", "--paths", saved["T1"], "--doc-item"))
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "two",
                               "--paths", saved["T2"], "--doc-item", ok=False)
        self.assertIn("paths overlap L1 held by perf/D2", refused)

    def saved_scope_pair(self, first, second):
        self.git_project()
        self.vendor_layout()
        self.commit_paths(f"{first},{second},t1.txt,t2.txt")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "filler")
        for ident, paths in (("T1", first), ("T2", second)):
            self.assertEqual(self.brigade("fire", "--tickets", ident, "--station", "bug-fix",
                                          "--summary", ident, "--paths", paths, ok=False),
                             "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("set", "--workers", "2")
        return self.brigade("startable")

    def test_startable_blocks_the_second_scope_when_only_the_first_saved_scope_holds_the_repository_root(self):
        # Only the first saved scope holds the root, so the overlap needs scopes_overlap, not paths_overlap.
        text = self.saved_scope_pair(".,t1.txt", "t2.txt")
        self.assertIn("waiting tickets: 2, startable: 1, blocked: 1", text)
        self.assertIn("T1 normal fix, 0m: startable; .,t1.txt", text)
        self.assertIn("T2 normal fix, 0m: blocked, starts after T1; t2.txt", text)

    def test_startable_blocks_the_second_scope_when_only_the_second_saved_scope_holds_the_repository_root(self):
        # Only the second saved scope holds the root, so the overlap needs scopes_overlap, not paths_overlap.
        text = self.saved_scope_pair("t1.txt", ".,t2.txt")
        self.assertIn("waiting tickets: 2, startable: 1, blocked: 1", text)
        self.assertIn("T1 normal fix, 0m: startable; t1.txt", text)
        self.assertIn("T2 normal fix, 0m: blocked, starts after T1; .,t2.txt", text)

    def test_fire_leases_a_stale_split_path_and_warns_on_stderr(self):
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), "fire", "--tickets", "T1",
                                      "--station", "bug-fix", "--summary", "s",
                                      "--paths", "tests/test_landing.py"])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text().strip(), "D1 (lease L1 held by perf/D1)")
        self.assertEqual(outcome.stderr_text().splitlines(),
                         ["fire: tests/test_landing.py is not in the checkout; a stale ticket path leases nothing real"])
        self.assertIn("tests/test_landing.py", self.land("lease", "list"))
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual(row["state"], "in-progress")
        self.assertIn("tests/test_landing.py", row["paths"])

    def test_fire_leases_a_missing_path_with_no_build_layout_and_warns_on_stderr(self):
        self.git_project()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), "fire", "--tickets", "T1",
                                      "--station", "bug-fix", "--summary", "s",
                                      "--paths", "missing.txt"])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text().strip(), "D1 (lease L1 held by perf/D1)")
        self.assertEqual(outcome.stderr_text().splitlines(),
                         ["fire: missing.txt is not in the checkout; a stale ticket path leases nothing real"])
        self.assertIn("missing.txt", self.land("lease", "list"))
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual(row["state"], "in-progress")
        self.assertIn("missing.txt", row["paths"])

    def test_fire_warns_once_per_missing_path_sorted_and_deduplicated(self):
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), "fire", "--tickets", "T1",
                                      "--station", "bug-fix", "--summary", "s",
                                      "--paths", "zebra.txt,apple.txt,zebra.txt"])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text().strip(), "D1 (lease L1 held by perf/D1)")
        self.assertEqual(outcome.stderr_text().splitlines(),
                         ["fire: apple.txt is not in the checkout; a stale ticket path leases nothing real",
                          "fire: zebra.txt is not in the checkout; a stale ticket path leases nothing real"])
        listed = self.land("lease", "list")
        self.assertIn("apple.txt", listed)
        self.assertIn("zebra.txt", listed)
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual(row["state"], "in-progress")
        self.assertIn("zebra.txt,apple.txt,zebra.txt", row["paths"])
