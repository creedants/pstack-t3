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
    SCRIPT,
    land,
    sh,
)

import contextlib
import errno
import os
import shutil
import signal
import subprocess
import sys
import time
from .base import BUILD_PY, GENERATED_PATHS, GENERATED_TEXT, REBUILD, RebuildsBase
from .trace import _GroupTrace, _DARWIN, _HOST, _names, _platform, _platforms, _signalled_after_reap, _zombie


class LandingTest(RebuildsBase, LandingTestBase):

    def test_no_generated_paths_lands_a_handwritten_skills_file(self):
        self._setup_generated()
        (self.work / "skills/sentinel.txt").write_text("sentinel\n")
        self.commit("sentinel")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init()
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "skills/hand.txt").write_text("worker hand\n")
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "skills/hand.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1", out)
        hand = sh("git", "show", "main:skills/hand.txt", cwd=self.base / "origin.git")
        self.assertEqual(hand, "worker hand")
        sentinel = sh("git", "show", "main:skills/sentinel.txt", cwd=self.base / "origin.git")
        self.assertEqual(sentinel, "sentinel")

    def test_init_refuses_a_bad_generated_path_before_the_store_exists(self):
        for bad, named in (("/abs", "'/abs'"), ("../x", "'../x'"), (".", "'.'"), ("a,,b", "''")):
            with self.subTest(generated=bad):
                err = self.land("init", "--trunk", "main", "--mode", "auto", "--generated", bad, ok=False)
                self.assertIn(named, err)
                self.assertEqual(sorted((land.state_home() / "landing").glob("*/land.db")), [])

    def test_rerun_keeps_generated_and_rebuild_and_an_empty_value_clears_each(self):
        out = self.init_generated()
        self.assertIn("generated: skills, docs/cli, docs/skills.md", out)
        self.assertIn("rebuild: python3 scripts/build.py", out)
        with land.Store.for_repo(self.work) as store:
            self.assertEqual(store.contract["generated"], GENERATED_PATHS)
            self.assertEqual(store.contract["rebuild"], REBUILD)
        with self.fake_gh():
            self.init()
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(store.contract["generated"], GENERATED_PATHS)
                self.assertEqual(store.contract["rebuild"], REBUILD)
            self.init(generated="", rebuild="")
        with land.Store.for_repo(self.work) as store:
            self.assertEqual(store.contract["generated"], [])
            self.assertIsNone(store.contract["rebuild"])

    def test_generated_paths_without_rebuild_refuse_a_generated_only_change(self):
        self._setup_generated()
        self.init(generated=GENERATED_TEXT)
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "skills/out.txt").write_text("worker generated\n")
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "skills/out.txt")
        err = self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1",
                        "--reviewer", REVIEWER, ok=False)
        self.assertIn(f"{sha[:12]} changes only generated paths skills/out.txt", err)
        self.assertNotIn("scripts/build.py", err)
        self.assertIn("the contract has no rebuild command", err)

    def test_wildcard_generated_root_does_not_capture_source_directory(self):
        """A generated root named out* captures its own directory, not the source directory out1."""
        wildcard_build = (
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "root = Path(__file__).resolve().parents[1]\n"
            "out = root / 'out*'\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "(out / 'gen.txt').write_text('built\\n')\n"
        )
        (self.work / "scripts").mkdir(exist_ok=True)
        (self.work / "scripts/build.py").write_text(wildcard_build)
        (self.work / "out1").mkdir(exist_ok=True)
        (self.work / "out1/keep.txt").write_text("keep\n")
        self._run_build(self.work)
        self.commit("wildcard baseline")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init(generated="out*", rebuild=REBUILD)
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "out1/payload.txt").write_text("payload\n")
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "out1/payload.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1", out)
        self.assertNotIn("bounced", out)
        origin = self.base / "origin.git"
        self.assertEqual(sh("git", "show", "main:out1/keep.txt", cwd=origin), "keep")
        self.assertEqual(sh("git", "show", "main:out1/payload.txt", cwd=origin), "payload")
        with land.Store.for_repo(self.work) as store:
            snapshot = land.Integration(store)._generated_snapshot()
        self.assertTrue([name for name in snapshot if name.startswith("out*")])
        self.assertEqual([name for name in snapshot if name.startswith("out1")], [])
