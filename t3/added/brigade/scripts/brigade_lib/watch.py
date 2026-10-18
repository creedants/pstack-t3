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


def option_key(text):
    return text.strip().rstrip(".").casefold()


def closing_options(row):
    if not row.get("dish", "").strip():
        return []
    default = option_key(row.get("default", ""))
    options = []
    for option in row.get("options", "").split(","):
        option = option.strip()
        if option and option_key(option) != default:
            options.append(option)
    return options


def open_item_decisions(rows):
    decisions = {}
    for row in rows:
        dish = row["dish"]
        if row["state"] == "open" and dish and dish not in decisions:
            decisions[dish] = row
    return decisions


def worker_where(dish):
    """The worker words a progress sentence names."""
    if dish["thread"]:
        return f"thread {dish['thread']}"
    if dish["task"]:
        return f"task {dish['task']}"
    return "no worker recorded"


def attempt_count(restaurant, dish):
    """The attempt this dish is on, counted from 1."""
    return len(attempt_starts(restaurant, dish))


def ended_row(restaurant, dish):
    """The log row that records this attempt's worker end, or None.

    The attempt number is the row's state cell, so no row is rewritten when a
    replacement worker starts another attempt.
    """
    number = str(attempt_count(restaurant, dish))
    found = None
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "ended" and row["id"] == dish["id"] and row["state"] == number:
            found = row
    return found


def recovery_lines(dish, error):
    """The flag line and the four recovery steps for a dish whose worker ended with no report."""
    ident = dish["id"]
    branch = dish.get("branch") or ""
    context = shlex.quote(error)
    # A brief needs a goal, an acceptance, a verify, a base, and a contract, so the line names them
    # instead of a command brief refuses.
    steps = [f"  keep the commits on {shlex.quote(branch)} and record no new branch; the dish keeps its lease"
             if branch else "  keep the commits where the worker left them; the dish keeps its lease",
             f"  write the continuation brief: brief {ident} with --base <trunk ref>, --context {context}, "
             "and the goal, acceptance, verify, and contract it needs",
             f"  launch a fresh thread in the same worktree and record it with dish {ident} --thread <new thread id>"]
    # The record needs a thread, but the cell can be cleared afterwards without starting another attempt.
    steps.append(f"  archive the dead thread {shlex.quote(dish['thread'])}" if dish["thread"]
                 else "  archive the dead worker")
    return [f"{ident}: worker ended with no report; the recorded reason is {error}", *steps]


