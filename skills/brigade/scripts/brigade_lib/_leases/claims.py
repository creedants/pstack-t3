import posixpath
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from brigade_lib import generated, seam
from brigade_lib.shared import BrigadeError, slug
from brigade_lib.store import change_note_dir
from brigade_lib.tickets import tracked_paths

def holder(restaurant, dish):
    return f"{slug(restaurant.meta['restaurant'])}/{dish}"


class ClaimError(BrigadeError):
    """land.py refused a lease claim. fire records the block and adds its own prefix."""


class LeaseReleased(ClaimError):
    """land.py refused a widen because the recorded lease is released or missing."""


@dataclass(frozen=True)
class Claim:
    text: str
    added: tuple


def _claim_norm(raw):
    cleaned = raw.strip().replace("\\", "/")
    if not cleaned:
        return ""
    made = posixpath.normpath(cleaned).lstrip("/")
    if made in (".", ""):
        return ""
    if made == ".." or made.startswith("../"):
        return None
    return made


def fragment_for(meta, branch):
    """The change-note fragment this branch leases, or None when the store records none.

    The fragment is the store's changeNoteDir holding <branch with % as %25 then / as %2F>.md.
    A store with no changeNoteDir leases no fragment. An empty branch leases nothing without reading the key.
    A malformed changeNoteDir is refused when the branch is present.
    """
    if not branch:
        return None
    directory = change_note_dir(meta)
    if not directory:
        return None
    encoded = branch.replace("%", "%25").replace("/", "%2F")
    return f"{directory}/{encoded}.md"


def with_fragment(paths, fragment):
    """The leased paths with the change-note fragment appended, or unchanged when there is none.

    A fragment already listed verbatim is not appended again.
    """
    if not paths or not fragment:
        return paths
    parts = [part for part in paths.split(",") if part]
    if fragment not in parts:
        parts.append(fragment)
    return ",".join(parts)


def missing_checkout_paths(project_root, raw_list):
    tracked = tracked_paths(project_root)
    if tracked is None:
        return []
    seen = set()
    for raw in raw_list:
        made = _claim_norm(raw)
        if not made:
            continue
        if made in tracked.files or made in tracked.directories:
            continue
        seen.add(made)
    return sorted(seen)


def claim_paths(meta, paths, branch, doc_item=False):
    if not paths:
        return Claim("", ())
    fragment = fragment_for(meta, branch)
    project_root = meta.get("projectRoot", "")
    raw_list = [part for part in paths.split(",") if part]
    if not raw_list:
        return Claim("", ())
    try:
        gen_map = generated.read(project_root)
    except KeyboardInterrupt:
        raise
    except BrigadeError:
        raise
    except Exception:
        gen_map = generated.EMPTY
    if not gen_map.rules:
        return Claim(with_fragment(paths, fragment), ())
    seen = set()
    for raw in raw_list:
        made = _claim_norm(raw)
        if made:
            seen.add(made)
    extras = []
    for raw in raw_list:
        made = _claim_norm(raw)
        if not made:
            continue
        try:
            found = generated.expand(gen_map, made, doc_item)
        except KeyboardInterrupt:
            raise
        except Exception:
            continue
        for one in found:
            if one not in seen:
                seen.add(one)
                extras.append(one)
    return Claim(with_fragment(",".join([*raw_list, *extras]), fragment), tuple(extras))


def fired_line(dish, lease, holder_name, added):
    base = f"{dish} (lease {lease} held by {holder_name})" if lease else dish
    if added:
        return f"{base}\nadded {', '.join(added)}"
    return base


def refusal_kind(message):
    if "repository at its cap" in message:
        return "repository"
    return "lease"


def paths_overlap(first, second):
    return any(a == b or a.startswith(b + "/") or b.startswith(a + "/") for a in first for b in second)


def paths_inside(paths, within):
    return all(any(a == b or a.startswith(b + "/") for b in within) for a in paths)


def land_result(project_root, *args):
    if seam.READ_ONLY:
        raise BrigadeError("brigade: next is read-only")
    return subprocess.run([sys.executable, str(seam.LAND), "--repo", str(project_root), *args],
                          capture_output=True, text=True)


def contract_mode(project_root):
    """`lands by merge`, or `no landing contract` when land.py status fails."""
    result = seam.land_result(project_root, "status")
    if result.returncode != 0 or not result.stdout.strip():
        return "no landing contract"
    return f"lands by {result.stdout.split(None, 1)[0]}"


def repository_cap(project_root):
    result = seam.land_result(project_root, "status")
    if result.returncode != 0:
        return None
    match = re.search(r"changes in flight: \d+ of (\d+)", result.stdout)
    return int(match.group(1)) if match else None


def claim_lease(restaurant, dish, paths):
    """Claim the dish's paths in the repository's landing queue. A refused claim fires nothing."""
    result = seam.land_result(restaurant.meta["projectRoot"], "lease", "claim",
                         "--holder", holder(restaurant, dish), "--paths", paths, *restaurant.land_owner())
    if result.returncode != 0:
        raise ClaimError(result.stderr.strip().removeprefix("land: "))
    return result.stdout.strip()


def widen_lease(restaurant, lease, paths):
    """Add paths to one live or expired lease. Raise LeaseReleased for a released or missing lease."""
    result = seam.land_result(restaurant.meta["projectRoot"], "lease", "widen", lease,
                              "--paths", paths, "--ttl-hours", "6", *restaurant.land_owner())
    if result.returncode != 0:
        message = result.stderr.strip().removeprefix("land: ")
        if message.endswith("is released") or message.startswith("no L"):
            raise LeaseReleased(message)
        raise ClaimError(message)
    return result.stdout.strip()


