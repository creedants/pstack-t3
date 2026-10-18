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

MERGE_REQUESTED = "merge requested by the queue"


WAITING_FOR_CHECKS = "waiting for required checks before merging"


BASE_MOVED_RETRIES = 3


BASE_MOVED_WAIT = 2


ABSENT_CHECKS_GRACE = timedelta(minutes=5)


BLOCKER_QUERY = (
    'def check_name: .name // .context; '
    '[.reviewDecision // "", '
    '([(.statusCheckRollup // [])[] | select((.status // "COMPLETED") != "COMPLETED" or (.state // "") == "PENDING")] | length), '
    '([(.statusCheckRollup // [])[] | select((.conclusion // "") as $c | (.state // "") as $s '
    '| ($c == "FAILURE" or $c == "CANCELLED" or $c == "TIMED_OUT" or $s == "FAILURE" or $s == "ERROR")) '
    '| check_name] | join(",")), '
    '((.statusCheckRollup // []) | length), '
    '(.autoMergeRequest != null), '
    '(.headRefOid // ""), '
    '([(.statusCheckRollup // [])[] | check_name | select(type == "string" and . != "")] | tojson)] | @tsv'
)


def classify_rollup(review, pending, failed, posted):
    """The gate for one check read. Absent means GitHub has posted nothing yet."""
    if review in ("REVIEW_REQUIRED", "CHANGES_REQUESTED"):
        return "review", review
    if failed:
        return "failed", failed
    if pending > 0:
        return "pending", ""
    if posted == 0:
        return "absent", ""
    return "", ""


def unposted_merge_refusal(stderr):
    """True when stderr contains the base branch policy prohibition."""
    return "the base branch policy prohibits the merge" in (stderr or "")


def base_moved_refusal(stderr):
    return "Base branch was modified" in (stderr or "")


def merge_requested(row):
    return row["note"] == MERGE_REQUESTED


OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


PR_STATES = ("OPEN", "CLOSED", "MERGED")


Line = namedtuple("Line", "entries stale tip")


CheckRead = namedtuple("CheckRead", "blocker detail armed head names")


def trunk_commit(store):
    return seam.git("rev-parse", seam.trunk_ref(store.contract), cwd=store.repo).stdout.strip()


def fetch_trunk(store):
    contract = store.contract
    seam.git("fetch", contract["remote"], contract["trunk"], cwd=store.repo)
    return trunk_commit(store)


def same_tree(repo, first, second):
    found = seam.git("rev-parse", f"{first}^{{tree}}", f"{second}^{{tree}}", cwd=repo, check=False)
    trees = found.stdout.split()
    return found.returncode == 0 and len(trees) == 2 and trees[0] == trees[1]


def read_tree(store, commit):
    """The tree of the commit with this full id, or None when git cannot read it.

    After a failed read the id is fetched from the contract remote and read once more."""
    for fetched in (False, True):
        found = seam.git("rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}", cwd=store.repo, check=False)
        tree = found.stdout.strip()
        if found.returncode == 0 and OBJECT_ID.fullmatch(tree):
            return tree
        if not fetched:
            seam.git("fetch", store.contract["remote"], commit, cwd=store.repo, check=False)
    return None


def built_on(store, entry, trunk):
    if entry["onto"]:
        return entry["onto"]
    base = seam.git("merge-base", entry["candidate"], trunk, cwd=store.repo, check=False)
    return base.stdout.strip() if base.returncode == 0 else ""


def merge_method(store):
    return store.contract.get("mergeMethod") or "merge"


def line_of(store, trunk):
    waiting = store.entries("awaiting-merge")
    built = [row for row in waiting if row["candidate"]]
    first = next((row for row in built if same_tree(store.repo, built_on(store, row, trunk), trunk)), None)
    entries = [first] if first else []
    behind = {row["onto"]: row for row in reversed(built)}
    if merge_method(store) != "rebase":
        while entries and entries[-1]["candidate"] in behind:
            entries.append(behind[entries[-1]["candidate"]])
    inside = {row["id"] for row in entries}
    stale = [row for row in waiting if row["id"] not in inside and row["pr"] and not merge_requested(row)]
    return Line(entries, stale, entries[-1]["candidate"] if entries else trunk)