def dish_progress(restaurant, dish, moment, held=False):
    """The in-progress lines watch prints for one dish. Empty when the dish is held or not in progress.

    The report file's mtime is read here. Call this under the store lock.
    """
    if held or dish["state"] != "in-progress":
        return []
    starts = attempt_starts(restaurant, dish)
    if not dish["thread"] and len(starts) > 1:
        return [f"{dish['id']}: in progress with no worker thread; launch a fresh worker"]
    start = starts[-1]
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    minutes = int((moment - start).total_seconds() // 60)
    timebox = int(dish.get("timebox") or 60)
    report_path = restaurant.dir / "reports" / f"{dish['id']}.md"
    where = worker_where(dish)
    written = datetime.fromtimestamp(report_path.stat().st_mtime, timezone.utc) if report_path.exists() else None
    if written and written >= start:
        if dish.get("reported") == "yes":
            ago = int((moment - written).total_seconds() // 60)
            if ago > OPEN_RUN_MINUTES:
                return [f"{dish['id']}: reported {ago}m ago, not in review; read the thread ({where})"]
            return [f"{dish['id']}: report written {ago}m ago; review it even if the worker's run is still open ({where})"]
        return [f"{dish['id']}: report written, no report-back ({where})"]
    # A dead worker is neither running nor over its timebox, so its lines come before both.
    ended = ended_row(restaurant, dish)
    if ended:
        return recovery_lines(dish, ended["note"])
    if minutes > timebox:
        return [f"{dish['id']}: over its {timebox}m timebox at {minutes}m with no report; read its thread and decide ({where})"]
    return [f"{dish['id']}: running {minutes}m of {timebox}m ({where})"]


HANDOFF_BYTES = 40000


def handoff_size_line(directory):
    path = directory / "handoff.md"
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
    except OSError:
        return None
    if size <= HANDOFF_BYTES:
        return None
    return f"handoff.md is {size} bytes. Rewrite its current-state section in place and move the old text to an archive file."


def _store_name(common):
    """The landing directory name for one git common directory."""
    base = common.parent.name if common.name == ".git" else common.name.removesuffix(".git")
    safe = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return safe + "-" + hashlib.sha1(str(common).encode()).hexdigest()[:8]


def _contract_trunk(project_root):
    """The trunk branch and the landing mode for one project root or None."""
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
        if mode == "auto":
            mode = "push"
        if not trunk or not mode:
            raise ValueError("missing trunk")
        return (trunk, mode)
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
    return (trunk, mode)


def _gh_runs(project_root, trunk):
    """The gh run list for one trunk or an error reason."""
    try:
        result = subprocess.run(
            ["gh", "run", "list", "--branch", trunk, "--limit", "5",
             "--json", "conclusion,status,headSha,createdAt,url,workflowName"],
            cwd=str(project_root), capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
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
    if run.get("status") == "completed":
        return True
    conclusion = run.get("conclusion")
    return conclusion is not None and conclusion != ""


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
    trunk, mode = contract
    if mode == "local":
        return None, None
    runs, error = _gh_runs(project_root, trunk)
    if runs is None:
        reason = error or "gh failed"
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
        reason = "no completed runs"
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
        line = "trunk CI failing since %s: %s %s" % (since, newest_workflow, newest_url)
        note = json.dumps({"sha": sha, "workflow": newest_workflow, "url": newest_url, "since": since})
        if latest and latest.get("id") == trunk and latest.get("state") == "red" and latest.get("sha") == sha:
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


def watch(restaurant):
    rails = sibling_rails(restaurant)
    with restaurant.checked():
        restaurant.fence()
        progress = {}
        moment = datetime.now(timezone.utc)
        dishes = [dish for dish in restaurant.rows("dishes.tsv") if dish["state"] not in ("merged", "dropped")]
        decisions = open_item_decisions(restaurant.rows("86.tsv"))
        debts = {dish["id"]: round_debt(restaurant, dish) for dish in dishes}
        for dish in dishes:
            held = bool(decisions.get(dish["id"]) or debts[dish["id"]])
            sentences = dish_progress(restaurant, dish, moment, held)
            if sentences:
                progress[dish["id"]] = sentences
        lines = []
        for ticket in restaurant.rows("rail.tsv"):
            if ticket["state"] != "moved":
                continue
            name = ticket["dish"].removeprefix("to:")
            target = restaurant.dir.parent / name
            handoff = handoff_id(restaurant.dir, ticket["id"])
            taken = taken_row(rails.get(target.resolve()), handoff)
            # take writes the ticket before it deletes the file, so a missing file with no ticket was never delivered.
            if inbox_file(target, handoff).exists():
                if taken is None:
                    lines.append(f"{ticket['id']}: moved to {name}, waiting for ticket take")
            elif taken is None:
                lines.append(f"{ticket['id']}: moved to {name}, not delivered; run ticket move {ticket['id']} --to {name} again")
        handed = handed_count(restaurant)
        if handed:
            lines.append(f"handed to you: {handed}; run ticket take")
        inputs = block_inputs(restaurant)
        workers = workers_line(restaurant.meta, inputs[1])
        # A skipped seat is a restaurant-wide fact, not a dish line, so it sits between the two.
        skipped = skip_lines(restaurant)
        project_root = restaurant.meta.get("projectRoot", "")
        latest = _latest_trunk(restaurant.rows("log.tsv"))
    # land.py waits on the landing database, so every call runs outside the store lock.
    item_lines, answered = [], True
    for dish in dishes:
        decision = decisions.get(dish["id"])
        pending = debts[dish["id"]]
        found = list(progress.get(dish["id"], []))
        entry, submitted = entry_line(restaurant, dish) if dish["state"] in ("passed", "queued") else (None, False)
        # A submitted entry owns the lease now, so a released lease is not a reason to claim again.
        if dish["lease"] and dish["state"] in LEASED_STATES and not submitted:
            line, ok = renew_line(restaurant, dish)
            answered = answered and ok
            found += [line] if line else []
        found += [entry] if entry else []
        if pending:
            item_lines.append(f"{dish['id']}: {debts[dish['id']]} send-backs, decision pending")
        if decision:
            item_lines.append(f"{dish['id']}: open decision {decision['id']}: {decision['question']}; "
                              f"launch no worker or verifier until 86 answer {decision['id']}")
        if decision or pending:
            item_lines += found
        else:
            item_lines += found or [f"{dish['id']}: {OPEN_LINES[dish['state']]}"]
    for ident, note, held in recheck_blocks(restaurant, inputs):
        lines.append(f"{ident}: {held[1]}" if held else f"{ident}: unblocked; run {fire_command(ident, note)}")
    try:
        trunk_line, trunk_row = _observe_trunk(project_root, latest)
    except Exception:
        trunk_line, trunk_row = None, None
    if trunk_row is not None:
        restaurant.log("trunk", trunk_row[0], trunk_row[1], trunk_row[2])
    if trunk_line:
        lines.append(trunk_line)
    if answered:
        # walk --stale-hours then measures whether this coordinator still keeps its leases alive.
        restaurant.change_meta(lastActivityAt=seam.now())
    text = "\n".join([workers, *skipped, *(item_lines + lines or ["no work in progress"])])
    sentence = handoff_size_line(restaurant.dir)
    if sentence:
        restaurant.warn(sentence)
    return text


def require_seat(seat):
    """The cleaned provider/model seat. Provider is the text before the first slash. The model id is the rest and may contain further slashes. Both parts must be non-empty, and the seat holds no whitespace."""
    value = clean(seat)
    provider, _, model = value.partition("/")
    if not (provider and model) or any(character.isspace() for character in value):
        raise BrigadeError("a model is provider/model")
    return value


def latest_models(restaurant):
    """The latest model log row per seat. The latest row for a seat wins."""
    found = {}
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "model":
            found[row["id"]] = row
    return found


def skip_lines(restaurant):
    """One line per seat whose latest model row is skipped, sorted by seat."""
    return [f"skipped {seat} ({row['note']}); run model clear {shlex.quote(seat)} after a smoke test passes"
            for seat, row in sorted(latest_models(restaurant).items()) if row["state"] == "skipped"]


def record_model_skip(restaurant, seat, cause):
    """Record one provider/model seat as skipped. The same cause writes no row while the seat stays skipped with it."""
    name = require_seat(seat)
    latest = latest_models(restaurant).get(name)
    if latest is not None and latest["state"] == "skipped" and latest["note"] == cause:
        return f"{name}: already skipped"
    restaurant.log("model", name, "skipped", cause)
    return f"{name}: recorded as skipped"


def record_model_clear(restaurant, seat):
    """Record a passing smoke test for one seat and clear its skip."""
    name = require_seat(seat)
    latest = latest_models(restaurant).get(name)
    if latest is None:
        raise BrigadeError(f"{name} is not skipped")
    if latest["state"] == "cleared":
        return f"{name}: already cleared"
    restaurant.log("model", name, "cleared", "smoke test passed")
    return f"{name}: no longer skipped"


def record_worker_end(restaurant, ident, error):
    """Record that this attempt's worker ended or failed and wrote no report.

    The log row is the only dish write, so the dish keeps its lease, its branch,
    and its thread cell.
    """
    reason = clean(error)
    if not reason:
        raise BrigadeError("--worker-ended needs a reason")
    _, dish = restaurant.find("dishes.tsv", ident)
    if dish["state"] != "in-progress":
        raise BrigadeError(f"{ident} is {dish['state']}; --worker-ended records an in-progress dish")
    if not dish["thread"]:
        raise BrigadeError(f"{ident} has no worker thread recorded")
    if dish.get("reported") == "yes":
        raise BrigadeError(f"{ident} has a report-back on this attempt")
    if report_is_current(restaurant, dish):
        raise BrigadeError(f"{ident} has a report file for this attempt")
    attempt = str(attempt_count(restaurant, dish))
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "ended" and row["id"] == ident and row["state"] == attempt:
            if row["note"] == reason:
                return f"{ident}: worker end already recorded"
            raise BrigadeError(f"{ident} already records a different worker end for this attempt")
    restaurant.log("ended", ident, attempt, reason)
    return f"{ident}: worker ended with no report; the recorded reason is {reason}"


def report_is_current(restaurant, dish):
    """True when reports/<id>.md exists and its mtime is at least the last attempt start."""
    path = restaurant.dir / "reports" / f"{dish['id']}.md"
    if not path.exists():
        return False
    written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    start = attempt_starts(restaurant, dish)[-1]
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return written >= start
