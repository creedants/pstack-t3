import os
import posixpath
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from brigade_lib import generated, seam
from brigade_lib.leases import Claim, claim_lease, claim_paths, holder, land_result, lease_states, release_lease
from brigade_lib.shared import BrigadeError
from brigade_lib.store import HELD_LOCKS


@dataclass(frozen=True)
class WidenPlan:
    """Old lease id and old paths cell plus new claim text from claim_paths on old union new."""

    old_lease: str
    old_paths: str
    claim: str


@dataclass(frozen=True)
class HeldLease:
    """Landing row id and state plus listed paths."""

    id: str
    state: str
    paths: tuple


@dataclass(frozen=True)
class CoverageDecision:
    """Changed source paths from base to sha and lease paths from landing row plus generated list."""

    changed: tuple
    lease_paths: tuple
    generated: tuple
    uncovered: tuple


@dataclass(frozen=True)
class GateInput:
    """Lease id and paths plus sha checked outside the lock."""

    lease: str
    paths: str
    sha: str


def canonical(path):
    """Repository relative POSIX path with no aliases. Empty string is the whole repository."""
    value = posixpath.normpath(path.strip().replace("\\", "/")).lstrip("/")
    if value in (".", ""):
        return ""
    if value == ".." or value.startswith("../"):
        raise BrigadeError(f"lease path {path!r} leaves the repository")
    return value


def covered(path, paths):
    """True when a lease path is empty or equals the path or holds it as a directory."""
    return any(p == "" or path == p or path.startswith(p + "/") for p in paths)


def source_paths(names, generated=()):
    """Git names that are sources. Generated names are dropped."""
    return [path for path in names if not covered(path, generated)]


