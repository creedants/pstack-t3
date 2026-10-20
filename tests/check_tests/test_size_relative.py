import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from .support import setUpModule, size_limit

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

RAISE = "a person merges that raise outside the queue"


class RelativePolicyTest(unittest.TestCase):

    def _sizes(self):
        return size_limit.load_sizes()

    def _base(self, files):
        return {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": files}

    def _refused(self, base_files, candidate_files):
        return self._sizes().refused_policy_change(
            self._base(base_files), self._base(candidate_files), 5000)

    def test_adding_a_relative_entry_is_refused(self):
        self.assertEqual(
            self._refused({}, {"big.py": {"relativeTo": "ref.md", "margin": 1}}),
            f"size baseline adds a relative allowance for big.py; {RAISE}")

    def test_an_integer_allowance_turning_relative_is_refused(self):
        self.assertEqual(
            self._refused({"big.py": 100}, {"big.py": {"relativeTo": "ref.md", "margin": 1}}),
            f"size baseline changes the allowance for big.py to a relative one; {RAISE}")

    def test_a_relative_allowance_turning_integer_is_refused(self):
        self.assertEqual(
            self._refused({"big.py": {"relativeTo": "ref.md", "margin": 1}}, {"big.py": 100}),
            f"size baseline changes the relative allowance for big.py to a fixed one; {RAISE}")

    def test_changing_the_reference_is_refused(self):
        self.assertEqual(
            self._refused({"big.py": {"relativeTo": "ref.md", "margin": 1}},
                          {"big.py": {"relativeTo": "other.md", "margin": 1}}),
            f"size baseline changes the reference for big.py; {RAISE}")

    def test_raising_the_margin_is_refused(self):
        self.assertEqual(
            self._refused({"big.py": {"relativeTo": "ref.md", "margin": 1}},
                          {"big.py": {"relativeTo": "ref.md", "margin": 2}}),
            f"size baseline raises the margin for big.py; {RAISE}")

    def test_removing_a_relative_entry_is_refused(self):
        self.assertEqual(
            self._refused({"big.py": {"relativeTo": "ref.md", "margin": 1}}, {}),
            f"size baseline removes the relative allowance for big.py; {RAISE}")

    def test_lowering_the_margin_on_the_same_reference_lands(self):
        self.assertIsNone(
            self._refused({"big.py": {"relativeTo": "ref.md", "margin": 300}},
                          {"big.py": {"relativeTo": "ref.md", "margin": 200}}))


class RelativeBridgeTest(unittest.TestCase):

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

    def _enabled(self, files):
        return {"maxFileTokens": 5000, "skip": [], "files": files, "enabled": True}

    def test_a_relative_allowance_follows_its_reference(self):
        root = self._git_repo(
            self._enabled({"big.py": {"relativeTo": "vendor/ref.md", "margin": 300}}),
            [("vendor/ref.md", 400), ("big.py", 400)])
        self.assertEqual(size_limit.findings(root), [])
        (root / "big.py").write_bytes(b"x" * 2000)
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("vendor/ref.md", findings[0])
        self.assertIn("at 100 tokens", findings[0])
        self.assertIn("margin 300", findings[0])
        (root / "big.py").write_bytes(b"x" * 1600)
        (root / "vendor/ref.md").write_bytes(b"x" * 1600)
        self.assertEqual(size_limit.findings(root), [])

    def test_a_dot_prefixed_reference_reads_the_same_file(self):
        root = self._git_repo(
            self._enabled({"big.py": {"relativeTo": "./vendor/ref.md", "margin": 300}}),
            [("vendor/ref.md", 400), ("big.py", 400)])
        self.assertEqual(size_limit.findings(root), [])
        (root / "big.py").write_bytes(b"x" * 2000)
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("vendor/ref.md", findings[0])
        self.assertNotIn("./vendor/ref.md", findings[0])
        self.assertIn("at 100 tokens", findings[0])

    def test_a_missing_reference_is_named_in_the_worktree(self):
        root = self._git_repo(
            self._enabled({"big.py": {"relativeTo": "missing.md", "margin": 300}}),
            [("big.py", 400)])
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("missing.md", findings[0])
        self.assertIn("is missing", findings[0])

    def test_each_invalid_object_form_is_refused_in_the_worktree(self):
        root = self._git_repo(self._enabled({}), [("big.py", 400)])
        for form in FORMS:
            with self.subTest(form=form):
                (root / "size-baseline.json").write_text(
                    json.dumps(self._enabled({"big.py": form})))
                findings = size_limit.findings(root)
                self.assertEqual(len(findings), 1)
                self.assertIn("cannot be read", findings[0])
