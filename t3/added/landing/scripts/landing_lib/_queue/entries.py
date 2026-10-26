import json
from datetime import timedelta
from landing_lib import seam
from .._merge.settle import clean_bounced

def submit(store, holder, branch, sha, lease, reviewer, title="", body="", owner=None):
    contract, repo = store.contract, store.repo
    lease_number = seam.lease_id(lease)
    sha = seam.git("rev-parse", "--verify", f"{sha}^{{commit}}", cwd=repo).stdout.strip()
    if contract["mode"] != "local":
        seam.git("fetch", contract["remote"], contract["trunk"], cwd=repo)
    base = seam.git("merge-base", seam.trunk_ref(contract), sha, cwd=repo).stdout.strip()
    if seam.git("rev-list", "--merges", f"{base}..{sha}", cwd=repo).stdout.strip():
        raise seam.LandError(f"{branch} has merge commits; make its history linear on {contract['trunk']} first")
    changed = seam.changed_paths(repo, base, sha)
    if not changed:
        raise seam.LandError(f"{sha[:12]} changes nothing relative to {contract['trunk']}")
    generated_paths = contract.get("generated") or []
    files = seam.source_paths(changed, generated_paths)
    if not files:
        if contract.get("rebuild"):
            raise seam.LandError(f"{sha[:12]} changes only generated paths {', '.join(changed)}. Change sources and run {contract['rebuild']}")
        raise seam.LandError(f"{sha[:12]} changes only generated paths {', '.join(changed)}. Change sources; the contract has no rebuild command")
    print_ = seam.fingerprint(repo, base, sha, generated_paths)
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
        # drain.py appends ". Fix it, then run land.py resume" to this text.
        reason = seam.git_reason(result.stderr, compact=True)
        raise seam.Infrastructure(f"push rejected: {reason}. Change the remote or its branch protection so it accepts this push. Selecting human mode with land.py mode human requires an empty queue")
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
        reason = integration.apply(entry, base)
        candidate = "" if reason else integration.head()
        if reason and reason != "already-in-trunk":
            clean_bounced(store, entry, reason)
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
        reason = f"checks failed: {failure}"
        if len(applied) == 1:
            clean_bounced(store, applied[0], reason)
        with store.tx() as db:
            if len(applied) > 1:
                for entry in applied:
                    store.set_entry(db, entry["id"], "queued", candidate="", note="batch failed checks; landing one at a time")
                return landed, bounced, [entry["id"] for entry in applied], base
            settle_bounced(store, db, applied[0]["id"], reason)
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
