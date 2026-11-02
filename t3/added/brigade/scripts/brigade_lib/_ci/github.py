import json
import subprocess
from datetime import datetime, timezone

from brigade_lib._ci.trigger import CiRun

TIMEOUT = 60


def _run_gh(args, root):
    """The parsed JSON gh prints for these args, run with the project root as cwd.

    Raises ValueError with one line on a gh failure, a timeout, or output that is not JSON.
    """
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
    """NaN, Infinity, and -Infinity are not JSON, so a document holding one is not read."""
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


def _api(root, endpoint):
    try:
        found = _run_gh(["api", endpoint], root)
    except ValueError as error:
        raise ValueError(str(error)) from None
    if not isinstance(found, dict):
        raise ValueError(f"gh api {endpoint} printed no JSON object")
    return found


def _workflow_runs(root, repo, head, pr):
    found = _api(root, f"repos/{repo}/actions/runs?head_sha={head}&per_page=5")
    runs = found.get("workflow_runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError(f"gh api actions/runs for PR {pr} named no workflow runs")
    return runs


def _jobs(root, repo, run_id, pr):
    found = _api(root, f"repos/{repo}/actions/runs/{run_id}/attempts/1/jobs")
    jobs = found.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError(f"gh api attempts/1/jobs for PR {pr} named no jobs")
    return jobs


def _annotations(root, repo, jobs, pr):
    """Every annotation message on a failed or cancelled job of this pull request.

    A job's check_run_url names its check run, whose annotations name a lost
    runner or an exceeded time limit. Run 41343828374 pins the lost form and
    run 41371125499 pins the time-limit form with its test step still running.
    """
    messages = []
    seen = set()
    for job in jobs:
        if job.get("conclusion") not in ("failure", "cancelled"):
            continue
        ident = str(job.get("check_run_url") or "").rstrip("/").rpartition("/")[2]
        if not ident.isdigit():
            raise ValueError(f"gh api attempts/1/jobs for PR {pr} named a job with no check run")
        if ident in seen:
            continue
        seen.add(ident)
        try:
            noted = _run_gh(["api", f"repos/{repo}/check-runs/{ident}/annotations"], root)
        except ValueError as error:
            raise ValueError(str(error)) from None
        if not isinstance(noted, list):
            raise ValueError(f"gh api annotations for PR {pr} printed no JSON list")
        for note in noted:
            if isinstance(note, dict) and isinstance(note.get("message"), str):
                messages.append(note["message"])
    return messages


def fetch_runs(root, known_prs, limit=10):
    """The fresh CiRun records for the last merged pull requests, oldest merged first.

    known_prs holds the PR numbers ci-times.tsv already records, so no gh call
    goes out for one. Returns (runs, None), or (None, reason) with one line on a
    gh failure, a timeout, or output that does not parse. Never raises.
    """
    known = set(known_prs)
    try:
        repo = _repo(root)
        listed = _merged_prs(root, limit)
        fresh = []
        for entry in listed:
            try:
                number = entry["number"]
                head = entry["headRefOid"]
                merged = _at(entry["mergedAt"], "mergedAt")
                created = _at(entry["createdAt"], "createdAt")
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"gh pr list named a pull request that does not parse: {error}") from None
            if not isinstance(number, int) or isinstance(number, bool) or not head:
                raise ValueError("gh pr list named a pull request that does not parse: a number and a head sha are required")
            if number in known:
                continue
            runs = _workflow_runs(root, repo, head, number)
            attempts = []
            for run in runs:
                attempt = run.get("run_attempt") if isinstance(run, dict) else None
                if isinstance(attempt, bool) or not isinstance(attempt, int):
                    raise ValueError(f"gh api actions/runs for PR {number} named no run_attempt integer")
                attempts.append(attempt)
            jobs = []
            for run in runs:
                jobs.extend(_jobs(root, repo, run.get("id"), number))
            starts, ends = [], []
            for job in jobs:
                if not isinstance(job, dict):
                    raise ValueError(f"gh api attempts/1/jobs for PR {number} named a job that is not an object")
                if job.get("started_at") and job.get("completed_at"):
                    starts.append(_at(job["started_at"], "started_at"))
                    ends.append(_at(job["completed_at"], "completed_at"))
            if not starts:
                raise ValueError(f"gh api attempts/1/jobs for PR {number} named no completed job")
            notes = _annotations(root, repo, jobs, number)
            lowered = [message.casefold() for message in notes]
            lost = sum(1 for message in lowered if "lost communication" in message)
            stops = 0
            if any("maximum execution time" in message for message in lowered):
                for job in jobs:
                    if job.get("conclusion") != "cancelled":
                        continue
                    steps = job.get("steps") or []
                    if any(isinstance(step, dict) and "test" in str(step.get("name") or "").casefold()
                           and step.get("status") != "completed" for step in steps):
                        stops += 1
            fresh.append(CiRun(number, merged, _seconds(min(starts), max(ends), "job time"),
                               _seconds(created, merged, "merge time"),
                               all(attempt == 1 for attempt in attempts), lost, stops))
    except ValueError as error:
        return None, f"ci-time: {error}"
    fresh.sort(key=lambda run: (run.merged_at, run.pr))
    return tuple(fresh), None