def release_lease(restaurant, lease):
    result = seam.land_result(restaurant.meta["projectRoot"], "lease", "release", lease, *restaurant.land_owner())
    if result.returncode != 0:
        diagnostic = (result.stderr or result.stdout).strip().removeprefix("land: ")
        print(f"brigade: warning: could not release {lease}: {diagnostic}", file=sys.stderr)


def lease_check(project_root, holder_name, paths):
    result = seam.land_result(project_root, "lease", "check", "--holder", holder_name, "--paths", paths)
    diagnostic = result.stderr.strip().removeprefix("land: ")
    return result.returncode, result.stdout.strip(), diagnostic


def lease_states(project_root):
    """Each held lease's state in the landing queue, active or submitted, by id."""
    result = seam.land_result(project_root, "lease", "list")
    return dict(line.split()[:2] for line in result.stdout.splitlines() if line.startswith("L"))


def holder_leases(project_root, holder_name):
    """This holder's live active lease ids, newest first. Empty when the list fails."""
    result = seam.land_result(project_root, "lease", "list")
    if result.returncode != 0:
        return []
    found = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].startswith("L") and parts[1] == "active" and parts[2] == holder_name:
            found.append(parts[0])
    return sorted(found, key=lambda lease: int(lease.lstrip("L")), reverse=True)


def renew_line(restaurant, dish):
    """Renew a live lease. Returns the line to print, or None, and whether the landing store answered."""
    lease = dish["lease"]
    result = seam.land_result(restaurant.meta["projectRoot"], "lease", "renew", lease, "--if-live", *restaurant.land_owner())
    error = result.stderr.strip().removeprefix("land: ")
    if result.returncode == 0 or error.startswith(f"{lease} is submitted"):
        return None, True
    if error == f"{lease} is released":
        return f"{dish['id']}: lease {lease} was released; claim again before submitting", True
    if error.startswith(f"{lease} expired at "):
        return f"{dish['id']}: lease {lease} expired; stop its worker, then run lease renew {lease}", True
    return f"{dish['id']}: could not renew {lease}: {error or result.stdout.strip()}", False


ENTRY_LINE = re.compile(r"^E(\d+) (\S+) \((\S+), ([0-9a-f]+)\)(.*)$")


def entry_line(restaurant, dish):
    """The queue's entry for this dish: its holder's highest entry at exactly the dish's SHA.

    Returns the line to print, or None, and whether that entry holds the dish's submission.
    """
    sha = dish["sha"].lower()
    if not sha:
        return (None if dish["state"] == "passed" else f"{dish['id']}: queued, but no SHA recorded"), False
    result = seam.land_result(restaurant.meta["projectRoot"], "status", "--holder", holder(restaurant, dish["id"]), "--sha", sha)
    if result.returncode != 0:
        return f"{dish['id']}: could not read the queue: {result.stderr.strip().removeprefix('land: ')}", False
    found = None
    for line in result.stdout.splitlines():
        found = ENTRY_LINE.match(line) or found
    if not found:
        return (None if dish["state"] == "passed" else f"{dish['id']}: queued, but no entry at {sha[:12]}"), False
    number, state, rest = found.group(1), found.group(2), found.group(5)
    ident = dish["id"]
    if state == "bounced":
        return f"{ident}: E{number} bounced{rest}", False
    if dish["state"] == "passed":
        return f"{ident}: E{number} already submitted; mark it queued", True
    if state == "landed":
        return f"{ident}: landed as E{number} ({rest.removeprefix(' as ')}); mark it merged", True
    if state == "awaiting-merge":
        return f"{ident}: E{number} awaiting merge {rest.strip()}; watch that PR", True
    return f"{ident}: E{number} {state}", True


@dataclass(frozen=True)
class FragmentGap:
    """Uncovered change-note fragment and the lease that should cover it.

    state is active or inactive or submitted. covered is true when the recorded paths already list the fragment.
    """
    lease: str
    state: str
    fragment: str
    covered: bool


def fragment_lines(restaurant, ident, branch):
    """The command that widens the lease to the new branch's change-note fragment. It is printed, not run.

    widen owns claim record release and race checks. None when the store records no change-note
    directory, or when the item's recorded paths already list the fragment verbatim.
    """
    fragment = fragment_for(restaurant.meta, branch)
    if fragment is None:
        return None
    states = lease_states(restaurant.meta["projectRoot"])
    with restaurant.checked():
        dish = restaurant.find("dishes.tsv", ident)[1]
    lease = dish["lease"]
    covered = with_fragment(dish["paths"], fragment) == dish["paths"]
    raw = states.get(lease)
    state = "active" if raw == "active" else "submitted" if raw == "submitted" else "inactive"
    gap = FragmentGap(lease, state, fragment, covered)
    if not gap.lease or dish["state"] in ("merged", "dropped") or gap.covered:
        return None
    if gap.state == "submitted":
        return (f"{ident}: {gap.lease} is submitted, so it cannot take {gap.fragment}; the submitted commit lands as it is. "
                f"Run dish {ident} --branch {branch} again if its entry bounces")
    wording = "does not cover" if gap.state == "active" else "is not active, so nothing covers"
    return "\n".join([
        f"{ident}: {gap.lease} {wording} {gap.fragment}; cover it with this command:",
        f"  $B dish {ident} --add-paths {shlex.quote(gap.fragment)}",
    ])
