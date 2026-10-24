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
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import size_limit as bridge

CANONICAL = "big.py"

INVALID_KEYS = (
    "/big.py",
    "a/../big.py",
    "./big.py",
    "dir/./big.py",
    "a//big.py",
    "big.py/",
    "dir\\big.py",
)

CANNOT_BE_READ = "size baseline cannot be read"


def _pure():
    return bridge.load_sizes()


def _base(files=None, skip=None):
    return {
        "enabled": True,
        "maxFileTokens": 5000,
        "skip": [] if skip is None else skip,
        "files": {} if files is None else files,
    }


def _relative_value():
    return {"relativeTo": "ref.md", "margin": 1}


class BaselineSchemaPureTest(unittest.TestCase):

    def test_integer_noncanonical_keys_are_refused_by_policy_comparison(self):
        sizes = _pure()
        for key in INVALID_KEYS:
            with self.subTest(key=key):
                self.assertEqual(
                    sizes.refused_policy_change(_base(), _base({key: 100}), 5000),
                    CANNOT_BE_READ)

    def test_relative_noncanonical_keys_are_refused_by_policy_comparison(self):
        sizes = _pure()
        for key in INVALID_KEYS:
            with self.subTest(key=key):
                self.assertEqual(
                    sizes.refused_policy_change(_base(), _base({key: _relative_value()}), 5000),
                    CANNOT_BE_READ)

    def test_alias_self_reference_is_refused_by_policy_comparison(self):
        sizes = _pure()
        candidate = _base({"./big.py": {"relativeTo": "big.py", "margin": 1}})
        self.assertEqual(sizes.refused_policy_change(_base(), candidate, 5000), CANNOT_BE_READ)

    def test_non_list_skip_is_refused_by_policy_comparison(self):
        sizes = _pure()
        candidate = _base({}, skip="not a list")
        self.assertEqual(sizes.refused_policy_change(_base(), candidate, 5000), CANNOT_BE_READ)

    def test_non_string_skip_entry_is_refused_by_policy_comparison(self):
        sizes = _pure()
        candidate = _base({}, skip=["ok", 123])
        self.assertEqual(sizes.refused_policy_change(_base(), candidate, 5000), CANNOT_BE_READ)

    def test_canonical_integer_key_still_lands_by_policy_comparison(self):
        sizes = _pure()
        self.assertIsNone(sizes.refused_policy_change(_base(), _base({CANONICAL: 100}), 5000))


class BaselineSchemaBridgeTest(unittest.TestCase):

    def _git_repo(self, baseline, files=()):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "LC_ALL": "C"}
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, env=env, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, env=env, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, env=env, check=True)
        if baseline is not None:
            (root / "size-baseline.json").write_text(json.dumps(baseline))
        for name, size in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)
        subprocess.run(["git", "add", "-A"], cwd=root, env=env, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, env=env, check=True)
        return root

    def _enabled(self, files, skip=None):
        return {"maxFileTokens": 5000, "skip": [] if skip is None else skip,
                "files": files, "enabled": True}

    def test_integer_noncanonical_keys_are_refused_in_the_worktree(self):
        root = self._git_repo(self._enabled({}), [("big.py", 400)])
        for key in INVALID_KEYS:
            with self.subTest(key=key):
                (root / "size-baseline.json").write_text(json.dumps(self._enabled({key: 100})))
                findings = bridge.findings(root)
                self.assertEqual(len(findings), 1)
                self.assertIn(CANNOT_BE_READ, findings[0])

    def test_relative_noncanonical_keys_are_refused_in_the_worktree(self):
        root = self._git_repo(self._enabled({}), [("big.py", 400)])
        for key in INVALID_KEYS:
            with self.subTest(key=key):
                (root / "size-baseline.json").write_text(
                    json.dumps(self._enabled({key: _relative_value()})))
                findings = bridge.findings(root)
                self.assertEqual(len(findings), 1)
                self.assertIn(CANNOT_BE_READ, findings[0])

    def test_alias_self_reference_is_refused_in_the_worktree(self):
        root = self._git_repo(self._enabled({}), [("big.py", 400)])
        (root / "size-baseline.json").write_text(
            json.dumps(self._enabled({"./big.py": {"relativeTo": "big.py", "margin": 1}})))
        findings = bridge.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn(CANNOT_BE_READ, findings[0])

    def test_non_list_skip_is_refused_in_the_worktree(self):
        root = self._git_repo(self._enabled({}), [("big.py", 400)])
        (root / "size-baseline.json").write_text(json.dumps(self._enabled({}, skip="not a list")))
        findings = bridge.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn(CANNOT_BE_READ, findings[0])

    def test_non_string_skip_entry_is_refused_in_the_worktree(self):
        root = self._git_repo(self._enabled({}), [("big.py", 400)])
        (root / "size-baseline.json").write_text(json.dumps(self._enabled({}, skip=["ok", 123])))
        findings = bridge.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn(CANNOT_BE_READ, findings[0])


