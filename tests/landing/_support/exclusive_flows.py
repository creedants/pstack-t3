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
from .slot_scripts import EXCLUSIVE_MAIN, READY_MAIN, CAP_CLOCK_MAIN, EXCLUSIVE_MAIN_WITHOUT_WAITID, _darwin_eperm_main
from .group_scripts import GROUP_SLEEP, IGNORING_GROUP


class ExclusiveFlowsBase:

    def exclusive_args(self, argv, cap=None, grace=None, cap_max=None, main=EXCLUSIVE_MAIN):
        return [sys.executable, "-c", main, str(paths.SCRIPT.parent),
                "" if cap is None else str(cap),
                "" if grace is None else str(grace),
                "" if cap_max is None else str(cap_max),
                *argv]


    def run_exclusive(self, argv, cap=None, grace=None, cap_max=None, timeout=20, main=EXCLUSIVE_MAIN):
        return _run(
            self.exclusive_args(argv, cap, grace, cap_max, main),
            env=self.slot_env(), cwd=self.base, timeout=timeout, reason=ProcessReason.GOVERNOR)


    def popen_exclusive(self, argv, cap=None, grace=None, cap_max=None, main=EXCLUSIVE_MAIN):
        return real_popen(
            ProcessReason.GOVERNOR, self.exclusive_args(argv, cap, grace, cap_max, main),
            env=self.slot_env(), cwd=self.base, start_new_session=True)


    def signal_exclusive_ignoring_child(self, signum):
        parent_path = self.base / f"signal-{signum}-parent.pid"
        child_path = self.base / f"signal-{signum}-child.pid"
        ready_path = self.base / f"signal-{signum}-ready"
        try:
            ready_path.unlink()
        except OSError:
            pass
        with mock.patch.dict(os.environ, {"LAND_EXCLUSIVE_READY": str(ready_path)}):
            process = self.popen_exclusive(
                ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP,
                 str(parent_path), str(child_path)],
                cap=30, grace=0.2, main=READY_MAIN)
            try:
                child_pid, child_group = (int(part) for part in self.wait_for_line(child_path).split())
                self.assertEqual(child_group, int(parent_path.read_text()))
                deadline = time.monotonic() + 20
                while not ready_path.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(ready_path.exists(), "the ready marker was not written before the signal")
                os.kill(process.pid, signum)
                code = process.wait(timeout=30)
                self.assert_dead(child_pid)
                self.assert_slots_free()
                stderr = process.stderr.read()
                return code, stderr
            finally:
                if process.poll() is None:
                    os.kill(process.pid, signal.SIGKILL)
                    process.wait(timeout=30)
                process.stdout.close()
                process.stderr.close()
                self.release_recorded_group(parent_path, child_path)


def cap_line(cap):
    return (f"land: stopped the command at the {cap}s exclusive cap. "
            f"--for sets a longer cap and needs --reason past {cap}s.\n")
