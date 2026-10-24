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

    def test_spaced_generated_filename_yields_empty_fingerprint(self):
        self._setup_generated()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        (self.work / "skills/space name.txt").write_text("generated\n")
        gen_only = self.commit("gen only spaced")
        self.assertEqual(land.fingerprint(self.work, base, gen_only, GENERATED_PATHS), "")

    def test_quoted_generated_filename_yields_empty_fingerprint(self):
        self._setup_generated()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        (self.work / 'skills/quote"name.txt').write_text("generated\n")
        gen_only = self.commit("gen only quoted")
        self.assertEqual(land.fingerprint(self.work, base, gen_only, GENERATED_PATHS), "")

    def test_leading_space_source_directory_is_fingerprinted(self):
        self._setup_generated()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        spaced = self.work / " skills"
        spaced.mkdir(exist_ok=True)
        (spaced / "x.txt").write_text("source\n")
        src_only = self.commit("src leading space")
        self.assertNotEqual(land.fingerprint(self.work, base, src_only, GENERATED_PATHS), "")

    def test_spaced_generated_rebase_lands_disjoint_sources(self):
        self._setup_generated()
        space_build = BUILD_PY + "\n_sp = root / 'skills/space name.txt'\n_sp.write_text(f\"built\\n{text}\")\n"
        (self.work / "scripts/build.py").write_text(space_build)
        self._run_build(self.work)
        self.commit("space builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_generated()
        sha1, _ = self._worker_with_build("w1", {"src/one.txt": "one1\n"})
        sha2, _ = self._worker_with_build("w2", {"src/two.txt": "two2\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "src/two.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", sha2, "--lease", "L2", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1", out)
        self.assertIn("E2 (r/D2)", out)
        self.assertNotIn("bounced", out)
        gen = sh("git", "show", "main:skills/out.txt", cwd=self.base / "origin.git")
        self.assertIn("one1", gen)
        self.assertIn("two2", gen)

    def test_carriage_return_source_replay_bounces_second(self):
        self._setup_generated()
        (self.work / ".gitattributes").write_text("src/* merge=union\n")
        (self.work / "src/carriage\rname.txt").write_text("base\n")
        self._run_build(self.work)
        self.commit("carriage baseline")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_generated()
        sha1, _ = self._worker_with_build("w1", {"src/carriage\rname.txt": "one-A\n"})
        sha2, _ = self._worker_with_build("w2", {"src/carriage\rname.txt": "one-B\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "src")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", sha2, "--lease", "L2", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("bounced E2", out)
        self.assertNotIn("landed E2", out)
        live = sh("git", "show", "main:src/carriage\rname.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "one-A")
        self.assertNotIn("one-B", live)

    def test_generated_snapshot_keeps_cr_and_newline_twins(self):
        self._setup_generated()
        self.init_generated()
        with land.Store.for_repo(self.work) as store:
            integration = land.Integration(store)
            first = integration.path / "skills/twin\nname.txt"
            second = integration.path / "skills/twin\rname.txt"
            first.write_text("newline\n")
            second.write_text("carriage\n")
            sh("git", "add", "-A", cwd=integration.path)
            snapshot = integration._generated_snapshot()
        self.assertIn("skills/twin\nname.txt", snapshot)
        self.assertIn("skills/twin\rname.txt", snapshot)
        self.assertEqual(len([key for key in snapshot if "twin" in key]), 2)

    def test_missing_builder_with_generated_state_bounces_source_only(self):
        self._setup_generated()
        (self.work / "scripts/build.py").unlink()
        self.commit("drop builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_generated()
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "src/one.txt").write_text("reviewed-new\n")
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("bounced E1", out)
        self.assertNotIn("landed", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "base1")

    def test_preexisting_trunk_payload_does_not_land(self):
        self._setup_generated()
        payload = self.work / "docs/cli/nested/stale.bin"
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(b"not produced by the real build")
        self.commit("trunk payload")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_generated()
        sha1, _ = self._worker_with_build("w1", {"src/one.txt": "one1\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1", out)
        names = sh("git", "ls-tree", "-r", "--name-only", "main", cwd=self.base / "origin.git").splitlines()
        self.assertNotIn("docs/cli/nested/stale.bin", names)
        gen = sh("git", "show", "main:skills/out.txt", cwd=self.base / "origin.git")
        self.assertIn("one1", gen)

    def test_source_symlink_parent_bounces_without_deleting_outside(self):
        self._setup_generated()
        self.init_generated()
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "cli"
        sentinel.write_text("sentinel\n")
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        shutil.rmtree(path / "docs")
        (path / "docs").symlink_to(outside)
        (path / "src/one.txt").write_text("one1\n")
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src,docs")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("bounced E1", out)
        self.assertNotIn("landed", out)
        self.assertTrue(sentinel.is_file())
        self.assertEqual(sentinel.read_text(), "sentinel\n")
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "base1")