def changed_paths(repo, base, head):
    """Git names changed from base to head with renames off."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "diff", "-z", "--name-only", "--no-renames", base, head],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            env={**os.environ, "LC_ALL": "C"},
        )
    except OSError as error:
        raise BrigadeError(f"git diff failed: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BrigadeError(f"git diff failed: {detail or result.returncode}")
    return [part for part in result.stdout.split("\0") if part]


def dish_doc_item(restaurant, ident):
    """True when the log holds a doc item row with a doc-item note for this dish."""
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "doc" and row["id"] == ident and row["state"] == "item":
            if row["note"].startswith("doc-item"):
                return True
    return False


def split_add(text):
    """Raw add paths split on comma with empties dropped."""
    return [part.strip() for part in str(text).split(",") if part.strip() != ""]


def canonical_list(text):
    """Canonical paths of a comma list with empties dropped. Repeats and outside paths are dropped."""
    found = []
    for part in str(text).split(","):
        if part.strip() == "":
            continue
        try:
            made = canonical(part)
        except BrigadeError:
            continue
        if made not in found:
            found.append(made)
    return found


def parse_status(text):
    """Mode and trunk ref plus generated list from land.py status text."""
    stripped = str(text).strip()
    words = stripped.split()
    mode = words[0] if words else ""
    trunk_ref = ""
    if len(words) >= 4 and words[1] == "mode" and words[2] == "onto":
        trunk_ref = words[3].rstrip(".")
    generated = ()
    match = re.search(r"generated:\s*(.*?)\s*;\s*rebuild:", stripped)
    if match:
        raw = match.group(1).strip()
        if raw and raw != "none":
            generated = tuple(part.strip() for part in raw.split(",") if part.strip() != "")
    return mode, trunk_ref, generated


def parse_lease_row(text, lease):
    """Held lease for this id from land.py lease list text or None. An empty path is the whole repository and is kept."""
    for line in str(text).splitlines():
        if not line.startswith(lease + " "):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        state = parts[1]
        after = line.split(": ", 1)
        paths_text = after[1] if len(after) == 2 else ""
        if paths_text.strip() == "(whole repo)":
            paths = ("",)
        elif paths_text.strip() == "":
            paths = ()
        else:
            found = []
            seen_root = False
            for raw in paths_text.split(","):
                part = raw.strip()
                if part == "":
                    if not seen_root:
                        seen_root = True
                        found.append("")
                else:
                    found.append(part)
            paths = tuple(found)
        return HeldLease(lease, state, paths)
    return None


def split_remote_trunk(trunk_ref):
    """Remote and trunk from a remote trunk ref. Remote is the first component."""
    rest = trunk_ref.removeprefix("refs/remotes/")
    if "/" not in rest:
        raise BrigadeError(f"cannot split trunk ref {trunk_ref!r}")
    remote, _, trunk = rest.partition("/")
    return remote, trunk


def git_run(repo, *args):
    """Run git in repo and return stdout stripped. Refuse on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            env={**os.environ, "LC_ALL": "C"},
        )
    except OSError as error:
        raise BrigadeError(f"git {' '.join(args)} failed: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BrigadeError(f"git {' '.join(args)} failed: {detail or result.returncode}")
    return result.stdout.strip()


def read_status(project_root):
    """Status text from land.py. Return the text on success. Return None only when the failed read names has no landing contract; run land.py init in it. That phrase is printed via land.py:337 and implies repository identity was read. Refuse every other failed or empty read. A not a git repository diagnostic is not proof of no contract."""
    if HELD_LOCKS:
        raise BrigadeError(f"affected may run git; release {HELD_LOCKS[0]}'s store lock before scanning")
    result = land_result(project_root, "status")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if "has no landing contract; run land.py init in it" in detail:
            return None
        raise BrigadeError(f"could not read landing status: {detail or result.returncode}")
    if not result.stdout.strip():
        raise BrigadeError("land.py status printed empty output")
    return result.stdout.strip()


def read_lease_list(project_root):
    """Lease list text from land.py outside the store lock."""
    if HELD_LOCKS:
        raise BrigadeError(f"affected may run git; release {HELD_LOCKS[0]}'s store lock before scanning")
    result = land_result(project_root, "lease", "list")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BrigadeError(detail or "lease list failed")
    return result.stdout.strip()


def widen(restaurant, ident, add_text):
    """Widen one dish lease. Claim new then record then release old."""
    wanted_raw = split_add(add_text)
    if not wanted_raw:
        raise BrigadeError("--add-paths takes comma-separated paths")
    with restaurant.checked():
        restaurant.fence()
        _, dish = restaurant.find("dishes.tsv", ident)
        old_lease = dish["lease"]
        old_paths = dish["paths"]
        state = dish["state"]
        if state in ("merged", "dropped"):
            raise BrigadeError(f"{ident} is {state}; it holds no lease")
        if not old_lease:
            raise BrigadeError(f"{ident} holds no lease; fire it with --paths first")
        branch = dish.get("branch", "")
        doc_item = dish_doc_item(restaurant, ident)
        meta = restaurant.meta
        old_canonical = canonical_list(old_paths)
        new_canonical = []
        for raw in wanted_raw:
            try:
                made = canonical(raw)
            except BrigadeError:
                made = raw.strip().replace("\\", "/").lstrip("/")
            if made not in new_canonical:
                new_canonical.append(made)
        if new_canonical and all(covered(path, old_canonical) for path in new_canonical):
            return f"{ident}: {old_lease} already covers {','.join(wanted_raw)}"
        union = old_paths + "," + ",".join(wanted_raw) if old_paths else ",".join(wanted_raw)
        claim = claim_paths(meta, union, branch, doc_item)
        plan = WidenPlan(old_lease, old_paths, claim.text)
    states = lease_states(meta.get("projectRoot", ""))
    if states.get(plan.old_lease) == "submitted":
        raise BrigadeError(
            f"{ident}: {plan.old_lease} is submitted, so it cannot take {','.join(wanted_raw)}; "
            "the submitted commit lands as it is"
        )
    if not plan.claim:
        raise BrigadeError(f"{ident}: widen claims nothing")
    try:
        new_lease = claim_lease(restaurant, ident, plan.claim)
    except Exception as error:
        raise BrigadeError(f"nothing widened: {error}") from error
    mismatch = False
    merged_now = False
    merged_state = ""
    with restaurant.checked():
        _, dish = restaurant.find("dishes.tsv", ident)
        if dish["lease"] != plan.old_lease or dish["paths"] != plan.old_paths:
            mismatch = True
        elif dish["state"] in ("merged", "dropped"):
            merged_now = True
            merged_state = dish["state"]
        else:
            restaurant.update("dishes.tsv", ident, "dish", lease=new_lease, paths=plan.claim)
    if mismatch:
        release_lease(restaurant, new_lease)
        raise BrigadeError(f"{ident} changed while widening; run dish {ident} --add-paths again")
    if merged_now:
        release_lease(restaurant, new_lease)
        raise BrigadeError(f"{ident} is {merged_state}; it holds no lease")
    release_lease(restaurant, plan.old_lease)
    return f"{ident}: {new_lease} replaces {plan.old_lease}"


def gate_outside(restaurant, ident, sha):
    """Coverage gate outside the lock. Return snapshot or None. None when dish holds no lease. None only for a clean missing-contract read on an identifiable repository. None when status names no trunk ref and a SHA is present. Refuse when the dish holds a lease under a contract but names no SHA. Refuse when the status read fails or when a fetch, merge-base, or diff step fails."""
    if HELD_LOCKS:
        raise BrigadeError(f"affected may run git; release {HELD_LOCKS[0]}'s store lock before scanning")
    with restaurant.checked():
        _, dish = restaurant.find("dishes.tsv", ident)
        lease = dish["lease"]
        paths = dish["paths"]
        project_root = restaurant.meta.get("projectRoot", "")
        holder_name = holder(restaurant, ident)
    if not lease:
        return None
    try:
        status_text = read_status(project_root)
    except BrigadeError as error:
        raise BrigadeError(
            f"{ident} cannot check coverage. {error}. "
            f"Repair the landing status error above and run dish {ident} --state in-review again"
        ) from error
    if status_text is None:
        return None
    if not sha:
        raise BrigadeError(
            f"{ident} has no SHA; pass --sha <head commit> when retrying dish {ident} --state in-review"
        )
    mode, trunk_ref, generated = parse_status(status_text)
    if not trunk_ref:
        return None
    lease_text = read_lease_list(project_root)
    held = parse_lease_row(lease_text, lease)
    if held is None or held.state != "active":
        raise BrigadeError(f"{lease} is not an active lease held by {holder_name}; claim one before review")
    repo = Path(project_root)
    if mode != "local":
        remote, trunk = split_remote_trunk(trunk_ref)
        try:
            git_run(repo, "fetch", remote, trunk)
        except BrigadeError as error:
            raise BrigadeError(
                f"{ident} cannot check coverage. {error}. "
                f"Fetch {remote} {trunk} and run dish {ident} --state in-review --sha {shlex.quote(sha)} again"
            ) from error
    try:
        base = git_run(repo, "merge-base", trunk_ref, sha)
    except BrigadeError as error:
        raise BrigadeError(
            f"{ident} at {sha} cannot check coverage. {error}. "
            f"Repair the git error above and restore {trunk_ref} if missing. "
            f"Pass a SHA that exists with --sha when retrying dish {ident} --state in-review"
        ) from error
    try:
        changed = changed_paths(repo, base, sha)
    except BrigadeError as error:
        raise BrigadeError(
            f"{ident} at {sha} cannot check coverage. {error}. "
            f"Repair the git error above and restore {trunk_ref} if missing. "
            f"Pass a SHA that exists with --sha when retrying dish {ident} --state in-review"
        ) from error
    files = source_paths(changed, generated)
    lease_paths = list(held.paths)
    outside = [path for path in files if not covered(path, lease_paths)]
    decision = CoverageDecision(tuple(files), tuple(lease_paths), tuple(generated), tuple(outside))
    if decision.uncovered:
        names = ", ".join(decision.uncovered)
        raise BrigadeError(
            f"{ident} at {sha[:12]} changes paths outside {lease}: {names}; "
            f"run dish {ident} --add-paths {shlex.quote(','.join(decision.uncovered))} or split the change"
        )
    return GateInput(lease, paths, sha)


def gate_recheck(restaurant, ident, snapshot):
    """Recheck under the lock that lease id and paths are unchanged."""
    _, dish = restaurant.find("dishes.tsv", ident)
    if dish["lease"] != snapshot.lease or dish["paths"] != snapshot.paths:
        raise BrigadeError(f"{ident} changed while checking; run dish {ident} --state in-review again")
    return None
