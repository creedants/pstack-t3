import os
import subprocess
import sys
import time
from pathlib import Path
import land


def git_env():
    """Identity for fixture commits. LC_ALL=C keeps git diagnostics in English."""
    return {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "LC_ALL": "C"}


def sh(*args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=git_env())
    if result.returncode != 0:
        raise AssertionError(f"{args} failed: {result.stderr}")
    return result.stdout.strip()


def paused_child(case, args):
    """Run land.py with its first transaction held until <case>/proceed exists, as a command started before a recovery."""
    case = Path(case)
    original = land.Store.tx

    def paused_tx(self):
        (case / "paused").touch()
        deadline = time.monotonic() + 60
        while not (case / "proceed").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return original(self)

    land.Store.tx = paused_tx
    sys.exit(land.main(args))
