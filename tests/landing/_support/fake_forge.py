import ast
import contextlib
import errno
import fcntl
import gc
import hashlib
import inspect
import io
import json
import os
import re
import shlex
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import land
from .shell import git_env, sh
from .inprocess import ProcessReason, _run, drive, real_popen, real_process
from . import paths
from .paths import ADMIN, REVIEWER, ROOT, SCRIPT, repository_state, stamp_in


class FakeForgeBase:

    def fake_gh(self):
        fake = self.base / "gh"
        fake.write_text(r"""#!PYTHON
import json, os, signal, subprocess, sys
from pathlib import Path
base = Path(BASE)
args = sys.argv[1:]
with open(base / "gh-calls", "a") as calls:
    print(" ".join(args), file=calls)


def git(*command, stdin=None, index=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "gh", "GIT_AUTHOR_EMAIL": "gh@gh",
           "GIT_COMMITTER_NAME": "gh", "GIT_COMMITTER_EMAIL": "gh@gh"}
    if index:
        env["GIT_INDEX_FILE"] = index
    return subprocess.run(["git", "--git-dir", str(base / "origin.git"), *command],
                          input=stdin, capture_output=True, text=True, env=env)


def prs():
    path = base / "prs.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save(table):
    (base / "prs.json").write_text(json.dumps(table))


def number_of(target):
    return target.rsplit("/", 1)[-1] if "/pull/" in target else ""


def head_of(pr):
    if pr["state"] == "MERGED":
        return pr["oid"]
    return git("rev-parse", "--verify", "--quiet", "refs/heads/" + pr["head"]).stdout.strip()


def refuse(text):
    print(text, file=sys.stderr)
    sys.exit(1)


def jq(query, doc):
    ran = subprocess.run(["jq", "-r", query], input=json.dumps(doc), capture_output=True, text=True)
    if ran.returncode != 0:
        refuse(ran.stderr)
    sys.stdout.write(ran.stdout if ran.stdout.endswith("\n") else ran.stdout + "\n")


def outside_commit():
    index = str(base / "gh-index")
    git("read-tree", "refs/heads/main", index=index)
    blob = git("hash-object", "-w", "--stdin", stdin="outside\n").stdout.strip()
    git("update-index", "--add", "--cacheinfo", "100644," + blob + ",outside.txt", index=index)
    tree = git("write-tree", index=index).stdout.strip()
    commit = git("commit-tree", tree, "-p", "refs/heads/main", "-m", "outside").stdout.strip()
    git("update-ref", "refs/heads/main", commit)


def replace_head(number, marker):
    path = base / ("pr-" + number + "-" + marker)
    pr = prs().get(number)
    if not path.exists() or pr is None:
        return
    replacement = path.read_text().strip()
    path.unlink()
    git("update-ref", "refs/heads/" + pr["head"], replacement)
    (base / ("pr-" + number + "-checks")).write_text("passed " + replacement)


def merge(number, pr, head):
    main = git("rev-parse", "refs/heads/main").stdout.strip()
    unmergeable = "GraphQL: Pull Request is not mergeable (mergePullRequest)"
    if "--rebase" in args:
        merged = main
        fork = git("merge-base", main, head).stdout.strip()
        for commit in git("rev-list", "--reverse", fork + ".." + head).stdout.split():
            tree = git("merge-tree", "--write-tree", "--merge-base", commit + "^", merged, commit)
            if tree.returncode != 0:
                refuse(unmergeable)
            message = git("log", "-1", "--format=%B", commit).stdout.strip()
            merged = git("commit-tree", tree.stdout.split()[0], "-p", merged, "-m", message).stdout.strip()
    else:
        tree = git("merge-tree", "--write-tree", main, head)
        if tree.returncode != 0:
            refuse(unmergeable)
        if "--squash" in args:
            parents = ["-p", main]
            message = pr.get("title") or "pull " + number
            if pr.get("body"):
                message += "\n\n" + pr["body"]
        else:
            parents = ["-p", main, "-p", head]
            message = "Merge pull request #" + number + " from " + pr["head"]
        merged = git("commit-tree", tree.stdout.split()[0], *parents, "-m", message).stdout.strip()
    git("update-ref", "refs/heads/main", merged, main)
    return merged


if args[:2] == ["repo", "view"]:
    if (base / "repo-view-fails").exists():
        print("repo view failed", file=sys.stderr)
        sys.exit(1)
    requested = ""
    if "--json" in args:
        requested = args[args.index("--json") + 1]
    if "mergeCommitAllowed" in requested.split(","):
        path = base / "repo-merge.json"
        if path.exists():
            print(path.read_text().strip())
        else:
            print(json.dumps({"mergeCommitAllowed": True, "squashMergeAllowed": True, "rebaseMergeAllowed": True}))
        sys.exit(0)
    print(json.dumps({"nameWithOwner": "o/r"}))
    sys.exit(0)
if args[:1] == ["api"] and any("required_status_checks" in arg for arg in args):
    required = base / "protection-required.json"
    if required.exists():
        text = required.read_text()
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        sys.exit(0)
    print("required checks unavailable", file=sys.stderr)
    sys.exit(1)
if args[:1] == ["api"] and any("/rules/branches/" in arg for arg in args):
    required = base / "rules-required.json"
    if required.exists():
        text = required.read_text()
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        sys.exit(0)
    print("rules unavailable", file=sys.stderr)
    sys.exit(1)
if args[:1] == ["api"] and any("/branches/" in arg for arg in args):
    endpoint = next(arg for arg in args if "/branches/" in arg)
    branch = endpoint.split("/branches/", 1)[1]
    if (base / "forge-says-missing").exists():
        print("branch-status 404", file=open(base / "gh-calls", "a"))
        print("HTTP/2.0 404 Not Found")
        print()
        print(json.dumps({"message": "Branch not found", "status": "404"}))
        sys.exit(1)
    if (base / "branch-query-fails").exists():
        print("branch-status 500", file=open(base / "gh-calls", "a"))
        print("HTTP/2.0 500 Internal Server Error")
        print()
        print(json.dumps({"message": "unavailable"}))
        sys.exit(1)
    exists = git("rev-parse", "--verify", "--quiet", "refs/heads/" + branch).returncode == 0
    status = "200" if exists else "404"
    print("branch-status " + status, file=open(base / "gh-calls", "a"))
    print("HTTP/2.0 " + ("200 OK" if exists else "404 Not Found"))
    print()
    if exists:
        print(json.dumps({"name": branch}))
        sys.exit(0)
    print(json.dumps({"message": "Branch not found", "status": "404"}))
    sys.exit(1)
if args[:2] == ["pr", "create"]:
    table = prs()
    head = args[args.index("--head") + 1] if "--head" in args else ""
    if head:
        (base / "pr-head").write_text(head)
    seq = base / "pr-seq"
    if seq.exists():
        number = seq.read_text().strip() or "9"
        seq.write_text(str(int(number) + 1) + "\n")
    else:
        number = str(max([8, *map(int, table)]) + 1)
    table[number] = {"head": head, "state": "OPEN",
                     "title": args[args.index("--title") + 1] if "--title" in args else "",
                     "body": args[args.index("--body") + 1] if "--body" in args else ""}
    save(table)
    if (base / "crash-on-create").exists():
        (base / "crash-on-create").unlink()
        os.kill(os.getppid(), signal.SIGKILL)
    print("https://github.com/o/r/pull/" + number)
elif args[:2] == ["pr", "merge"]:
    print(" ".join(args), file=open(base / "merge-calls", "a"))
    if "--disable-auto" in args:
        if (base / "disable-auto-fails").exists():
            print((base / "disable-auto-fails").read_text() or "API unavailable", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    if "--match-head-commit" in args:
        pinned = prs().get(number_of(args[2]))
        if pinned and pinned["state"] == "OPEN" and head_of(pinned) != args[args.index("--match-head-commit") + 1]:
            refuse("GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest)")
    if (base / "merge-refused").exists():
        refuse("GraphQL: At least 1 approving review is required by reviewers with write access.")
    if "--auto" in args and (base / "auto-merge-disabled").exists():
        refuse("GraphQL: Auto merge is not allowed for this repository (enablePullRequestAutoMerge)")
    if "--auto" not in args and (base / "plain-merge-fails").exists():
        refuse((base / "plain-merge-fails").read_text().strip() or "merge conflict")
    if "--auto" in args and not (base / "auto-merges-immediately").exists():
        if not (base / "required-checks").exists():
            refuse("GraphQL: Pull request is in clean status (enablePullRequestAutoMerge)")
        sys.exit(0)
    checks_now = (base / "checks").read_text().strip() if (base / "checks").exists() else ""
    if (base / "policy-until-passed").exists() and checks_now != "passed":
        refuse("X Pull request o/r#9 is not mergeable: the base branch policy prohibits the merge.")
    number = number_of(args[2])
    moved = base / ("pr-" + number + "-base-moved")
    if moved.exists() and int(moved.read_text()) > 0:
        moved.write_text(str(int(moved.read_text()) - 1))
        replace_head(number, "head-replaced-on-refusal")
        refuse("GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)")
    table = prs()
    pr = table.get(number)
    head = head_of(pr) if pr and pr["state"] == "OPEN" else ""
    if not head:
        refuse("GraphQL: Pull Request is not mergeable (mergePullRequest)")
    if (base / "outside-commit-before-merge").exists():
        (base / "outside-commit-before-merge").unlink()
        outside_commit()
    before = git("rev-parse", "refs/heads/main").stdout.strip()
    pr.update(state="MERGED", oid=head, merge=merge(number, pr, head),
              lag=1 if (base / "merge-state-lags").exists() else 0)
    save(table)
    if (base / "trunk-rewound-after-merge").exists():
        git("update-ref", "refs/heads/main", before)
    if (base / "crash-after-merge").exists():
        (base / "crash-after-merge").unlink()
        os.kill(os.getppid(), signal.SIGKILL)
elif args[:2] == ["pr", "view"] and any("statusCheckRollup" in arg for arg in args):
    if (base / "checks-query-fails").exists():
        refuse("API unavailable")
    number = number_of(args[2])
    pr = prs().get(number)
    own = base / ("pr-" + number + "-checks")
    if (base / "pr-checks.json").exists() and not own.exists():
        payload = json.loads((base / "pr-checks.json").read_text())
    else:
        kind = (base / "checks").read_text().strip() if (base / "checks").exists() else ""
        if own.exists():
            kind, _, posted_for = own.read_text().strip().partition(" ")
            if pr is None or posted_for != head_of(pr):
                kind = ""
        review = "REVIEW_REQUIRED" if (base / "merge-refused").exists() else "APPROVED"
        rollup = {
            "pending": [{"name": "test", "status": "IN_PROGRESS"}],
            "failed": [{"name": "test (3.12)", "status": "COMPLETED", "conclusion": "FAILURE"}],
            "passed": [
                {"name": "test (3.10)", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "test (3.12)", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        }.get(kind, [])
        payload = {"reviewDecision": review, "statusCheckRollup": rollup}
    if "autoMergeRequest" not in payload:
        marker = base / "auto-merge-request.json"
        payload["autoMergeRequest"] = json.loads(marker.read_text()) if marker.exists() else None
    if "headRefOid" not in payload:
        payload["headRefOid"] = head_of(pr) if pr else ""
    jq(args[args.index("-q") + 1], payload)
    replace_head(number, "head-replaced-after-check-read")
    flip = base / "checks-flip"
    if flip.exists():
        (base / "checks").write_text(flip.read_text())
        flip.unlink()
        saved = base / "pr-checks.json"
        if saved.exists():
            saved.unlink()
elif args[:2] == ["pr", "view"] and "--json" in args and args[args.index("--json") + 1] == "headRefName":
    pr = prs().get(number_of(args[2]))
    created = (base / "pr-head").read_text().strip() if (base / "pr-head").exists() else ""
    print(pr["head"] if pr else created or "landing/e1")
elif args[:2] == ["pr", "view"] and "--json" in args and "url" in args[args.index("--json") + 1].split(","):
    fields = args[args.index("--json") + 1].split(",")
    target = args[2] if len(args) > 2 and not args[2].startswith("-") else ""
    table = prs()
    on_branch = [number for number, pr in table.items() if pr["head"] == target]
    state = "OPEN"
    head_oid = ""
    if on_branch:
        number = max(on_branch, key=int)
        url = "https://github.com/o/r/pull/" + number
        state = table[number]["state"]
        head_oid = head_of(table[number])
    elif (base / "pr-url").exists():
        url = (base / "pr-url").read_text().strip()
    else:
        sys.exit(1)
    prior = None
    if target.startswith("landing/q"):
        prior = base / "legacy-pr-state"
    elif target.startswith("landing/e"):
        prior = base / "entry-pr-state"
    if prior is not None and prior.exists():
        parts = prior.read_text().strip().split()
        if parts:
            state = parts[0] or "OPEN"
        if len(parts) > 1:
            head_oid = parts[1]
    doc = {"url": url} if "url" in fields else {}
    if "state" in fields:
        doc["state"] = state
    if "headRefOid" in fields:
        doc["headRefOid"] = head_oid
    if "-q" not in args:
        print(url)
    else:
        jq(args[args.index("-q") + 1], doc)
elif args[:2] == ["pr", "view"]:
    if (base / "state-query-fails").exists():
        refuse("API unavailable")
    table = prs()
    pr = table.get(number_of(args[2]))
    if (base / "pr-state").exists():
        print((base / "pr-state").read_text())
    elif pr is None:
        print("OPEN")
    elif pr["state"] == "MERGED" and not pr["lag"]:
        print("MERGED " + pr["oid"] + " " + pr["merge"])
    else:
        print("OPEN " + head_of(pr) + " ")
        if pr["state"] == "MERGED":
            pr["lag"] -= 1
            save(table)
    nxt = base / "pr-state-after"
    if nxt.exists():
        (base / "pr-state").write_text(nxt.read_text())
        nxt.unlink()
""".replace("PYTHON", sys.executable, 1).replace("BASE", repr(str(self.base)), 1))
        fake.chmod(0o755)
        return mock.patch.dict(os.environ, {"LAND_GH": str(fake)})


    def gh(self, *args):
        result = subprocess.run([os.environ["LAND_GH"], *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


    def pause_repo_view(self):
        """Block gh repo view until repo-view-release appears. The waiting file is the barrier."""
        real = os.environ["LAND_GH"]
        wrapper = self.base / "gh-pause-view"
        wrapper.write_text(
            f"""#!{sys.executable}
import os, sys, time
from pathlib import Path
base = Path({str(self.base)!r})
real = {real!r}
args = sys.argv[1:]
if args[:2] == ["repo", "view"] and (base / "pause-repo-view").exists():
    (base / "repo-view-waiting").touch()
    end = time.monotonic() + 30
    while not (base / "repo-view-release").exists() and time.monotonic() < end:
        time.sleep(0.01)
os.execv(real, [real, *args])
"""
        )
        wrapper.chmod(0o755)
        os.environ["LAND_GH"] = str(wrapper)


    def allow_methods(self, merge=False, squash=False, rebase=False):
        (self.base / "repo-merge.json").write_text(json.dumps({
            "mergeCommitAllowed": merge,
            "squashMergeAllowed": squash,
            "rebaseMergeAllowed": rebase,
        }))


    def stored_merge_method(self):
        with land.Store.for_repo(self.work) as store:
            return store.contract["mergeMethod"]


    def arm_auto_merge(self):
        (self.base / "auto-merge-request.json").write_text(
            '{"authorEmail":null,"commitBody":null,"commitHeadline":null,'
            '"mergeMethod":"SQUASH","enabledAt":"2026-10-05T02:54:42Z",'
            '"enabledBy":{"login":"octocat","id":"U_1","name":"Octo Cat"}}'
        )
