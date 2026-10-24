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
    _run,
    ADMIN,
    os,
    Path,
    ProcessReason,
    repository_state,
    REVIEWER,
    sh,
    subprocess,
    sys,
    tempfile,
    unittest,
)
from .base import IdentityBase

class GitEnvironmentFenceTest(unittest.TestCase):
    def test_a_fixture_test_leaves_a_repository_named_by_the_callers_git_variables_unchanged(self):
        for names in (("GIT_DIR", "GIT_WORK_TREE"), ("GIT_DIR",)):
            with self.subTest(leaked=names), tempfile.TemporaryDirectory() as tmp:
                victim = Path(tmp) / "victim"
                subprocess.run(["git", "init", "-q", "-b", "main", str(victim)], check=True)
                subprocess.run(["git", "-c", "user.name=v", "-c", "user.email=v@v", "commit", "-q", "--allow-empty", "-m", "init"],
                               cwd=victim, check=True)
                before = repository_state(victim)
                leaked = {"GIT_DIR": str(victim / ".git"), "GIT_WORK_TREE": str(victim)}
                child = _run(
                    [sys.executable, "-m", "unittest",
                     "tests.landing.queue.test_landing.LandingTest.test_a_conflict_rerere_resolved_to_trunk_bounces"],
                    env={**os.environ, **{name: leaked[name] for name in names}},
                    timeout=300, reason=ProcessReason.INHERITED_ENV)
                self.assertEqual(repository_state(victim), before)
                self.assertEqual(child.returncode, 0, child.stderr)
