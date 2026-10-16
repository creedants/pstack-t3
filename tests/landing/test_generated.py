from .support import (
    setUpModule,
    LandingTest,
    REVIEWER,
    land,
    sh,
)

import subprocess
import sys


BUILD_PY = """#!/usr/bin/env python3
from pathlib import Path
root = Path(__file__).resolve().parents[1]
src = root / "src"
one = root / "skills/out.txt"
two = root / "docs/cli/out.md"
three = root / "docs/skills.md"
parts = []
for name in sorted(p.name for p in src.glob("*.txt") if p.is_file()):
    parts.append(f"{name}:{(src / name).read_text()}")
text = "".join(parts)
one.parent.mkdir(parents=True, exist_ok=True)
two.parent.mkdir(parents=True, exist_ok=True)
three.parent.mkdir(parents=True, exist_ok=True)
one.write_text(f"built\\n{text}")
two.write_text(f"built\\n{text}")
three.write_text(f"built\\n{text}")
"""


class GeneratedTest(LandingTest):
    def _run_build(self, path):
        result = subprocess.run([sys.executable, "scripts/build.py"], cwd=path, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _setup_generated(self):
        (self.work / "src").mkdir(exist_ok=True)
        (self.work / "src/one.txt").write_text("base1\n")
        (self.work / "src/two.txt").write_text("base2\n")
        (self.work / "scripts").mkdir(exist_ok=True)
        (self.work / "scripts/build.py").write_text(BUILD_PY)
        self._run_build(self.work)
        self.commit("generated baseline")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)

    def _worker_with_build(self, name, edits, base="origin/main"):
        path = self.base / name
        sh("git", "worktree", "add", "-q", "-b", name, str(path), base, cwd=self.work)
        for file, text in edits.items():
            full = path / file
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(text)
        self._run_build(path)
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", name, cwd=path)
        return sh("git", "rev-parse", "HEAD", cwd=path), path

    def test_generated_only_conflict_lands_with_rebuilt_output(self):
        self._setup_generated()
        self.init()
        sha1, _ = self._worker_with_build("w1", {"src/one.txt": "one1\n"})
        sha2, _ = self._worker_with_build("w2", {"src/two.txt": "two2\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "src/two.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", sha2, "--lease", "L2", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1", out)
        self.assertIn("E1 (r/D1)", out)
        self.assertIn("E2 (r/D2)", out)
        gen = sh("git", "show", "main:skills/out.txt", cwd=self.base / "origin.git")
        self.assertIn("one1", gen)
        self.assertIn("two2", gen)
        cli = sh("git", "show", "main:docs/cli/out.md", cwd=self.base / "origin.git")
        self.assertIn("one1", cli)
        self.assertIn("two2", cli)

    def test_source_conflict_still_bounces(self):
        self._setup_generated()
        self.init()
        sha1, _ = self._worker_with_build("w1", {"src/one.txt": "one-A\n"})
        sha2, _ = self._worker_with_build("w2", {"src/one.txt": "one-B\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", sha2, "--lease", "L2", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("bounced E2", out)
        self.assertIn("conflict with trunk", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "one-A")
        gen = sh("git", "show", "main:skills/out.txt", cwd=self.base / "origin.git")
        self.assertIn("one-A", gen)
        self.assertNotIn("one-B", gen)

    def test_build_failure_bounces(self):
        self._setup_generated()
        self.init()
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "src/one.txt").write_text("one1\n")
        self._run_build(path)
        build = path / "scripts/build.py"
        build.write_text(build.read_text() + "\nimport sys as _sys; _sys.exit(1)\n")
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt,scripts/build.py")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("bounced E1", out)
        self.assertIn("rebuild failed", out)
        self.assertNotIn("landed", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "base1")

    def test_fingerprint_ignores_generated_but_catches_source(self):
        self._setup_generated()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        (self.work / "skills/out.txt").write_text("tampered\n")
        gen_only = self.commit("gen only")
        self.assertEqual(land.fingerprint(self.work, base, gen_only), "")
        sh("git", "reset", "-q", "--hard", base, cwd=self.work)
        (self.work / "src/one.txt").write_text("changed\n")
        self._run_build(self.work)
        src_only = self.commit("src change")
        self.assertNotEqual(land.fingerprint(self.work, base, src_only), "")
        first_hash = land.fingerprint(self.work, base, src_only)
        sh("git", "reset", "-q", "--hard", base, cwd=self.work)
        (self.work / "src/one.txt").write_text("changed again\n")
        self._run_build(self.work)
        src_other = self.commit("src different")
        self.assertNotEqual(first_hash, land.fingerprint(self.work, base, src_other))
        sh("git", "reset", "-q", "--hard", base, cwd=self.work)
        (self.work / "src/one.txt").write_text("changed\n")
        self._run_build(self.work)
        (self.work / "skills/out.txt").write_text("different generated\n")
        (self.work / "docs/skills.md").write_text("different\n")
        alt = self.commit("src same gen different")
        self.assertEqual(land.fingerprint(self.work, base, src_only), land.fingerprint(self.work, base, alt))
