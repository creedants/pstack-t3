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
from landing_lib import seam

MODES = ("human", "merge", "push", "local")


LEGACY_MODES = {"auto": "push"}


REMOTE_MODES = ("human", "merge", "push")


MERGE_METHODS = ("merge", "squash", "rebase")


MERGE_FLAGS = {
    "merge": "mergeCommitAllowed",
    "squash": "squashMergeAllowed",
    "rebase": "rebaseMergeAllowed",
}


METHOD_REFUSAL = (
    ("merge", "Merge commits are not allowed on this repository"),
    ("squash", "Squash merges are not allowed on this repository"),
    ("rebase", "Rebase merges are not allowed on this repository"),
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS contract (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lease (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, holder TEXT NOT NULL, paths TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'submitted', 'released')), expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entry (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, holder TEXT NOT NULL, branch TEXT NOT NULL,
  sha TEXT NOT NULL, base TEXT NOT NULL, fingerprint TEXT NOT NULL, lease INTEGER NOT NULL REFERENCES lease(id),
  reviewer TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued', 'landing', 'awaiting-merge', 'landed', 'bounced')),
  candidate TEXT NOT NULL DEFAULT '', landed TEXT NOT NULL DEFAULT '', pr TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '', UNIQUE (sha, holder));
CREATE TABLE IF NOT EXISTS attempt (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, base TEXT NOT NULL, candidate TEXT NOT NULL,
  entries TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('publishing', 'published', 'abandoned')));
