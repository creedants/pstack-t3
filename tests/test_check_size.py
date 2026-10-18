import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def setUpModule():
    fence_git_environment()


ROOT = Path(__file__).resolve().parents[1]


class CheckSizeTrunkTipTest(unittest.TestCase):
    def test_missing_trunk_with_committed_enabled_baseline_reports_trunk_tip_through_check_cli(self):
        """Subprocess reason: exit status."""
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            for rel in ("scripts/check.py", "scripts/size_limit.py", "t3/scripts/roles.py",
                        "t3/added/landing/scripts/landing_lib/sizes.py"):
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / rel).read_bytes())
            env = {**os.environ,
                   "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                   "XDG_STATE_HOME": str(root / "state")}
            # No origin ref exists, so _base_commit finds no trunk and returns None.
            subprocess.run(["git", "init", "-q", "-b", "work"], cwd=root, env=env, check=True)
            baseline = {"enabled": True, "files": {}, "maxFileTokens": 5000, "skip": []}
            (root / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
            subprocess.run(["git", "add", "size-baseline.json"], cwd=root, env=env, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, env=env, check=True)
            proc = subprocess.run([sys.executable, str(root / "scripts" / "check.py")],
                                  capture_output=True, text=True, env=env, cwd=root)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("size-baseline.json: size baseline at trunk tip cannot be read", proc.stdout)


if __name__ == "__main__":
    unittest.main()
