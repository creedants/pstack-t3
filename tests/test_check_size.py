if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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

# Covers the 12 split files today.
ROLES_MODULES = tuple(
    f"t3/scripts/_roles/{p.name}"
    for p in sorted((ROOT / "t3/scripts/_roles").glob("*.py"))
)

COPIED = (
    "scripts/check.py",
    "scripts/_check/__init__.py",
    "scripts/_check/changes.py",
    "scripts/_check/fence.py",
    "scripts/_check/fragments.py",
    "scripts/_check/identity.py",
    "scripts/_check/pyast.py",
    "scripts/_check/readme.py",
    "scripts/_check/shared_source.py",
    "scripts/_check/siblings.py",
    "scripts/_check/tree.py",
    "scripts/_check/vocabulary.py",
    "scripts/_check/wording.py",
    "scripts/size_limit.py",
    "t3/scripts/roles.py",
    *ROLES_MODULES,
    "t3/added/landing/scripts/landing_lib/sizes.py",
    "tests/landing/support.py",
)

BASELINE = {
    "enabled": True,
    "files": {},
    "maxFileTokens": 5000,
    "skip": ["scripts/*", "t3/*", "tests/*"],
}


class CheckSizeTrunkTipTest(unittest.TestCase):
    def run_check(self, trunk):
        root = Path(self.scratch)
        for rel in COPIED:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / rel).read_bytes())
        version = json.loads((ROOT / "upstream.json").read_text())["version"]
        (root / "upstream.json").write_text(json.dumps({"version": version}) + "\n")
        badge = (
            '<a href="upstream.json">'
            f'<img src="https://img.shields.io/badge/upstream%20pstack-{version}-555.svg"'
            f' alt="upstream pstack {version}"></a>\n'
        )
        (root / "README.md").write_text(badge)
        (root / "size-baseline.json").write_text(json.dumps(BASELINE, indent=2, sort_keys=True) + "\n")
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
               "XDG_STATE_HOME": str(root / "state")}
        subprocess.run(["git", "init", "-q", "-b", "work"], cwd=root, env=env, check=True)
        subprocess.run(["git", "add", "size-baseline.json"], cwd=root, env=env, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, env=env, check=True)
        if trunk:
            subprocess.run(["git", "branch", "main"], cwd=root, env=env, check=True)
        return subprocess.run([sys.executable, str(root / "scripts" / "check.py")],
                              capture_output=True, text=True, env=env, cwd=root)

    def setUp(self):
        self._scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self._scratch.cleanup)
        self.scratch = self._scratch.name

    def test_missing_trunk_with_committed_enabled_baseline_reports_trunk_tip_through_check_cli(self):
        """Subprocess reads exit status, stdout, and stderr."""
        proc = self.run_check(trunk=False)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "size-baseline.json: size baseline at trunk tip cannot be read\n")
        self.assertEqual(proc.stderr, "")

    def test_present_trunk_with_unchanged_baseline_passes_through_check_cli(self):
        """Subprocess reads exit status, stdout, and stderr."""
        proc = self.run_check(trunk=True)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "ok\n")
        self.assertEqual(proc.stderr, "")


if __name__ == "__main__":
    unittest.main()