def absent_since(store, ident, candidate):
    """When this entry first saw no posted check for this candidate.

    A missing, legacy, unreadable, or other-candidate marker records now and
    returns that moment. A second read in the same run finds the stored record.
    """
    raw = store.contract.get("absentDrain") or {}
    record = raw.get(str(ident))
    since = None
    if isinstance(record, dict) and record.get("candidate") == candidate:
        try:
            since = datetime.fromisoformat(record.get("since") or "")
        except (ValueError, TypeError):
            since = None
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
    if since is None:
        since = seam.now()
        with store.tx() as db:
            row = db.execute("SELECT value FROM contract WHERE key = 'absentDrain'").fetchone()
            raw = json.loads(row["value"]) if row else {}
            raw[str(ident)] = {"since": seam.stamp(since), "candidate": candidate}
            db.execute("INSERT OR REPLACE INTO contract VALUES ('absentDrain', ?)", (json.dumps(raw),))
            store.set_entry(db, ident, "awaiting-merge", note=WAITING_FOR_CHECKS)
    return since


def absence_waits(since, moment):
    """True while moment is still inside the grace after since."""
    return moment - since < ABSENT_CHECKS_GRACE


def tsv_field_text(field):
    """Reverse jq @tsv escapes in one field.

    A backslash, tab, line feed, or carriage return comes back as itself.
    An unknown escape or a trailing backslash returns None.
    """
    out = []
    index = 0
    size = len(field)
    while index < size:
        char = field[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= size:
            return None
        nxt = field[index + 1]
        if nxt == "\\":
            out.append("\\")
        elif nxt == "t":
            out.append("\t")
        elif nxt == "n":
            out.append("\n")
        elif nxt == "r":
            out.append("\r")
        else:
            return None
        index += 2
    return "".join(out)


def merge_blocker(store, url):
    """The check read, plus the posted names from that same rollup.

    names is a tuple of non-empty strings. Field 7 is the @tsv form of their JSON array.
    tsv_field_text reverses that escaping before the array is read.
    A field count other than 7, or a field 7 that is not a JSON list of strings after that, raises Infrastructure.
    """
    view = seam.gh("pr", "view", url, "--json", "reviewDecision,statusCheckRollup,autoMergeRequest,headRefOid",
              "-q", BLOCKER_QUERY, cwd=store.repo)
    if view.returncode != 0:
        raise seam.Infrastructure(f"could not read checks for {url}: {view.stderr.strip() or 'gh pr view failed'}")
    fields = view.stdout.rstrip("\n").split("\t")
    names = None
    if len(fields) == 7:
        text = tsv_field_text(fields[6])
        parsed = None
        if text is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            names = tuple(item for item in parsed if item)
    if names is None or not fields[1].isdigit() or not fields[3].isdigit() or fields[4] not in ("true", "false"):
        raise seam.Infrastructure(f"could not read checks for {url}: unparseable check rollup")
    blocker, detail = classify_rollup(fields[0], int(fields[1]), fields[2], int(fields[3]))
    return CheckRead(blocker, detail, fields[4] == "true", fields[5], names)


def disarm_auto_merge(store, url, armed):
    """Turn auto-merge off when the check read showed an autoMergeRequest.

    An empty return means it is off or was never requested. A string is a failure of
    gh pr merge --disable-auto."""
    if not armed:
        return ""
    result = seam.gh("pr", "merge", url, "--disable-auto", cwd=store.repo)
    if result.returncode == 0:
        return ""
    return (result.stderr or result.stdout or "gh pr merge --disable-auto failed").strip()


def bounce_open_pr(store, ident, url, reason, armed):
    """Bounce an open merge-mode PR. Disable auto-merge when it is on, and leave the PR open.

    A PR whose auto-merge stays on can still merge without the queue, so the queue pauses and keeps tracking it."""
    problem = disarm_auto_merge(store, url, armed)
    if problem:
        raise seam.AutoMergeStillEnabled(
            f"{reason}. Auto-merge is still enabled: {problem}. "
            f"Turn auto-merge off on {url}, then run land.py resume and land.py land")
    with store.tx() as db:
        seam.settle_bounced(store, db, ident, reason)


def pause_for_review(store, url, detail, disarm, armed=False):
    problem = disarm_auto_merge(store, url, armed) if disarm else ""
    requirement = "has changes requested" if detail == "CHANGES_REQUESTED" else "needs an approving review"
    if problem:
        raise seam.Infrastructure(f"{url} {requirement}. Auto-merge is still enabled: {problem}")
    raise seam.Infrastructure(f"{url} {requirement}")


def read_required_contexts(store):
    """Contexts for store.contract["trunk"] from branch protection, or None.

    The gh path is repos/{owner}/{repo}/branches/<trunk>/protection/required_status_checks.
    gh fills {owner} and {repo} from the checkout, as forge_branch_status already does.
    Return a tuple of non-empty strings when gh exits 0 and the body is a JSON object whose contexts is a list of non-empty strings.
    An empty list returns ().
    Return None on a non-zero gh exit, on a body that is not that object, and on a contexts element that is not a non-empty string.
    Ignore checks. Do not call the rules endpoint.
    """
    result = seam.gh(
        "api",
        "repos/{owner}/{repo}/branches/" + store.contract["trunk"] + "/protection/required_status_checks",
        cwd=store.repo,
    )
    if result.returncode != 0:
        return None
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    contexts = body.get("contexts") if isinstance(body, dict) else None
    if not isinstance(contexts, list):
        return None
    if any(not isinstance(item, str) or item == "" for item in contexts):
        return None
    return tuple(contexts)


def missing_required(contexts, rollup_names):
    """Context strings that rollup_names does not contain, in contexts order."""
    posted = set(rollup_names)
    return tuple(context for context in contexts if context not in posted)


def refusal_waits(store, plain, checks):
    """True when this plain-merge stderr waits instead of pausing the queue.

    The policy phrase must be in plain. A blocker of absent waits and does not read protection.
    Any other blocker waits only when read_required_contexts returns a tuple and missing_required is non-empty.
    None does not wait. An empty tuple does not wait.
    Stderr without the policy phrase returns False.
    """
    if not unposted_merge_refusal(plain):
        return False
    if checks.blocker == "absent":
        return True
    contexts = read_required_contexts(store)
    if contexts is None or contexts == ():
        return False
    return missing_required(contexts, checks.names) != ()


def request_merge(store, ident, out):
    entry = entry_row(store, ident)
    url = entry["pr"]
    line = line_of(store, trunk_commit(store)).entries
    ahead_of = {row["id"]: ahead for ahead, row in zip([None, *line], line)}
    if ident not in ahead_of:
        return False
    ahead = ahead_of[ident]
    checks = merge_blocker(store, url)
    if checks.head != entry["candidate"]:
        return False
    if checks.blocker == "review":
        pause_for_review(store, url, checks.detail, disarm=False)
    waiting = checks.blocker == "pending"
    if checks.blocker == "absent":
        since = absent_since(store, ident, entry["candidate"])
        waiting = ahead or absence_waits(since, seam.now())
    if waiting:
        with store.tx() as db:
            store.set_entry(db, ident, "awaiting-merge", note=WAITING_FOR_CHECKS)
        return False
    if ahead:
        with store.tx() as db:
            store.set_entry(db, ident, "awaiting-merge", note=f"waiting for {seam.entry_label(ahead['id'])} to merge first")
        return False
    if checks.blocker == "failed":
        bounce_open_pr(store, ident, url, f"required checks failed on {url}: {checks.detail}", checks.armed)
        out.bounced.append(ident)
        return False
    trunk = fetch_trunk(store)
    if not same_tree(store.repo, built_on(store, entry, trunk), trunk):
        return False
    pinned = (f"--{merge_method(store)}", "--match-head-commit", entry["candidate"])
    queued = seam.gh("pr", "merge", url, "--auto", *pinned, cwd=store.repo)
    if queued.returncode == 0:
        with store.tx() as db:
            store.set_entry(db, ident, "awaiting-merge", note=MERGE_REQUESTED)
        return True
    for asked in range(BASE_MOVED_RETRIES + 1):
        now_ = seam.gh("pr", "merge", url, *pinned, cwd=store.repo)
        if now_.returncode == 0 or not base_moved_refusal(now_.stderr) or asked == BASE_MOVED_RETRIES:
            break
        time.sleep(BASE_MOVED_WAIT)
    if now_.returncode != 0:
        plain = (now_.stderr or "").strip()
        if refusal_waits(store, plain, checks):
            with store.tx() as db:
                store.set_entry(db, ident, "awaiting-merge", note=WAITING_FOR_CHECKS)
            return False
        if base_moved_refusal(plain):
            return False
        head = open_pr_head(store, url)
        if head and head != entry["candidate"]:
            with store.tx() as db:
                store.set_entry(db, ident, "awaiting-merge", note=head_changed_note(head))
            raise seam.HeadChanged(
                f"GitHub refused to merge {url}, and its head is now {head[:12]}, not the checked candidate "
                f"{entry['candidate'][:12]}. {seam.entry_label(ident)} was not merged. Close that PR to bounce "
                f"{seam.entry_label(ident)}, or push {entry['candidate'][:12]} back to that PR's branch. "
                "Then run land.py resume and land.py land")
        raise seam.Infrastructure(f"GitHub refused to merge {url}: {plain or 'gh pr merge failed'}")
    with store.tx() as db:
        store.set_entry(db, ident, "awaiting-merge", note=MERGE_REQUESTED)
    return True


_CLIENT_ABSENT_REF = re.compile(r"^error: unable to delete '[^']*': remote ref does not exist$", re.M)


_GITHUB_OWNER_REPO = re.compile(
    r"^(?:https://github\.com/|ssh://git@github\.com/|git://github\.com/|git@github\.com:)"
    r"([^/]+)/([^/]+?)(?:\.git)?/?$"
)


def http_status(text):
    """The status code from an HTTP status line, or None when the text has none."""
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("HTTP/") and parts[1].isdigit():
            return int(parts[1])
    return None


def forge_branch_status(repo, branch):
    """HTTP status for branch on the forge this checkout already uses for gh pr.

    gh fills {owner} and {repo} from the checkout. 404 means the branch is gone.
    200 means it exists. None means the read did not return a status line."""
    result = seam.gh("api", "--include", "repos/{owner}/{repo}/branches/" + branch, cwd=repo)
    return http_status(result.stdout)


def github_owner_repo(url):
    """owner/repo from a GitHub remote URL, or None when the URL names something else."""
    match = _GITHUB_OWNER_REPO.match((url or "").strip())
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if not owner or not repo or owner in (".", "..") or repo in (".", ".."):
        return None
    return f"{owner}/{repo}"


def push_urls(repo, remote):
    """Every push URL of remote, or None when git cannot list them."""
    listed = seam.git("remote", "get-url", "--push", "--all", remote, cwd=repo, check=False)
    if listed.returncode != 0:
        return None
    return [line.strip() for line in listed.stdout.splitlines() if line.strip()]


def forge_name_with_owner(repo):
    """owner/repo that gh resolves for this checkout, or None when that read fails."""
    viewed = seam.gh("repo", "view", "--json", "nameWithOwner", cwd=repo)
    if viewed.returncode != 0 or not (viewed.stdout or "").strip():
        return None
    try:
        payload = json.loads(viewed.stdout)
    except json.JSONDecodeError:
        return None
    name = payload.get("nameWithOwner") if isinstance(payload, dict) else None
    if not isinstance(name, str) or name.count("/") != 1:
        return None
    owner, repo_name = name.split("/", 1)
    if not owner or not repo_name:
        return None
    return name


def forge_is_only_push_target(store):
    """True when the failed delete went only to the repository gh will be asked about."""
    urls = push_urls(store.repo, store.contract["remote"])
    if urls is None or len(urls) != 1:
        return False
    pushed = github_owner_repo(urls[0])
    if pushed is None:
        return False
    resolved = forge_name_with_owner(store.repo)
    if resolved is None:
        return False
    return pushed.lower() == resolved.lower()


def remote_branch_is_gone(store, entry, branch, stderr):
    """Whether a failed delete left the queue branch absent.

    Merge and human mode ask the forge when the contract remote has exactly
    one push URL and that URL names the same owner/repo gh resolves. The
    forge's answer is final. Only HTTP 404 counts as gone, and any other
    status or no status line leaves the entry.
    With no forge to ask, the queue cannot tell a branch hidden from git
    from a missing one. It then accepts git's absent line only when the
    entry's PR was opened from another branch, which an older queue did.
    That settles an entry whose hidden landing/e<n> still exists. The
    alternative is an entry that can never land.
    Push and local mode accept only git's absent line, and any other
    failure waits for the next run."""
    if store.contract["mode"] in ("merge", "human"):
        if forge_is_only_push_target(store):
            return forge_branch_status(store.repo, branch) == 404
        return opened_on_another_branch(store, entry, branch, stderr)
    return _CLIENT_ABSENT_REF.search(stderr or "") is not None


def forget_local_branch(store, branch):
    """Drop the local branch and its remote-tracking ref. A branch git cannot delete is named in the warning."""
    remote = store.contract["remote"]
    seam.git("update-ref", "-d", f"refs/remotes/{remote}/{branch}", cwd=store.repo, check=False)
    if seam.git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=store.repo, check=False).returncode != 0:
        return ""
    local = seam.git("branch", "-D", branch, cwd=store.repo, check=False)
    if local.returncode != 0:
        return f"left local {branch}: {seam.git_reason(local.stderr)}"
    return ""


