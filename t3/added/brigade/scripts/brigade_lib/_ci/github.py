import json
import subprocess
from datetime import datetime, timezone
from typing import NamedTuple

from brigade_lib._ci.trigger import CiRun

TIMEOUT = 60
RUNS_PER_PAGE = 5
PAGE_SIZE = 100


class Batch(NamedTuple):
    runs: tuple
    notices: tuple


def _run_gh(args, root):
    try:
        done = subprocess.run(["gh", *args], cwd=str(root), capture_output=True, text=True, timeout=TIMEOUT)
    except OSError as error:
        raise ValueError(f"gh {' '.join(args)} did not start: {error.strerror or error}") from error
    except subprocess.TimeoutExpired:
        raise ValueError(f"gh {' '.join(args)} timed out after {TIMEOUT}s") from None
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        raise ValueError(f"gh {' '.join(args)} exited {done.returncode}: {detail[0] if detail else 'no output'}")
    try:
        return json.loads(done.stdout, parse_constant=_reject_constant)
    except ValueError:
        raise ValueError(f"gh {' '.join(args)} printed no JSON object") from None


def _reject_constant(name):
    raise ValueError(name)


def _at(text, what):
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError(f"{what} {text!r} is not a timestamp") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _seconds(start, end, what):
    return int((_at(end, what) - _at(start, what)).total_seconds())


def _repo(root):
    try:
        found = _run_gh(["repo", "view", "--json", "nameWithOwner"], root)
        name = found["nameWithOwner"]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"gh repo view gave no nameWithOwner: {error}") from None
    if not isinstance(name, str) or "/" not in name:
        raise ValueError(f"gh repo view gave no nameWithOwner: {name!r}")
    return name


def _merged_prs(root, limit):
    try:
        found = _run_gh(["pr", "list", "--state", "merged", "--limit", str(limit),
                         "--json", "number,mergedAt,createdAt,headRefOid"], root)
    except ValueError as error:
        raise ValueError(str(error)) from None
    if not isinstance(found, list):
        raise ValueError("gh pr list printed no JSON list")
    return found


def _object_page(root, endpoint, pr):
    try:
        found = _run_gh(["api", endpoint], root)
    except ValueError as error:
        raise ValueError(str(error)) from None
    if not isinstance(found, dict):
        raise ValueError(f"gh api {endpoint} printed no JSON object")
    return found


def _workflow_runs(root, repo, head, pr):
    runs = []
    page = 1
    while True:
        found = _object_page(root, f"repos/{repo}/actions/runs?head_sha={head}&per_page={RUNS_PER_PAGE}&page={page}", pr)
        batch = found.get("workflow_runs")
        if not isinstance(batch, list):
            raise ValueError(f"gh api actions/runs for PR {pr} named no workflow runs list")
        for run in batch:
            if not isinstance(run, dict):
                raise ValueError(f"gh api actions/runs for PR {pr} named a run that is not an object")
            attempt = run.get("run_attempt")
            ident = run.get("id")
            if isinstance(attempt, bool) or not isinstance(attempt, int):
                raise ValueError(f"gh api actions/runs for PR {pr} named no run_attempt integer")
            if isinstance(ident, bool) or not isinstance(ident, int):
                raise ValueError(f"gh api actions/runs for PR {pr} named no run id integer")
            runs.append(run)
        if len(batch) < RUNS_PER_PAGE:
            return runs
        page += 1


def _jobs(root, repo, run_id, pr):
    jobs = []
    page = 1
    while True:
        found = _object_page(root, f"repos/{repo}/actions/runs/{run_id}/attempts/1/jobs?per_page={PAGE_SIZE}&page={page}", pr)
        batch = found.get("jobs")
        if not isinstance(batch, list):
            raise ValueError(f"gh api attempts/1/jobs for PR {pr} named no jobs list")
        for job in batch:
            if not isinstance(job, dict):
                raise ValueError(f"gh api attempts/1/jobs for PR {pr} named a job that is not an object")
            jobs.append(job)
        if len(batch) < PAGE_SIZE:
            return jobs
        page += 1


def _check_runs(root, repo, head, pr):
    checks = []
    page = 1
    while True:
        found = _object_page(root, f"repos/{repo}/commits/{head}/check-runs?per_page={PAGE_SIZE}&page={page}", pr)
        batch = found.get("check_runs")
        if not isinstance(batch, list):
            raise ValueError(f"gh api check-runs for PR {pr} named no check runs list")
        for check in batch:
            if not isinstance(check, dict):
                raise ValueError(f"gh api check-runs for PR {pr} named a check that is not an object")
            checks.append(check)
        if len(batch) < PAGE_SIZE:
            return checks
        page += 1


