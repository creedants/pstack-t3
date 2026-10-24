if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    land,
    sh,
)

import json

FORMS = (
    {"relativeTo": "ref.md", "margin": 1, "other": 1},
    {"relativeTo": "ref.md"},
    {"relativeTo": "ref.md", "margin": "1"},
    {"relativeTo": "ref.md", "margin": -1},
    {"relativeTo": "ref.md", "margin": True},
    {"relativeTo": 1, "margin": 1},
    {"relativeTo": "/ref.md", "margin": 1},
    {"relativeTo": "a/../ref.md", "margin": 1},
    {"relativeTo": "big.py", "margin": 1},
    {"relativeTo": "./big.py", "margin": 1},
)


class RelativeSizeTest(LandingTestBase):

    def seed(self, files=None, extra=()):
        """Commit a baseline holding one relative entry, then enable the limit."""
        (self.work / "ref.md").write_text("x" * 1600)
        (self.work / "big.py").write_text("x" * 1600)
        for name, text in extra:
            (self.work / name).write_text(text)
        baseline = {"maxFileTokens": 5000, "skip": [], "enabled": True,
                    "files": files or {"big.py": {"relativeTo": "ref.md", "margin": 300}}}
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()

    def candidate(self, name):
        path = self.base / name
        sh("git", "worktree", "add", "-q", "-b", name, str(path), "origin/main", cwd=self.work)
        return path

    def test_a_relative_allowance_is_judged_in_the_worktree(self):
        self.seed()
        (self.work / "big.py").write_text("x" * 3000)
        reported = self.land("size", ok=False)
        self.assertIn("ref.md", reported)
        self.assertIn("400 tokens", reported)
        self.assertIn("margin 300", reported)
        (self.work / "ref.md").write_text("x" * 3200)
        (self.work / "big.py").write_text("x" * 3200)
        self.assertIn("size ok", self.land("size"))

    def test_a_relative_allowance_is_judged_in_the_index(self):
        self.seed()
        (self.work / "ref.md").write_text("x" * 400)
        (self.work / "big.py").write_text("x" * 2000)
        sh("git", "add", "big.py", cwd=self.work)
        self.assertIn("size ok", self.land("size", "--staged"))
        (self.work / "big.py").write_text("x" * 2804)
        sh("git", "add", "big.py", cwd=self.work)
        self.assertIn("ref.md", self.land("size", "--staged", ok=False))

    def test_a_relative_allowance_is_judged_in_the_candidate_commit(self):
        self.seed()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        path = self.candidate("w1")
        (path / "big.py").write_text("x" * 3000)
        grown = self.commit("grown", cwd=path)
        with land.Store.for_repo(self.work) as store:
            self.assertIn("ref.md", land.check_candidate_size(store, base, grown))
        (path / "ref.md").write_text("x" * 3200)
        (path / "big.py").write_text("x" * 3200)
        both = self.commit("both", cwd=path)
        with land.Store.for_repo(self.work) as store:
            self.assertIsNone(land.check_candidate_size(store, base, both))

    def test_a_missing_reference_is_named_in_the_index(self):
        self.seed({"big.py": {"relativeTo": "missing.md", "margin": 300}})
        (self.work / "big.py").write_text("x" * 3000)
        sh("git", "add", "big.py", cwd=self.work)
        reported = self.land("size", "--staged", ok=False)
        self.assertIn("missing.md", reported)
        self.assertIn("is missing", reported)

    def test_a_missing_reference_is_named_in_the_candidate_commit(self):
        self.seed({"big.py": {"relativeTo": "missing.md", "margin": 300}})
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        path = self.candidate("w1")
        (path / "big.py").write_text("x" * 3000)
        grown = self.commit("grown", cwd=path)
        with land.Store.for_repo(self.work) as store:
            reported = land.check_candidate_size(store, base, grown)
        self.assertIn("missing.md", reported)
        self.assertIn("is missing", reported)

    def test_each_invalid_object_form_is_refused_in_the_index(self):
        self.seed()
        valid = (self.work / "size-baseline.json").read_text()
        for form in FORMS:
            with self.subTest(form=form):
                baseline = {"maxFileTokens": 5000, "skip": [], "files": {"big.py": form},
                            "enabled": True}
                (self.work / "size-baseline.json").write_text(json.dumps(baseline))
                sh("git", "add", "size-baseline.json", cwd=self.work)
                (self.work / "size-baseline.json").write_text(valid)
                self.assertIn("cannot be read", self.land("size", "--staged", ok=False))

    def test_each_invalid_object_form_is_refused_in_the_candidate_commit(self):
        self.seed()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        path = self.candidate("w1")
        for form in FORMS:
            with self.subTest(form=form):
                baseline = {"maxFileTokens": 5000, "skip": [], "files": {"big.py": form},
                            "enabled": True}
                (path / "size-baseline.json").write_text(json.dumps(baseline))
                sha = self.commit("w1", cwd=path)
                with land.Store.for_repo(self.work) as store:
                    self.assertIn("cannot be read", land.check_candidate_size(store, base, sha))

    def test_shrink_leaves_a_relative_entry_unchanged(self):
        self.seed({"big.py": {"relativeTo": "ref.md", "margin": 300}, "old.py": 4000},
                  extra=(("old.py", "x" * 24000),))
        (self.work / "old.py").write_text("x" * 6000)
        self.assertIn("size ok", self.land("size", "--shrink"))
        files = json.loads((self.work / "size-baseline.json").read_text())["files"]
        self.assertEqual(files.get("big.py"), {"relativeTo": "ref.md", "margin": 300})
        self.assertEqual(files.get("old.py"), 1500)
