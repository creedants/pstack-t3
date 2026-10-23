import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import math
import os
import posixpath
import re
import shlex
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from landing_lib import seam

Landing = namedtuple("Landing", "id holder pr minutes")
Times = namedtuple("Times", "n median p90 max")


def open_read_only(path):
    """mode=ro reads land.db through SQLite's locks, so the read is one committed snapshot and changes no committed data.
    A read-only open can create a log with no frames and a shared-memory index beside a database whose last writer just closed.
    With a write-ahead log and its index beside land.db, the read reads that log.
    With a log and no index, a read would create the index or miss the log's rows, so the open is refused.
    """
    log, index = Path(f"{path}-wal").exists(), Path(f"{path}-shm").exists()
    if log and not index:
        raise seam.LandError("the write-ahead log has no index beside it, so a read would create it or miss the log's rows")
    connection = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, isolation_level=None)
    connection.execute("PRAGMA query_only=1")
    connection.execute("BEGIN")
    return connection


def database(repo):
    common = seam.common_dir(repo)
    path = seam.state_home() / "landing" / seam.store_name(common) / "land.db"
    if not path.exists():
        raise seam.LandError(f"{common.parent} has no landing contract; run land.py init in it")
    return path


def holder_matches(holder, prefix):
    if prefix is None:
        return True
    return holder == prefix or (prefix.endswith("/") and holder.startswith(prefix))


def landings(db, holder, last):
    db.row_factory = sqlite3.Row
    selected = []
    skipped = 0
    rows = db.execute("""
        SELECT entry.id, entry.holder, entry.pr, entry.at AS submitted,
               (SELECT log.at FROM log
                WHERE log.kind = 'entry' AND log.id = entry.id AND log.state = 'landed'
                ORDER BY log.rowid LIMIT 1) AS landed_at
        FROM entry WHERE entry.state = 'landed'
    """)
    for row in rows:
        if not holder_matches(row["holder"], holder):
            continue
        if row["landed_at"] is None:
            skipped += 1
            continue
        submitted = datetime.fromisoformat(row["submitted"])
        landed = datetime.fromisoformat(row["landed_at"])
        minutes = (landed - submitted).total_seconds() / 60
        selected.append((landed, row["id"], Landing(row["id"], row["holder"], row["pr"], minutes)))
    selected.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in selected[-last:]], skipped


def summarize(minutes):
    count = len(minutes)
    if count == 0:
        return Times(0, None, None, None)
    ordered = sorted(minutes)
    return Times(count, ordered[-(-5 * count // 10) - 1], ordered[-(-9 * count // 10) - 1], ordered[-1])


def text_report(rows, times, skipped):
    def shown(value):
        return "-" if value is None else f"{value:.1f}"

    lines = [f"E{row.id} {row.holder} {row.pr or '-'} {row.minutes:.1f}" for row in rows]
    lines.append(f"n={times.n} median={shown(times.median)} p90={shown(times.p90)} max={shown(times.max)} skipped={skipped}")
    return "\n".join(lines)


def json_report(rows, times, skipped):
    return json.dumps({
        "entries": [
            {"id": row.id, "holder": row.holder, "pr": row.pr or None, "minutes": row.minutes}
            for row in rows
        ],
        "n": times.n,
        "median": times.median,
        "p90": times.p90,
        "max": times.max,
        "skipped": skipped,
    })


def landing_times(repo, last, holder, as_json):
    if last < 1:
        raise seam.LandError("--last must be at least 1")
    path = database(repo)
    connection = None
    try:
        connection = open_read_only(path)
        rows, skipped = landings(connection, holder, last)
    except sqlite3.DatabaseError as error:
        raise seam.LandError(f"the landing database at {path} is locked or unreadable: {error}") from None
    finally:
        if connection is not None:
            connection.close()
    times = summarize([row.minutes for row in rows])
    return json_report(rows, times, skipped) if as_json else text_report(rows, times, skipped)