def _annotations(root, repo, job, pr):
    url = job.get("check_run_url")
    if not isinstance(url, str) or not url:
        raise ValueError(f"gh api attempts/1/jobs for PR {pr} named a job with no check run")
    ident = url.rstrip("/").rpartition("/")[2]
    if not ident.isdigit():
        raise ValueError(f"gh api attempts/1/jobs for PR {pr} named a job with no check run")
    messages = []
    page = 1
    while True:
        try:
            noted = _run_gh(["api", f"repos/{repo}/check-runs/{ident}/annotations?per_page={PAGE_SIZE}&page={page}"], root)
        except ValueError as error:
            raise ValueError(str(error)) from None
        if not isinstance(noted, list):
            raise ValueError(f"gh api annotations for PR {pr} printed no JSON list")
        for note in noted:
            if not isinstance(note, dict):
                raise ValueError(f"gh api annotations for PR {pr} named an annotation that is not an object")
            if isinstance(note.get("message"), str):
                messages.append(note["message"])
        if len(noted) < PAGE_SIZE:
            return messages
        page += 1


def _label(check):
    name = check.get("name")
    if isinstance(name, str) and name:
        return name
    return str(check.get("id"))


def _span(checks, pr):
    for check in checks:
        status = check.get("status")
        if not isinstance(status, str):
            raise ValueError(f"gh api check-runs for PR {pr} named a check with no status")
        conclusion = check.get("conclusion")
        if conclusion is not None and not isinstance(conclusion, str):
            raise ValueError(f"gh api check-runs for PR {pr} named a check with no conclusion")
        for field in ("started_at", "completed_at"):
            raw = check.get(field)
            if raw is not None and raw != "" and not isinstance(raw, str):
                raise ValueError(f"gh api check-runs for PR {pr} named a check with no {field}")
    for check in checks:
        if check.get("status") != "completed":
            return None, f"PR {pr}: check {_label(check)} is still running; deferred to the next run"
        completed_raw = check.get("completed_at") or ""
        if completed_raw and _at(completed_raw, "completed_at").year == 1:
            return None, f"PR {pr}: check {_label(check)} is still running; deferred to the next run"
    earliest, latest = None, None
    for check in checks:
        started_raw, completed_raw = check.get("started_at"), check.get("completed_at")
        if not started_raw or not completed_raw:
            continue
        started, completed = _at(started_raw, "started_at"), _at(completed_raw, "completed_at")
        if started.year == 1:
            return None, f"PR {pr}: no usable check time; no row written"
        if completed < started:
            if check.get("conclusion") == "skipped":
                completed = started
            else:
                return None, f"PR {pr}: no usable check time; no row written"
        if earliest is None or started < earliest:
            earliest = started
        if latest is None or completed > latest:
            latest = completed
    if earliest is None:
        return None, f"PR {pr}: no usable check time; no row written"
    return int((latest - earliest).total_seconds()), None


def _signals(root, repo, runs, pr):
    lost, stops = 0, 0
    for run in runs:
        for job in _jobs(root, repo, run["id"], pr):
            conclusion = job.get("conclusion")
            if conclusion not in ("failure", "cancelled"):
                continue
            lowered = [message.casefold() for message in _annotations(root, repo, job, pr)]
            if conclusion == "failure":
                if any("lost communication" in message for message in lowered):
                    lost += 1
            elif any("maximum execution time" in message for message in lowered):
                steps = job.get("steps")
                if steps is None:
                    steps = []
                if not isinstance(steps, list):
                    raise ValueError(f"gh api attempts/1/jobs for PR {pr} named steps that are not a list")
                for step in steps:
                    if not isinstance(step, dict):
                        raise ValueError(f"gh api attempts/1/jobs for PR {pr} named a step that is not an object")
                if any(step.get("status") != "completed" for step in steps):
                    stops += 1
    return lost, stops


def fetch_runs(root, known_prs, limit=10):
    """The fresh CiRun records for the last merged pull requests, oldest merged first, plus one line per deferred or skipped PR.

    known_prs holds the PR numbers ci-times.tsv already records, so no gh call
    goes out for one. Returns (Batch, None), or (None, reason) with one line on a
    gh failure, a timeout, or output that does not parse. Never raises.
    """
    known = set(known_prs)
    try:
        repo = _repo(root)
        listed = _merged_prs(root, limit)
        runs, notices = [], []
        for entry in listed:
            if not isinstance(entry, dict):
                raise ValueError("gh pr list named a pull request that is not an object")
            try:
                number = entry["number"]
                head = entry["headRefOid"]
                merged = _at(entry["mergedAt"], "mergedAt")
                created = _at(entry["createdAt"], "createdAt")
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"gh pr list named a pull request that does not parse: {error}") from None
            if isinstance(number, bool) or not isinstance(number, int) or not isinstance(head, str) or not head:
                raise ValueError("gh pr list named a pull request that does not parse: a number and a head sha are required")
            if number in known:
                continue
            workflow = _workflow_runs(root, repo, head, number)
            seconds, notice = _span(_check_runs(root, repo, head, number), number)
            if notice is not None:
                notices.append(notice)
                continue
            lost, stops = _signals(root, repo, workflow, number)
            runs.append(CiRun(number, merged, seconds, _seconds(created, merged, "merge time"),
                              all(run["run_attempt"] == 1 for run in workflow), lost, stops))
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
        return None, f"ci-time: {error}"
    runs.sort(key=lambda run: (run.merged_at, run.pr))
    return Batch(tuple(runs), tuple(notices)), None
