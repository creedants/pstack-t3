import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from brigade_lib import seam
from brigade_lib._store.restaurant import Restaurant, holder_prefix
from brigade_lib._store.vocab import ADMIN_DIR, ADMIN_NAME, TABLES
from brigade_lib.compact import effective_provider, sync_meta
from brigade_lib.shared import BrigadeError, clean, slug

MENU = """# Menu: {restaurant}

## Purpose

What this restaurant exists to achieve, in one or two sentences.

## What good looks like

Checkable outcomes, one per line.

## Off the menu

Work this restaurant does not take, even when asked.

## Budget

Any cap on parallel workers, and whether a provider's usage limit switches this restaurant to light mode.
"""


ADMIN_MENU = """# Menu: {restaurant}

## Purpose

Keep the user up to date across every coordinator on this repository, route the user's requests, and settle conflicts between coordinators by published rules.

## Priorities

Coordinator names, highest first, one per line. Only the user changes this list.

## Off the menu

Writing code, landing work, and every decision that belongs to the user: the repository cap, the landing mode, priorities, and purposes.
"""


HOUSE_RULES = """# House rules: {restaurant}

Paste these into every brief verbatim.

1. Write in plain engineering prose. brigade's kitchen terms name its files and commands only. Never use them in replies, reports, commits, or PRs.
2. Work lands only through the repository's landing queue (the landing skill). Workers never merge, rebase shared branches, or push trunk.
"""


PURPOSE_TEMPLATE = "What this restaurant exists to achieve"


OFF_MENU_TEMPLATE = "Work this restaurant does not take"


def menu_section(text, heading):
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


def siblings(directory, project_root):
    """The other coordinators in this project directory on the same project root, by directory name."""
    found = {}
    for meta_path in sorted(directory.parent.glob("*/restaurant.json")):
        if meta_path.parent == directory:
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("projectRoot") == project_root:
            found[meta_path.parent.name] = meta
    return found


def sibling_lines(restaurant):
    blocks = []
    for directory_name, meta in siblings(restaurant.dir, restaurant.meta.get("projectRoot")).items():
        meta_path = restaurant.dir.parent / directory_name / "restaurant.json"
        menu_path = meta_path.parent / "menu.md"
        menu = menu_path.read_text() if menu_path.is_file() else ""
        purpose = menu_section(menu, "Purpose")
        if not purpose or purpose.startswith(PURPOSE_TEMPLATE):
            purpose = "not written yet"
        else:
            purpose = " ".join(purpose.split())
        off = menu_section(menu, "Off the menu")
        if not off or off.startswith(OFF_MENU_TEMPLATE):
            off_line = "not written yet"
        else:
            off_line = "; ".join(line.strip() for line in off.splitlines() if line.strip())
        thread = (meta.get("thread") or "").strip() or "not recorded"
        name = meta.get("restaurant") or meta_path.parent.name
        blocks.append(
            f"sibling {name} ({meta_path.parent}), thread {thread}\n"
            f"  purpose: {purpose}\n"
            f"  off the menu: {off_line}")
    return blocks


def intake_owner(directory, project_root, source):
    for name, meta in siblings(directory, project_root).items():
        if source in (meta.get("intake") or []):
            return name
    return None


def refuse_owned_intake(directory, project_root, sources):
    for source in sources:
        owner = intake_owner(directory, project_root, source)
        if owner:
            raise BrigadeError(f"{owner} already owns intake from {source}; move tickets to it instead")


