#!/usr/bin/env python3
"""Land many agents' work on one repository through one queue.

Each repository (one git common directory) gets a contract, a lease table
that keeps writers off each other's paths, and a queue that is the only
writer to trunk. The queue is a lock, not a process: whoever runs `land`
while holding it drains the queue. State lives in SQLite so every change is
one transaction. Heavy commands take a slot from a machine-wide governor.
"""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import posixpath
import re
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODES = ("human", "merge", "push", "local")
LEGACY_MODES = {"auto": "push"}
REMOTE_MODES = ("human", "merge", "push")
MERGE_METHODS = ("merge", "squash", "rebase")
SCHEMA = """
CREATE TABLE IF NOT EXISTS contract (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lease (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, holder TEXT NOT NULL, paths TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'submitted', 'released')), expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entry (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, holder TEXT NOT NULL, branch TEXT NOT NULL,
  sha TEXT NOT NULL, base TEXT NOT NULL, fingerprint TEXT NOT NULL, lease INTEGER NOT NULL REFERENCES lease(id),
  reviewer TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued', 'landing', 'awaiting-merge', 'landed', 'bounced')),
  candidate TEXT NOT NULL DEFAULT '', landed TEXT NOT NULL DEFAULT '', pr TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '', UNIQUE (sha, holder));
CREATE TABLE IF NOT EXISTS attempt (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, base TEXT NOT NULL, candidate TEXT NOT NULL,
  entries TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('publishing', 'published', 'abandoned')));
CREATE TABLE IF NOT EXISTS log (at TEXT NOT NULL, kind TEXT NOT NULL, id INTEGER NOT NULL, state TEXT NOT NULL, note TEXT NOT NULL);
"""


class LandError(Exception):
    pass


def now():
    return datetime.now(timezone.utc)


def stamp(moment=None):
    return (moment or now()).isoformat(timespec="microseconds")


def state_home():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "pstack-t3"


def git(*args, cwd, check=True, stdin=None):
    result = subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True, text=True,
                            encoding="utf-8", errors="surrogateescape")
    if check and result.returncode != 0:
        raise LandError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result


def common_dir(path):
    """The git common directory identifies a repository across all of its worktrees."""
    top = git("rev-parse", "--show-toplevel", cwd=path).stdout.strip()
    return Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=top).stdout.strip()).resolve()


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


def fingerprint(repo, base, head):
    """Hash of the change itself: content, whitespace, modes, and binaries, without line numbers or blob IDs."""
    diff = git("diff", "--binary", "--full-index", "--no-renames", "--no-textconv", "--no-ext-diff", "-U0", base, head, cwd=repo).stdout
    lines = [line for line in diff.splitlines() if not line.startswith("index ")]
    lines = [re.sub(r"^@@ [^@]* @@", "@@", line) for line in lines]
    return hashlib.sha256("\n".join(lines).encode("utf-8", "surrogateescape")).hexdigest() if diff else ""


def changed_paths(repo, base, head):
    return [p for p in git("diff", "-z", "--name-only", "--no-renames", base, head, cwd=repo).stdout.split("\0") if p]


# Governor


def governor_dir():
    return state_home() / "governor"


