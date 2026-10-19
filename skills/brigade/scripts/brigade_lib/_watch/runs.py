import argparse
import fcntl
import fnmatch
import functools
import hashlib
import io
import json
import os
import posixpath
import re
import runpy
import shlex
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib import seam
from brigade_lib.items import attempt_starts
from brigade_lib.leases import block_inputs, entry_line, fire_command, recheck_blocks, renew_line, workers_line
from brigade_lib.reviews import round_debt
from brigade_lib.shared import BrigadeError, clean
from brigade_lib.store import LEASED_STATES, OPEN_LINES, OPEN_RUN_MINUTES
from brigade_lib.tickets import handed_count, handoff_id, inbox_file, sibling_rails, taken_row
from brigade_lib._watch.progress import worker_where

def _worker_skip(error):
    """The single watch line for an unreadable T3 database."""
    text = str(error).strip().splitlines()
    detail = text[-1].strip() if text and text[-1].strip() else "unavailable"
    detail = " ".join(detail.split())
    if len(detail) > 200:
        detail = detail[:200]
    return f"cannot see worker runs: {detail}"


def _read_worker_runs(threads):
    """The latest run per thread id and an optional skip line.

    Reads statev2.sqlite read-only through activity.py. Never raises.
    A missing database returns empty runs with no line. Any other
    failure, including a threads table without updated_at, returns
    empty runs with one line.
    """
    wanted = list(dict.fromkeys(thread for thread in threads if thread))
    if not wanted:
        return {}, None
    try:
        loaded = runpy.run_path(str(Path(__file__).resolve().parents[2] / "activity.py"))
    except Exception as error:
        return {}, _worker_skip(error)
    try:
        database = loaded["t3_database"](None, os.environ, Path.home())
    except Exception as error:
        if "no T3 Code database" in str(error):
            return {}, None
        return {}, _worker_skip(error)
    try:
        connection = loaded["open_t3"](database)
    except Exception as error:
        return {}, _worker_skip(error)
    try:
        loaded["check_shape"](connection)
        marks = ", ".join("?" * len(wanted))
        table = loaded["RUNS"]
        rows = connection.execute(
            f"SELECT thread_id, status, requested_at, completed_at FROM {table} WHERE thread_id IN ({marks})",
            wanted,
        ).fetchall()
        runs = {}
        for thread_id, status_raw, requested_raw, completed_raw in rows:
            thread = loaded["text_in"](thread_id, table, "thread_id")
            status_text = loaded["text_in"](status_raw, table, "status")
            requested_ts = loaded["time_in"](requested_raw, table, "requested_at")
            completed_ts = loaded["time_in"](completed_raw, table, "completed_at")
            loaded["parse_status"](status_text, completed_ts is not None)
            prev = runs.get(thread)
            if prev is None or requested_ts > prev["requested"]:
                runs[thread] = {"status": status_text, "requested": requested_ts, "completed": completed_ts,
                                "updated": None}
        threads_table = loaded["THREADS"]
        thread_rows = connection.execute(
            f"SELECT thread_id, updated_at FROM {threads_table} WHERE thread_id IN ({marks})",
            wanted,
        ).fetchall()
        for thread_id_raw, updated_raw in thread_rows:
            thread = loaded["text_in"](thread_id_raw, threads_table, "thread_id")
            if thread not in runs:
                continue
            if updated_raw is None:
                runs[thread]["updated"] = None
            else:
                runs[thread]["updated"] = loaded["time_in"](updated_raw, threads_table, "updated_at")
    except Exception as error:
        try:
            connection.close()
        except Exception:
            pass
        return {}, _worker_skip(error)
    try:
        connection.close()
    except Exception:
        pass
    return runs, None


def _idle_completed(runs, thread, moment):
    """The completed or quiet timestamp when this thread is idle, else None.

    Idle means the latest run ended, or its open run saw no thread
    activity for at least OPEN_RUN_MINUTES.
    """
    entry = runs.get(thread) if runs else None
    if not entry:
        return None
    completed = entry.get("completed")
    if completed is not None:
        return completed
    updated = entry.get("updated")
    if updated is None:
        return None
    try:
        age = moment.timestamp() - updated
    except Exception:
        return None
    if age >= OPEN_RUN_MINUTES * 60:
        return updated
    return None


def _local_stamp(completed):
    """The local time text for one completed timestamp."""
    return datetime.fromtimestamp(completed).astimezone().isoformat(timespec="minutes")


def _idle_line(dish, completed):
    """The watch line for one idle worker with no report."""
    where = worker_where(dish)
    return f"{dish['id']}: idle since {_local_stamp(completed)} with no report ({where})"


