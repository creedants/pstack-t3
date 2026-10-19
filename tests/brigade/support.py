if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    __package__ = "tests.brigade"

import enum
import fcntl
import io
import json
import locale
import os
import re
import runpy
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ._support.names import BRIGADE_DOCS, CLAUDE, CODEX, DOC_SETTINGS, HELD_LINE, HELD_QUESTION, ITEM_DECISION, LAND_SCRIPT, ROOT, SCRIPT, STILL_OPEN, SUPPORT, THREAD_A, THREAD_B, THREAD_C, THREAD_D, _land_has_rulings, _owner_words, _shown_root, brigade_docs
from ._support.inprocess import Outcome, ProcessReason, call, call_in_process, real_popen, real_process, run_globals
from ._support.race_child import _race_args, _race_child, _wait_for_path
from ._support.report_texts import A_LIST_THEN_A_PROSE_SECTION, BULLETS, FENCED, LEAD_IN, LONGER_THAN_A_NO_WORK_PHRASE, NESTED, NO_WORK, NUMBERED, PROSE, PROSE_ABOVE_A_LEAD_IN, REAL_FOLLOW_UPS, TRACKED, TWO_SECTIONS
from ._support.case import CaseBase
from ._support.brief_flows import BriefFlows
from ._support.landing_flows import LandingFlows
from ._support.ticket_flows import TicketFlows
from ._support.edits_flows import EditsFlows
from ._support.goal_flows import GoalFlows
from ._support.close_flows import CloseFlows
from ._support.sheet_flows import SheetFlows
from ._support.stores import StoresBase
from ._support.doc_checks import AnswerRelayDocChecks, LivenessIdleDocChecks, RoundBudgetDocChecks, SeatLaunchDocChecks, UsageLimitDocChecks
from ._support.admin_split_doc import BrigadeAdminSplitDocChecks
from ._support.races import _AdminRace, _HandoffRace, _ItemsRace, _ReportingRace, _TicketsRace, _WakeRace

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


class BrigadeTest(CaseBase, BriefFlows, LandingFlows, TicketFlows, EditsFlows, GoalFlows, CloseFlows, SheetFlows, unittest.TestCase):
    pass


class StoresTest(StoresBase, unittest.TestCase):
    pass


class SeatLaunchDocTest(SeatLaunchDocChecks, unittest.TestCase):
    pass


class LivenessIdleDocTest(LivenessIdleDocChecks, unittest.TestCase):
    pass


class UsageLimitDocTest(UsageLimitDocChecks, unittest.TestCase):
    pass


class RoundBudgetDocTest(RoundBudgetDocChecks, unittest.TestCase):
    pass


class AnswerRelayDocTest(AnswerRelayDocChecks, unittest.TestCase):
    pass


class BrigadeAdminSplitDocTest(BrigadeAdminSplitDocChecks, unittest.TestCase):
    pass


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--race-child":
        try:
            _race_child(sys.argv[2], sys.argv[3], sys.argv[4:])
        except TimeoutError as error:
            print(f"race child timed out waiting for {error}", file=sys.stderr)
            sys.exit(2)
    else:
        unittest.main()
