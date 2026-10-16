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

def submit(store, holder, branch, sha, lease, reviewer, title="", body="", owner=None):
    contract, repo = store.contract, store.repo
    lease_number = seam.lease_id(lease)
    sha = seam.git("rev-parse", "--verify", f"{sha}^{{commit}}", cwd=repo).stdout.strip()
    if contract["mode"] != "local":
        seam.git("fetch", contract["remote"], contract["trunk"], cwd=repo)
    base = seam.git("merge-base", seam.trunk_ref(contract), sha, cwd=repo).stdout.strip()
    if seam.git("rev-list", "--merges", f"{base}..{sha}", cwd=repo).stdout.strip():
        raise seam.LandError(f"{branch} has merge commits; make its history linear on {contract['trunk']} first")
    changed = [seam.canonical(f, "changed") for f in seam.changed_paths(repo, base, sha)]
    if not changed:
        raise seam.LandError(f"{sha[:12]} changes nothing relative to {contract['trunk']}")
    files = [f for f in changed if not seam.is_generated(f)]
    if not files:
        raise seam.LandError(f"{sha[:12]} changes only generated paths {', '.join(changed)}. Change sources and run python3 scripts/build.py")
    print_ = seam.fingerprint(repo, base, sha)
    seam.git("update-ref", seam.pin_ref(sha), sha, cwd=repo)
    with store.tx() as db:
        seam.check_owner(db, holder, owner)
        existing = db.execute("SELECT id, state FROM entry WHERE sha = ? AND holder = ?", (sha, holder)).fetchone()
        if existing and existing["state"] != "bounced":
            return f"{seam.entry_label(existing['id'])} already {existing['state']}"
        row = db.execute("SELECT * FROM lease WHERE id = ?", (lease_number,)).fetchone()
        if not row or row["holder"] != holder or row["state"] != "active" or row["expires"] <= seam.stamp():
            raise seam.LandError(f"L{lease_number} is not an active lease held by {holder}; claim one before submitting")
        outside = [f for f in files if not seam.covered(f, row["paths"].split("\n"))]
        if outside:
            raise seam.LandError(f"changes paths outside L{lease_number}: {', '.join(outside)}; widen the lease or split the change")
        if existing:
            db.execute("DELETE FROM entry WHERE id = ?", (existing["id"],))
        cursor = db.execute("INSERT INTO entry (at, holder, branch, sha, base, fingerprint, lease, reviewer, state, title, body) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                            (seam.stamp(), holder, branch, sha, base, print_, lease_number, reviewer, title, body))
        db.execute("UPDATE lease SET state = 'submitted' WHERE id = ?", (lease_number,))
        store.log(db, "entry", cursor.lastrowid, "queued", f"{holder}: {branch}")
    return seam.entry_label(cursor.lastrowid)


def cherry_pick_stopped_for_conflict(result):
    """True when this cherry-pick stopped on a conflict.

    rerere.autoupdate can stage a resolution that matches HEAD. The index is then
    clean and the exit status is nonzero, the same as a commit that became empty.
    A conflict prints CONFLICT or "could not apply". An empty commit does not.
    """
    detail = result.stderr + result.stdout
    return "CONFLICT" in detail or "could not apply" in detail


def _tree_hashes(root):
    found = {}
    for path in Path(root).rglob("*"):
        if ".git" in path.parts:
            continue
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        found[path.relative_to(root).as_posix()] = hashlib.sha256(data).hexdigest()
    return found


def _tail(output):
    lines = output.strip().splitlines()[-3:]
    return " / ".join(lines) if lines else "no output"


class Integration:
    """A detached worktree only the queue uses, restored to a clean base before every attempt."""

    def __init__(self, store):
        self.store = store
        self.path = store.dir / "integration"
        if not (self.path / ".git").exists():
            seam.git("worktree", "add", "--detach", str(self.path), seam.trunk_ref(store.contract), cwd=store.repo)

    def git(self, *args, check=True):
        return seam.git(*args, cwd=self.path, check=check)

    def reset(self, commit):
        for operation in ("cherry-pick", "rebase", "merge"):
            self.git(operation, "--abort", check=False)
        self.git("checkout", "--force", "--detach", commit)
        self.git("reset", "--hard", commit)
        self.git("clean", "-ffdx")

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def _unmerged_paths(self):
        out = self.git("diff", "--name-only", "--diff-filter=U", check=False).stdout
        names = [line for line in out.splitlines() if line]
        if names:
            return names
        out = self.git("ls-files", "-u", check=False).stdout
        names = []
        for line in out.splitlines():
            if not line:
                continue
            if "\t" in line:
                names.append(line.split("\t", 1)[-1])
            else:
                names.append(line.split()[-1])
        return names

    def _generated_names(self, commit):
        result = self.git("ls-tree", "-r", "--name-only", commit, "--", *seam.GENERATED_PATHS, check=False)
        if result.returncode != 0:
            return None
        return set(line for line in result.stdout.splitlines() if line)

    def _resolve_generated(self, before, identity, result):
        did = False
        while result.returncode != 0 and cherry_pick_stopped_for_conflict(result):
            unmerged = self._unmerged_paths()
            if not unmerged or any(not seam.is_generated(path) for path in unmerged):
                return result, did
            picked = self.git("rev-parse", "--verify", "CHERRY_PICK_HEAD", check=False)
            if picked.returncode != 0:
                return result, did
            head_names = self._generated_names(picked.stdout.strip())
            base_names = self._generated_names(before)
            if head_names is None or base_names is None or head_names != base_names:
                return result, did
            checkout = self.git("checkout", "--ours", "--", *unmerged, check=False)
            if checkout.returncode != 0:
                return result, did
            staged = self.git("add", "--", *unmerged, check=False)
            if staged.returncode != 0:
                return result, did
            result = self.git(*identity, "-c", "rerere.enabled=false", "cherry-pick", "--continue", check=False)
            did = True
        return result, did

    def _dirty_tracked(self):
        names = []
        for args in (("diff", "--no-renames", "--name-only"), ("diff", "--cached", "--no-renames", "--name-only")):
            out = self.git(*args, check=False).stdout
            names.extend(line for line in out.splitlines() if line)
        return sorted(set(names))

    def _rebuild(self, before):
        for path in seam.GENERATED_PATHS:
            exists = self.git("rev-parse", "--verify", "--quiet", f"{before}:{path}", check=False)
            if exists.returncode == 0:
                done = self.git("checkout", before, "--", path, check=False)
            else:
                done = self.git("rm", "-r", "--ignore-unmatch", "--", path, check=False)
            if done.returncode != 0:
                self.git("reset", "--hard", before)
                return f"rebuild failed. `git checkout` for {path} exited {done.returncode}"
        contract = self.store.contract
        timeout = contract.get("timeout") or 1800
        command = "python3 scripts/build.py"
        try:
            slot = seam.slot
        except AttributeError:
            slot = None
        holder = slot("landing", repo=self.path, command=command) if slot else contextlib.nullcontext()
        with holder:
            first = seam.supervised(command, self.path, timeout)
            if first[0] is None or first[0] != 0:
                reason = "timed out" if first[0] is None else f"exited {first[0]}"
                self.git("reset", "--hard", before)
                return f"rebuild failed. `{command}` {reason}. {_tail(first[1])}"
            dirty = self._dirty_tracked()
            sources = sorted(path for path in dirty if not seam.is_generated(path))
            if sources:
                self.git("reset", "--hard", before)
                return f"build changed sources {', '.join(sources)}. Rebuild must only change generated paths"
            snapshot = _tree_hashes(self.path)
            second = seam.supervised(command, self.path, timeout)
            if second[0] is None or second[0] != 0:
                reason = "timed out" if second[0] is None else f"exited {second[0]}"
                self.git("reset", "--hard", before)
                return f"rebuild failed. `{command}` {reason}. {_tail(second[1])}"
            if _tree_hashes(self.path) != snapshot:
                self.git("reset", "--hard", before)
                return "build did not reproduce file bytes"
        added = self.git("add", "--all", "--", *seam.GENERATED_PATHS, check=False)
        if added.returncode != 0:
            self.git("reset", "--hard", before)
            return f"rebuild failed. `git add` exited {added.returncode}. {_tail(added.stderr + added.stdout)}"
        if self.git("diff", "--cached", "--quiet", check=False).returncode != 0:
            amend = self.git(*seam.committer(self.path), "commit", "--amend", "--no-edit", check=False)
            if amend.returncode != 0:
                self.git("reset", "--hard", before)
                return f"rebuild failed. `git commit --amend` exited {amend.returncode}. {_tail(amend.stderr + amend.stdout)}"
        return None

    def apply(self, entry):
        """Replay the pinned commits onto HEAD. Returns None, or why the entry cannot land."""
        before = self.head()
        spec = f"{entry['base']}..{seam.pin_ref(entry['sha'])}"
        identity = seam.committer(self.path)
        result = self.git(*identity, "-c", "rerere.enabled=false", "cherry-pick", spec, check=False)
        result, resolved = self._resolve_generated(before, identity, result)
        # git 2.43 has no cherry-pick --empty=drop. Git added it in 2.45. Drop a commit that
        # became empty, and stop on a commit that started empty. That matches --empty=drop.
        if result.returncode != 0:
            counted = self.git("rev-list", "--count", spec, check=False)
            remaining = int(counted.stdout.strip() or "0") if counted.returncode == 0 else 0
            while result.returncode != 0 and remaining > 0 and not cherry_pick_stopped_for_conflict(result):
                picked = self.git("rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD", check=False)
                if picked.returncode != 0:
                    break
                sha = picked.stdout.strip()
                parent_tree = self.git("rev-parse", "-q", "--verify", f"{sha}^^{{tree}}", check=False)
                own_tree = self.git("rev-parse", "-q", "--verify", f"{sha}^{{tree}}", check=False)
                started_empty = parent_tree.returncode == 0 and parent_tree.stdout.strip() == own_tree.stdout.strip()
                dirty = (self.git("diff", "--quiet", check=False).returncode != 0
                         or self.git("diff", "--cached", "--quiet", check=False).returncode != 0)
                unmerged = bool(self.git("ls-files", "-u", check=False).stdout.strip())
                if started_empty or dirty or unmerged:
                    break
                result = self.git(*identity, "-c", "rerere.enabled=false", "cherry-pick", "--skip", check=False)
                remaining -= 1
        if result.returncode != 0:
            self.git("cherry-pick", "--abort", check=False)
            self.git("reset", "--hard", before)
            if cherry_pick_stopped_for_conflict(result):
                return "conflict with trunk"
            detail = (result.stderr + result.stdout).strip()
            raise seam.Infrastructure("cherry-pick failed: " + (detail.splitlines() or ["no output"])[-1])
        changed = [seam.canonical(path, "changed") for path in seam.changed_paths(self.path, before, self.head())]
        sources = [path for path in changed if not seam.is_generated(path)]
        generated = [path for path in changed if seam.is_generated(path)]
        if not sources:
            self.git("reset", "--hard", before)
            return "already-in-trunk"
        if generated or resolved:
            failed = self._rebuild(before)
            if failed:
                return failed
        if seam.fingerprint(self.path, before, self.head()) != entry["fingerprint"]:
            self.git("reset", "--hard", before)
            return "the rebased change differs from the reviewed one; review it again"
        reason = seam.foreign_author(self.store, entry)
        if reason:
            self.git("reset", "--hard", before)
        return reason

    def check(self):
        """Run setup and checks on the candidate. Returns None, or the failure. Raises when the run left the tree dirty."""
        contract = self.store.contract
        expected = self.head()
        commands = ([contract["setup"]] if contract.get("setup") else []) + contract["checks"]
        with seam.slot("landing", repo=self.path, command=" ".join(commands)):
            for command in commands:
                code, output = seam.supervised(command, self.path, contract.get("timeout") or 1800)
                if code != 0:
                    tail = " / ".join(output.strip().splitlines()[-3:])
                    reason = "timed out" if code is None else f"exited {code}"
                    return f"`{command}` {reason}: {tail}"
        if self.head() != expected or self.git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            raise seam.LandError("a check changed tracked files in the integration worktree; fix the check, then run land again")
        return None


def remote_trunk(store):
    contract = store.contract
    line = seam.git("ls-remote", contract["remote"], f"refs/heads/{contract['trunk']}", cwd=store.repo).stdout.split()
    return line[0] if line else ""


def publish(store, base, candidate):
    """Move trunk from exactly base to candidate. Returns None, or why not."""
    contract, repo = store.contract, store.repo
    if seam.git("merge-base", "--is-ancestor", base, candidate, cwd=repo, check=False).returncode != 0:
        raise seam.LandError(f"candidate {candidate[:12]} does not descend from {base[:12]}")
    if contract["mode"] == "local":
        result = seam.git("update-ref", seam.trunk_ref(contract), candidate, base, cwd=repo, check=False)
        return None if result.returncode == 0 else "trunk moved during landing"
    result = seam.git_push(f"--force-with-lease=refs/heads/{contract['trunk']}:{base}", contract["remote"],
                      f"{candidate}:refs/heads/{contract['trunk']}", cwd=repo, check=False)
    if result.returncode != 0:
        if remote_trunk(store) != base:
            return "trunk moved during landing"
        raise seam.Infrastructure("push rejected: " + seam.git_reason(result.stderr))
    seam.git("fetch", contract["remote"], contract["trunk"], cwd=repo)
    return None


def pause(store, reason):
    with store.tx() as db:
        if store.contract.get("paused") != reason:
            mark_paused(store, db, reason)


def mark_paused(store, db, reason):
    db.execute("INSERT OR REPLACE INTO contract VALUES ('paused', ?)", (json.dumps(reason),))
    store.log(db, "queue", 0, "paused", reason)


def forget_absent(db, ident):
    """Drop this entry's drain marker. A settled entry must not stay in the map."""
    row = db.execute("SELECT value FROM contract WHERE key = 'absentDrain'").fetchone()
    if not row:
        return
    raw = json.loads(row["value"])
    if str(ident) not in raw:
        return
    del raw[str(ident)]
    db.execute("INSERT OR REPLACE INTO contract VALUES ('absentDrain', ?)", (json.dumps(raw),))


def settle_landed(store, db, ident, landed, note=""):
    forget_absent(db, ident)
    lease = db.execute("SELECT lease FROM entry WHERE id = ?", (ident,)).fetchone()["lease"]
    store.set_entry(db, ident, "landed", landed=landed, note=note)
    seam.release_lease(store, db, lease, f"{seam.entry_label(ident)} landed")


def settle_bounced(store, db, ident, reason):
    """A bounced entry gives its lease back to the holder for the fix, with a fresh expiry."""
    forget_absent(db, ident)
    lease = db.execute("SELECT lease FROM entry WHERE id = ?", (ident,)).fetchone()["lease"]
    store.set_entry(db, ident, "bounced", note=reason, candidate="")
    db.execute("UPDATE lease SET state = 'active', expires = ? WHERE id = ?", (seam.stamp(seam.now() + timedelta(hours=6)), lease))
    store.log(db, "lease", lease, "active", f"{seam.entry_label(ident)} bounced; the lease is back for the fix")


def reconcile(store):
    """Settle what a crashed run left behind, by asking git what actually happened."""
    contract, repo = store.contract, store.repo
    if contract["mode"] != "local":
        seam.git("fetch", contract["remote"], contract["trunk"], cwd=repo)
    for attempt in store.db.execute("SELECT * FROM attempt WHERE state = 'publishing'").fetchall():
        published = seam.git("merge-base", "--is-ancestor", attempt["candidate"], seam.trunk_ref(contract), cwd=repo, check=False).returncode == 0
        with store.tx() as db:
            for ident in json.loads(attempt["entries"]):
                if published:
                    settle_landed(store, db, ident, attempt["candidate"], "recovered after an interrupted run")
                else:
                    store.set_entry(db, ident, "queued", candidate="", note="requeued after an interrupted run")
            db.execute("UPDATE attempt SET state = ? WHERE id = ?", ("published" if published else "abandoned", attempt["id"]))
    with store.tx() as db:
        for entry in db.execute("SELECT id FROM entry WHERE state = 'landing'").fetchall():
            store.set_entry(db, entry["id"], "queued", candidate="", note="requeued after an interrupted run")


def rewound(store, base):
    """Pause when trunk no longer contains the last landed commit, or when git cannot tell. Returns True when paused."""
    tip = store.contract.get("tip") or ""
    if not tip:
        return False
    # git merge-base --is-ancestor exits 0 for an ancestor, 1 for a commit that is not one, and another code on an error.
    code = seam.git("merge-base", "--is-ancestor", tip, base, cwd=store.repo, check=False).returncode
    if code == 0:
        return False
    if code == 1:
        pause(store, f"trunk no longer contains the last landed commit {tip[:12]} (now {base[:12]}); "
                     "check what happened to trunk, then run land.py resume")
    else:
        pause(store, f"could not check that trunk contains the last landed commit {tip[:12]} "
                     f"(git merge-base --is-ancestor exited {code}); check that git can read that commit here, "
                     "then run land.py resume")
    return True


def attempt(store, integration, entries):
    """Rebase entries onto trunk, check once, publish. Returns (landed ids, bounced ids, requeued ids, base)."""
    contract = store.contract
    if contract["mode"] != "local":
        seam.git("fetch", contract["remote"], contract["trunk"], cwd=store.repo)
    base = seam.git("rev-parse", seam.trunk_ref(contract), cwd=store.repo).stdout.strip()
    if rewound(store, base):
        return [], [], [], base
    integration.reset(base)
    applied, bounced, landed = [], [], []
    for entry in entries:
        reason = integration.apply(entry)
        candidate = "" if reason else integration.head()
        with store.tx() as db:
            if reason == "already-in-trunk":
                settle_landed(store, db, entry["id"], base, "already in trunk")
                landed.append(entry["id"])
            elif reason:
                settle_bounced(store, db, entry["id"], reason)
                bounced.append(entry["id"])
            else:
                store.set_entry(db, entry["id"], "landing", candidate=candidate)
                applied.append(entry)
    if not applied:
        return landed, bounced, [], base
    failure = integration.check()
    if failure:
        with store.tx() as db:
            if len(applied) > 1:
                for entry in applied:
                    store.set_entry(db, entry["id"], "queued", candidate="", note="batch failed checks; landing one at a time")
                return landed, bounced, [entry["id"] for entry in applied], base
            settle_bounced(store, db, applied[0]["id"], f"checks failed: {failure}")
            return landed, bounced + [applied[0]["id"]], [], base
    candidate = integration.head()
    with store.tx() as db:
        cursor = db.execute("INSERT INTO attempt (at, base, candidate, entries, state) VALUES (?, ?, ?, ?, 'publishing')",
                            (seam.stamp(), base, candidate, json.dumps([entry["id"] for entry in applied])))
    problem = seam.publish(store, base, candidate)
    with store.tx() as db:
        db.execute("UPDATE attempt SET state = ? WHERE id = ?", ("abandoned" if problem else "published", cursor.lastrowid))
        for entry in applied:
            if problem:
                store.set_entry(db, entry["id"], "queued", candidate="", note=problem)
            else:
                settle_landed(store, db, entry["id"], candidate)
        if not problem:
            db.execute("INSERT OR REPLACE INTO contract VALUES ('tip', ?)", (json.dumps(candidate),))
    if problem:
        return landed, bounced, [entry["id"] for entry in applied], base
    return landed + [entry["id"] for entry in applied], bounced, [], candidate


def gh(*args, cwd):
    return subprocess.run([os.environ.get("LAND_GH", "gh"), *args], cwd=cwd, capture_output=True, text=True)


Outcome = namedtuple("Outcome", "landed bounced opened adopted rebuilt")


def build(store, integration, entry, onto, trunk, out):
    contract, ident = store.contract, entry["id"]
    integration.reset(onto)
    reason = integration.apply(entry)
    if not reason:
        reason = integration.check()
        reason = f"checks failed: {reason}" if reason else None
    if reason == "already-in-trunk" and not seam.same_tree(store.repo, onto, trunk):
        return onto
    if reason:
        with store.tx() as db:
            if reason == "already-in-trunk":
                settle_landed(store, db, ident, trunk, "already in trunk")
            else:
                settle_bounced(store, db, ident, reason)
        out.bounced.append(ident)
        return onto
    head = integration.head()
    push = seam.git_push("--force", contract["remote"], f"{head}:refs/heads/{human_branch(entry)}", cwd=store.repo, check=False)
    if push.returncode != 0:
        raise seam.Infrastructure("push failed: " + seam.git_reason(push.stderr))
    rebuilt = entry["state"] == "awaiting-merge"
    with store.tx() as db:
        forget_absent(db, ident)
        store.set_entry(db, ident, "awaiting-merge", candidate=head, onto=onto, pr=entry["pr"] if rebuilt else "", note="")
    if rebuilt:
        out.rebuilt.append(ident)
        return head
    seam.ensure_pr(store, {**dict(entry), "candidate": head})
    out.opened.append(ident)
    if contract["mode"] == "merge":
        seam.request_merge(store, ident, out)
    return onto if seam.entry_row(store, ident)["state"] == "bounced" else head


def build_line(store, integration, out):
    trunk = seam.fetch_trunk(store)
    line = seam.line_of(store, trunk)
    tip, todo = line.tip, line.stale + seam.unheld_queued(store)
    # Under merge method rebase GitHub replays every commit of a pull request, so no entry is built on another.
    if seam.merge_method(store) == "rebase":
        todo = todo[:1] if len(store.entries("awaiting-merge")) == len(line.stale) else []
    for entry in todo:
        tip = build(store, integration, entry, tip, trunk, out)
    return {entry["id"] for entry in todo}


def human_branch(entry):
    return f"landing/e{entry['id']}"


def publish_absent_branch(store, entry, branch):
    """Push the checked candidate when the entry's branch is not on the remote.

    An entry queued by an older queue stores a candidate whose branch was
    never pushed as landing/e<n>, so gh pr create refuses the name. The push
    sends the commit the queue already checked. A branch that is already on
    the remote is left where it is."""
    candidate = (entry["candidate"] or "").strip()
    if not candidate:
        return
    remote = store.contract["remote"]
    listed = seam.git("ls-remote", "--heads", remote, f"refs/heads/{branch}", cwd=store.repo, check=False)
    if listed.returncode != 0 or listed.stdout.strip():
        return
    push = seam.git_push("--force", remote, f"{candidate}:refs/heads/{branch}", cwd=store.repo, check=False)
    if push.returncode != 0:
        raise seam.Infrastructure("push failed: " + seam.git_reason(push.stderr))


def pr_adoptable_url(store, entry, branch):
    """URL of a pull request on this branch that belongs to this entry.

    An open pull request counts. A closed or merged one counts only when its
    head is this entry's stored candidate. Any other pull request on the name
    is a reused id. With no stored candidate, only an open pull request counts.
    """
    candidate = (entry["candidate"] or "").strip()
    if candidate:
        fields = "url,state,headRefOid"
        query = f'select(.state == "OPEN" or .headRefOid == "{candidate}") | .url'
    else:
        fields = "url,state"
        query = 'select(.state == "OPEN") | .url'
    found = gh("pr", "view", branch, "--json", fields, "-q", query, cwd=store.repo)
    if found.returncode != 0:
        return ""
    return found.stdout.strip()


def ensure_pr(store, entry):
    """Store this entry's PR. Open one on landing/e<n> when that name has none this entry can use.

    A pull request on landing/e<n> is adopted only by pr_adoptable_url. A
    reused id publishes landing/e<n> and opens the pull request there. A crash
    after gh pr create and before the URL is stored is safe to rerun. Returns
    "adopted" when an existing pull request was stored, and "created" when
    this run opened one."""
    contract = store.contract
    branch = human_branch(entry)
    url = pr_adoptable_url(store, entry, branch)
    adopted = bool(url)
    if not url:
        publish_absent_branch(store, entry, branch)
        title = entry["title"] or seam.git("log", "-1", "--format=%s", entry["sha"], cwd=store.repo).stdout.strip()
        created = gh("pr", "create", "--base", contract["trunk"], "--head", branch, "--title", title,
                     "--body", seam.pr_body(store, entry), cwd=store.repo)
        if created.returncode != 0:
            raise seam.Infrastructure("gh pr create failed: " + created.stderr.strip())
        url = created.stdout.strip().splitlines()[-1]
        adopted = False
    with store.tx() as db:
        store.set_entry(db, entry["id"], "awaiting-merge", pr=url, note="")
    return "adopted" if adopted else "created"
