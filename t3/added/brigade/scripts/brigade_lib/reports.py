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
from brigade_lib.shared import BrigadeError

REVIEW_FILE = re.compile(r"(D\d+)-review(-.+)?\.md")
ITEM_REPORT = re.compile(r"(D\d+)\.md")


def in_reports(restaurant, report, name):
    """True when report is the bare name, `reports/<name>`, or a path whose directory resolves to this store's reports/."""
    return report in (name, f"reports/{name}") or Path(report).parent.resolve() == restaurant.dir.resolve() / "reports"


# What reports/<name> is when it is not a regular file of this store. Each value finishes the words `reports/<name> `.
NOT_A_REPORT = {
    "link": "is a symbolic link",
    "missing": "does not exist",
    "other": "is not a regular file",
    "elsewhere": "resolves outside this store's reports/",
}


def report_stat(restaurant, name):
    """(kind, status) of reports/<name>. The kind is `file` or a key of NOT_A_REPORT, and the status is None for `missing`.

    The entry is read once, with one lstat that follows no link at <name>. Whether it is there, a symbolic link, or a
    regular file comes from that status alone, and `elsewhere` adds where reports/ itself resolves.
    The first that holds wins. `missing` is a name with nothing there, which is that lstat finding no entry or finding
    that reports/ is not a directory. `link` is a symbolic link, whether or not its target exists. `other` is an entry
    that is not a regular file. `elsewhere` is a regular file under a reports/ that resolves to another place.
    """
    reports = restaurant.dir / "reports"
    try:
        status = (reports / name).lstat()
    except (FileNotFoundError, NotADirectoryError):
        return "missing", None
    if stat.S_ISLNK(status.st_mode):
        return "link", status
    if not stat.S_ISREG(status.st_mode):
        return "other", status
    if reports.resolve() != restaurant.dir.resolve() / "reports":
        return "elsewhere", status
    return "file", status


def report_entry(restaurant, name):
    """The kind report_stat gives reports/<name>."""
    return report_stat(restaurant, name)[0]


def item_report(restaurant, report):
    """The bare name of an item report under this store's reports/.

    Its content is read, so report_entry must call reports/<name> a `file`.
    A path that in_reports turns down is refused.
    A `..` component is refused as written, before anything is resolved.
    """
    if ".." in Path(report).parts:
        raise BrigadeError(f"{report} has a .. component; name a file like reports/D2.md")
    name = Path(report).name
    if not ITEM_REPORT.fullmatch(name):
        raise BrigadeError(f"{name} is not an item report; name a file like reports/D2.md")
    if not in_reports(restaurant, report, name):
        raise BrigadeError(f"{report} is outside this store's reports/; name reports/{name}")
    kind = seam.report_entry(restaurant, name)
    if kind != "file":
        raise BrigadeError(f"reports/{name} {NOT_A_REPORT[kind]}; nothing added")
    return name
