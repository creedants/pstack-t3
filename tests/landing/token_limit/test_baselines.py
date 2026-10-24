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
    REVIEWER,
    sh,
)

import json


class LandingTest(LandingTestBase):

    def test_a_nested_generated_root_lands_over_the_limit(self):
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh",
                  "--generated", "build/output", "--max-file-tokens", "5000")
        sha = self.worker("w1", {"a.txt": "agent\n", "build/output/big.py": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt,build/output")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_a_shrink_uses_the_effective_maximum(self):
        (self.work / "old.py").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["maxFileTokens"] = 5000
        baseline["files"] = {"old.py": 6000}
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh",
                  "--max-file-tokens", "4000")
        (self.work / "old.py").write_text("x" * 18000)
        self.assertIn("size ok", self.land("size", "--shrink"))
        shrunk = json.loads((self.work / "size-baseline.json").read_text())
        self.assertEqual(shrunk["files"].get("old.py"), 4500)

    def test_an_uppercase_image_svg_and_nested_lockfile_land_over_the_limit(self):
        self.init_with_limit()
        sha = self.worker("w1", {"a.txt": "agent\n",
                                 "photo.PNG": "x" * 20004,
                                 "diagram.svg": "x" * 20004,
                                 "web/package-lock.json": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D1",
                  "--paths", "a.txt,photo.PNG,diagram.svg,web/package-lock.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_a_project_source_under_skills_still_bounces(self):
        self.init_with_limit()
        sha = self.worker("w1", {"skills/agent.py": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "skills/agent.py")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E1 (r/D1)", reported)
        self.assertIn("skills/agent.py", reported)

    def test_a_candidate_baseline_without_a_trunk_baseline_lands_instead_of_crashing(self):
        self.init_with_limit()
        payload = json.dumps({"maxFileTokens": 5000, "files": {"fresh.py": 4000}, "skip": []})
        sha = self.worker("w1", {"size-baseline.json": payload})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_an_unparsable_baseline_bounces(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        sha = self.worker("w1", {"size-baseline.json": "{bad"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E1", reported)
        self.assertIn("cannot be read", reported)

    def test_a_first_baseline_above_the_contract_maximum_bounces(self):
        self.init_with_limit()
        payload = json.dumps({"maxFileTokens": 5000, "files": {"big.py": 5001}, "skip": []})
        sha = self.worker("w1", {"size-baseline.json": payload})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E1", reported)
        self.assertIn("big.py", reported)

    def test_a_baseline_deletion_under_a_contract_bounces(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        sh("git", "worktree", "add", "-q", "-b", "w1", str(self.base / "w1"), "origin/main", cwd=self.work)
        path = self.base / "w1"
        sh("git", "rm", "-q", "size-baseline.json", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E1", reported)
        self.assertIn("removes enabled policy", reported)

    def test_removing_a_below_maximum_allowance_bounces(self):
        (self.work / "small.py").write_text("x" * 16000)
        baseline = {"maxFileTokens": 5000, "files": {"small.py": 4000}, "skip": []}
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        tightened = {"maxFileTokens": 5000, "files": {}, "skip": []}
        sha = self.worker("w1", {"size-baseline.json": json.dumps(tightened, indent=2, sort_keys=True) + "\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E1", reported)
        self.assertIn("small.py", reported)

    def test_removing_an_above_maximum_allowance_lands(self):
        (self.work / "small.py").write_text("x" * 24000)
        baseline = {"maxFileTokens": 5000, "files": {"small.py": 6000}, "skip": []}
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        tightened = {"maxFileTokens": 5000, "files": {}, "skip": []}
        sha = self.worker("w1", {"size-baseline.json": json.dumps(tightened, indent=2, sort_keys=True) + "\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_a_staged_first_baseline_above_the_contract_maximum_bounces(self):
        self.init_with_limit()
        (self.work / "size-baseline.json").write_text(json.dumps(
            {"maxFileTokens": 5000, "files": {"big.py": 5001}, "skip": []}))
        sh("git", "add", "size-baseline.json", cwd=self.work)
        reported = self.land("size", "--staged", ok=False)
        self.assertIn("big.py", reported)

    def test_a_staged_deletion_with_the_worktree_present_bounces(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["enabled"] = True
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        sh("git", "rm", "-q", "--cached", "size-baseline.json", cwd=self.work)
        reported = self.land("size", "--staged", ok=False)
        self.assertIn("removes enabled policy", reported)

    def test_a_staged_broken_index_baseline_reports_cannot_be_read_when_the_worktree_copy_is_broken(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        (self.work / "size-baseline.json").write_text("{broken worktree")
        sh("git", "add", "size-baseline.json", cwd=self.work)
        reported = self.land("size", "--staged", ok=False)
        self.assertIn("size baseline cannot be read", reported)

    def test_seed_omits_a_skipped_over_limit_file(self):
        (self.work / "vendor").mkdir(exist_ok=True)
        (self.work / "vendor" / "big.py").write_text("x" * 20004)
        self.assertIn("size ok", self.land("size"))
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        self.assertNotIn("vendor/big.py", baseline["files"])

    def test_queue_refuses_disabling_enabled_baseline_under_contract(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["enabled"] = True
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        disabled = dict(baseline)
        disabled["enabled"] = False
        sha = self.worker("w1", {"size-baseline.json": json.dumps(disabled, indent=2, sort_keys=True) + "\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E1", reported)
        self.assertIn("removes enabled policy", reported)

    def test_queue_refuses_unmarking_enabled_baseline_under_contract(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["enabled"] = True
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        unmarked = {key: value for key, value in baseline.items() if key != "enabled"}
        sha = self.worker("w1", {"size-baseline.json": json.dumps(unmarked, indent=2, sort_keys=True) + "\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E1", reported)
        self.assertIn("removes enabled policy", reported)

    def test_queue_protection_remains_after_refused_opt_out(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["enabled"] = True
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        disabled = dict(baseline)
        disabled["enabled"] = False
        sha = self.worker("w1", {"size-baseline.json": json.dumps(disabled, indent=2, sort_keys=True) + "\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        self.assertIn("removes enabled policy", self.land("land"))
        trunk_baseline = json.loads(sh("git", "show", "origin/main:size-baseline.json", cwd=self.work))
        self.assertTrue(trunk_baseline.get("enabled") is True)
        oversized = self.worker("w2", {"big.py": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "big.py")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", oversized,
                  "--lease", "L2", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E2", reported)
        self.assertIn("big.py", reported)

    def test_staged_refuses_disabling_enabled_baseline_under_contract(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["enabled"] = True
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        disabled = dict(baseline)
        disabled["enabled"] = False
        (self.work / "size-baseline.json").write_text(json.dumps(disabled, indent=2, sort_keys=True) + "\n")
        sh("git", "add", "size-baseline.json", cwd=self.work)
        reported = self.land("size", "--staged", ok=False)
        self.assertIn("removes enabled policy", reported)

    def test_staged_refuses_unmarking_enabled_baseline_under_contract(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["enabled"] = True
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        unmarked = {key: value for key, value in baseline.items() if key != "enabled"}
        (self.work / "size-baseline.json").write_text(json.dumps(unmarked, indent=2, sort_keys=True) + "\n")
        sh("git", "add", "size-baseline.json", cwd=self.work)
        reported = self.land("size", "--staged", ok=False)
        self.assertIn("removes enabled policy", reported)
