if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    __package__ = "tests.landing"

import ast
import contextlib
import enum
import errno
import fcntl
import gc
import hashlib
import inspect
import io
import json
import locale
import os
import re
import runpy
import shlex
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
import traceback
import unittest
import weakref
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "t3/added/landing/scripts/land.py"
sys.path.insert(0, str(SCRIPT.parent))

import land  # noqa: E402

REVIEWER = "codex/gpt-6.1-sol"
ADMIN = ("--owner", ".admin/@1")

from ._support.descriptors import _Saved, _close_live_owners, _close_new_fds, _descriptors, _fd_numbers, _fd_paths, _open_fd_directory, _restore, _save
from ._support.inprocess import ProcessReason, _capture_stream, _child_codecs, _load, _pipe_encoding, _popen, _run, _status, _text_from_pipe, drive, real_popen, real_process
from ._support.shell import git_env, paused_child, sh
from ._support.paths import repository_state, stamp_in
from ._support.slot_scripts import CAP_CLOCK_MAIN, EPERM_KILLPG_STUB, EXCLUSIVE_MAIN, EXCLUSIVE_MAIN_WITHOUT_WAITID, FAILED_WITNESS_PROBE, FOREIGN_UID_WITNESS_PROBE, HOLD_SCRIPT, INTERRUPT_AFTER_THE_LEADER_EXITS, READY_MAIN, SLOT_HOLD, SLOT_WRAPPED, _darwin_eperm_main
from ._support.group_scripts import BACKGROUND_CHILD, GROUP_SLEEP, IGNORING_GROUP, RECORD_REPO_SCRIPT, REPORT_SCRIPT, SLOW_PUBLISH_MAIN, SURVIVING_IGNORING_GROUP, WAIT_SCRIPT
from ._support.previous_slot import PREVIOUS_SLOT_SCRIPT
from ._support.ofd_slot import OFD_SLOT_SCRIPT
from ._support.slot_callers import SlotCaller, SlotCallers
from ._support.case import CaseBase
from ._support.fake_forge import FakeForgeBase
from ._support.pr_flows import PrFlowsBase
from ._support.merge_flows import MergeFlowsBase
from ._support.store_faults import StoreFaultsBase
from ._support.lease_flows import LeaseFlowsBase
from ._support.identity_flows import IdentityFlowsBase
from ._support.slot_flows import SlotFlowsBase
from ._support.exclusive_flows import ExclusiveFlowsBase, cap_line

def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def setUpModule():
    fence_git_environment()


class LandingTest(CaseBase, FakeForgeBase, PrFlowsBase, MergeFlowsBase, StoreFaultsBase, LeaseFlowsBase, IdentityFlowsBase, SlotFlowsBase, ExclusiveFlowsBase, unittest.TestCase):
    pass


if __name__ == "__main__":
    if sys.argv[1:2] == ["--paused-child"]:
        paused_child(sys.argv[2], sys.argv[3:])
    unittest.main()
