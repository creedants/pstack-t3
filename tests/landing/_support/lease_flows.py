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
from . import paths
from .paths import ADMIN, REVIEWER, ROOT, SCRIPT, repository_state, stamp_in


class LeaseFlowsBase:

    def claim(self, holder, paths, *extra, ok=True):
        return self.land("lease", "claim", "--holder", holder, "--paths", paths, *extra, ok=ok)


    def lease_row(self, number):
        with land.Store.for_repo(self.work) as store:
            return tuple(store.db.execute("SELECT * FROM lease WHERE id = ?", (number,)).fetchone())


    def owner_state(self):
        with land.Store.for_repo(self.work) as store:
            return ([tuple(row) for row in store.db.execute("SELECT * FROM owner")],
                    store.db.execute("SELECT count(*) FROM log").fetchone()[0])


    def lease_check(self, holder, paths):
        code, out, _err = drive(
            paths.SCRIPT,
            ["--repo", str(self.work), "lease", "check", "--holder", holder, "--paths", paths],
            env=os.environ.copy())
        return code, out.strip()


    def reserve(self, prefix, paths, ruling, *extra, ok=True):
        return self.land("lease", "reserve", "--for", prefix, "--paths", paths, "--ruling", ruling, *ADMIN, *extra, ok=ok)


    def share(self, prefix, count, *extra, ok=True):
        return self.land("share", "--for", prefix, str(count), *ADMIN, *extra, ok=ok)


    def contest(self, *args, ok=True):
        return self.land("contest", *args, *ADMIN, ok=ok)


    def ruling_rows(self, table):
        with land.Store.for_repo(self.work) as store:
            return [dict(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY 1")]


    def reserved_until(self, number):
        return next(row for row in self.ruling_rows("reservation") if row["id"] == number)["expires"][:16]


    def listed(self):
        """lease list with each expiry cut out, so lines compare as literals."""
        return [re.sub(r" until \S+:", ":", line) for line in self.land("lease", "list").splitlines()]


    def concurrent(self, *commands):
        procs = [real_popen(
            ProcessReason.RACE,
            [sys.executable, str(paths.SCRIPT), "--repo", str(self.work), *command],
            env=os.environ.copy())
            for command in commands]
        return [(proc.wait(), *(part.strip() for part in proc.communicate())) for proc in procs]


    def armed_log(self):
        return [row["id"] for row in self.ruling_rows("log") if (row["kind"], row["state"]) == ("reservation", "armed")]


    def check_with_dump_unchanged(self, holder, paths):
        before = self.dumped()
        answer = self.lease_check(holder, paths)
        self.assertEqual(self.dumped(), before)
        return answer


    def assert_reserved_for_two_hours(self, text):
        match = re.fullmatch(r"(?:land: )?paths reserved for docs/ by ruling R4 until (\d{4}-\d\d-\d\dT\d\d:\d\d)", text)
        self.assertIsNotNone(match, text)
        left = datetime.fromisoformat(match[1]).replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
        self.assertLess(abs(left - timedelta(hours=2)), timedelta(minutes=2))


    def reservation_counts_again_after_its_lease_ends(self, end, limit, refused, outsider):
        self.init()
        limit()
        self.assertEqual(self.reserve("docs/", "a.txt,b.txt", "R4"), "S1")
        self.assertEqual(self.claim("docs/D7", "a.txt", *(["--ttl-hours", "0"] if end == "expire" else [])), "L1")
        if end == "release":
            self.assertEqual(self.land("lease", "release", "L1"), "L1 released")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: b.txt"])
        self.assertEqual(self.claim(outsider, "lib", ok=False), f"land: {refused}")
        self.assertEqual(self.lease_check(outsider, "lib"), (1, refused))
        self.assertEqual(self.claim("docs/D7", "b.txt"), "L2")
        self.assertEqual(self.listed(), ["L2 active docs/D7: b.txt"])
        self.assertEqual(self.ruling_rows("reservation")[0]["state"], "claimed")


    def contest_holds_both_holders(self, mode, **extra):
        self.init(mode=mode, **extra)
        self.assertEqual(self.queue_one(path="a.txt", name="w1", holder="docs/D7"), "E1")
        self.assertEqual(self.contest("--holders", "docs/D7,engine/D3"), "C1")
        self.assertEqual(self.contest("--holders", "engine/D3,docs/D7"), "C1")
        self.assertEqual(self.queue_one(path="b.txt", name="w2", holder="engine/D3"), "E2")
        self.assertEqual(self.land("land"), "still queued: E1 (held by C1), E2 (held by C1)")
        self.assertEqual(self.land("status", "--holder", "docs/D7").split(" (")[0], "E1 queued")
        self.assertEqual(self.land("status", "--holder", "engine/D3").split(" (")[0], "E2 queued")