def governor_slots():
    config = governor_dir() / "governor.json"
    if config.exists():
        return max(1, int(json.loads(config.read_text()).get("slots", 1)))
    return max(1, min(4, (os.cpu_count() or 4) // 4))


@contextlib.contextmanager
def exclusive_slot():
    """Hold every slot, landing included, so a benchmark runs on a quiet machine.
    Slots are taken in a fixed order with blocking locks, so two exclusive callers cannot deadlock."""
    if os.environ.get("LAND_SLOT"):
        raise LandError("an exclusive slot must be the outermost slot; this command already runs inside one")
    directory = governor_dir()
    directory.mkdir(parents=True, exist_ok=True)
    names = ["landing-0"] + [f"worker-{index}" for index in range(governor_slots())]
    handles = [open(directory / name, "a") for name in names]
    try:
        for handle in handles:
            fcntl.flock(handle, fcntl.LOCK_EX)
        os.environ["LAND_SLOT"] = "exclusive"
        yield
    finally:
        os.environ.pop("LAND_SLOT", None)
        for handle in handles:
            handle.close()


@contextlib.contextmanager
def slot(pool="worker"):
    """Hold one slot of a pool. Landing checks use their own one-slot pool so workers never starve the queue.
    A command already inside a slot (LAND_SLOT set) runs without taking another, so nesting cannot deadlock."""
    if os.environ.get("LAND_SLOT"):
        yield
        return
    directory = governor_dir()
    directory.mkdir(parents=True, exist_ok=True)
    count = 1 if pool == "landing" else governor_slots()
    handles = [open(directory / f"{pool}-{index}", "a") for index in range(count)]
    held = None
    try:
        while held is None:
            for handle in handles:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = handle
                    break
                except BlockingIOError:
                    continue
            if held is None:
                time.sleep(0.5)
        os.environ["LAND_SLOT"] = pool
        yield
    finally:
        os.environ.pop("LAND_SLOT", None)
        for handle in handles:
            handle.close()


def supervised(command, cwd, timeout):
    """Run a shell command in its own process group and kill the whole group on timeout."""
    process = subprocess.Popen(command, cwd=cwd, shell=True, start_new_session=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        output, _ = process.communicate(timeout=timeout)
        return process.returncode, output.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            # A descendant left the group with setsid and still holds the pipe. Stop waiting for it.
            process.stdout.close()
            process.wait()
            output = b""
        return None, output.decode("utf-8", "replace")


# Store


class Store:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.db = sqlite3.connect(self.dir / "land.db", timeout=60, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(entry)")}
        for column in ("title", "body"):
            if column not in columns:
                self.db.execute(f"ALTER TABLE entry ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    @classmethod
    def for_repo(cls, path):
        common = common_dir(path)
        directory = state_home() / "landing" / store_name(common)
        if not (directory / "land.db").exists():
            raise LandError(f"{common.parent} has no landing contract; run land.py init in it")
        store = cls(directory)
        if Path(store.contract["commonDir"]) != common:
            raise LandError(f"store {directory} belongs to {store.contract['commonDir']}, not {common}")
        return store

    @contextlib.contextmanager
    def tx(self):
        """One short write transaction. Never run git or checks inside it."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    @property
    def contract(self):
        contract = {row["key"]: json.loads(row["value"]) for row in self.db.execute("SELECT key, value FROM contract")}
        if "mode" in contract:
            contract["mode"] = LEGACY_MODES.get(contract["mode"], contract["mode"])
        return contract

    @property
    def repo(self):
        return Path(self.contract["repo"])

    def log(self, db, kind, ident, state, note=""):
        db.execute("INSERT INTO log VALUES (?, ?, ?, ?, ?)", (stamp(), kind, ident, state, note))

    def set_entry(self, db, ident, state, **fields):
        assignments = ", ".join(f"{key} = ?" for key in ["state", *fields])
        db.execute(f"UPDATE entry SET {assignments} WHERE id = ?", (state, *fields.values(), ident))
        self.log(db, "entry", ident, state, fields.get("note", ""))

    def entries(self, *states):
        marks = ",".join("?" * len(states))
        return list(self.db.execute(f"SELECT * FROM entry WHERE state IN ({marks}) ORDER BY id", states))


def store_name(common):
    name = common.parent.name if common.name == ".git" else common.name.removesuffix(".git")
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + "-" + hashlib.sha1(str(common).encode()).hexdigest()[:8]


def pin_ref(sha):
    return f"refs/landing/pins/{sha}"


def committer(repo):
    """Cherry-picks need a committer. Use the repository's identity, or a fixed one when it has none."""
    if git("config", "user.email", cwd=repo, check=False).stdout.strip():
        return []
    return ["-c", "user.name=pstack landing", "-c", "user.email=landing@localhost"]


def git_reason(stderr):
    """The useful line of a git failure: what the remote said, else the last line."""
    lines = [line.strip() for line in stderr.strip().splitlines() if line.strip()]
    remote = [line for line in lines if line.startswith("remote:") and line != "remote:"]
    return " ".join(remote) if remote else (lines[-1] if lines else "no output")


class Infrastructure(LandError):
    """A failure that is not the entry's fault. The queue pauses instead of bouncing."""


def trunk_ref(contract):
    if contract["mode"] == "local":
        return f"refs/landing/{contract['trunk']}"
    return f"refs/remotes/{contract['remote']}/{contract['trunk']}"


def init(path, trunk, mode, remote, base, checks, setup, batch, timeout, merge_method="merge"):
    mode = LEGACY_MODES.get(mode, mode)
    common = common_dir(path)
    repo = common.parent if common.name == ".git" else Path(git("rev-parse", "--show-toplevel", cwd=path).stdout.strip())
    directory = state_home() / "landing" / store_name(common)
    directory.mkdir(parents=True, exist_ok=True)
    store = Store(directory)
    wanted = {"commonDir": str(common), "repo": str(repo), "trunk": trunk, "mode": mode, "remote": remote,
              "checks": checks, "setup": setup, "batch": batch, "timeout": timeout, "mergeMethod": merge_method, "paused": ""}
    fixed = ("commonDir", "trunk", "mode", "remote")

    def refuse_clash(current):
        current = {**current, "mode": LEGACY_MODES.get(current.get("mode"), current.get("mode"))} if "mode" in current else current
        clash = [key for key in fixed if key in current and current[key] != wanted[key]]
        if clash:
            raise LandError(f"the contract already sets {', '.join(f'{k}={current[k]!r}' for k in clash)}; "
                            "change the mode with land.py mode; changing trunk or remote needs a new repository contract")

    refuse_clash(store.contract)
    if mode in REMOTE_MODES:
        if not git("remote", "get-url", remote, cwd=repo, check=False).stdout.strip():
            raise LandError(f"mode {mode} needs remote {remote!r}; use --mode local for a repository without one")
        git("fetch", remote, trunk, cwd=repo)
    with store.tx() as db:
        current = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key, value FROM contract")}
        refuse_clash(current)
        for key, value in wanted.items():
            if key != "paused" or key not in current:
                db.execute("INSERT OR REPLACE INTO contract VALUES (?, ?)", (key, json.dumps(value)))
    if mode == "local" and git("rev-parse", "--verify", "--quiet", trunk_ref(wanted), cwd=repo, check=False).returncode != 0:
        if not base:
            raise LandError(f"local mode lands on {trunk_ref(wanted)}; pass --base <commit> to create it")
        git("update-ref", trunk_ref(wanted), git("rev-parse", "--verify", f"{base}^{{commit}}", cwd=repo).stdout.strip(), "", cwd=repo)
    return f"{'updated' if current else 'created'} {directory}"


def change_mode(store, mode, merge_method):
    """Switch between the remote modes while nothing is in flight. Local mode lands on a different ref, so it needs its own contract."""
    mode = LEGACY_MODES.get(mode, mode)
    current = store.contract["mode"]
    if "local" in (mode, current) and mode != current:
        raise LandError("local mode lands on refs/landing/<trunk>, not the remote trunk; switching to or from it needs a new contract")
    with store.tx() as db:
        busy = db.execute("SELECT count(*) FROM entry WHERE state IN ('queued', 'landing', 'awaiting-merge')").fetchone()[0]
        if busy:
            raise LandError(f"{busy} entries are queued, landing, or awaiting merge; change the mode when the queue is empty")
        db.execute("INSERT OR REPLACE INTO contract VALUES ('mode', ?)", (json.dumps(mode),))
        if merge_method:
            db.execute("INSERT OR REPLACE INTO contract VALUES ('mergeMethod', ?)", (json.dumps(merge_method),))
        store.log(db, "queue", 0, f"mode {mode}", f"was {current}")
    return f"landing mode is now {mode}" + (f", merging with --{merge_method}" if merge_method else "")


def lease_claim(store, holder, paths, ttl_hours):
    wanted = sorted({canonical(p) for p in paths.split(",")})
    with store.tx() as db:
        rows = db.execute("SELECT * FROM lease WHERE state != 'released' AND holder != ? AND (state = 'submitted' OR expires > ?)",
                          (holder, stamp())).fetchall()
        clash = [row for row in rows if overlaps(wanted, row["paths"].split("\n"))]
        if clash:
            raise LandError("paths overlap " + "; ".join(f"L{row['id']} held by {row['holder']} on {row['paths'].replace(chr(10), ', ') or '(whole repo)'}" for row in clash))
        cursor = db.execute("INSERT INTO lease (at, holder, paths, state, expires) VALUES (?, ?, ?, 'active', ?)",
                            (stamp(), holder, "\n".join(wanted), stamp(now() + timedelta(hours=ttl_hours))))
        store.log(db, "lease", cursor.lastrowid, "active", holder)
    return f"L{cursor.lastrowid}"


def lease_id(text):
    if not re.fullmatch(r"L?\d+", text or ""):
        raise LandError(f"{text!r} is not a lease id such as L3")
    return int(text.lstrip("L"))


def submit(store, holder, branch, sha, lease, reviewer, title="", body=""):
    contract, repo = store.contract, store.repo
    lease_number = lease_id(lease)
    sha = git("rev-parse", "--verify", f"{sha}^{{commit}}", cwd=repo).stdout.strip()
    if contract["mode"] != "local":
        git("fetch", contract["remote"], contract["trunk"], cwd=repo)
    base = git("merge-base", trunk_ref(contract), sha, cwd=repo).stdout.strip()
    if git("rev-list", "--merges", f"{base}..{sha}", cwd=repo).stdout.strip():
        raise LandError(f"{branch} has merge commits; make its history linear on {contract['trunk']} first")
    files = [canonical(f, "changed") for f in changed_paths(repo, base, sha)]
    if not files:
        raise LandError(f"{sha[:12]} changes nothing relative to {contract['trunk']}")
    print_ = fingerprint(repo, base, sha)
    git("update-ref", pin_ref(sha), sha, cwd=repo)
    with store.tx() as db:
        existing = db.execute("SELECT id, state FROM entry WHERE sha = ? AND holder = ?", (sha, holder)).fetchone()
        if existing and existing["state"] != "bounced":
            return f"Q{existing['id']} already {existing['state']}"
        row = db.execute("SELECT * FROM lease WHERE id = ?", (lease_number,)).fetchone()
        if not row or row["holder"] != holder or row["state"] != "active" or row["expires"] <= stamp():
            raise LandError(f"L{lease_number} is not an active lease held by {holder}; claim one before submitting")
        outside = [f for f in files if not covered(f, row["paths"].split("\n"))]
        if outside:
            raise LandError(f"changes paths outside L{lease_number}: {', '.join(outside)}; widen the lease or split the change")
        if existing:
            db.execute("DELETE FROM entry WHERE id = ?", (existing["id"],))
        cursor = db.execute("INSERT INTO entry (at, holder, branch, sha, base, fingerprint, lease, reviewer, state, title, body) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                            (stamp(), holder, branch, sha, base, print_, lease_number, reviewer, title, body))
        db.execute("UPDATE lease SET state = 'submitted' WHERE id = ?", (lease_number,))
        store.log(db, "entry", cursor.lastrowid, "queued", f"{holder}: {branch}")
    return f"Q{cursor.lastrowid}"


class Integration:
    """A detached worktree only the queue uses, restored to a clean base before every attempt."""

    def __init__(self, store):
        self.store = store
        self.path = store.dir / "integration"
        if not (self.path / ".git").exists():
            git("worktree", "add", "--detach", str(self.path), trunk_ref(store.contract), cwd=store.repo)

    def git(self, *args, check=True):
        return git(*args, cwd=self.path, check=check)

    def reset(self, commit):
        for operation in ("cherry-pick", "rebase", "merge"):
            self.git(operation, "--abort", check=False)
        self.git("checkout", "--force", "--detach", commit)
        self.git("reset", "--hard", commit)
        self.git("clean", "-ffdx")

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def apply(self, entry):
        """Replay the pinned commits onto HEAD. Returns None, or why the entry cannot land."""
        before = self.head()
        result = self.git(*committer(self.path), "cherry-pick", "--empty=drop", f"{entry['base']}..{pin_ref(entry['sha'])}", check=False)
        if result.returncode != 0:
            self.git("cherry-pick", "--abort", check=False)
            self.git("reset", "--hard", before)
            detail = (result.stderr + result.stdout).strip()
            if "CONFLICT" in detail or "could not apply" in detail:
                return "conflict with trunk"
            raise Infrastructure("cherry-pick failed: " + (detail.splitlines() or ["no output"])[-1])
        if self.head() == before:
            return "already-in-trunk"
        if fingerprint(self.path, before, self.head()) != entry["fingerprint"]:
            self.git("reset", "--hard", before)
            return "the rebased change differs from the reviewed one; review it again"
        return None

    def check(self):
        """Run setup and checks on the candidate. Returns None, or the failure. Raises when the run left the tree dirty."""
        contract = self.store.contract
        expected = self.head()
        with slot("landing"):
            for command in ([contract["setup"]] if contract.get("setup") else []) + contract["checks"]:
                code, output = supervised(command, self.path, contract.get("timeout") or 1800)
                if code != 0:
                    tail = " / ".join(output.strip().splitlines()[-3:])
                    reason = "timed out" if code is None else f"exited {code}"
                    return f"`{command}` {reason}: {tail}"
        if self.head() != expected or self.git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            raise LandError("a check changed tracked files in the integration worktree; fix the check, then run land again")
        return None


def remote_trunk(store):
    contract = store.contract
    line = git("ls-remote", contract["remote"], f"refs/heads/{contract['trunk']}", cwd=store.repo).stdout.split()
    return line[0] if line else ""


def publish(store, base, candidate):
    """Move trunk from exactly base to candidate. Returns None, or why not."""
    contract, repo = store.contract, store.repo
    if git("merge-base", "--is-ancestor", base, candidate, cwd=repo, check=False).returncode != 0:
        raise LandError(f"candidate {candidate[:12]} does not descend from {base[:12]}")
    if contract["mode"] == "local":
        result = git("update-ref", trunk_ref(contract), candidate, base, cwd=repo, check=False)
        return None if result.returncode == 0 else "trunk moved during landing"
    result = git("push", f"--force-with-lease=refs/heads/{contract['trunk']}:{base}", contract["remote"],
                 f"{candidate}:refs/heads/{contract['trunk']}", cwd=repo, check=False)
    if result.returncode != 0:
        if remote_trunk(store) != base:
            return "trunk moved during landing"
        raise Infrastructure("push rejected: " + git_reason(result.stderr))
    git("fetch", contract["remote"], contract["trunk"], cwd=repo)
    return None


def pause(store, reason):
    with store.tx() as db:
        db.execute("INSERT OR REPLACE INTO contract VALUES ('paused', ?)", (json.dumps(reason),))
        store.log(db, "queue", 0, "paused", reason)


def settle_landed(store, db, ident, landed, note=""):
    lease = db.execute("SELECT lease FROM entry WHERE id = ?", (ident,)).fetchone()["lease"]
    store.set_entry(db, ident, "landed", landed=landed, note=note)
    db.execute("UPDATE lease SET state = 'released' WHERE id = ?", (lease,))
    store.log(db, "lease", lease, "released", f"Q{ident} landed")


def settle_bounced(store, db, ident, reason):
    """A bounced entry gives its lease back to the holder for the fix, with a fresh expiry."""
    lease = db.execute("SELECT lease FROM entry WHERE id = ?", (ident,)).fetchone()["lease"]
    store.set_entry(db, ident, "bounced", note=reason, candidate="")
    db.execute("UPDATE lease SET state = 'active', expires = ? WHERE id = ?", (stamp(now() + timedelta(hours=6)), lease))
    store.log(db, "lease", lease, "active", f"Q{ident} bounced; the lease is back for the fix")


def reconcile(store):
    """Settle what a crashed run left behind, by asking git what actually happened."""
    contract, repo = store.contract, store.repo
    if contract["mode"] != "local":
        git("fetch", contract["remote"], contract["trunk"], cwd=repo)
    for attempt in store.db.execute("SELECT * FROM attempt WHERE state = 'publishing'").fetchall():
        published = git("merge-base", "--is-ancestor", attempt["candidate"], trunk_ref(contract), cwd=repo, check=False).returncode == 0
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
    """Pause when trunk no longer contains the last landed commit. Returns True when paused."""
    tip = store.contract.get("tip") or ""
    if tip and git("merge-base", "--is-ancestor", tip, base, cwd=store.repo, check=False).returncode != 0:
        pause(store, f"trunk no longer contains the last landed commit {tip[:12]} (now {base[:12]}); "
                     "check what happened to trunk, then run land.py resume")
        return True
    return False


def attempt(store, integration, entries):
    """Rebase entries onto trunk, check once, publish. Returns (landed ids, bounced ids, requeued ids, base)."""
    contract = store.contract
    if contract["mode"] != "local":
        git("fetch", contract["remote"], contract["trunk"], cwd=store.repo)
    base = git("rev-parse", trunk_ref(contract), cwd=store.repo).stdout.strip()
    if rewound(store, base):
        return [], [], [], base
    integration.reset(base)
    applied, bounced, landed = [], [], []
    for entry in entries:
        reason = integration.apply(entry)
        with store.tx() as db:
            if reason == "already-in-trunk":
                settle_landed(store, db, entry["id"], base, "already in trunk")
                landed.append(entry["id"])
            elif reason:
                settle_bounced(store, db, entry["id"], reason)
                bounced.append(entry["id"])
            else:
                store.set_entry(db, entry["id"], "landing", candidate=integration.head())
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
                            (stamp(), base, candidate, json.dumps([entry["id"] for entry in applied])))
    problem = publish(store, base, candidate)
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


def land_human(store, integration, entry):
    """Human mode: rebase onto trunk, check, push to a queue-owned branch, open its PR. Returns True when opened."""
    contract = store.contract
    git("fetch", contract["remote"], contract["trunk"], cwd=store.repo)
    integration.reset(git("rev-parse", trunk_ref(contract), cwd=store.repo).stdout.strip())
    reason = integration.apply(entry)
    if not reason:
        reason = integration.check()
        reason = f"checks failed: {reason}" if reason else None
    if reason == "already-in-trunk":
        with store.tx() as db:
            settle_landed(store, db, entry["id"], integration.head(), "already in trunk")
        return False
    if reason:
        with store.tx() as db:
            settle_bounced(store, db, entry["id"], reason)
        return False
    head = integration.head()
    push = git("push", "--force", contract["remote"], f"{head}:refs/heads/{human_branch(entry)}", cwd=store.repo, check=False)
    if push.returncode != 0:
        raise Infrastructure("push failed: " + git_reason(push.stderr))
    with store.tx() as db:
        store.set_entry(db, entry["id"], "awaiting-merge", candidate=head, pr="", note="")
    return ensure_pr(store, {**dict(entry), "candidate": head})


def human_branch(entry):
    return f"landing/q{entry['id']}"


def ensure_pr(store, entry):
    """Adopt the PR for the entry's branch, or open it. A crash between push and PR creation is safe to rerun."""
    contract = store.contract
    view = gh("pr", "view", human_branch(entry), "--json", "url", "-q", ".url", cwd=store.repo)
    url = view.stdout.strip() if view.returncode == 0 else ""
    if not url:
        title = entry["title"] or git("log", "-1", "--format=%s", entry["sha"], cwd=store.repo).stdout.strip()
        receipt = f"Queued by {entry['holder']} from `{entry['branch']}`. Reviewed by {entry['reviewer']} at {entry['sha']}."
        body = f"{entry['body'].rstrip()}\n\n{receipt}" if entry["body"] else receipt
        created = gh("pr", "create", "--base", contract["trunk"], "--head", human_branch(entry), "--title", title, "--body", body, cwd=store.repo)
        if created.returncode != 0:
            raise Infrastructure("gh pr create failed: " + created.stderr.strip())
        url = created.stdout.strip().splitlines()[-1]
    with store.tx() as db:
        store.set_entry(db, entry["id"], "awaiting-merge", pr=url, note="")
    if contract["mode"] == "merge":
        request_merge(store, entry["id"], url)
    return True


MERGE_REQUESTED = "merge requested by the queue"


WAITING_FOR_CHECKS = "waiting for required checks before merging"
BLOCKER_QUERY = (
    '[.reviewDecision // "", '
    '([.statusCheckRollup[] | select((.status // "COMPLETED") != "COMPLETED" or (.state // "") == "PENDING")] | length), '
    '([.statusCheckRollup[] | select((.conclusion // "") as $c | (.state // "") as $s '
    '| ($c == "FAILURE" or $c == "CANCELLED" or $c == "TIMED_OUT" or $s == "FAILURE" or $s == "ERROR")) '
    '| (.name // .context)] | join(","))] | @tsv'
)


def merge_blocker(store, url):
    """Why this PR must not merge yet.

    Returns ('review', ''), ('pending', ''), ('failed', names), or ('', '') when the read
    succeeded and nothing posted is pending or failed. A failed command or an unparseable
    rollup raises Infrastructure, and the caller must not merge."""
    view = gh("pr", "view", url, "--json", "reviewDecision,statusCheckRollup", "-q", BLOCKER_QUERY, cwd=store.repo)
    if view.returncode != 0:
        raise Infrastructure(f"could not read checks for {url}: {view.stderr.strip() or 'gh pr view failed'}")
    fields = view.stdout.rstrip("\n").split("\t")
    if len(fields) != 3 or not fields[1].isdigit():
        raise Infrastructure(f"could not read checks for {url}: unparseable check rollup")
    review, pending, failed = fields
    if review == "REVIEW_REQUIRED" or review == "CHANGES_REQUESTED":
        return "review", ""
    if failed:
        return "failed", failed
    if int(pending) > 0:
        return "pending", ""
    return "", ""


def request_merge(store, ident, url):
    """Merge only after a successful check read shows nothing pending or failed.

    The read runs before any gh pr merge, including --auto. A pending posted check waits.
    A failed posted check bounces. With no posted check, try auto-merge and then merge now.
    A required review still reaches gh, which refuses the merge and pauses the queue."""
    blocker, detail = merge_blocker(store, url)
    if blocker == "pending":
        with store.tx() as db:
            store.set_entry(db, ident, "awaiting-merge", note=WAITING_FOR_CHECKS)
        return
    if blocker == "failed":
        with store.tx() as db:
            settle_bounced(store, db, ident, f"required checks failed on {url}: {detail}")
        return
    method = f"--{store.contract.get('mergeMethod') or 'merge'}"
    queued = gh("pr", "merge", url, "--auto", method, cwd=store.repo)
    if queued.returncode == 0:
        with store.tx() as db:
            store.set_entry(db, ident, "awaiting-merge", note=MERGE_REQUESTED)
        return
    now_ = gh("pr", "merge", url, method, cwd=store.repo)
    if now_.returncode != 0:
        raise Infrastructure(f"GitHub refused to merge {url}: {(now_.stderr or queued.stderr).strip()}")
    with store.tx() as db:
        store.set_entry(db, ident, "awaiting-merge", note=MERGE_REQUESTED)


def delete_queue_branch(store, entry):
    """Drop landing/q<n> after the PR has merged.

    Returns (deleted, warning). A missing remote ref counts as deleted. A real remote
    delete failure returns deleted False so the next land retries. A local branch that
    exists and cannot be deleted is named in warning. The entry still lands."""
    branch = human_branch(entry)
    remote = store.contract["remote"]
    pushed = git("push", remote, "--delete", branch, cwd=store.repo, check=False)
    if pushed.returncode != 0 and "does not exist" not in pushed.stderr:
        return False, ""
    git("update-ref", "-d", f"refs/remotes/{remote}/{branch}", cwd=store.repo, check=False)
    warning = ""
    if git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=store.repo, check=False).returncode == 0:
        local = git("branch", "-D", branch, cwd=store.repo, check=False)
        if local.returncode != 0:
            warning = f"left local {branch}: {git_reason(local.stderr)}"
    return True, warning


def poll_human(store):
    landed, bounced = [], []
    for entry in store.entries("awaiting-merge"):
        if not entry["pr"]:
            ensure_pr(store, entry)
            entry = store.db.execute("SELECT * FROM entry WHERE id = ?", (entry["id"],)).fetchone()
        elif store.contract["mode"] == "merge" and entry["note"] == MERGE_REQUESTED:
            blocker, detail = merge_blocker(store, entry["pr"])
            if blocker == "failed":
                with store.tx() as db:
                    settle_bounced(store, db, entry["id"], f"required checks failed on {entry['pr']}: {detail}")
                bounced.append(entry["id"])
                continue
        elif store.contract["mode"] == "merge":
            request_merge(store, entry["id"], entry["pr"])
            entry = store.db.execute("SELECT * FROM entry WHERE id = ?", (entry["id"],)).fetchone()
            if entry["state"] == "bounced":
                bounced.append(entry["id"])
                continue
        view = gh("pr", "view", entry["pr"], "--json", "state,headRefOid,mergeCommit",
                  "-q", '.state + " " + .headRefOid + " " + (.mergeCommit.oid // "")', cwd=store.repo)
        state, head, merged = (view.stdout.strip().split(" ") + ["", "", ""])[:3]
        warning = ""
        if state == "MERGED":
            deleted, warning = delete_queue_branch(store, entry)
            if not deleted:
                continue
        with store.tx() as db:
            if state == "MERGED" and head == entry["candidate"]:
                settle_landed(store, db, entry["id"], merged, warning)
                db.execute("INSERT OR REPLACE INTO contract VALUES ('tip', ?)", (json.dumps(merged),))
                landed.append(entry["id"])
            elif state == "MERGED":
                settle_bounced(store, db, entry["id"], f"merged at {merged[:12]} with head {head[:12]}, not the checked "
                                                       f"{entry['candidate'][:12]}; review what reached trunk")
                db.execute("INSERT OR REPLACE INTO contract VALUES ('tip', ?)", (json.dumps(merged),))
                bounced.append(entry["id"])
            elif state == "CLOSED":
                settle_bounced(store, db, entry["id"], "PR closed without merging")
                bounced.append(entry["id"])
            elif state == "OPEN" and head and head != entry["candidate"]:
                store.set_entry(db, entry["id"], "awaiting-merge", note=f"PR head changed to {head[:12]} outside the queue")
    return landed, bounced


def land(store):
    handle = open(store.dir / ".queue.lock", "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return "queue busy: another run is landing and will take queued entries. Run land again later if entries stay queued."
    try:
        if store.contract.get("paused"):
            return f"queue paused: {store.contract['paused']}"
        reconcile(store)
        integration = Integration(store)
        landed, bounced, opened = [], [], []
        if store.contract["mode"] in ("human", "merge"):
            git("fetch", store.contract["remote"], store.contract["trunk"], cwd=store.repo)
            if rewound(store, git("rev-parse", trunk_ref(store.contract), cwd=store.repo).stdout.strip()):
                return report(store, [], [], [])
            landed, bounced = poll_human(store)
            for entry in store.entries("queued"):
                (opened if land_human(store, integration, entry) else bounced).append(entry["id"])
            if store.contract["mode"] == "merge" and opened:
                more_landed, more_bounced = poll_human(store)
                landed, bounced = landed + more_landed, bounced + more_bounced
            states = {row["id"]: row["state"] for row in store.db.execute("SELECT id, state FROM entry")}
            bounced += [ident for ident in opened if states[ident] == "bounced" and ident not in bounced]
            opened = [ident for ident in opened if states[ident] == "awaiting-merge"]
            return report(store, landed, bounced, opened)
        single, rounds = False, 0
        while (queued := store.entries("queued")) and not store.contract.get("paused"):
            rounds += 1
            if rounds > 4 * len(queued) + 4:
                break
            size = 1 if single else max(1, int(store.contract.get("batch") or 1))
            done, out, requeued, _ = attempt(store, integration, queued[:size])
            landed += done
            bounced += out
            single = bool(requeued)
        return report(store, landed, bounced, opened)
    except Infrastructure as problem:
        with store.tx() as db:
            for entry in db.execute("SELECT id FROM entry WHERE state = 'landing'").fetchall():
                store.set_entry(db, entry["id"], "queued", candidate="", note=str(problem))
        pause(store, f"{problem}. Fix it, then run land.py resume")
        return report(store, [], [], [])
    finally:
        handle.close()


def report(store, landed, bounced, opened):
    rows = {row["id"]: row for row in store.db.execute("SELECT * FROM entry")}
    lines = []
    if landed:
        parts = []
        for i in landed:
            text = f"Q{i} ({rows[i]['holder']})"
            if rows[i]["note"].startswith("left local "):
                text += f": {rows[i]['note']}"
            parts.append(text)
        lines.append("landed " + ", ".join(parts))
    if opened:
        lead = "opened PRs that merge when their checks pass: " if store.contract["mode"] == "merge" else "opened PRs for "
        lines.append(lead + ", ".join(f"Q{i} ({rows[i]['holder']}) {rows[i]['pr']}" for i in opened))
    lines += [f"bounced Q{i} ({rows[i]['holder']}): {rows[i]['note']}" for i in bounced]
    waiting = [f"Q{row['id']}" for row in rows.values() if row["state"] in ("queued", "landing")]
    if waiting:
        lines.append("still queued: " + ", ".join(waiting))
    if store.contract.get("paused"):
        lines.append(f"queue paused: {store.contract['paused']}")
    return "\n".join(lines) or "nothing to land"


def status(store, ident=None):
    if ident:
        row = store.db.execute("SELECT * FROM entry WHERE id = ?", (int(ident.lstrip("Q")),)).fetchone()
        if not row:
            raise LandError(f"no {ident}")
        detail = [f"landed as {row['landed'][:12]}" if row["landed"] else "", row["pr"], row["note"]]
        return f"Q{row['id']} {row['state']} ({row['holder']}, {row['branch']})" + "".join(f". {part}" for part in detail if part)
    counts = dict(store.db.execute("SELECT state, count(*) FROM entry GROUP BY state").fetchall())
    leases = store.db.execute("SELECT count(*) FROM lease WHERE state = 'submitted' OR (state = 'active' AND expires > ?)", (stamp(),)).fetchone()[0]
    contract = store.contract
    parts = [f"{state}: {count}" for state, count in counts.items()] + ([f"leases held: {leases}"] if leases else [])
    paused = f" Paused: {contract['paused']}" if contract.get("paused") else ""
    return f"{contract['mode']} mode onto {trunk_ref(contract)}. " + (", ".join(parts) or "empty") + "." + paused


def parser():
    top = argparse.ArgumentParser(prog="land.py", description=__doc__.splitlines()[0])
    top.add_argument("--repo", default=".", help="any checkout of the repository (default: current directory)")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write the repository's landing contract")
    p.add_argument("--trunk", required=True, help="branch on the remote, or a lane name in local mode")
    p.add_argument("--mode", choices=MODES + tuple(LEGACY_MODES), required=True,
                   help="human: PRs you merge; merge: PRs the queue merges; push: no PRs, the queue pushes trunk; local: a lane ref")
    p.add_argument("--remote", default="origin")
    p.add_argument("--base", default="", help="local mode: the commit the lane starts from")
    p.add_argument("--check", action="append", default=[], help="command that must pass before landing; repeatable")
    p.add_argument("--setup", default="", help="command run before checks in the clean integration worktree, such as npm ci")
    p.add_argument("--batch", type=int, default=1, help="entries checked together; a failed batch lands one at a time")
    p.add_argument("--timeout", type=int, default=1800, help="seconds before a check is killed")
    p.add_argument("--merge-method", choices=MERGE_METHODS, default="merge", help="merge mode: how the queue merges its PRs")

    p = sub.add_parser("mode", help="switch between human, merge, and push while nothing is in flight")
    p.add_argument("mode", choices=MODES + tuple(LEGACY_MODES))
    p.add_argument("--merge-method", choices=MERGE_METHODS)

    p = sub.add_parser("lease", help="claim, renew, release, or list path leases")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("claim")
    a.add_argument("--holder", required=True)
    a.add_argument("--paths", required=True, help="comma-separated files or directories; . is the whole repository")
    a.add_argument("--ttl-hours", type=float, default=6)
    a = t.add_parser("renew")
    a.add_argument("id")
    a.add_argument("--ttl-hours", type=float, default=6)
    a = t.add_parser("release")
    a.add_argument("id")
    t.add_parser("list")

    p = sub.add_parser("submit", help="queue a reviewed commit for landing")
    p.add_argument("--holder", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--sha", required=True, help="the exact SHA the review passed")
    p.add_argument("--lease", required=True)
    p.add_argument("--reviewer", required=True, help="provider/model of the reviewer that passed this SHA")
    p.add_argument("--title", default="", help="human mode: the PR title (default: the last commit subject)")
    p.add_argument("--body-file", default="", help="human mode: a file holding the PR body")

    sub.add_parser("land", help="drain the queue unless another run holds it")
    sub.add_parser("resume", help="clear a pause after you checked trunk")
    p = sub.add_parser("status")
    p.add_argument("id", nargs="?")
    p = sub.add_parser("slot", help="run a heavy command under a governor slot")
    p.add_argument("--exclusive", action="store_true", help="hold every slot: for benchmarks that need a quiet machine")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    return top


def run(argv):
    args = parser().parse_args(argv)
    if args.command == "slot":
        command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        if not command:
            raise LandError("slot needs a command after --")
        with (exclusive_slot() if args.exclusive else slot()):
            return None, subprocess.run(command).returncode
    if args.command == "init":
        return init(args.repo, args.trunk, args.mode, args.remote, args.base, args.check, args.setup, args.batch, args.timeout,
                    args.merge_method), 0
    store = Store.for_repo(args.repo)
    if args.command == "lease":
        if args.action == "claim":
            return lease_claim(store, args.holder, args.paths, args.ttl_hours), 0
        if args.action == "list":
            rows = store.db.execute("SELECT * FROM lease WHERE state = 'submitted' OR (state = 'active' AND expires > ?) ORDER BY id", (stamp(),))
            return "\n".join(f"L{r['id']} {r['state']} {r['holder']} until {r['expires'][:16]}: {r['paths'].replace(chr(10), ', ') or '(whole repo)'}" for r in rows) or "no leases held", 0
        number = lease_id(args.id)
        with store.tx() as db:
            row = db.execute("SELECT state FROM lease WHERE id = ?", (number,)).fetchone()
            if not row or row["state"] != "active":
                raise LandError(f"L{number} is not active; a submitted lease is released when its entry lands or bounces")
            if args.action == "renew":
                db.execute("UPDATE lease SET expires = ? WHERE id = ?", (stamp(now() + timedelta(hours=args.ttl_hours)), number))
            else:
                db.execute("UPDATE lease SET state = 'released' WHERE id = ?", (number,))
            store.log(db, "lease", number, "renewed" if args.action == "renew" else "released")
        return f"L{number} {'renewed' if args.action == 'renew' else 'released'}", 0
    if args.command == "mode":
        return change_mode(store, args.mode, args.merge_method), 0
    if args.command == "submit":
        body = Path(args.body_file).read_text() if args.body_file else ""
        return submit(store, args.holder, args.branch, args.sha, args.lease, args.reviewer, args.title, body), 0
    if args.command == "land":
        return land(store), 0
    if args.command == "resume":
        with store.tx() as db:
            db.execute("INSERT OR REPLACE INTO contract VALUES ('paused', '\"\"')")
            db.execute("DELETE FROM contract WHERE key = 'tip'")
            store.log(db, "queue", 0, "resumed")
        return "queue resumed", 0
    return status(store, args.id), 0


def main(argv=None):
    try:
        output, code = run(sys.argv[1:] if argv is None else argv)
    except LandError as error:
        print(f"land: {error}", file=sys.stderr)
        return 1
    if output is not None:
        print(output)
    return code


if __name__ == "__main__":
    sys.exit(main())