def opened_on_another_branch(store, entry, branch, stderr):
    """True when git says branch is absent and the entry's PR was opened from a different branch.

    An older queue opened the PR on landing/q<n> and never created
    landing/e<n>. The merged PR then settles without deleting that name.
    Only remote_branch_is_gone calls this, and only when no forge answers.
    A failed read leaves the entry."""
    if _CLIENT_ABSENT_REF.search(stderr or "") is None:
        return False
    view = seam.gh("pr", "view", entry["pr"], "--json", "headRefName", "-q", ".headRefName", cwd=store.repo)
    head = (view.stdout or "").strip()
    return view.returncode == 0 and bool(head) and head != branch


def delete_queue_branch(store, entry):
    """Drop landing/e<n> after the PR has merged.

    Returns (deleted, warning). The branch is done when the server accepts
    the delete or when remote_branch_is_gone says it is gone. A local
    branch that exists and cannot be deleted is named in warning. The entry
    still lands when the remote ref is gone."""
    branch = seam.human_branch(entry)
    pushed = seam.git_push(store.contract["remote"], "--delete", branch, cwd=store.repo, check=False)
    if pushed.returncode != 0:
        stderr = pushed.stderr or ""
        if not remote_branch_is_gone(store, entry, branch, stderr):
            return False, ""
    return True, forget_local_branch(store, branch)


