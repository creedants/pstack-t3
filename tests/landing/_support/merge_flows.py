import ast
import contextlib
import errno
import fcntl
import gc
import hashlib
import inspect
import io
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import land
from .shell import git_env, sh
from .inprocess import ProcessReason, _run, drive, real_popen, real_process
from .paths import ADMIN, REVIEWER, ROOT, SCRIPT, repository_state, stamp_in


class MergeFlowsBase:

    def merged_queue_branch(self):
        """Save the queue head, move trunk to it, then mark the PR merged at that head with that head as the merge commit."""
        remote = self.base / "origin.git"
        sha = sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote)
        sh("git", "update-ref", "refs/heads/main", sha, cwd=remote)
        (self.base / "pr-state").write_text(f"MERGED {sha} {sha}")
        return sha


    def base_land_script(self):
        """Pre-rename land.py. The fixture replaces git show, which fails in a shallow clone."""
        path = self.base / "land-3678d11.py"
        source = ROOT / "tests/fixtures/land_before_e_rename.py"
        path.write_bytes(source.read_bytes())
        return path


    def use_land_script(self, path):
        global SCRIPT
        previous = SCRIPT
        SCRIPT = path
        return previous


    def restore_land_script(self, previous):
        global SCRIPT
        SCRIPT = previous


    def entry_table_without_onto(self, candidates):
        with self.raw_db() as db:
            newer = db.execute("SELECT sql FROM sqlite_master WHERE name = 'entry'").fetchone()[0]
            older = newer.replace(", onto TEXT NOT NULL DEFAULT ''", "")
            self.assertNotEqual(older, newer)
            kept = ", ".join(name for name in self.entry_columns() if name != "onto")
            db.execute("ALTER TABLE entry RENAME TO entry_newer")
            db.execute(older)
            db.execute(f"INSERT INTO entry ({kept}) SELECT {kept} FROM entry_newer")
            db.execute("DROP TABLE entry_newer")
            for ident, candidate in candidates.items():
                db.execute("UPDATE entry SET candidate = ? WHERE id = ?", (candidate, ident))
            db.commit()
