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
from .trace import _DARWIN, _HOST, _GroupTrace, _names, _platform, _platforms, _signalled_after_reap, _zombie


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


GENERATED_TEXT = "skills,docs/cli,docs/skills.md"


GENERATED_PATHS = ["skills", "docs/cli", "docs/skills.md"]


REBUILD = "python3 scripts/build.py"


class RebuildsBase:

    def init_generated(self, **extra):
        return self.init(generated=GENERATED_TEXT, rebuild=REBUILD, **extra)

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

    def _grace_run(self, hits):
        """Run a build whose detached child holds the pipe, send hits SIGINTs during grace, and return the elapsed time and deliveries."""
        import selectors
        import shlex
        from unittest import mock
        pidfile = self.base / "sleeper.pid"
        script = (
            "import os, sys, time\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    os.setsid()\n"
            "    open(sys.argv[1], 'w').write(str(os.getpid()))\n"
            "    time.sleep(30)\n"
            "else:\n"
            "    os.waitpid(child, 0)\n"
        )
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} {shlex.quote(str(pidfile))}"
        # The command is a process whose detached child holds the pipe open into grace.
        calls = []
        previous = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, previous)

        def recording_previous(signum, frame):
            calls.append(1)

        signal.signal(signal.SIGINT, recording_previous)
        real_select = selectors.DefaultSelector.select
        sent = []
        started = [None]

        def wrapping(self, timeout=None):
            # A grace select carries the five second budget. Shorter selects are the initial drain or the exit poll.
            if timeout is not None and timeout > 1 and len(sent) < hits:
                sent.append(1)
                signal.raise_signal(signal.SIGINT)
            return real_select(self, timeout)

        with mock.patch.object(selectors.DefaultSelector, "select", wrapping):
            started[0] = time.monotonic()
            try:
                land.run_build(command, str(self.work), 0.3)
            finally:
                signal.signal(signal.SIGINT, previous)
                try:
                    sleeper = int(pidfile.read_text().strip())
                except (OSError, ValueError):
                    sleeper = None
                if sleeper is not None:
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(sleeper, signal.SIGKILL)
        return time.monotonic() - started[0], calls
