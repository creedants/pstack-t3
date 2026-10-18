import os
import subprocess
from collections import namedtuple
from landing_lib import seam
from .entries import forget_absent, settle_bounced, settle_landed

def gh(*args, cwd):
    return subprocess.run([os.environ.get("LAND_GH", "gh"), *args], cwd=cwd, capture_output=True, text=True)

Outcome = namedtuple("Outcome", "landed bounced opened adopted rebuilt")

def build(store, integration, entry, onto, trunk, out):
    contract, ident = store.contract, entry["id"]
    integration.reset(onto)
    reason = integration.apply(entry, trunk)
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
