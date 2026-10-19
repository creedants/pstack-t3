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


class IdentityFlowsBase:

    def configure_owner(self, name="Owner", email="owner@example.com"):
        sh("git", "config", "user.name", name, cwd=self.work)
        sh("git", "config", "user.email", email, cwd=self.work)


    def foreign_bounce(self, sha, author="t <t@t>", configured="Owner <owner@example.com>"):
        return (f"bounced E1 (r/D1): commit {sha[:12]} has author {author}, not the configured {configured}; "
                "recommit it as the configured author")


    def land_one_by(self, author, *message, body=None, owner=("Owner", "owner@example.com")):
        """Submit one commit by this author under the owner identity in push mode, then run land. Returns the commit id and what land printed."""
        self.init(mode="push")
        self.configure_owner(*owner)
        self.worker("w1", {"a.txt": "agent\n"})
        sh("git", "commit", "-q", "--amend", "--author", author, *message, cwd=self.base / "w1")
        sha = sh("git", "rev-parse", "HEAD", cwd=self.base / "w1")
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        submitted = []
        if body is not None:
            (self.base / "body.md").write_text(body)
            submitted = ["--body-file", str(self.base / "body.md")]
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, *submitted)
        return sha, self.land("land")


    def identity_pause(self, now, before="Owner <owner@example.com>"):
        return (f"the configured git identity is now {now} and the queue last ran as {before}; "
                f"check git config --show-origin user.email in {os.path.realpath(self.work)}, then run land.py resume")


    UNSET = "unset (user.name and user.email do not both have a non-empty value)"


    def configured_value(self, key):
        """What git config -z --get prints for key in the checkout, with the final NUL removed. None when git exits 1."""
        result = subprocess.run(["git", "config", "-z", "--get", key], cwd=self.work, capture_output=True, env=git_env())
        return None if result.returncode == 1 else result.stdout.decode().removesuffix("\0")


    def stored_identity(self):
        """The JSON text under the contract key identity, or None when the key is absent."""
        with self.raw_db() as db:
            row = db.execute("SELECT value FROM contract WHERE key = 'identity'").fetchone()
        return row and row[0]


    def queue_one_as(self, name, email, number=1):
        """Queue E<number> for r/D<number> with one commit whose author git builds from this name and email."""
        path, branch = ("a.txt", "b.txt", "lib/x.py")[number - 1], f"w{number}"
        self.worker(branch, {path: "agent\n"})
        subprocess.run(["git", "commit", "-q", "--amend", "--reset-author", "-m", branch], cwd=self.base / branch, check=True,
                       capture_output=True, env={**git_env(), "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email})
        sha = sh("git", "rev-parse", "HEAD", cwd=self.base / branch)
        lease = self.land("lease", "claim", "--holder", f"r/D{number}", "--paths", path)
        self.assertEqual(self.land("submit", "--holder", f"r/D{number}", "--branch", branch, "--sha", sha, "--lease", lease,
                                   "--reviewer", REVIEWER), f"E{number}")


    def remember_then_configure(self, mode, before, after):
        """Init in mode, run land with before configured and nothing queued, and configure after.

        Queue E1 with the author git builds from after.
        """
        self.init(mode=mode)
        self.configure_owner(*before)
        self.assertEqual(self.land("land"), "nothing to land")
        self.assertEqual(self.stored_identity(), json.dumps(before))
        self.configure_owner(*after)
        self.assertEqual((self.configured_value("user.name"), self.configured_value("user.email")), after)
        self.queue_one_as(*after)


    def assert_push_mode_pauses(self, before, after, now, last):
        self.remember_then_configure("push", before, after)
        entries, refs = self.queue_rows()["entry"], self.on_origin("for-each-ref")
        self.assertEqual(self.land("land"), "queue paused: " + self.identity_pause(now, last))
        self.assertEqual(self.origin_log(), ["init"])
        self.assertEqual(self.on_origin("for-each-ref"), refs)
        self.assertEqual(tuple(self.entry_fields(1, "state", "pr")), ("queued", ""))
        self.assertEqual(self.queue_rows()["entry"], entries)


    def assert_human_mode_pauses(self, before, after, now, last):
        with self.fake_gh():
            self.remember_then_configure("human", before, after)
            calls = self.base / "gh-calls"
            before_calls = calls.read_text() if calls.exists() else None
            entries, refs = self.queue_rows()["entry"], self.on_origin("for-each-ref")
            self.assertEqual(self.land("land"), "queue paused: " + self.identity_pause(now, last))
            self.assertEqual(calls.read_text() if calls.exists() else None, before_calls)
        self.assertEqual(self.on_origin("for-each-ref"), refs)
        self.assertEqual(tuple(self.entry_fields(1, "state", "pr")), ("queued", ""))
        self.assertEqual(self.queue_rows()["entry"], entries)


    OWNER = ("Owner", "owner@example.com")


    IDENTITY_CHANGES = {
        "name": (OWNER, ("Other", "owner@example.com"), "Other <owner@example.com>", "Owner <owner@example.com>"),
        "email": (OWNER, ("Owner", "other@example.com"), "Owner <other@example.com>", "Owner <owner@example.com>"),
        "name_spaces": (OWNER, (" Owner ", "owner@example.com"), " Owner  <owner@example.com>", "Owner <owner@example.com>"),
        "email_spaces": (OWNER, ("Owner", " owner@example.com "), "Owner < owner@example.com >", "Owner <owner@example.com>"),
        "name_case": (OWNER, ("owner", "owner@example.com"), "owner <owner@example.com>", "Owner <owner@example.com>"),
        "email_case": (OWNER, ("Owner", "Owner@example.com"), "Owner <Owner@example.com>", "Owner <owner@example.com>"),
        "same_joined_text": (("A <b>", "c"), ("A", "b> <c"), "A <b> <c>", "A <b> <c>"),
    }


    def land_one_then_pause_as_audit(self):
        """Push mode. E1 lands as Owner, the identity becomes audit, E2 by audit is queued, and land pauses. Returns the pause reason."""
        self.init(mode="push")
        self.configure_owner()
        self.queue_one_as("Owner", "owner@example.com")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.configure_owner("audit", "audit@example.com")
        self.queue_one_as("audit", "audit@example.com", number=2)
        reason = self.identity_pause("audit <audit@example.com>")
        self.assertEqual(self.land("land"), f"queue paused: {reason}")
        return reason


    def assert_an_unset_identity_pauses(self, *config):
        """E1 lands as Owner, each git config command in config runs, and E2 by t is queued.

        land must pause, set the paused row, add one log row, and change no other row and no origin ref.
        """
        self.init(mode="push")
        self.configure_owner()
        self.queue_one_as("Owner", "owner@example.com")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        for command in config:
            sh("git", "config", *command, cwd=self.work)
        self.queue_one_as("t", "t@t", number=2)
        before, refs = self.queue_rows(), self.on_origin("for-each-ref")
        reason = self.identity_pause(self.UNSET)
        self.assertEqual(self.land("land"), f"queue paused: {reason}")
        after = self.queue_rows()
        self.assertEqual(after["log"][:-1], before["log"])
        self.assertEqual(after["log"][-1][1:], ("queue", 0, "paused", reason))
        self.assertEqual(after["contract"], [(key, json.dumps(reason) if key == "paused" else value) for key, value in before["contract"]])
        self.assertEqual({**after, "log": before["log"], "contract": before["contract"]}, before)
        self.assertEqual(self.on_origin("for-each-ref"), refs)


    def assert_resume_then_land_lands_and_stores_no_identity(self):
        self.assertEqual(self.land("resume"), "queue resumed")
        self.assertEqual(self.land("land"), "landed E2 (r/D2)")
        self.assertIsNone(self.stored_identity())


    def stray_worktree_line(self, value, quoted=None, shown=None):
        config = (self.work / ".git").resolve() / "config"
        return (f"land: {shown or config} sets core.worktree to {value}, where git does not find this repository; "
                f"remove it with git --work-tree=. config --file {quoted or config} --unset-all core.worktree\n")


    def use_non_english_locale(self):
        """Set LANG and LC_ALL to de_DE.UTF-8 for the rest of this test.

        Returns whether git on this machine prints German under that locale.
        When it does not, git on PATH hides the English conflict strings unless
        that process has LC_ALL=C, and records each cherry-pick environment.
        """
        self.git_locale_log = self.base / "git-locale.log"
        translated = False
        try:
            locpath = self.base / "locales"
            locpath.mkdir()
            compiled = subprocess.run(
                ["localedef", "-f", "UTF-8", "-i", "de_DE", str(locpath / "de_DE.UTF-8")],
                capture_output=True)
        except OSError:
            compiled = None
        if compiled is not None and compiled.returncode == 0:
            os.environ["LOCPATH"] = str(locpath)
            env = os.environ.copy()
            env["LANG"] = "de_DE.UTF-8"
            env["LC_ALL"] = "de_DE.UTF-8"
            probe = subprocess.run(["git", "status"], cwd=self.work, capture_output=True, text=True, env=env)
            translated = "Auf Branch" in probe.stdout
        os.environ["LANG"] = "de_DE.UTF-8"
        os.environ["LC_ALL"] = "de_DE.UTF-8"
        if not translated:
            self.install_c_locale_gate()
        return translated


    def install_c_locale_gate(self):
        """Hide English conflict text unless this git process has LC_ALL=C."""
        current = shutil.which("git")
        bindir = self.base / "locale-gate"
        bindir.mkdir()
        script = bindir / "git"
        script.write_text(
            "#!" + sys.executable + "\n"
            "import os, subprocess, sys\n"
            f"real = {current!r}\n"
            f"log = {str(self.git_locale_log)!r}\n"
            "args = sys.argv[1:]\n"
            "if 'cherry-pick' in args:\n"
            "    with open(log, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('cherry-pick LC_ALL=%s\\n' % os.environ.get('LC_ALL', ''))\n"
            "proc = subprocess.run([real, *args], capture_output=True)\n"
            "out, err = proc.stdout, proc.stderr\n"
            "if os.environ.get('LC_ALL') != 'C':\n"
            "    out = out.replace(b'CONFLICT', b'KONFLIKT').replace(b'could not apply', b'nicht anwenden')\n"
            "    err = err.replace(b'CONFLICT', b'KONFLIKT').replace(b'could not apply', b'nicht anwenden')\n"
            "sys.stdout.buffer.write(out)\n"
            "sys.stderr.buffer.write(err)\n"
            "raise SystemExit(proc.returncode)\n"
        )
        script.chmod(0o755)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ["PATH"]