def entry_row(store, ident):
    return store.db.execute("SELECT * FROM entry WHERE id = ?", (ident,)).fetchone()


def read_pr_state(store, url):
    """State, head commit, and merge commit of a PR. Raises Infrastructure when gh fails or prints no known state."""
    view = seam.gh("pr", "view", url, "--json", "state,headRefOid,mergeCommit",
              "-q", '.state + " " + .headRefOid + " " + (.mergeCommit.oid // "")', cwd=store.repo)
    fields = (view.stdout.strip().split(" ") + ["", "", ""])[:3]
    if view.returncode != 0 or fields[0] not in PR_STATES:
        raise seam.Infrastructure(f"could not read the state of {url}. gh said {view.stderr.strip() or 'nothing'}")
    return fields


def open_pr_head(store, url):
    """The head commit of a PR that a state read shows open. Empty when the read fails or shows another state."""
    try:
        state, head, _merged = read_pr_state(store, url)
    except seam.Infrastructure:
        return ""
    return head if state == "OPEN" else ""


def head_changed_note(head):
    return f"PR head changed to {head[:12]} outside the queue"


def tree_mismatch_pause(store, entry, merged, trunk):
    """The pause text when trunk holds the merge commit and its tree is not the candidate's, and empty when trunk holds it and the trees are the same.

    Raises MergeNotOnTrunk when git says the trunk commit does not hold the merge commit. Raises UnverifiedMerge when
    the merge commit has no full id, either tree cannot be read, or git cannot tell whether trunk holds the merge commit."""
    label, remote, url = seam.entry_label(entry["id"]), store.contract["remote"], entry["pr"]
    unverified = (f"The merge of {label} could not be checked. {{cause}}, so the tree of the merge commit was not "
                  f"compared with the tree of the candidate. {label} stays awaiting merge. "
                  "{fix}, then run land.py resume and land.py land")
    if not OBJECT_ID.fullmatch(merged or ""):
        raise seam.UnverifiedMerge(unverified.format(
            cause=f"The state read of {url} gave no full commit id for the merge commit",
            fix=f"Check what gh pr view {url} --json mergeCommit prints"))
    trees = []
    for commit in (merged, entry["candidate"]):
        tree = read_tree(store, commit)
        if tree is None:
            raise seam.UnverifiedMerge(unverified.format(
                cause=f"Git could not read commit {commit} even after asking {remote} for it",
                fix=f"Check that {remote} serves that commit"))
        trees.append(tree)
    # The exit codes are those in rewound. Only 0 shows that the merge commit is on trunk.
    code = seam.git("merge-base", "--is-ancestor", merged, trunk, cwd=store.repo, check=False).returncode
    if code == 1:
        raise seam.MergeNotOnTrunk(f"{url} is recorded as merged at {merged[:12]}, and trunk at {trunk[:12]} does not hold "
                              f"that commit. {label} stays awaiting merge. Check what happened to trunk. Once trunk "
                              "holds that commit again, run land.py resume and land.py land")
    if code != 0:
        raise seam.UnverifiedMerge(unverified.format(
            cause=f"Git could not tell whether trunk at {trunk} holds merge commit {merged} "
                  f"(git merge-base --is-ancestor exited {code})",
            fix="Check that git can read both commits here"))
    if trees[0] == trees[1]:
        return ""
    return (f"{label} merged as {merged[:12]}, whose tree is not the checked candidate "
            f"{entry['candidate'][:12]}. Trunk holds a change no check ran against. Check trunk, then run land.py resume")


