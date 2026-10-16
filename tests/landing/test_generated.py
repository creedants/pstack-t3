from .support import (
    setUpModule,
    LandingTest,
    REVIEWER,
    land,
    sh,
)

import shutil
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

    def test_source_only_submission_rebuilds_generated_output(self):
        self._setup_generated()
        self.init()
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "src/one.txt").write_text("reviewed-new\n")
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "reviewed-new")
        gen = sh("git", "show", "main:skills/out.txt", cwd=self.base / "origin.git")
        self.assertIn("reviewed-new", gen)

    def test_source_only_failing_build_bounces(self):
        self._setup_generated()
        self.init()
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "src/one.txt").write_text("one1\n")
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

    def test_nested_generated_payload_is_dropped_on_land(self):
        self._setup_generated()
        self.init()
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "src/one.txt").write_text("one1\n")
        self._run_build(path)
        payload = path / "docs/cli/nested/payload.bin"
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(b"not produced by the real build")
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1", out)
        names = sh("git", "ls-tree", "-r", "--name-only", "main", cwd=self.base / "origin.git").splitlines()
        self.assertNotIn("docs/cli/nested/payload.bin", names)
        gen = sh("git", "show", "main:skills/out.txt", cwd=self.base / "origin.git")
        self.assertIn("one1", gen)

    def test_generated_mode_change_bounces(self):
        self._setup_generated()
        mode_build = BUILD_PY + (
            "\nfrom pathlib import Path as _P\n"
            "_here = _P(__file__).resolve().parents[1]\n"
            "_mark = _here / '.build_count'\n"
            "try:\n"
            "    _run = int(_mark.read_text().strip() or '0')\n"
            "except OSError:\n"
            "    _run = 0\n"
            "_mark.write_text(str(_run + 1))\n"
            "if _run:\n"
            "    _p = _here / 'skills/out.txt'\n"
            "    _p.chmod(_p.stat().st_mode ^ 0o111)\n"
        )
        (self.work / "scripts/build.py").write_text(mode_build)
        (self.work / ".gitignore").write_text(".build_count\n")
        self._run_build(self.work)
        self.commit("mode builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init()
        sha1, _ = self._worker_with_build("w1", {"src/one.txt": "one1\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("bounced E1", out)
        self.assertIn("did not reproduce", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "base1")

    def test_generated_symlink_target_change_bounces(self):
        self._setup_generated()
        link_build = BUILD_PY + (
            "\nfrom pathlib import Path as _P\n"
            "_root = _P(__file__).resolve().parents[1]\n"
            "_mark = _root / '.build_count'\n"
            "try:\n"
            "    _run = int(_mark.read_text().strip() or '0')\n"
            "except OSError:\n"
            "    _run = 0\n"
            "_mark.write_text(str(_run + 1))\n"
            "_link = _root / 'docs/cli/link'\n"
            "_nxt = '../../skills/out.txt' if _run else 'out.md'\n"
            "try:\n"
            "    if _link.is_symlink() or _link.exists():\n"
            "        _link.unlink()\n"
            "except OSError:\n"
            "    pass\n"
            "_link.symlink_to(_nxt)\n"
        )
        (self.work / "scripts/build.py").write_text(link_build)
        (self.work / ".gitignore").write_text(".build_count\n")
        self._run_build(self.work)
        self.commit("link builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init()
        sha1, _ = self._worker_with_build("w1", {"src/one.txt": "one1\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("bounced E1", out)
        self.assertIn("did not reproduce", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "base1")

    def test_second_run_source_dirt_bounces(self):
        self._setup_generated()
        dirty_build = (
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "root = Path(__file__).resolve().parents[1]\n"
            "src = root / 'src'\n"
            "one = root / 'skills/out.txt'\n"
            "two = root / 'docs/cli/out.md'\n"
            "three = root / 'docs/skills.md'\n"
            "parts = []\n"
            "for name in sorted(p.name for p in src.glob('*.txt') if p.is_file()):\n"
            "    parts.append(f\"{name}:{(src / name).read_text()}\")\n"
            "text = ''.join(parts)\n"
            "expected = f\"built\\n{text}\"\n"
            "try:\n"
            "    _run = int((root / '.build_count').read_text().strip() or '0')\n"
            "except OSError:\n"
            "    _run = 0\n"
            "(root / '.build_count').write_text(str(_run + 1))\n"
            "if _run:\n"
            "    t = src / 'two.txt'\n"
            "    try:\n"
            "        t.chmod(t.stat().st_mode ^ 0o111)\n"
            "    except OSError:\n"
            "        pass\n"
            "one.parent.mkdir(parents=True, exist_ok=True)\n"
            "two.parent.mkdir(parents=True, exist_ok=True)\n"
            "three.parent.mkdir(parents=True, exist_ok=True)\n"
            "one.write_text(expected)\n"
            "two.write_text(expected)\n"
            "three.write_text(expected)\n"
        )
        (self.work / "scripts/build.py").write_text(dirty_build)
        (self.work / ".gitignore").write_text(".build_count\n")
        self._run_build(self.work)
        self.commit("dirty builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init()
        sha1, _ = self._worker_with_build("w1", {"src/one.txt": "one1\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("bounced E1", out)
        self.assertIn("changed sources", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "base1")

    def test_different_generated_names_land_disjoint_sources(self):
        self._setup_generated()
        three_build = BUILD_PY + (
            "\nthree_extra = root / 'docs/cli/three.md'\n"
            "if (src / 'three.txt').exists():\n"
            "    three_extra.write_text(f\"built\\nthree:{(src / 'three.txt').read_text()}\")\n"
            "else:\n"
            "    try:\n"
            "        three_extra.unlink()\n"
            "    except OSError:\n"
            "        pass\n"
        )
        (self.work / "scripts/build.py").write_text(three_build)
        self._run_build(self.work)
        self.commit("three builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init()
        sha1, _ = self._worker_with_build("w1", {"src/three.txt": "three1\n"})
        sha2, _ = self._worker_with_build("w2", {"src/two.txt": "two2\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/three.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "src/two.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", sha2, "--lease", "L2", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1", out)
        self.assertIn("E2 (r/D2)", out)
        self.assertNotIn("bounced", out)
        names = sh("git", "ls-tree", "-r", "--name-only", "main", cwd=self.base / "origin.git").splitlines()
        self.assertIn("docs/cli/three.md", names)
        gen = sh("git", "show", "main:skills/out.txt", cwd=self.base / "origin.git")
        self.assertIn("three1", gen)
        self.assertIn("two2", gen)

    def test_generated_conflict_after_empty_skip_lands(self):
        self._setup_generated()
        self.init()
        sha1, p1 = self._worker_with_build("w1", {"src/one.txt": "one1\n"})
        w1_skills = (p1 / "docs/skills.md").read_text()
        path = self.base / "w2"
        sh("git", "worktree", "add", "-q", "-b", "w2", str(path), "origin/main", cwd=self.work)
        (path / "docs/skills.md").write_text(w1_skills)
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w2c1", cwd=path)
        (path / "src/two.txt").write_text("two2\n")
        self._run_build(path)
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w2c2", cwd=path)
        sha2 = sh("git", "rev-parse", "HEAD", cwd=path)
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

    def test_spaced_generated_filename_yields_empty_fingerprint(self):
        self._setup_generated()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        (self.work / "skills/space name.txt").write_text("generated\n")
        gen_only = self.commit("gen only spaced")
        self.assertEqual(land.fingerprint(self.work, base, gen_only), "")

    def test_quoted_generated_filename_yields_empty_fingerprint(self):
        self._setup_generated()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        (self.work / 'skills/quote"name.txt').write_text("generated\n")
        gen_only = self.commit("gen only quoted")
        self.assertEqual(land.fingerprint(self.work, base, gen_only), "")

    def test_leading_space_source_directory_is_fingerprinted(self):
        self._setup_generated()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        spaced = self.work / " skills"
        spaced.mkdir(exist_ok=True)
        (spaced / "x.txt").write_text("source\n")
        src_only = self.commit("src leading space")
        self.assertNotEqual(land.fingerprint(self.work, base, src_only), "")

    def test_spaced_generated_rebase_lands_disjoint_sources(self):
        self._setup_generated()
        space_build = BUILD_PY + "\n_sp = root / 'skills/space name.txt'\n_sp.write_text(f\"built\\n{text}\")\n"
        (self.work / "scripts/build.py").write_text(space_build)
        self._run_build(self.work)
        self.commit("space builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init()
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
        self.init()
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
        self.init()
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
        self.init()
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
        self.init()
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
        self.init()
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

    def test_failed_first_build_cleans_generated_remnants(self):
        self._setup_generated()
        self.init()
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "src/one.txt").write_text("one1\n")
        self._run_build(path)
        build = path / "scripts/build.py"
        build.write_text(build.read_text() + "\nfrom pathlib import Path as _P\n_P(__file__).resolve().parents[1].joinpath('docs/cli/new-output.txt').write_text('leftover')\nimport sys as _sys; _sys.exit(1)\n")
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt,scripts/build.py")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("bounced E1", out)
        self.assertNotIn("landed", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "base1")
        with land.Store.for_repo(self.work) as store:
            status = sh("git", "status", "--porcelain", cwd=store.dir / "integration")
        self.assertEqual(status, "")

    def test_failed_second_build_cleans_generated_remnants(self):
        self._setup_generated()
        counter_build = BUILD_PY + (
            "\nfrom pathlib import Path as _P\n"
            "_here = _P(__file__).resolve().parents[1]\n"
            "_mark = _here / '.build_count'\n"
            "try:\n"
            "    _run = int(_mark.read_text().strip() or '0')\n"
            "except OSError:\n"
            "    _run = 0\n"
            "_mark.write_text(str(_run + 1))\n"
            "if _run:\n"
            "    _here.joinpath('docs/cli/new-output.txt').write_text('leftover')\n"
            "    raise SystemExit(1)\n"
        )
        (self.work / "scripts/build.py").write_text(counter_build)
        (self.work / ".gitignore").write_text(".build_count\n")
        self._run_build(self.work)
        self.commit("counting builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init()
        sha1, _ = self._worker_with_build("w1", {"src/one.txt": "one1\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("bounced E1", out)
        self.assertNotIn("landed", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "base1")
        with land.Store.for_repo(self.work) as store:
            status = sh("git", "status", "--porcelain", cwd=store.dir / "integration")
        self.assertEqual(status, "")

    def test_generated_modify_delete_conflict_lands_both(self):
        self._setup_generated()
        optional_build = BUILD_PY + (
            "\nopt = root / 'docs/cli/optional.md'\n"
            "if 'delete' in (src / 'one.txt').read_text():\n"
            "    try:\n"
            "        opt.unlink()\n"
            "    except OSError:\n"
            "        pass\n"
            "else:\n"
            "    opt.write_text(f\"built\\n{text}\")\n"
        )
        (self.work / "scripts/build.py").write_text(optional_build)
        self._run_build(self.work)
        self.commit("optional builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init()
        sha1, _ = self._worker_with_build("w1", {"src/one.txt": "delete\n"})
        sha2, _ = self._worker_with_build("w2", {"src/two.txt": "two2\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "src/two.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", sha2, "--lease", "L2", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1", out)
        self.assertIn("E2 (r/D2)", out)
        self.assertNotIn("bounced", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "delete")
        names = sh("git", "ls-tree", "-r", "--name-only", "main", cwd=self.base / "origin.git").splitlines()
        self.assertNotIn("docs/cli/optional.md", names)

    def test_quoted_generated_filename_lands_source_change(self):
        self._setup_generated()
        quote_build = BUILD_PY + "\n_q = root / 'skills/quote\"name.txt'\n_q.write_text(f\"built\\n{text}\")\n"
        (self.work / "scripts/build.py").write_text(quote_build)
        self._run_build(self.work)
        self.commit("quote builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init()
        sha1, _ = self._worker_with_build("w1", {"src/one.txt": "one1\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha1, "--lease", "L1", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1", out)
        self.assertNotIn("bounced", out)
        gen = sh("git", "show", "main:skills/out.txt", cwd=self.base / "origin.git")
        self.assertIn("one1", gen)

    def test_quoted_generated_conflict_lands_disjoint_sources(self):
        self._setup_generated()
        quote_build = BUILD_PY + "\n_q = root / 'skills/quote\"name.txt'\n_q.write_text(f\"built\\n{text}\")\n"
        (self.work / "scripts/build.py").write_text(quote_build)
        self._run_build(self.work)
        self.commit("quote builder")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init()
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
