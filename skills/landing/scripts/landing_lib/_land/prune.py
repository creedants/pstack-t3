"""Reconcile the remote landing branches with the entries the queue has settled."""

import re
from landing_lib import seam
from .._merge.settle import close_pr_with_reason, delete_landing_branch

_LANDING_BRANCH = re.compile(r"^landing/[eq](\d+)$")

def landing_branches(text):
    """The landing/e<n> and landing/q<n> branch names in ls-remote output, sorted."""
    found = set()
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].startswith("refs/heads/"):
            continue
        branch = parts[1][len("refs/heads/"):]
        if _LANDING_BRANCH.match(branch):
            found.add(branch)
    return sorted(found)

def settled_entry(store, branch):
    """The entry row for a landing branch, or None when the entry is missing or still in flight.

    Settled means landed or bounced. A queued, landing, or awaiting-merge entry is in flight."""
    found = _LANDING_BRANCH.match(branch or "")
    if not found:
        return None
    row = store.db.execute("SELECT * FROM entry WHERE id = ?", (int(found.group(1)),)).fetchone()
    return row if row is not None and row["state"] in ("landed", "bounced") else None

def prune(store, dry_run=False):
    """Close the PR and delete the remote branch of every settled entry whose landing branch still exists.

    A branch outside landing/e<n> and landing/q<n>, a branch whose entry is missing, and a branch whose entry is
    still in flight are left alone. Each line names one action, and --dry-run prints one line per branch and acts on none.
    A failed close or delete prints a warning line and leaves the entry's state alone."""
    remote, repo = store.contract["remote"], store.repo
    listed = seam.git("ls-remote", "--heads", remote, "refs/heads/landing/*", cwd=repo, check=False)
    if listed.returncode != 0:
        raise seam.Infrastructure(f"could not list the landing branches on {remote}: {seam.git_reason(listed.stderr)}")
    lines = []
    for branch in landing_branches(listed.stdout):
        row = settled_entry(store, branch)
        if row is None:
            continue
        label = f"{seam.entry_label(row['id'])}, {row['state']}"
        if dry_run:
            lines.append(f"would delete {branch} ({label})")
            continue
        closed, warning = close_pr_with_reason(store, row["pr"], f"queue prune: {label}")
        if warning:
            lines.append(f"warning: {warning}")
        elif closed:
            lines.append(f"closed {row['pr']} ({label})")
        deleted, warning = delete_landing_branch(store, branch)
        if warning:
            lines.append(f"warning: {warning}")
        elif deleted:
            lines.append(f"deleted {branch} ({label})")
    return "\n".join(lines)
