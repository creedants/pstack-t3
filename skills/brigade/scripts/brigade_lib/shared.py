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



class BrigadeError(Exception):
    pass


class MalformedTable(BrigadeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def slug(text):
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not value:
        raise BrigadeError(f"cannot make a slug from {text!r}")
    return value


def clean(value):
    return re.sub(r"[\t\r\n]+", " ", str(value if value is not None else "")).strip()


# The cells save_rows and append write as they are given. read_labels decides whether each holds a legal value, and
# clean changes no legal value, so cleaning one could only make a cell that is not legal read as legal after a rewrite.
KEPT_CELLS = {"rail.tsv": ("kind", "goal"), "dishes.tsv": ("kind", "goal"), "goals.tsv": ("id",)}


def stored(table, key, value):
    """The text save_rows and append write into one cell.

    A cell in KEPT_CELLS is written as it is given when it is text with no tab and no newline. That is every value a
    table read gives. Every other value is written as clean gives it.
    """
    if key in KEPT_CELLS.get(table, ()) and isinstance(value, str) and "\t" not in value and "\n" not in value:
        return value
    return clean(value)


def one_of(words):
    """`a, b, or c` from three or more words."""
    return ", ".join(words[:-1]) + f", or {words[-1]}"


def plural(count, word):
    """`1 send-back`, `0 send-backs`, `2 send-backs`."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"
FENCE_OPEN = re.compile(r"(`{3,})[^`]*|(~{3,}).*")
FENCE_OPEN_INDENTED = re.compile(r" {0,3}(?:(`{3,})[^`]*|(~{3,}).*)")


def fence_open(line):
    """The (character, length) of the fence a line opens, or None when it opens none.

    A fence opens at a line that starts at the first column with three or more backticks and holds no other
    backtick, or that starts there with three or more tildes.
    """
    opened = FENCE_OPEN.fullmatch(line)
    run = opened.group(1) or opened.group(2) if opened else None
    return (run[0], len(run)) if run else None


def threads_fence_open(line):
    """The (character, length) of the fence a line of a worker report opens, or None when it opens none.

    A fence opens at a line that starts, after up to three spaces, with three or more backticks and holds no
    other backtick, or that starts there with three or more tildes.
    """
    opened = FENCE_OPEN_INDENTED.fullmatch(line)
    run = opened.group(1) or opened.group(2) if opened else None
    return (run[0], len(run)) if run else None


def fence_close(line, run):
    """Whether a line closes a fence that opened with that (character, length).

    A fence closes at a line that is at most three spaces, at least as many of the same character, and then
    only spaces and tabs.
    """
    return re.compile(f" {{0,3}}{run[0]}{{{run[1]},}}[ \t]*").fullmatch(line) is not None


def same_text(value):
    return " ".join(value.split()).casefold()


def command_words(command):
    try:
        return shlex.split(command)
    except ValueError as error:
        raise BrigadeError(f"{command!r} is not a command line: {error}") from None


def age(delta):
    seconds = max(0, int(delta.total_seconds()))
    if seconds >= 86400:
        return f"{seconds // 86400}d"
    if seconds >= 3600:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"
