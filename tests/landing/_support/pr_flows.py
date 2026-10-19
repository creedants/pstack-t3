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
from .paths import ADMIN, REVIEWER, ROOT, SCRIPT, repository_state, stamp_in


class PrFlowsBase:

    def queue_one(self, path="a.txt", text="agent\n", name="w1", holder="r/D1"):
        sha = self.worker(name, {path: text})
        lease = self.land("lease", "claim", "--holder", holder, "--paths", path)
        return self.land("submit", "--holder", holder, "--branch", name, "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)


    def queue_entries(self, count):
        edits = [("a.txt", "one\n"), ("b.txt", "two\n"), ("lib/x.py", "three\n")]
        for number, (path, text) in enumerate(edits[:count], 1):
            self.assertEqual(self.queue_one(path=path, text=text, name=f"w{number}", holder=f"r/D{number}"), f"E{number}")


    def post_check(self, number, kind):
        branch = json.loads((self.base / "prs.json").read_text())[str(number)]["head"]
        (self.base / f"pr-{number}-checks").write_text(f"{kind} {self.on_origin('rev-parse', branch)}")


    def merge_calls(self):
        path = self.base / "merge-calls"
        return path.read_text().splitlines() if path.exists() else []


    def pinned(self, number, *flags):
        """The merge request for PR <number> with these flags, pinned to the candidate its entry stores."""
        candidate = self.entry_fields(number - 8, "candidate")[0]
        return f"pr merge https://github.com/o/r/pull/{number} {' '.join(flags)} --match-head-commit {candidate}"


    def unreviewed_head(self):
        """A commit on top of landing/e1 that no entry submitted, present on origin under another branch."""
        rogue = self.worker("rogue", {"rogue.txt": "UNREVIEWED\n"}, base=self.on_origin("rev-parse", "landing/e1"))
        sh("git", "push", "-q", "origin", f"{rogue}:refs/heads/rogue", cwd=self.work)
        return rogue


    def head_changed_pause(self, rogue, candidate):
        return (f"GitHub refused to merge https://github.com/o/r/pull/9, and its head is now {rogue[:12]}, "
                f"not the checked candidate {candidate[:12]}. E1 was not merged. Close that PR to bounce E1, "
                f"or push {candidate[:12]} back to that PR's branch. Then run land.py resume and land.py land")


    def unread_commit_pause(self, commit):
        return ("The merge of E1 could not be checked. "
                f"Git could not read commit {commit} even after asking origin for it, "
                "so the tree of the merge commit was not compared with the tree of the candidate. "
                "E1 stays awaiting merge. Check that origin serves that commit, then run land.py resume and land.py land")


    def open_line(self, count):
        self.init(mode="merge", merge_method="squash")
        self.queue_entries(count)
        self.land("land")


    def require_pr_head_on_origin(self):
        """GitHub rejects pr create when the head branch is not on the remote."""
        real = os.environ["LAND_GH"]
        wrapper = self.base / "gh-head"
        wrapper.write_text(
            f"""#!{sys.executable}
import os, subprocess, sys
from pathlib import Path
base = Path({str(self.base)!r})
real = {real!r}
args = sys.argv[1:]
if args[:2] == ["pr", "create"]:
    if (base / "pr-create-fails").exists():
        print("gh: not authenticated", file=sys.stderr)
        sys.exit(1)
    head = args[args.index("--head") + 1]
    present = subprocess.run(
        ["git", "--git-dir", str(base / "origin.git"), "rev-parse", "--verify", "--quiet", "refs/heads/" + head],
        capture_output=True).returncode == 0
    if not present:
        print("pull request create failed: GraphQL: Head ref must be a branch (createPullRequest)", file=sys.stderr)
        sys.exit(1)
os.execv(real, [real, *args])
"""
        )
        wrapper.chmod(0o755)
        os.environ["LAND_GH"] = str(wrapper)


    def record_created_pr_branch(self):
        """A PR is visible to `gh pr view` only for the head `pr create` used."""
        real = os.environ["LAND_GH"]
        wrapper = self.base / "gh-branch"
        wrapper.write_text(
            f"""#!{sys.executable}
import os, sys
from pathlib import Path
base = Path({str(self.base)!r})
args = sys.argv[1:]
if args[:2] == ["pr", "create"] and "--head" in args:
    (base / "pr-head").write_text(args[args.index("--head") + 1])
def asks_url(argv):
    if "--json" not in argv:
        return False
    return "url" in argv[argv.index("--json") + 1].split(",")
if args[:2] == ["pr", "view"] and asks_url(args) and len(args) > 2 and args[2].startswith("landing/"):
    recorded = (base / "pr-head").read_text().strip() if (base / "pr-head").exists() else ""
    if recorded != args[2]:
        sys.exit(1)
os.execv({real!r}, [{real!r}, *args])
"""
        )
        wrapper.chmod(0o755)
        os.environ["LAND_GH"] = str(wrapper)


    def created_heads(self):
        calls = (self.base / "gh-calls").read_text().splitlines()
        return [line.split("--head ", 1)[1].split(" ", 1)[0]
                for line in calls if line.startswith("pr create ")]


    def crashed_landing_q_pr(self):
        """An older land.py opened the PR on landing/q<n> and died before storing the URL."""
        self.record_created_pr_branch()
        self.require_pr_head_on_origin()
        previous = self.use_land_script(self.base_land_script())
        try:
            self.init(mode="human")
            self.queue_one()
            (self.base / "crash-on-create").write_text("")
            crashed = real_process(
                ProcessReason.KILLED_CALLER, SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            self.assertEqual(crashed.returncode, -9, crashed.stdout + crashed.stderr)
            self.restore_land_script(previous)
            previous = None
        finally:
            if previous is not None:
                self.restore_land_script(previous)
        return sh("git", "rev-parse", "refs/heads/landing/q1", cwd=self.base / "origin.git")


    def pr_views_of_landing_q(self):
        calls = (self.base / "gh-calls").read_text().splitlines()
        return [line for line in calls if line.startswith("pr view landing/q")]


    def ignore_non_open_pr_on_landing_q(self, state):
        """The queue opens its own pull request whatever state the one on landing/q<n> is in."""
        with self.fake_gh():
            (self.base / "pr-seq").write_text("9\n")
            candidate = self.crashed_landing_q_pr()
            (self.base / "legacy-pr-state").write_text(f"{state} 0123456789abcdef0123456789abcdef01234567\n")
            reported = self.land("land")
        self.assertEqual(self.created_heads(), ["landing/q1", "landing/e1"], reported)
        self.assertEqual(self.pr_views_of_landing_q(), [])
        remote = self.base / "origin.git"
        self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote), candidate)
        self.assertNotIn("adopted", reported)
        status = self.land("status", "E1")
        self.assertIn("awaiting-merge", status)
        self.assertIn("https://github.com/o/r/pull/10", status)
        self.assertNotIn("https://github.com/o/r/pull/9", status)


    def open_pr_on_landing_q_with_the_url_stored(self, remote_is_github):
        """An older queue opened the PR on landing/q1 and stored its URL. landing/e1 was never created."""
        if not remote_is_github:
            sh("git", "remote", "set-url", "origin", str(self.base / "origin.git"), cwd=self.work)
        previous = self.use_land_script(self.base_land_script())
        try:
            self.init(mode="human")
            self.queue_one()
            opened = self.land("land")
        finally:
            self.restore_land_script(previous)
        remote = self.base / "origin.git"
        self.assertIn("https://github.com/o/r/pull/9", opened)
        self.assertEqual((self.base / "pr-head").read_text().strip(), "landing/q1")
        self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))
        return sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote)


    def hide_a_landing_e1_the_older_queue_never_made(self, candidate):
        remote = self.base / "origin.git"
        sh("git", "update-ref", "refs/heads/landing/e1", candidate, cwd=remote)
        sh("git", "config", "receive.hideRefs", "refs/heads/landing/e1", cwd=remote)
        (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
        return remote


    def leave_awaiting_merge_without_a_pr(self):
        """Push landing/e<n>, mark the entry awaiting merge, and die before gh pr create."""
        with land.Store.for_repo(self.work) as store:
            with mock.patch.object(land, "ensure_pr", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    land.land(store)
        status = self.land_in_another_process("status", "E1")
        self.assertIn("awaiting-merge", status)
        self.assertNotIn("pull/", status)
        self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))


    def absent_marker(self):
        with land.Store.for_repo(self.work) as store:
            return store.contract.get("absentDrain") or {}


    def pauses_on_a_merged_pr_whose_state_read_reports_the_merge_commit_as(self, reported):
        with self.fake_gh():
            self.open_line(1)
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "pr-state").write_text(f"MERGED {candidate} {reported}")
            self.assertEqual(
                self.land("land"),
                "queue paused: The merge of E1 could not be checked. "
                "The state read of https://github.com/o/r/pull/9 gave no full commit id for the merge commit, "
                "so the tree of the merge commit was not compared with the tree of the candidate. "
                "E1 stays awaiting merge. Check what gh pr view https://github.com/o/r/pull/9 --json mergeCommit prints, "
                "then run land.py resume and land.py land")
            self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)


    def off_trunk_pause(self, merged, trunk):
        return (f"https://github.com/o/r/pull/9 is recorded as merged at {merged[:12]}, and trunk at {trunk[:12]} "
                "does not hold that commit. E1 stays awaiting merge. Check what happened to trunk. "
                "Once trunk holds that commit again, run land.py resume and land.py land")


    def pauses_on_a_merge_of_the_first_of_two_entries_that_trunk_was_rewound_past(self, outside):
        """PR 9 merges outside the queue and trunk is put back to init before land runs.

        With <outside>, trunk gains another commit just before that merge, so the merge commit holds another tree
        than the candidate. Returns the candidate and the merge commit, with the merge commit back on trunk and
        the queue resumed."""
        self.open_line(2)
        self.post_check(9, "passed")
        self.post_check(10, "passed")
        initial, candidate = self.on_origin("rev-parse", "main"), self.on_origin("rev-parse", "landing/e1")
        if outside:
            (self.base / "outside-commit-before-merge").write_text("")
        self.gh("pr", "merge", "https://github.com/o/r/pull/9", "--squash", "--match-head-commit", candidate)
        merged = self.on_origin("rev-parse", "main")
        self.assertEqual(self.on_origin("rev-parse", f"{merged}^{{tree}}") != self.on_origin("rev-parse", f"{candidate}^{{tree}}"),
                         outside)
        self.on_origin("update-ref", "refs/heads/main", initial)
        self.assertFalse(self.ref_exists(f"{merged}^{{commit}}", self.work))
        pause = self.off_trunk_pause(merged, initial)
        for _run in range(2):
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            self.assertEqual(self.origin_log(), ["init"])
            self.assertTrue(self.ref_exists(f"{merged}^{{commit}}", self.work))
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                        "waiting for required checks before merging")
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt", "L2 submitted r/D2: b.txt"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)
            self.assertEqual(self.land("resume"), "queue resumed")
        self.on_origin("update-ref", "refs/heads/main", merged)
        return candidate, merged


    def commit_on_origin(self, message, path="merged.txt"):
        clone = self.base / "merger"
        if not clone.exists():
            sh("git", "clone", "-q", "origin.git", "merger", cwd=self.base)
        sh("git", "pull", "-q", "origin", "main", cwd=clone)
        (clone / path).write_text(message)
        sha = self.commit(message, cwd=clone)
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=clone)
        return sha


    def create_args_without_a_submitted_body(self, *message):
        """Land a worker commit with this message and no --body-file. Returns the gh pr create arguments."""
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.init(mode="human")
            self.worker("w1", {"a.txt": "agent\n"})
            sh("git", "commit", "-q", "--amend", *message, cwd=self.base / "w1")
            sha = sh("git", "rev-parse", "HEAD", cwd=self.base / "w1")
            self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
            self.land("land")
        return ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][0])


    def store_item(self, branch, kind, goal):
        store = self.base / "state/pstack-t3/brigade/proj/store"
        store.mkdir(parents=True, exist_ok=True)
        (store / "restaurant.json").write_text(json.dumps({"projectRoot": str(self.work)}))
        (store / "goals.tsv").write_text("id\tname\nG2\trules in the tool\n")
        (store / "dishes.tsv").write_text(
            "id\tat\tstate\tstation\ttickets\ttask\tthread\tbranch\tpr\tsha\tsummary\ttimebox\tlease\tpaths\t"
            f"reported\tkind\tgoal\nD255\t2026-10-14T13:29:20.318522+00:00\tin-progress\tbug-fix\tT606\t\t\t{branch}\t\t\t"
            f"batch\t90\tL293\t\t\t{kind}\t{goal}\n")
