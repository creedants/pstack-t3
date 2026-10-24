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

    def test_failed_first_build_cleans_generated_remnants(self):
        self._setup_generated()
        self.init_generated()
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
        self.init_generated()
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
        self.init_generated()
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
        self.init_generated()
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

    def test_build_launch_exception_bounces_and_cleans(self):
        self._setup_generated()
        self.init_generated()
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
        real_process = os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
        try:
            out = self.land("land")
        finally:
            subprocess.Popen = real_popen
            if real_process is not None:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = real_process
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
        self.init_generated()
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
