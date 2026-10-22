import re
from landing_lib import seam
from .._merge.settle import close_pr_with_reason, delete_landing_branch

_LANDING_BRANCH = re.compile(r"^landing/[eq]([1-9][0-9]*)$")

def landing_branches(text):
    """Canonical landing branch names in ls-remote output sorted. A canonical name is landing/e<n> or landing/q<n> with n in [1-9][0-9]* in ASCII and no leading zero."""
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
    """The entry row for a canonical landing branch. A canonical name is landing/e<n> or landing/q<n> with n in [1-9][0-9]* in ASCII and no leading zero. None means the name is not canonical or the suffix is not an integer or the id is outside 1 to 9223372036854775807 or the entry is missing or still in flight. Settled means landed or bounced. A queued, landing, or awaiting-merge entry is in flight."""
    found = _LANDING_BRANCH.match(branch or "")
    if not found:
        return None
    try:
        ident = int(found.group(1))
    except ValueError:
        return None
    if ident < 1 or ident > 9223372036854775807:
        return None
    try:
        row = store.db.execute("SELECT * FROM entry WHERE id = ?", (ident,)).fetchone()
    except OverflowError:
        return None
    return row if row is not None and row["state"] in ("landed", "bounced") else None

def prune(store, dry_run=False):
    """Close the open PR and delete the remote branch of every settled entry with a canonical branch still on the remote. A canonical branch names landing/e<n> or landing/q<n> with n in [1-9][0-9]* in ASCII and no leading zero. A branch with another name is left alone. A branch whose entry is missing or still in flight is left alone. Each line names one action and --dry-run prints one line per settled branch and acts on none. A failed close or delete prints a warning line and leaves the entry state alone."""
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
        deleted, warning = delete_landing_branch(store, branch, row)
        if warning:
            lines.append(f"warning: {warning}")
        elif deleted:
            lines.append(f"deleted {branch} ({label})")
    return "\n".join(lines)