def take_pr(store, entry, landed, bounced):
    try:
        state, head, merged = read_pr_state(store, entry["pr"])
    except seam.Infrastructure:
        if store.contract["mode"] == "merge":
            raise
        return False
    if state == "MERGED":
        checked, unchecked = head == entry["candidate"], ""
        if store.contract["mode"] == "merge":
            trunk = fetch_trunk(store)
            unchecked = tree_mismatch_pause(store, entry, merged, trunk) if checked else ""
        deleted, warning = delete_queue_branch(store, entry)
        if not deleted:
            return True
        with store.tx() as db:
            if checked:
                seam.settle_landed(store, db, entry["id"], merged, warning)
            else:
                seam.settle_bounced(store, db, entry["id"], f"merged at {merged[:12]} with head {head[:12]}, not the checked "
                                                       f"{entry['candidate'][:12]}; review what reached trunk")
            db.execute("INSERT OR REPLACE INTO contract VALUES ('tip', ?)", (json.dumps(merged),))
            if unchecked:
                seam.mark_paused(store, db, unchecked)
        (landed if checked else bounced).append(entry["id"])
        if unchecked:
            raise seam.UncheckedTrunk(unchecked)
        return True
    if state == "CLOSED":
        with store.tx() as db:
            seam.settle_bounced(store, db, entry["id"], "PR closed without merging")
        bounced.append(entry["id"])
        return True
    if state == "OPEN" and head and head != entry["candidate"]:
        with store.tx() as db:
            store.set_entry(db, entry["id"], "awaiting-merge", note=head_changed_note(head))
        return True
    return False


