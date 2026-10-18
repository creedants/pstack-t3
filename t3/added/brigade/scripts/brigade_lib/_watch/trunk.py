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

def _store_name(common):
    """The landing directory name for one git common directory."""
    base = common.parent.name if common.name == ".git" else common.name.removesuffix(".git")
    safe = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return safe + "-" + hashlib.sha1(str(common).encode()).hexdigest()[:8]


_GITHUB_OWNER_REPO = re.compile(
    r"^(?:https://github\.com/|ssh://git@github\.com/|git://github\.com/|git@github\.com:)"
    r"([^/]+)/([^/]+?)(?:\.git)?/?$")


def _github_owner_repo(url):
    """The owner/repo for one GitHub remote URL or None."""
    match = _GITHUB_OWNER_REPO.match((url or "").strip())
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if not owner or not repo or owner in (".", "..") or repo in (".", ".."):
        return None
    return "%s/%s" % (owner, repo)


def _contract_trunk(project_root):
    """The trunk contract for one project root or None."""
    root = str(project_root or "")
    if not root:
        return None
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root, capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            raise OSError(proc.stderr.strip())
        common = Path(proc.stdout.strip())
        base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
        db_path = Path(base) / "pstack-t3" / "landing" / _store_name(common) / "land.db"
        con = sqlite3.connect(str(db_path), timeout=5)
        try:
            rows = dict(con.execute("SELECT key, value FROM contract").fetchall())
        finally:
            con.close()
        trunk = json.loads(rows["trunk"]) if "trunk" in rows else None
        mode = json.loads(rows["mode"]) if "mode" in rows else None
        repo = json.loads(rows["repo"]) if "repo" in rows else root
        remote = json.loads(rows["remote"]) if "remote" in rows else "origin"
        if mode == "auto":
            mode = "push"
        if not trunk or not mode:
            raise ValueError("missing trunk")
        if not repo:
            repo = root
        if not remote:
            remote = "origin"
        return {"trunk": trunk, "mode": mode, "repo": str(repo), "remote": str(remote)}
    except Exception:
        pass
    try:
        result = seam.land_result(root, "status")
    except Exception:
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    match = re.search(r"^(\S+) mode onto (\S+)", text)
    if not match:
        return None
    mode, ref = match.group(1), match.group(2).rstrip(".")
    if mode == "auto":
        mode = "push"
    trunk = ref.rsplit("/", 1)[-1] if "/" in ref else ref
    if not trunk or not mode:
        return None
    parts = ref.split("/")
    remote = parts[-2] if len(parts) >= 2 else "origin"
    if not remote:
        remote = "origin"
    return {"trunk": trunk, "mode": mode, "repo": root, "remote": str(remote)}


def _owner_repo(repo, remote):
    """The owner/repo for one contract remote or an error reason."""
    if not Path(str(repo)).is_dir():
        return None, "repo not found"
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", remote],
            cwd=str(repo), capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        if not Path(str(repo)).is_dir():
            return None, "repo not found"
        return None, "git not found"
    except subprocess.TimeoutExpired:
        return None, "git timed out"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        reason = err[-1] if err else "exit %d" % proc.returncode
        if not reason or reason == "":
            reason = "no remote %s" % remote
        return None, reason
    url = (proc.stdout or "").strip().splitlines()
    text = url[0].strip() if url else ""
    if not text:
        return None, "no remote %s" % remote
    owner_repo = _github_owner_repo(text)
    if owner_repo is None:
        return None, "unsupported remote URL"
    return owner_repo, ""