class BaselineSchemaLandingTest(LandingTestBase):

    def seed(self, files=None):
        (self.work / "ref.md").write_text("x" * 1600)
        (self.work / "big.py").write_text("x" * 1600)
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

    def test_integer_noncanonical_keys_are_refused_in_the_index(self):
        self.seed()
        valid = (self.work / "size-baseline.json").read_text()
        for key in INVALID_KEYS:
            with self.subTest(key=key):
                baseline = {"maxFileTokens": 5000, "skip": [], "files": {key: 100}, "enabled": True}
                (self.work / "size-baseline.json").write_text(json.dumps(baseline))
                sh("git", "add", "size-baseline.json", cwd=self.work)
                (self.work / "size-baseline.json").write_text(valid)
                self.assertIn(CANNOT_BE_READ, self.land("size", "--staged", ok=False))

    def test_relative_noncanonical_keys_are_refused_in_the_index(self):
        self.seed()
        valid = (self.work / "size-baseline.json").read_text()
        for key in INVALID_KEYS:
            with self.subTest(key=key):
                baseline = {"maxFileTokens": 5000, "skip": [], "files": {key: _relative_value()},
                            "enabled": True}
                (self.work / "size-baseline.json").write_text(json.dumps(baseline))
                sh("git", "add", "size-baseline.json", cwd=self.work)
                (self.work / "size-baseline.json").write_text(valid)
                self.assertIn(CANNOT_BE_READ, self.land("size", "--staged", ok=False))

    def test_integer_noncanonical_keys_are_refused_in_the_candidate_commit(self):
        self.seed()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        path = self.candidate("w1")
        for key in INVALID_KEYS:
            with self.subTest(key=key):
                baseline = {"maxFileTokens": 5000, "skip": [], "files": {key: 100}, "enabled": True}
                (path / "size-baseline.json").write_text(json.dumps(baseline))
                sha = self.commit("w1", cwd=path)
                with land.Store.for_repo(self.work) as store:
                    self.assertIn(CANNOT_BE_READ, land.check_candidate_size(store, base, sha))

    def test_relative_noncanonical_keys_are_refused_in_the_candidate_commit(self):
        self.seed()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        path = self.candidate("w1")
        for key in INVALID_KEYS:
            with self.subTest(key=key):
                baseline = {"maxFileTokens": 5000, "skip": [], "files": {key: _relative_value()},
                            "enabled": True}
                (path / "size-baseline.json").write_text(json.dumps(baseline))
                sha = self.commit("w1", cwd=path)
                with land.Store.for_repo(self.work) as store:
                    self.assertIn(CANNOT_BE_READ, land.check_candidate_size(store, base, sha))

    def test_alias_self_reference_is_refused_in_the_index(self):
        self.seed()
        valid = (self.work / "size-baseline.json").read_text()
        baseline = {"maxFileTokens": 5000, "skip": [],
                    "files": {"./big.py": {"relativeTo": "big.py", "margin": 1}}, "enabled": True}
        (self.work / "size-baseline.json").write_text(json.dumps(baseline))
        sh("git", "add", "size-baseline.json", cwd=self.work)
        (self.work / "size-baseline.json").write_text(valid)
        self.assertIn(CANNOT_BE_READ, self.land("size", "--staged", ok=False))

    def test_alias_self_reference_is_refused_in_the_candidate_commit(self):
        self.seed()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        path = self.candidate("w1")
        baseline = {"maxFileTokens": 5000, "skip": [],
                    "files": {"./big.py": {"relativeTo": "big.py", "margin": 1}}, "enabled": True}
        (path / "size-baseline.json").write_text(json.dumps(baseline))
        sha = self.commit("w1", cwd=path)
        with land.Store.for_repo(self.work) as store:
            self.assertIn(CANNOT_BE_READ, land.check_candidate_size(store, base, sha))

    def test_non_list_skip_is_refused_in_the_index(self):
        self.seed()
        valid = (self.work / "size-baseline.json").read_text()
        baseline = {"maxFileTokens": 5000, "skip": "not a list", "files": {}, "enabled": True}
        (self.work / "size-baseline.json").write_text(json.dumps(baseline))
        sh("git", "add", "size-baseline.json", cwd=self.work)
        (self.work / "size-baseline.json").write_text(valid)
        self.assertIn(CANNOT_BE_READ, self.land("size", "--staged", ok=False))

    def test_non_list_skip_is_refused_in_the_candidate_commit(self):
        self.seed()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        path = self.candidate("w1")
        baseline = {"maxFileTokens": 5000, "skip": "not a list", "files": {}, "enabled": True}
        (path / "size-baseline.json").write_text(json.dumps(baseline))
        sha = self.commit("w1", cwd=path)
        with land.Store.for_repo(self.work) as store:
            self.assertIn(CANNOT_BE_READ, land.check_candidate_size(store, base, sha))

    def test_non_string_skip_entry_is_refused_in_the_index(self):
        self.seed()
        valid = (self.work / "size-baseline.json").read_text()
        baseline = {"maxFileTokens": 5000, "skip": ["ok", 123], "files": {}, "enabled": True}
        (self.work / "size-baseline.json").write_text(json.dumps(baseline))
        sh("git", "add", "size-baseline.json", cwd=self.work)
        (self.work / "size-baseline.json").write_text(valid)
        self.assertIn(CANNOT_BE_READ, self.land("size", "--staged", ok=False))

    def test_non_string_skip_entry_is_refused_in_the_candidate_commit(self):
        self.seed()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        path = self.candidate("w1")
        baseline = {"maxFileTokens": 5000, "skip": ["ok", 123], "files": {}, "enabled": True}
        (path / "size-baseline.json").write_text(json.dumps(baseline))
        sha = self.commit("w1", cwd=path)
        with land.Store.for_repo(self.work) as store:
            self.assertIn(CANNOT_BE_READ, land.check_candidate_size(store, base, sha))

    def test_invalid_key_fails_closed_with_all_flag(self):
        self.seed()
        baseline = {"maxFileTokens": 5000, "skip": [], "files": {"./big.py": 100}, "enabled": True}
        (self.work / "size-baseline.json").write_text(json.dumps(baseline))
        self.land("size", "--all", ok=False)

    def test_invalid_key_fails_closed_for_shrink(self):
        self.seed()
        baseline = {"maxFileTokens": 5000, "skip": [], "files": {"./big.py": 100}, "enabled": True}
        (self.work / "size-baseline.json").write_text(json.dumps(baseline))
        self.land("size", "--shrink", ok=False)
