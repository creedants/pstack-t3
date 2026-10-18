import json
import re
from landing_lib import seam

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
