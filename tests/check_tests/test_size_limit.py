import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from .support import (
    setUpModule,
    check,
    size_limit,
)


class SizeLimitBridgeTest(unittest.TestCase):
    def _repo(self, baseline=None, files=()):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        if baseline is not None:
            (root / "size-baseline.json").write_text(json.dumps(baseline))
        for name, size in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)
        return root

    def test_no_baseline_and_no_contract_reports_nothing(self):
        self.assertEqual(size_limit.findings(self._repo()), [])

    def test_a_file_over_the_baseline_maximum_is_reported(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True}, [("big.py", 24)])
        self.assertEqual(size_limit.findings(root),
                         ["size limit: big.py: 6 tokens over allowance 5 for code; "
                          "code moves one responsibility to a sibling module"])

    def test_a_file_within_its_baseline_allowance_passes(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {"big.py": 6}, "enabled": True}, [("big.py", 24)])
        self.assertEqual(size_limit.findings(root), [])

    def test_a_skipped_file_over_the_maximum_passes(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": ["vendor/*"], "files": {}, "enabled": True},
                          [("vendor/huge.py", 400)])
        self.assertEqual(size_limit.findings(root), [])

    def test_a_malformed_incidental_baseline_without_trusted_policy_reports_nothing(self):
        root = self._repo()
        (root / "size-baseline.json").write_text("{broken")
        self.assertEqual(size_limit.findings(root), [])

    def test_a_differently_shaped_incidental_baseline_without_trusted_policy_reports_nothing(self):
        root = self._repo()
        (root / "size-baseline.json").write_text(json.dumps({"foo": 1}))
        self.assertEqual(size_limit.findings(root), [])

    def test_a_baseline_filename_without_enabled_marker_reports_nothing(self):
        root = self._repo({"maxFileTokens": 5, "skip": [], "files": {}}, [("big.py", 24)])
        self.assertEqual(size_limit.findings(root), [])

    def test_an_uppercase_image_and_nested_lockfile_over_the_maximum_pass(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True},
                          [("photo.PNG", 400), ("diagram.svg", 400), ("web/package-lock.json", 400)])
        self.assertEqual(size_limit.findings(root), [])

    def test_a_nested_skip_root_over_the_maximum_passes(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": ["build/output"], "files": {}, "enabled": True},
                          [("build/output/big.py", 400)])
        self.assertEqual(size_limit.findings(root), [])

    def test_a_candidate_maximum_that_cannot_be_parsed_is_refused(self):
        sizes = size_limit.load_sizes()
        base = {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": {}}
        candidate = {"enabled": True, "maxFileTokens": "lots", "skip": [],
                     "files": {"fresh.py": 6000}}
        self.assertEqual(sizes.refused_policy_change(base, candidate), "size baseline cannot be read")

    def test_a_candidate_files_map_that_is_not_a_dict_is_refused(self):
        sizes = size_limit.load_sizes()
        base = {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": {}}
        candidate = {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": ["fresh.py"]}
        self.assertEqual(sizes.refused_policy_change(base, candidate), "size baseline cannot be read")

    def test_a_candidate_negative_allowance_is_refused_as_unreadable(self):
        sizes = size_limit.load_sizes()
        base = {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": {}}
        candidate = {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": {"bad.py": -1}}
        self.assertEqual(sizes.refused_policy_change(base, candidate), "size baseline cannot be read")

    def test_a_new_allowance_above_the_effective_ceiling_is_refused(self):
        sizes = size_limit.load_sizes()
        base = {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": {}}
        above = {"enabled": True, "maxFileTokens": 5000, "skip": [],
                 "files": {"fresh.py": 4500}}
        self.assertEqual(
            sizes.refused_policy_change(base, above, 4000),
            "size baseline adds a grandfathered file fresh.py; a person merges that raise outside the queue")
        at_ceiling = {"enabled": True, "maxFileTokens": 5000, "skip": [],
                      "files": {"fresh.py": 4000}}
        self.assertIsNone(sizes.refused_policy_change(base, at_ceiling, 4000))
        below_raised_ceiling = {"enabled": True, "maxFileTokens": 5000, "skip": [],
                                "files": {"fresh.py": 6000}}
        self.assertIsNone(sizes.refused_policy_change(base, below_raised_ceiling, 8000))
        above_raised_ceiling = {"enabled": True, "maxFileTokens": 5000, "skip": [],
                                "files": {"fresh.py": 8001}}
        self.assertEqual(
            sizes.refused_policy_change(base, above_raised_ceiling, 8000),
            "size baseline adds a grandfathered file fresh.py; a person merges that raise outside the queue")

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

    def test_a_worktree_allowance_increase_is_refused(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {"big.py": 6}, "enabled": True},
                              [("big.py", 24)])
        (root / "size-baseline.json").write_text(json.dumps(
            {"maxFileTokens": 5, "skip": [], "files": {"big.py": 5001}, "enabled": True}))
        (root / "big.py").write_bytes(b"x" * 20004)
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("raises the allowance for big.py", findings[0])

    def test_a_disabled_candidate_baseline_does_not_disable_trusted_policy(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True},
                              [("big.py", 24)])
        (root / "size-baseline.json").write_text(json.dumps(
            {"maxFileTokens": 5, "skip": [], "files": {}, "enabled": False}))
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("removes enabled policy", findings[0])

    def test_a_deleted_candidate_baseline_does_not_disable_trusted_policy(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True},
                              [("big.py", 24)])
        (root / "size-baseline.json").unlink()
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("removes enabled policy", findings[0])

    def test_a_missing_trunk_ref_with_enabled_policy_fails_closed(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True},
                              [("big.py", 24)])
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "LC_ALL": "C"}
        subprocess.run(["git", "checkout", "-q", "--detach", "HEAD"], cwd=root, env=env, check=True)
        subprocess.run(["git", "branch", "-q", "-D", "main"], cwd=root, env=env, check=True)
        (root / "size-baseline.json").write_text(json.dumps(
            {"maxFileTokens": 5, "skip": [], "files": {"big.py": 5001}, "enabled": True}))
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("cannot be read", findings[0])

    def test_a_malformed_candidate_baseline_with_trusted_policy_is_refused(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True},
                              [("big.py", 24)])
        (root / "size-baseline.json").write_text("{broken")
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("cannot be read", findings[0])

    def test_removing_a_below_maximum_allowance_is_refused(self):
        sizes = size_limit.load_sizes()
        base = {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": {"small.py": 4000}}
        candidate = {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": {}}
        self.assertEqual(
            sizes.refused_policy_change(base, candidate, 5000),
            "size baseline raises the allowance for small.py; a person merges that raise outside the queue")

    def test_removing_an_above_maximum_allowance_lands(self):
        sizes = size_limit.load_sizes()
        base = {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": {"small.py": 6000}}
        candidate = {"enabled": True, "maxFileTokens": 5000, "skip": [], "files": {}}
        self.assertIsNone(sizes.refused_policy_change(base, candidate, 5000))

    def _detach_trunk(self, root):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "LC_ALL": "C"}
        subprocess.run(["git", "checkout", "-q", "--detach", "HEAD"], cwd=root, env=env, check=True)
        subprocess.run(["git", "branch", "-q", "-D", "main"], cwd=root, env=env, check=True)

    def test_a_missing_trunk_disabled_candidate_fails_closed(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True},
                              [("big.py", 24)])
        self._detach_trunk(root)
        (root / "size-baseline.json").write_text(json.dumps(
            {"maxFileTokens": 5, "skip": [], "files": {}, "enabled": False}))
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("size baseline at trunk tip cannot be read", findings[0])

    def test_a_missing_trunk_deleted_candidate_fails_closed(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True},
                              [("big.py", 24)])
        self._detach_trunk(root)
        (root / "size-baseline.json").unlink()
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("size baseline at trunk tip cannot be read", findings[0])

    def test_a_missing_trunk_malformed_candidate_fails_closed(self):
        root = self._git_repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True},
                              [("big.py", 24)])
        self._detach_trunk(root)
        (root / "size-baseline.json").write_text("{broken")
        findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("size baseline at trunk tip cannot be read", findings[0])

    def test_an_enabled_candidate_alone_does_not_enable_enforcement(self):
        root = self._repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True}, [("big.py", 24)])
        self.assertEqual(size_limit.findings(root), [])

    def test_a_missing_head_with_a_contract_fails_closed(self):
        root = self._repo({"maxFileTokens": 5, "skip": [], "files": {}, "enabled": True}, [("big.py", 24)])
        with mock.patch.object(size_limit, "read_contract", return_value={"sizeMaxTokens": 5}):
            findings = size_limit.findings(root)
        self.assertEqual(findings, ["size-baseline.json: size baseline at trunk tip cannot be read"])

    def test_a_contract_preserves_enabled_marker_on_disable(self):
        root = self._git_repo({"maxFileTokens": 5000, "skip": [], "files": {}, "enabled": True},
                              [("big.py", 24)])
        (root / "size-baseline.json").write_text(json.dumps(
            {"maxFileTokens": 5000, "skip": [], "files": {}, "enabled": False}))
        with mock.patch.object(size_limit, "read_contract", return_value={"sizeMaxTokens": 5000}):
            findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("removes enabled policy", findings[0])

    def test_a_contract_preserves_enabled_marker_on_unmark(self):
        root = self._git_repo({"maxFileTokens": 5000, "skip": [], "files": {}, "enabled": True},
                              [("big.py", 24)])
        (root / "size-baseline.json").write_text(json.dumps(
            {"maxFileTokens": 5000, "skip": [], "files": {}}))
        with mock.patch.object(size_limit, "read_contract", return_value={"sizeMaxTokens": 5000}):
            findings = size_limit.findings(root)
        self.assertEqual(len(findings), 1)
        self.assertIn("removes enabled policy", findings[0])
