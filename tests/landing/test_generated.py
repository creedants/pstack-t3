from .support import (
    setUpModule,
    LandingTest,
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


class _GroupTrace:
    """Record killpg and reaping waitpid as event pairs with pids."""

    def __init__(self, on_killpg=None):
        """Keep the callback that runs after the record and before the real killpg."""
        self._on_killpg = on_killpg
        self.events = []
        self._real_killpg = None
        self._real_waitpid = None

    def __enter__(self):
        """Patch os.killpg and os.waitpid and return self."""
        self._real_killpg = os.killpg
        self._real_waitpid = os.waitpid
        os.killpg = self._recording_killpg
        os.waitpid = self._recording_waitpid
        return self

    def __exit__(self, *exc):
        """Restore os.killpg and os.waitpid. Never swallows."""
        os.killpg = self._real_killpg
        os.waitpid = self._real_waitpid
        return False

    def clear(self):
        """Drop recorded events. Patches stay in place."""
        del self.events[:]

    def leader(self):
        """Return the one group id killpg named."""
        groups = sorted({pgid for name, pgid in self.events if name == "killpg"})
        assert len(groups) == 1
        return groups[0]

    def _recording_killpg(self, pgid, sig):
        self.events.append(("killpg", pgid))
        if self._on_killpg is not None:
            self._on_killpg()
        return self._real_killpg(pgid, sig)

    def _recording_waitpid(self, pid, options):
        try:
            result = self._real_waitpid(pid, options)
        except ChildProcessError:
            if pid > 0:
                self.events.append(("reaped", pid))
            raise
        if result[0] != 0:
            self.events.append(("reaped", result[0]))
        return result


def _names(events, pid):
    """Return event names for this pid in order."""
    return [name for name, seen in events if seen == pid]


def _signalled_after_reap(events):
    """Return True when a killpg names a pid that an earlier event reaped."""
    reaped = set()
    for name, pid in events:
        if name == "reaped":
            reaped.add(pid)
        if name == "killpg" and pid in reaped:
            return True
    return False


def _zombie(pid):
    """Return True when pid is a zombie on Linux and macOS."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            stat = handle.read()
        return stat.rpartition(")")[2].split()[0] == "Z"
    except OSError:
        pass
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return False
    if result.returncode != 0:
        return False
    return (result.stdout or "").strip().startswith("Z")


_HOST = "host"


_DARWIN = "darwin without waitid"


def _platforms():
    """Return the host row plus the simulated Darwin row, except on a Darwin host without os.waitid, where it returns only the Darwin row."""
    if sys.platform == "darwin" and not hasattr(os, "waitid"):
        return [_DARWIN]
    return [_HOST, _DARWIN]


@contextlib.contextmanager
def _platform(name):
    """Run the body on the host, or with sys.platform set to darwin and os.waitid removed."""
    if name == _HOST:
        yield
        return
    saved_platform = sys.platform
    saved_waitid = getattr(os, "waitid", None)
    had_waitid = hasattr(os, "waitid")
    sys.platform = "darwin"
    if had_waitid:
        del os.waitid
    try:
        yield
    finally:
        sys.platform = saved_platform
        if had_waitid:
            os.waitid = saved_waitid


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

    def test_build_launch_exception_bounces_and_cleans(self):
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
        real_popen = subprocess.Popen

        def failing_popen(*args, **kwargs):
            command = args[0] if args else kwargs.get("args")
            if command == "python3 scripts/build.py":
                raise OSError(errno.EAGAIN, "Resource temporarily unavailable")
            return real_popen(*args, **kwargs)

        subprocess.Popen = failing_popen
        try:
            out = self.land("land")
        finally:
            subprocess.Popen = real_popen
        self.assertIn("bounced E1", out)
        self.assertIn("rebuild failed", out)
        self.assertIn("BlockingIOError", out)
        live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
        self.assertEqual(live, "base1")
        with land.Store.for_repo(self.work) as store:
            status = sh("git", "status", "--porcelain", cwd=store.dir / "integration")
        self.assertEqual(status, "")

    def test_user_interrupt_cleans_generated_remnants(self):
        self._setup_generated()
        self.init()
        sleep_build = BUILD_PY + (
            "\nimport os as _os, time as _time\n"
            "_slept = _os.path.join(str(root), 'skills', '.build_started')\n"
            "open(_slept, 'w').write(f'{_os.getpid()} {_os.getpgrp()}')\n"
            "_time.sleep(60)\n"
        )
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "src/one.txt").write_text("reviewed-new\n")
        (path / "scripts/build.py").write_text(sleep_build)
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt,scripts/build.py")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        with land.Store.for_repo(self.work) as store:
            integration = store.dir / "integration"
        marker = integration / "skills" / ".build_started"
        queue = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--repo", str(self.work), "land"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, env=os.environ.copy())
        build_pgid = None
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if queue.poll() is not None:
                    break
                try:
                    text = marker.read_text().strip()
                except OSError:
                    time.sleep(0.05)
                    continue
                if text:
                    build_pgid = int(text.split()[1])
                    break
                time.sleep(0.05)
            if queue.poll() is not None:
                out, err = queue.communicate()
                self.fail(f"queue exited before the interrupt: {out[-2000:]} {err[-2000:]}")
            self.assertTrue(marker.exists(), "fixture build never recorded readiness")
            queue.send_signal(signal.SIGINT)
            queue.wait(timeout=120)
            self.assertNotEqual(queue.returncode, 0)
            live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
            self.assertEqual(live, "base1")
            with land.Store.for_repo(self.work) as store:
                status = sh("git", "status", "--porcelain", cwd=store.dir / "integration")
            self.assertEqual(status, "")
        finally:
            if build_pgid is not None:
                try:
                    os.killpg(build_pgid, signal.SIGKILL)
                except OSError:
                    pass
            if queue.poll() is None:
                try:
                    os.killpg(queue.pid, signal.SIGKILL)
                except OSError:
                    pass
            try:
                queue.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            for pipe in (queue.stdout, queue.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass

    def test_exited_leader_helper_dies_on_interrupt(self):
        self._setup_generated()
        self.init()
        exited_build = BUILD_PY + (
            "\nimport os as _os, time as _time\n"
            "_marker = root / 'skills' / '.build_helper'\n"
            "_forked = _os.fork()\n"
            "if _forked == 0:\n"
            "    _marker.write_text(f'{_os.getpid()} {_os.getpgrp()}')\n"
            "    _time.sleep(60)\n"
            "else:\n"
            "    _os._exit(0)\n"
        )
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "src/one.txt").write_text("reviewed-new\n")
        (path / "scripts/build.py").write_text(exited_build)
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt,scripts/build.py")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        with land.Store.for_repo(self.work) as store:
            integration = store.dir / "integration"
        marker = integration / "skills" / ".build_helper"
        queue = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--repo", str(self.work), "land"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, env=os.environ.copy())
        build_pgid = None
        helper_pid = None
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if queue.poll() is not None:
                    break
                try:
                    text = marker.read_text().strip()
                except OSError:
                    time.sleep(0.05)
                    continue
                if text:
                    helper_pid, build_pgid = (int(part) for part in text.split()[:2])
                    break
                time.sleep(0.05)
            if queue.poll() is not None:
                out, err = queue.communicate()
                self.fail(f"queue exited before the interrupt: {out[-2000:]} {err[-2000:]}")
            self.assertTrue(marker.exists(), "fixture build never recorded readiness")
            self.assertIsNotNone(helper_pid)
            self.assertIsNotNone(build_pgid)

            deadline = time.monotonic() + 10
            while not _zombie(build_pgid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(_zombie(build_pgid), "the leader did not exit before the interrupt")
            try:
                os.kill(helper_pid, 0)
            except ProcessLookupError:
                self.fail("the helper did not survive until the interrupt")
            queue.send_signal(signal.SIGINT)
            queue.wait(timeout=120)
            self.assertNotEqual(queue.returncode, 0)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    os.kill(helper_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            try:
                os.kill(helper_pid, 0)
                self.fail("the helper survived the interrupt")
            except ProcessLookupError:
                pass
            live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
            self.assertEqual(live, "base1")
            with land.Store.for_repo(self.work) as store:
                status = sh("git", "status", "--porcelain", cwd=store.dir / "integration")
            self.assertEqual(status, "")
        finally:
            if build_pgid is not None:
                try:
                    os.killpg(build_pgid, signal.SIGKILL)
                except OSError:
                    pass
            if helper_pid is not None:
                try:
                    os.kill(helper_pid, signal.SIGKILL)
                except OSError:
                    pass
            if queue.poll() is None:
                try:
                    os.killpg(queue.pid, signal.SIGKILL)
                except OSError:
                    pass
            try:
                queue.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            for pipe in (queue.stdout, queue.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass

    def test_reaped_leader_gets_no_killpg_on_interrupt(self):
        """Runner sends SIGKILL only before it reaps."""
        real_killpg = os.killpg
        real_waitpid = os.waitpid
        previous = signal.getsignal(signal.SIGINT)
        attempts = []
        injected = []

        def recording_killpg(pgid, sig):
            attempts.append((pgid, sig))
            return real_killpg(pgid, sig)

        def injecting_waitpid(pid, options):
            try:
                result = real_waitpid(pid, options)
            except ChildProcessError:
                if not injected:
                    injected.append(True)
                    signal.raise_signal(signal.SIGINT)
                raise
            if result[0] != 0 and not injected:
                injected.append(True)
                signal.raise_signal(signal.SIGINT)
            return result

        os.killpg = recording_killpg
        os.waitpid = injecting_waitpid
        try:
            with self.assertRaises(KeyboardInterrupt):
                land.run_build("exit 0", str(self.work), 10)
        finally:
            os.killpg = real_killpg
            os.waitpid = real_waitpid
        self.assertEqual(injected, [True])
        self.assertEqual(attempts, [])
        self.assertIs(signal.getsignal(signal.SIGINT), previous)

    def test_sigint_ignored_preserves_build_when_sig_ign(self):
        parent = str(SCRIPT.parent)
        work = str(self.work)
        cmd = repr("sleep 0.05; printf finished")
        lines = [
            "import select, signal, sys",
            f"sys.path.insert(0, {parent!r})",
            "import land",
            "signal.signal(signal.SIGINT, signal.SIG_IGN)",
            "real = select.select",
            "sent = []",
            "def wrapping(*args, **kwargs):",
            "    if not sent:",
            "        sent.append(True)",
            "        signal.raise_signal(signal.SIGINT)",
            "    return real(*args, **kwargs)",
            "select.select = wrapping",
            f"try:\n    code, out = land.run_build({cmd}, {work!r}, 10)",
            "except KeyboardInterrupt:",
            '    print("CAUGHT_KEYBOARD_INTERRUPT")',
            "    raise SystemExit(1)",
            'print("RESULT " + repr((code, out)))',
            'raise SystemExit(0 if code == 0 and out == "finished" else 2)',
        ]
        result = subprocess.run(
            [sys.executable, "-c", "\n".join(lines)],
            capture_output=True, text=True, timeout=60, cwd=work)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("finished", result.stdout)
        self.assertNotIn("CAUGHT", result.stdout + result.stderr)

    def test_sigint_default_terminates_runner_when_sig_dfl(self):
        parent = str(SCRIPT.parent)
        work = str(self.work)
        cmd = repr("sleep 0.05; printf finished")
        lines = [
            "import select, signal, sys",
            f"sys.path.insert(0, {parent!r})",
            "import land",
            "signal.signal(signal.SIGINT, signal.SIG_DFL)",
            "real = select.select",
            "sent = []",
            "def wrapping(*args, **kwargs):",
            "    if not sent:",
            "        sent.append(True)",
            "        signal.raise_signal(signal.SIGINT)",
            "    return real(*args, **kwargs)",
            "select.select = wrapping",
            f"try:\n    result = land.run_build({cmd}, {work!r}, 10)",
            "except KeyboardInterrupt:",
            '    print("CAUGHT_KEYBOARD_INTERRUPT")',
            "    raise SystemExit(4)",
            'print("RETURNED " + repr(result))',
            "raise SystemExit(3)",
        ]
        result = subprocess.run(
            [sys.executable, "-c", "\n".join(lines)],
            capture_output=True, text=True, timeout=60, cwd=work)
        self.assertEqual(result.returncode, -signal.SIGINT, result.stdout + result.stderr)
        self.assertNotIn("RETURNED", result.stdout)
        self.assertNotIn("CAUGHT", result.stdout + result.stderr)

    def test_install_race_restores_handler_after_interrupt(self):
        """Install race restores the entry handler."""
        real_signal = signal.signal
        previous = signal.getsignal(signal.SIGINT)
        injected = []

        def wrapping_signal(signum, handler):
            result = real_signal(signum, handler)
            if signum == signal.SIGINT and handler is not previous and not injected:
                injected.append(True)
                signal.raise_signal(signal.SIGINT)
            return result

        signal.signal = wrapping_signal
        try:
            with self.assertRaises(KeyboardInterrupt):
                land.run_build("sleep 30", str(self.work), 30)
        finally:
            signal.signal = real_signal
        self.assertEqual(injected, [True])
        self.assertIs(signal.getsignal(signal.SIGINT), previous)

    def test_previous_callable_runs_after_the_reap(self):
        """Previous runs after the reap."""
        previous = signal.getsignal(signal.SIGINT)
        for platform in _platforms():
            with self.subTest(platform=platform):
                with _platform(platform):
                    snapshots = []
                    with _GroupTrace(on_killpg=lambda: signal.raise_signal(signal.SIGINT)) as trace:
                        def returning_previous(signum, frame):
                            snapshots.append(list(trace.events))
                            try:
                                os.waitpid(-1, os.WNOHANG)
                            except ChildProcessError:
                                pass

                        signal.signal(signal.SIGINT, returning_previous)
                        try:
                            land.run_build("sleep 5", str(self.work), 0.1)
                            restored = signal.getsignal(signal.SIGINT)
                        finally:
                            signal.signal(signal.SIGINT, previous)
                    leader = trace.leader()
                    self.assertEqual(_names(trace.events, leader), ["killpg", "reaped"])
                    self.assertEqual([_names(seen, leader) for seen in snapshots], [["killpg", "reaped"]])
                    self.assertIs(restored, returning_previous)

    def test_darwin_ps_reap_is_not_a_leader_reap(self):
        """Reaps of a non-leader child are not leader reaps."""
        with _platform(_DARWIN):
            with _GroupTrace() as trace:
                land.run_build("sleep 5", str(self.work), 0.1)
            leader = trace.leader()
            self.assertTrue(any(name == "reaped" and pid != leader for name, pid in trace.events))
            self.assertEqual(_names(trace.events, leader), ["killpg", "reaped"])
            self.assertFalse(_signalled_after_reap(trace.events))

    def test_sigint_sampled_lines_never_signal_after_reap(self):
        """Sampled lines never signal after the reap."""
        entry = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, entry)
        codes = {land.run_build.__code__}
        for value in vars(land._Build).values():
            if callable(value):
                codes.add(value.__code__)

        def line_events(command, timeout):
            seen = []

            def tracer(frame, event, arg):
                if frame.f_code in codes:
                    if event == "line":
                        seen.append((frame.f_code.co_name, frame.f_lineno))
                    return tracer
                return None

            sys.settrace(tracer)
            try:
                land.run_build(command, str(self.work), timeout)
            except KeyboardInterrupt:
                pass
            finally:
                sys.settrace(None)
            return seen

        builds = [("exit 0", 5), ("sleep 5", 0.05), ("(sleep 30) & exit 0", 0.05)]
        for platform in _platforms():
            with self.subTest(platform=platform):
                with _platform(platform):
                    counts = [len(line_events(command, timeout)) for command, timeout in builds]
                    with _GroupTrace() as trace:
                        for kind in ("raising", "returning"):
                            for (command, timeout), total in zip(builds, counts):
                                for index in range(total):
                                    trace.clear()
                                    calls = []

                                    def raising_previous(signum, frame):
                                        calls.append(1)
                                        raise KeyboardInterrupt

                                    def returning_previous(signum, frame):
                                        calls.append(1)

                                    entry = raising_previous if kind == "raising" else returning_previous
                                    signal.signal(signal.SIGINT, entry)
                                    fired = [0]
                                    # hit records reach when raise interrupts before the increment.
                                    hit = [False]

                                    def tracer(frame, event, arg):
                                        if frame.f_code in codes:
                                            if event == "line":
                                                if fired[0] == index:
                                                    hit[0] = True
                                                    signal.raise_signal(signal.SIGINT)
                                                fired[0] += 1
                                            return tracer
                                        return None

                                    started = time.monotonic()
                                    sys.settrace(tracer)
                                    try:
                                        land.run_build(command, str(self.work), timeout)
                                    except KeyboardInterrupt:
                                        pass
                                    finally:
                                        sys.settrace(None)
                                    elapsed = time.monotonic() - started
                                    after = _signalled_after_reap(trace.events)
                                    restored = signal.getsignal(signal.SIGINT) is entry
                                    self.assertFalse(after, f"{platform} {kind} {command} line {index}: {trace.events}")
                                    self.assertTrue(restored, f"{platform} {kind} {command} line {index}")
                                    if hit[0]:
                                        self.assertEqual(calls, [1], f"{platform} {kind} {command} line {index}")
                                    else:
                                        self.assertEqual(calls, [], f"{platform} {kind} {command} line {index}")
                                    self.assertLess(elapsed, 6, f"{platform} {kind} {command} line {index}")

    def test_timeout_kills_live_build_with_no_exit_detector(self):
        """Timeout kills a live build without an exit detector."""
        parent = str(SCRIPT.parent)
        work = str(self.work)
        lines = [
            "import os, sys, time",
            f"sys.path.insert(0, {parent!r})",
            "import land",
            "if hasattr(os, 'waitid'):",
            "    try: del os.waitid",
            "    except AttributeError: pass",
            "os.environ['PATH'] = ''",
            "build = sys.executable + \" -c 'import time; time.sleep(0.8)'\"",
            "start = time.monotonic()",
            "code, out = land.run_build(build, sys.argv[1], 0.05)",
            "first = time.monotonic() - start",
            "holder = object.__new__(land.Integration)",
            "holder.path = sys.argv[1]",
            "start = time.monotonic()",
            "code2, out2 = land.Integration._run_build(holder, build, 0.05)",
            "second = time.monotonic() - start",
            "ok = code is None and code2 is None and first < 0.5 and second < 0.5",
            "print('RESULT ' + repr((code, code2, round(first, 3), round(second, 3))))",
            "raise SystemExit(0 if ok else 2)",
        ]
        result = subprocess.run(
            [sys.executable, "-c", "\n".join(lines), work],
            capture_output=True, text=True, timeout=60, cwd=work)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RESULT (None, None", result.stdout)