def poll(store, entry, out):
    merging = store.contract["mode"] == "merge"
    if not entry["pr"]:
        reason = seam.foreign_author(store, entry)
        if reason:
            with store.tx() as db:
                seam.settle_bounced(store, db, entry["id"], reason)
            out.bounced.append(entry["id"])
            return
        outcome = seam.ensure_pr(store, entry)
        (out.adopted if outcome == "adopted" else out.opened).append(entry["id"])
        if merging:
            request_merge(store, entry["id"], out)
        entry = entry_row(store, entry["id"])
        if entry["state"] != "awaiting-merge":
            return
    if take_pr(store, entry, out.landed, out.bounced) or not merging:
        return
    if merge_requested(entry):
        checks = merge_blocker(store, entry["pr"])
        if take_pr(store, entry, out.landed, out.bounced):
            return
        if checks.blocker == "failed":
            bounce_open_pr(store, entry["id"], entry["pr"],
                           f"required checks failed on {entry['pr']}: {checks.detail}", checks.armed)
            out.bounced.append(entry["id"])
        elif checks.blocker == "review":
            pause_for_review(store, entry["pr"], checks.detail, disarm=True, armed=checks.armed)
        return
    # A merge command can return before pr view reports MERGED.
    reads = 4 if request_merge(store, entry["id"], out) else 1
    entry = entry_row(store, entry["id"])
    if entry["state"] != "awaiting-merge":
        return
    for attempt in range(reads):
        if take_pr(store, entry, out.landed, out.bounced):
            break
        entry = entry_row(store, entry["id"])
        if entry["state"] != "awaiting-merge" or attempt + 1 == reads:
            break
        time.sleep(0.5)


def walk(store, out, only=None):
    polled = set()
    while True:
        line = line_of(store, trunk_commit(store)).entries
        inside = {row["id"] for row in line}
        rows = [row for row in store.entries("awaiting-merge") if row["id"] not in inside] + line
        entry = next((row for row in rows if row["id"] not in polled and (only is None or row["id"] in only)), None)
        if entry is None:
            return
        polled.add(entry["id"])
        poll(store, entry, out)
