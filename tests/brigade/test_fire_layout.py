from .support import (
    setUpModule,
    BrigadeTest,
    Path,
)
import posixpath
import subprocess
from .fire_layout.test_mapped_copies import MappedCopiesMixin
from .fire_layout.test_doc_items import DocItemsMixin
from .fire_layout.test_doc_item_blocks import DocItemBlocksMixin
from .fire_layout.test_derived_fire import DerivedFireMixin
from .fire_layout.test_lease_scope import LeaseScopeMixin


class FireLayoutTest(
    BrigadeTest,
    MappedCopiesMixin,
    DocItemsMixin,
    DocItemBlocksMixin,
    DerivedFireMixin,
    LeaseScopeMixin,
):
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
