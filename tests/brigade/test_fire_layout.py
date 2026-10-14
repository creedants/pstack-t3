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

    def test_source_under_each_mapped_root_adds_its_copy(self):
        given = "t3/runtime.md,t3/added/brigade/scripts/brigade_lib/store.py,t3/agents/a.md"
        copy_one = "skills/pstack-runtime/SKILL.md"
        copy_two = "skills/brigade/scripts/brigade_lib/store.py"
        copy_three = "skills/pstack-runtime/agents/a.md"
        frag = "changes/perf%2Fd1.md"
        out = self.layout_fire(given)
        self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {copy_one}, {copy_two}, {copy_three}")
        self.assertEqual(self.dish_paths(), f"{given},{copy_one},{copy_two},{copy_three},{frag}")
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, skills/brigade/scripts/brigade_lib/store.py, skills/pstack-runtime/SKILL.md, skills/pstack-runtime/agents/a.md, t3/added/brigade/scripts/brigade_lib/store.py, t3/agents/a.md, t3/runtime.md"])

    def test_path_with_no_generated_copy_adds_nothing(self):
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        out = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                           "--paths", "README.md")
        self.assertEqual(out, "D1 (lease L1 held by perf/D1)")
        self.assertEqual(self.dish_paths(), "README.md,changes/perf%2Fd1.md")
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: README.md, changes/perf%2Fd1.md"])

    def test_tool_script_with_docs_page_adds_copy_plus_page(self):
        given = "t3/added/brigade/scripts/brigade.py"
        copy = "skills/brigade/scripts/brigade.py"
        page = "docs/cli/brigade.md"
        frag = "changes/perf%2Fd1.md"
        out = self.layout_fire(given)
        self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {copy}, {page}")
        self.assertEqual(self.dish_paths(), f"{given},{copy},{page},{frag}")
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, docs/cli/brigade.md, skills/brigade/scripts/brigade.py, t3/added/brigade/scripts/brigade.py"])

    def test_tool_script_without_docs_page_adds_copy_only(self):
        given = "t3/added/brigade/scripts/brigade_lib/store.py"
        copy = "skills/brigade/scripts/brigade_lib/store.py"
        frag = "changes/perf%2Fd1.md"
        out = self.layout_fire(given)
        self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {copy}")
        self.assertEqual(self.dish_paths(), f"{given},{copy},{frag}")
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, skills/brigade/scripts/brigade_lib/store.py, t3/added/brigade/scripts/brigade_lib/store.py"])

    def test_project_with_no_build_layout_leases_exactly_the_given_paths(self):
        self.git_project()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        given = "t3/added/brigade/scripts/brigade.py"
        out = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                           "--paths", given)
        self.assertEqual(out, "D1 (lease L1 held by perf/D1)")
        self.assertEqual(self.dish_paths(), f"{given},changes/perf%2Fd1.md")
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, t3/added/brigade/scripts/brigade.py"])
