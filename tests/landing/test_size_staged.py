from .support import setUpModule

import json
import os
from unittest import mock

from .support import LandingTest as LandingTestBase
from .support import sh


class StagedSizePolicyTest(LandingTestBase):
    """Staged policy refusals run land.py size --staged in a real process for exit status."""

    def init_with_limit(self, maximum=5000, extra=()):
        args = ["init", "--trunk", "main", "--mode", "push", "--check", "./check.sh",
                "--max-file-tokens", str(maximum), *extra]
        return self.land(*args)

    def _seed_enabled(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["enabled"] = True
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()

    def _staged_real(self):
        with mock.patch.dict(os.environ, {"PSTACK_TEST_REAL_PROCESS": "1"}):
            return self.land("size", "--staged", ok=False)

    def test_staged_deletion_of_an_enabled_baseline_is_refused(self):
        self._seed_enabled()
        sh("git", "rm", "-q", "--cached", "size-baseline.json", cwd=self.work)
        self.assertTrue((self.work / "size-baseline.json").is_file())
        self.assertIn("size baseline removes enabled policy", self._staged_real())

    def test_staged_disabling_of_an_enabled_baseline_is_refused(self):
        self._seed_enabled()
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["enabled"] = False
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        sh("git", "add", "size-baseline.json", cwd=self.work)
        self.assertIn("size baseline removes enabled policy", self._staged_real())
