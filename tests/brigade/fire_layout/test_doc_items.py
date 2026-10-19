class DocItemsMixin:
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
