import json
from landing_lib import seam

class Infrastructure(seam.LandError):
    """A failure that is not the entry's fault. The queue pauses instead of bouncing."""

class AutoMergeStillEnabled(Infrastructure):
    """The message is the whole pause. land() stores it unchanged."""

class UncheckedTrunk(Infrastructure):
    pass

class UnverifiedMerge(Infrastructure):
    pass

class MergeNotOnTrunk(Infrastructure):
    pass

class HeadChanged(Infrastructure):
    pass

def trunk_ref(contract):
    if contract["mode"] == "local":
        return f"refs/landing/{contract['trunk']}"
    return f"refs/remotes/{contract['remote']}/{contract['trunk']}"

def allowed_merge_methods(repo):
    """The methods gh says this repository allows, or None when gh cannot say."""
    fields = ",".join(seam.MERGE_FLAGS[method] for method in seam.MERGE_METHODS)
    try:
        result = seam.gh("repo", "view", "--json", fields, cwd=repo)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    allowed = []
    for method in seam.MERGE_METHODS:
        flag = payload.get(seam.MERGE_FLAGS[method])
        if not isinstance(flag, bool):
            return None
        if flag:
            allowed.append(method)
    return tuple(allowed)

def allowed_list(allowed):
    return ", ".join(allowed) if allowed else "none"

def pick_merge_method(explicit, allowed):
    """The method init stores. When gh cannot say, an omitted method stays merge."""
    if allowed is None:
        return explicit or "merge"
    if explicit:
        if explicit not in allowed:
            raise seam.LandError(f"repository does not allow {explicit}; allowed: {allowed_list(allowed)}")
        return explicit
    if len(allowed) == 1:
        return allowed[0]
    if "merge" in allowed:
        return "merge"
    if "squash" in allowed:
        return "squash"
    raise seam.LandError(f"repository allows no merge method; allowed: {allowed_list(allowed)}")

def refused_merge_method(text):
    for method, phrase in seam.METHOD_REFUSAL:
        if phrase in (text or ""):
            return method
    return None

def suggested_merge_method(repo, problem):
    """An allowed method other than the one GitHub just refused, preferring squash."""
    refused = refused_merge_method(problem)
    if not refused:
        return None
    allowed = allowed_merge_methods(repo)
    if not allowed:
        return None
    for method in ("squash", "rebase", "merge"):
        if method in allowed and method != refused:
            return method
    return None

def busy_queue_message(count):
    noun = "entry is" if count == 1 else "entries are"
    return f"{count} {noun} queued, landing, or awaiting merge; change the mode when the queue is empty"