def _gh_runs(repo, trunk, owner_repo):
    """The gh run list for one trunk or an error reason."""
    if not Path(str(repo)).is_dir():
        return None, "repo not found"
    try:
        result = subprocess.run(
            ["gh", "run", "list", "--repo", "github.com/" + owner_repo, "--branch", trunk, "--limit", "100",
             "--json", "conclusion,status,headSha,createdAt,url,workflowName"],
            cwd=str(repo), capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        if not Path(str(repo)).is_dir():
            return None, "repo not found"
        return None, "gh not found"
    except subprocess.TimeoutExpired:
        return None, "gh timed out"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip().splitlines()
        reason = err[-1] if err else "exit %d" % result.returncode
        return None, reason
    try:
        runs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as error:
        return None, "bad JSON %s" % error.msg
    if not isinstance(runs, list):
        return None, "bad JSON not a list"
    return runs, ""


def _is_completed(run):
    """True when one gh run row counts as completed."""
    return isinstance(run, dict) and run.get("status") == "completed"


def _is_valid_run(run):
    """True when one gh run row carries usable fields."""
    if not isinstance(run, dict):
        return False
    status = run.get("status")
    if not isinstance(status, str) or not status:
        return False
    conclusion = run.get("conclusion")
    if conclusion is not None and not isinstance(conclusion, str):
        return False
    for key in ("headSha", "createdAt", "url", "workflowName"):
        value = run.get(key)
        if not isinstance(value, str) or not value:
            return False
    return True


def _latest_trunk(rows):
    """The latest trunk log row as a small dict or None."""
    found = None
    for row in rows:
        if row.get("kind") == "trunk":
            found = row
    if found is None:
        return None
    state = found.get("state")
    ident = found.get("id")
    note = found.get("note") or ""
    if state == "red":
        try:
            data = json.loads(note)
            if isinstance(data, dict):
                return {"id": ident, "state": "red", "sha": data.get("sha", ""),
                        "workflow": data.get("workflow", ""), "url": data.get("url", ""),
                        "since": data.get("since", "")}
        except Exception:
            pass
        return {"id": ident, "state": "red", "sha": "", "workflow": "", "url": note, "since": ""}
    if state == "green":
        try:
            data = json.loads(note)
            if isinstance(data, dict) and "sha" in data:
                return {"id": ident, "state": "green", "sha": data.get("sha", "")}
        except Exception:
            pass
        return {"id": ident, "state": "green", "sha": note}
    if state == "unseen":
        try:
            data = json.loads(note)
            if isinstance(data, dict) and "reason" in data:
                return {"id": ident, "state": "unseen", "reason": data.get("reason", "")}
        except Exception:
            pass
        return {"id": ident, "state": "unseen", "reason": note}
    return None


def _observe_trunk(project_root, latest):
    """The trunk watch line and the log row to write or Nones."""
    contract = _contract_trunk(project_root)
    if contract is None:
        return None, None
    trunk = contract["trunk"]
    mode = contract["mode"]
    repo = contract.get("repo") or str(project_root)
    remote = contract.get("remote") or "origin"
    if mode == "local":
        return None, None
    owner_repo, resolve_error = _owner_repo(repo, remote)
    if owner_repo is None:
        reason = resolve_error or "unsupported remote"
        line = "cannot see trunk CI: %s" % reason
        if latest and latest.get("id") == trunk and latest.get("state") == "unseen" and latest.get("reason") == reason:
            return line, None
        return line, (trunk, "unseen", reason)
    runs, error = _gh_runs(repo, trunk, owner_repo)
    if runs is None:
        reason = error or "gh failed"
        line = "cannot see trunk CI: %s" % reason
        if latest and latest.get("id") == trunk and latest.get("state") == "unseen" and latest.get("reason") == reason:
            return line, None
        return line, (trunk, "unseen", reason)
    for run in runs:
        if not _is_valid_run(run):
            reason = "malformed run row"
            line = "cannot see trunk CI: %s" % reason
            if latest and latest.get("id") == trunk and latest.get("state") == "unseen" and latest.get("reason") == reason:
                return line, None
            return line, (trunk, "unseen", reason)
    completed = None
    for run in runs:
        if _is_completed(run):
            completed = run
            break
    if completed is None:
        reason = "no completed run in the last 100 runs"
        line = "cannot see trunk CI: %s" % reason
        if latest and latest.get("id") == trunk and latest.get("state") == "unseen" and latest.get("reason") == reason:
            return line, None
        return line, (trunk, "unseen", reason)
    conclusion = completed.get("conclusion") or ""
    sha = completed.get("headSha") or ""
    if conclusion == "failure":
        newest_workflow = completed.get("workflowName") or ""
        newest_url = completed.get("url") or ""
        since = completed.get("createdAt") or ""
        for run in runs:
            if not _is_completed(run):
                continue
            if (run.get("conclusion") or "") != "failure":
                break
            if run.get("createdAt"):
                since = run.get("createdAt")
        else:
            if len(runs) >= 100:
                oldest = runs[-1].get("createdAt") or ""
                if oldest:
                    since = "before %s" % oldest
        line = "trunk CI failing since %s: %s %s" % (since, newest_workflow, newest_url)
        note = json.dumps({"sha": sha, "workflow": newest_workflow, "url": newest_url, "since": since})
        if latest == {"id": trunk, "state": "red", "sha": sha,
                      "workflow": newest_workflow, "url": newest_url, "since": since}:
            return line, None
        return line, (trunk, "red", note)
    if conclusion == "success":
        if latest and latest.get("id") == trunk and latest.get("state") == "green" and latest.get("sha") == sha:
            return None, None
        return None, (trunk, "green", sha)
    reason = "conclusion %s" % conclusion if conclusion else "no conclusion"
    line = "cannot see trunk CI: %s" % reason
    if latest and latest.get("id") == trunk and latest.get("state") == "unseen" and latest.get("reason") == reason:
        return line, None
    return line, (trunk, "unseen", reason)


