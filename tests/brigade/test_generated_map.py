from .support import (
    setUpModule,
    BrigadeTest,
    Path,
    ROOT,
    SCRIPT,
    _owner_words,
    call,
)
import json
import posixpath
import shutil
import subprocess
import tempfile


GENERATED = "skills,docs/cli,docs/skills.md"
MAP = ",".join([
    "t3/added/**=skills/**",
    "t3/overrides/**=skills/**",
    "t3/overrides/*/SKILL.md=docs/skills.md!^",
    "vendor/pstack/skills/*/SKILL.md=docs/skills.md!^",
])


class GeneratedMapTest(BrigadeTest):
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

    def map_fire(self, paths, doc_item=False):
        self.git_project()
        self.commit_paths(paths)
        self.init_landing("--generated", GENERATED, "--generated-map", MAP)
        self.landing_env()
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        args = ["fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                "--paths", paths]
        if doc_item:
            args.append("--doc-item")
        return self.brigade(*args)

    def dish_paths(self):
        return self.table_row(self.at, "dishes.tsv", "D1")["paths"]

    def test_added_path_expands_to_skills_copy(self):
        given = "t3/added/brigade/scripts/brigade_lib/store.py"
        copy = "skills/brigade/scripts/brigade_lib/store.py"
        frag = "changes/perf%2Fd1.md"
        out = self.map_fire(given)
        self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {copy}")
        self.assertEqual(self.dish_paths(), f"{given},{copy},{frag}")
        self.assertEqual(self.lease_lines(), [f"L1 active perf/D1: {frag}, {copy}, {given}"])

    def test_overrides_skill_expands_to_copy_and_catalog(self):
        given = "t3/overrides/teach/SKILL.md"
        copy = "skills/teach/SKILL.md"
        catalog = "docs/skills.md"
        frag = "changes/perf%2Fd1.md"
        out = self.map_fire(given)
        self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {copy}, {catalog}")
        self.assertEqual(self.dish_paths(), f"{given},{copy},{catalog},{frag}")
        self.assertEqual(self.lease_lines(), [f"L1 active perf/D1: {frag}, {catalog}, {copy}, {given}"])

    def test_vendor_skill_expands_to_catalog_only(self):
        given = "vendor/pstack/skills/teach/SKILL.md"
        catalog = "docs/skills.md"
        frag = "changes/perf%2Fd1.md"
        out = self.map_fire(given)
        self.assertEqual(out, f"D1 (lease L1 held by perf/D1)\nadded {catalog}")
        self.assertEqual(self.dish_paths(), f"{given},{catalog},{frag}")
        self.assertEqual(self.lease_lines(), [f"L1 active perf/D1: {frag}, {catalog}, {given}"])

    def test_no_generated_list_gives_no_expansion(self):
        self.git_project()
        self.commit_paths("t3/added/brigade/scripts/brigade.py")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        out = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                           "--paths", "t3/added/brigade/scripts/brigade.py")
        self.assertEqual(out, "D1 (lease L1 held by perf/D1)")
        self.assertEqual(self.dish_paths(), "t3/added/brigade/scripts/brigade.py,changes/perf%2Fd1.md")

    def test_build_output_matches_three_copy_pairs_and_three_repo_targets_exist(self):
        scratch = Path(tempfile.mkdtemp())
        try:
            built = subprocess.run(["python3", str(ROOT / "scripts/build.py"), "--out", str(scratch)],
                                   cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            pairs = [
                ("t3/added/brigade/scripts/brigade_lib/store.py",
                 "skills/brigade/scripts/brigade_lib/store.py"),
                ("t3/added/brigade/scripts/brigade.py",
                 "skills/brigade/scripts/brigade.py"),
                ("t3/scripts/roles.py",
                 "skills/pstack-runtime/scripts/roles.py"),
            ]
            for src, dst in pairs:
                rel = dst.removeprefix("skills/") if dst.startswith("skills/") else dst
                self.assertEqual((ROOT / src).read_bytes(), (scratch / rel).read_bytes())
            for target in ("docs/cli/brigade.md", "docs/cli/roles.md", "docs/skills.md"):
                self.assertTrue((ROOT / target).is_file(), target)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_vendor_ancestor_index_expansion_ignores_single_star_ancestors(self):
        import importlib
        import sys
        scripts = str(ROOT / "t3/added/brigade/scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
            self.addCleanup(sys.path.remove, scripts)
        for name in [n for n in list(sys.modules) if n == "brigade_lib" or n.startswith("brigade_lib.")]:
            del sys.modules[name]
        generated = importlib.import_module("brigade_lib.generated")
        full = generated.parse([
            ["vendor/pstack", "docs/skills.md", "index", True],
            ["vendor/pstack/skills", "docs/skills.md", "index", True],
            ["vendor/pstack/skills/*/SKILL.md", "docs/skills.md", "index", True],
        ])
        wild_only = generated.parse([
            ["vendor/pstack/skills/*/SKILL.md", "docs/skills.md", "index", True],
        ])
        catalog = ("docs/skills.md",)
        cases = [
            ("vendor", False, full, catalog),
            ("vendor/pstack", False, full, catalog),
            ("vendor/pstack/skills", False, full, catalog),
            ("vendor/pstack/skills/teach/SKILL.md", False, full, catalog),
            ("vendo", False, full, ()),
            ("vendor/pstack/README.md", False, full, ()),
            ("vendor", True, full, ()),
            ("vendor", False, wild_only, ()),
        ]
        for norm, doc_item, gen_map, expected in cases:
            with self.subTest(norm=norm, doc_item=doc_item, rules=len(gen_map.rules)):
                self.assertEqual(generated.expand(gen_map, norm, doc_item), expected)