CREATE TABLE IF NOT EXISTS log (at TEXT NOT NULL, kind TEXT NOT NULL, id INTEGER NOT NULL, state TEXT NOT NULL, note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS owner (prefix TEXT PRIMARY KEY, generation INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reservation (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, prefix TEXT NOT NULL, paths TEXT NOT NULL,
  ruling TEXT NOT NULL UNIQUE, ttl REAL NOT NULL, armed TEXT NOT NULL DEFAULT '', expires TEXT NOT NULL DEFAULT '',
  taken TEXT NOT NULL DEFAULT '', state TEXT NOT NULL CHECK (state IN ('standing', 'claimed', 'lifted')));
CREATE TABLE IF NOT EXISTS share (prefix TEXT PRIMARY KEY, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS contest (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, a TEXT NOT NULL, b TEXT NOT NULL, first TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL CHECK (state IN ('open', 'settled', 'cancelled')));
"""


class LandError(Exception):
    pass


def now():
    return datetime.now(timezone.utc)


def stamp(moment=None):
    return (moment or now()).isoformat(timespec="microseconds")


def state_home():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "pstack-t3"


def git(*args, cwd, check=True, stdin=None, exact=False):
    """Run git with LC_ALL=C. Callers parse English diagnostics such as CONFLICT.

    Python's text mode turns a carriage return in the output, with or without a newline after it, into one newline.
    With exact, the output is decoded with no such change.
    """
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    if exact:
        result = subprocess.run(["git", *args], cwd=cwd, input=None if stdin is None else stdin.encode("utf-8", "surrogateescape"),
                                capture_output=True, env=env)
        result.stdout, result.stderr = (stream.decode("utf-8", "surrogateescape") for stream in (result.stdout, result.stderr))
    else:
        result = subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True, text=True,
                                encoding="utf-8", errors="surrogateescape", env=env)
    if check and result.returncode != 0:
        raise LandError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result


def git_push(*args, cwd, check=False):
    """Every queue push passes --no-follow-tags, so push.followTags cannot publish a local tag."""
    return git("push", "--no-follow-tags", *args, cwd=cwd, check=check)


IGNORE_CORE_WORKTREE = ("--work-tree=.",)


LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"


ESCAPED = re.compile("[\\\\" + LINE_BREAKS + "]")


def one_line(text):
    """text with each backslash and each LINE_BREAKS character written as its Python escape."""
    return ESCAPED.sub(lambda found: found.group().encode("unicode_escape").decode("ascii"), text)


def shell_word(text):
    """A shell word that expands to text and holds no LINE_BREAKS character. text must not end in a newline.

    It is shlex.quote(text) when text holds none. Otherwise it is a printf %b command substitution in double quotes,
    and a shell drops newlines from the end of one. Its argument writes each byte of a backslash or a LINE_BREAKS
    character as a backslash, a zero, and three octal digits.
    """
    if not set(LINE_BREAKS) & set(text):
        return shlex.quote(text)
    octal = ESCAPED.sub(lambda found: "".join(f"\\0{byte:03o}" for byte in os.fsencode(found.group())), text)
    return f'"$(printf %b {shlex.quote(octal)})"'


def refuse_stray_worktree(common):
    """Stop when the common config file sets core.worktree to a non-empty value where git does not find this repository."""
    config = common / "config"
    # -z ends the value with a NUL, so a newline before the NUL belongs to the value.
    value = git(*IGNORE_CORE_WORKTREE, "config", "--file", str(config), "-z", "--get", "core.worktree",
                cwd=common, check=False, exact=True).stdout.removesuffix("\0")
    if not value:
        return
    # git resolves a relative core.worktree against the git directory.
    found = git("-C", os.path.join(common, value), "rev-parse", "--path-format=absolute", "--git-common-dir",
                cwd=common, check=False, exact=True)
    if found.returncode != 0 or Path(found.stdout.removesuffix("\n")).resolve() != common:
        raise LandError(f"{one_line(str(config))} sets core.worktree to {one_line(value)}, where git does not find this repository; "
                        f"remove it with git --work-tree=. config --file {shell_word(str(config))} --unset-all core.worktree")


def common_dir(path):
    """The git common directory identifies a repository across all of its worktrees."""
    found = git(*IGNORE_CORE_WORKTREE, "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path, exact=True)
    common = Path(found.stdout.removesuffix("\n")).resolve()
    refuse_stray_worktree(common)
    # Require git rev-parse --show-toplevel to succeed.
    git("rev-parse", "--show-toplevel", cwd=path)
    return common


def canonical(path, kind="lease"):
    """Repository-relative POSIX path with no aliases. An empty string is the whole repository."""
    value = posixpath.normpath(path.strip().replace("\\", "/")).lstrip("/")
    if value in (".", ""):
        return ""
    if value == ".." or value.startswith("../"):
        raise LandError(f"{kind} path {path!r} leaves the repository")
    return value


def overlaps(first, second):
    return any(a == "" or b == "" or a == b or a.startswith(b + "/") or b.startswith(a + "/") for a in first for b in second)


def covered(path, paths):
    return any(p == "" or path == p or path.startswith(p + "/") for p in paths)


GENERATED_PATHS = ("skills", "docs/cli", "docs/skills.md")


def is_generated(path):
    return covered(path, GENERATED_PATHS)


def _header_paths(line):
    rest = line[len("diff --git "):]
    parts = []
    pos = 0
    while len(parts) < 2:
        if pos >= len(rest):
            return None
        if rest[pos] == '"':
            end = pos + 1
            while end < len(rest) and rest[end] != '"':
                if rest[end] == "\\":
                    return None
                end += 1
            if end >= len(rest):
                return None
            parts.append(rest[pos + 1:end])
            pos = end + 1
            if pos < len(rest):
                if rest[pos] != " ":
                    return None
                pos += 1
            elif len(parts) < 2:
                return None
        else:
            end = rest.find(" ", pos)
            if end == -1:
                parts.append(rest[pos:])
                pos = len(rest)
            else:
                parts.append(rest[pos:end])
                pos = end + 1
    if pos != len(rest):
        return None
    if not parts[0].startswith("a/") or not parts[1].startswith("b/"):
        return None
    return parts[0][2:], parts[1][2:]


def fingerprint(repo, base, head):
    """Hash of the source change. Generated sections are dropped before hashing."""
    diff = git("diff", "--binary", "--full-index", "--no-renames", "--no-textconv", "--no-ext-diff", "-U0", base, head, cwd=repo).stdout
    if not diff:
        return ""
    lines = diff.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("diff --git ")]
    if not starts:
        kept = lines
    else:
        kept = lines[:starts[0]]
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(lines)
            section = lines[start:end]
            parsed = _header_paths(section[0])
            if parsed is None:
                kept.extend(section)
                continue
            a_path, b_path = parsed
            if is_generated(a_path) or is_generated(b_path):
                continue
            kept.extend(section)
        if not kept:
            return ""
    lines = [line for line in kept if not line.startswith("index ")]
    lines = [re.sub(r"^@@ [^@]* @@", "@@", line) for line in lines]
    return hashlib.sha256("\n".join(lines).encode("utf-8", "surrogateescape")).hexdigest()


def changed_paths(repo, base, head):
    return [p for p in git("diff", "-z", "--name-only", "--no-renames", base, head, cwd=repo).stdout.split("\0") if p]