@contextmanager
def _project_lock(parent):
    """Exclusive lock on a project's store directory, held across a directory claim or move.

    open and open --rename take it, so one target's creation and the other's move never interleave.
    The lock is on the directory itself, so a killed holder releases it and leaves no lock file.
    """
    fd = os.open(parent, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def open_restaurant(root, project_root, name, reporting="milestones", intake=(), workers=None, admin=False, mode=None,
                  context_cap=None, coordinator_provider=None):
    project_root = Path(project_root).resolve()
    if admin:
        name = ADMIN_NAME
    directory = root / slug(project_root.name) / (ADMIN_DIR if admin else slug(name))
    # The project directory is shared by every coordinator on this path slug.
    # The coordinator directory is the claim, so mkdir must fail when it exists.
    directory.parent.mkdir(parents=True, exist_ok=True)
    with _project_lock(directory.parent):
        try:
            directory.mkdir()
        except FileExistsError:
            created = False
        else:
            created = True
    if not created:
        meta = seam.wait_for_meta(directory)
        if meta.get("projectRoot") != str(project_root):
            other = meta.get("projectRoot")
            raise BrigadeError(f"{directory} already holds a coordinator for {other}; pick another --name")
        return Restaurant(directory), False
    try:
        refuse_owned_intake(directory, str(project_root), intake)
    except BrigadeError:
        directory.rmdir()
        raise
    meta = {"restaurant": name, "projectRoot": str(project_root),
            "reporting": reporting, "openedAt": seam.now(), "lastActivityAt": seam.now(),
            "lastReportAt": None, "thread": None, "schedules": {}, "intake": list(intake)}
    if workers is not None:
        meta["workers"] = workers
    if mode is not None:
        meta["mode"] = mode
    if context_cap is not None:
        meta["contextCap"] = context_cap
    if coordinator_provider is not None:
        meta["coordinatorProvider"] = coordinator_provider
    if admin:
        meta["role"] = "admin"
    if (context_cap is not None or coordinator_provider is not None) and effective_provider(meta) == "claude":
        try:
            sync_meta(meta)
        except BrigadeError:
            directory.rmdir()
            raise
    for filename, template in (("menu.md", ADMIN_MENU if admin else MENU), ("house-rules.md", HOUSE_RULES)):
        seam.write_atomic(directory / filename, template.format(restaurant=name))
    for table in TABLES:
        if table == "rulings.tsv" and not admin:
            continue
        header = TABLES[table]
        seam.write_atomic(directory / table, "\t".join(header) + "\n")
    seam.write_atomic(directory / "restaurant.json", json.dumps(meta, indent=2) + "\n")
    return Restaurant(directory), True


NO_QUEUE = ("has no landing contract", "not a git repository")

SETTLED_ENTRIES = ("landed", "bounced")

SETTLED_DISHES = ("merged", "dropped")

HEADINGS = (("menu.md", "# Menu:"), ("house-rules.md", "# House rules:"))

REQUEST_TO = re.compile(r"^to (\S+):")


def _rename_request(args):
    """The project root, old name, new name, and owner one open --rename works from, or a refusal.

    It refuses the executive admin, a missing --name, a new name whose slug is empty or equals --name's,
    and nonempty --intake, --workers, --mode, --context-cap, and --coordinator-provider values.
    """
    if args.admin:
        raise BrigadeError("open --rename renames a coordinator; the executive admin keeps its directory")
    if not args.name:
        raise BrigadeError("open --rename needs --name, the restaurant's current name")
    for flag, value in (("--intake", args.intake), ("--workers", args.workers), ("--mode", args.mode),
                        ("--context-cap", args.context_cap), ("--coordinator-provider", args.coordinator_provider)):
        if value not in (None, ""):
            raise BrigadeError(f"open --rename takes no {flag}")
    new_name = clean(args.rename)
    # A new name with no letter or digit refuses here, in slug's own sentence.
    slug(args.rename)
    if slug(new_name) == slug(args.name):
        raise BrigadeError(f"open --rename {new_name} keeps the directory {slug(args.name)}; "
                           "pick a name with another slug")
    return Path(args.project_root).resolve(), clean(args.name), new_name, args.owner


def _lease_lines(text, prefix):
    """The lease and reservation lines of land.py lease list whose prefix or holder sits under prefix."""
    lines = []
    for line in text.splitlines():
        words = line.split()
        if len(words) >= 3 and words[0][:1] in ("L", "S") and words[2].startswith(prefix):
            lines.append(f"{words[0]} {words[1]} {words[2]}")
    return lines


def _entry_lines(text):
    """The land.py status lines for entries that have not landed or bounced."""
    lines = []
    for line in text.splitlines():
        words = line.split()
        if len(words) >= 2 and words[0][:1] == "E" and words[1] not in SETTLED_ENTRIES:
            lines.append(line.strip())
    return lines


def _held_lines(project_root, prefix):
    """The landing leases, reservations, and queue entries held under a holder prefix, one line each.

    A repository with no landing contract, or a project root that is not a git repository, holds nothing.
    A read that fails another way refuses the rename.
    """
    lines = []
    for read in (("lease", "list"), ("status", "--holder", prefix)):
        result = seam.land_result(project_root, *read)
        if result.returncode != 0:
            error = (result.stderr or "").replace("land: ", "").strip()
            if any(text in error for text in NO_QUEUE):
                return []
            raise BrigadeError(f"could not read the landing queue: {error}")
        if read[0] == "lease":
            lines.extend(_lease_lines(result.stdout, prefix))
        else:
            lines.extend(_entry_lines(result.stdout))
    return lines


def _own_handoff_blocks(rows):
    """The rail rows of this store that name another restaurant, as a moved ticket or a ticket taken from a handoff."""
    blocks = []
    for row in rows:
        if row["state"] == "moved":
            blocks.append(f"{row['id']} is moved to {row['dish'].removeprefix('to:')}")
        elif "(from " in row["source"]:
            blocks.append(f"{row['id']} came from {row['source'].rpartition('(from ')[2].rstrip(')')}")
    return blocks


def _reference_blocks(directory, project_root):
    """The executive admin requests and sibling handoffs that name this store, read before its lock.

    The admin's log.tsv records a request as a row of kind request whose note starts `to <directory name>: `,
    and publishes `inbox/A<n>.line` into this directory. A sibling's rail.tsv names this store in a row whose
    dish is `to:<directory name>`, or whose source contains `(from <store path>/`.
    No process holds two stores' locks, so the siblings are read before this store's lock is taken.
    """
    name = directory.name
    path = f"{directory.parent.name}/{name}"
    blocks = []
    for sibling in siblings(directory, str(project_root)):
        other = Restaurant(directory.parent / sibling)
        for row in other.rows("log.tsv"):
            found = REQUEST_TO.match(row["note"]) if row["kind"] == "request" else None
            if found and found.group(1) == name:
                blocks.append(f"the executive admin recorded {row['id']} for {name}")
        for row in other.rows("rail.tsv"):
            if row["dish"] == f"to:{name}":
                blocks.append(f"{row['id']} in {sibling} is moved to {name}")
            elif f"(from {path}/" in row["source"]:
                blocks.append(f"{row['id']} in {sibling} came from {path}")
    for filename in sorted(file.name for file in (directory / "inbox").glob("*.line")):
        if re.fullmatch(r"A\d+\.line", filename):
            blocks.append(f"inbox/{filename} is an executive admin request")
    return blocks


def _restate_headings(store, old_names, new_name):
    """Rewrite a menu.md or house-rules.md first line that states one of the old names, in this directory."""
    for filename, heading in HEADINGS:
        path = store.dir / filename
        if not path.is_file():
            continue
        first, newline, rest = path.read_text().partition("\n")
        if first != f"{heading} {new_name}" and first in {f"{heading} {old}" for old in old_names}:
            store.write(filename, f"{heading} {new_name}{newline}{rest}")


def _prior_bytes(directory):
    """The bytes of the files a rename rewrites, before it rewrites them."""
    return {name: (directory / name).read_bytes()
            for name in ("restaurant.json", *(filename for filename, _ in HEADINGS))
            if (directory / name).is_file()}


def _restore(directory, prior):
    """Put each prior byte string back after a failed write or a failed move, so the store keeps its bytes."""
    for name, data in prior.items():
        if directory.is_dir():
            (directory / name).write_bytes(data)


def _drop_marker(marker):
    """Remove a reservation marker, if it is still there."""
    try:
        marker.unlink()
    except OSError:
        pass


def _release_target(directory, target, marker):
    """Undo a failed rename's reservation: clear the marker's inode record, remove the empty target, then the marker.

    The marker loses its inode first, so a rerun never adopts a target directory this marker did not record.
    The caller passes the marker _reserve_target returned, so the target is this run's reservation.
    """
    try:
        marker.write_text(json.dumps({"from": directory.name}))
    except OSError:
        pass
    try:
        if target.is_dir() and not any(target.iterdir()):
            target.rmdir()
    except OSError:
        pass
    _drop_marker(marker)


def _marker_from(marker):
    """The directory name a reservation marker records, or None when it records none."""
    try:
        return json.loads(marker.read_text()).get("from")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _marker_is_empty(marker):
    """Whether the marker is empty."""
    try:
        return marker.stat().st_size == 0
    except OSError:
        return False


def _left_reservation(directory, target, marker):
    """Whether the marker and the target are the empty reservation an interrupted rename of this directory left."""
    try:
        recorded = json.loads(marker.read_text())
        return (recorded.get("from") == directory.name
                and recorded.get("inode") == target.stat().st_ino
                and not any(target.iterdir()))
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _reserve_target(directory, target, refused):
    """Claim the marker beside target and create the empty directory this rename fills, or adopt the one it left.

    The caller holds the project directory lock, so no other open or rename creates or moves a directory at target
    until this call returns. A target is removed only when this run created it or adopted it from this directory's
    marker, and a marker is removed only when this run created or adopted it. A rerun adopts only its own recorded
    empty reservation, or an empty marker when the target is missing.
    """
    marker = target.parent / f".rename-{target.name}.json"
    try:
        os.close(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        ours = True
    except FileExistsError:
        ours = False
        if _left_reservation(directory, target, marker):
            return marker
        if _marker_is_empty(marker) and not target.exists() and not target.is_symlink():
            ours = True
        else:
            if _marker_from(marker) != directory.name:
                raise BrigadeError(refused + f"{marker} is not this rename's record; remove it") from None
            if target.exists() or target.is_symlink():
                raise BrigadeError(refused + f"{target} exists; pick another --rename") from None
    created = False
    try:
        marker.write_text(json.dumps({"from": directory.name}))
        target.mkdir()
        created = True
        marker.write_text(json.dumps({"from": directory.name, "inode": target.stat().st_ino}))
    except FileExistsError:
        if ours:
            _drop_marker(marker)
        raise BrigadeError(refused + f"{target} exists; pick another --rename") from None
    except BaseException:
        if created:
            try:
                target.rmdir()
            except OSError:
                pass
        if ours:
            _drop_marker(marker)
        raise
    return marker


def rename_restaurant(root, args):
    """Rename the --name restaurant to --rename, move its store, and return the lines open prints."""
    project_root, name, new_name, owner = _rename_request(args)
    parent = root / slug(project_root.name)
    directory = parent / slug(name)
    target = parent / slug(new_name)
    store = Restaurant(directory, owner)
    # Sibling and landing reads never run under the store lock, so both run before the lock for the rewrite is taken.
    references = _reference_blocks(directory, project_root)
    refused = "open --rename refused, nothing renamed: "
    with store.locked():
        store.fence()
        stored_root = str(store.meta.get("projectRoot") or "")
        if stored_root != str(project_root):
            raise BrigadeError(refused + f"{directory} records the project root {stored_root}; pass that one")
        meta = store.meta
        blocks = [f"{row['id']} is {row['state']}; finish or drop it first"
                  for row in store.rows("dishes.tsv") if row["state"] not in SETTLED_DISHES]
        reports_to = (meta.get("reportsTo") or "").strip()
        if reports_to:
            blocks.append(f"reports to {reports_to}; clear it first")
        blocks.extend(_own_handoff_blocks(store.rows("rail.tsv")))
        blocks.extend(references)
        prefix = holder_prefix(meta)
    blocks.extend(_held_lines(project_root, prefix))
    if blocks:
        raise BrigadeError(refused + "; ".join(blocks))
    before = meta["restaurant"]
    with _project_lock(parent):
        with store.guarded():
            marker = _reserve_target(directory, target, refused)
            prior = _prior_bytes(directory)
            try:
                if before != new_name:
                    meta = store.change_meta(restaurant=new_name)
                _restate_headings(store, (before, name), new_name)
                os.rename(directory, target)
            except BaseException as error:
                _restore(directory, prior)
                _release_target(directory, target, marker)
                if isinstance(error, OSError):
                    raise BrigadeError(refused + str(error)) from None
                raise
            _drop_marker(marker)
    lines = [f"renamed {directory} to {target}",
             f"holder prefix {prefix} is now {holder_prefix(meta)}"]
    if meta.get("schedules"):
        lines.append("recreate schedules " + ", ".join(meta["schedules"])
                     + f" against {target}, then record each new id with set --schedule")
    else:
        lines.append("no schedules recorded")
    return "\n".join(lines)
