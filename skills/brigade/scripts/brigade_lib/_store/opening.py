import json
import re
from pathlib import Path
from brigade_lib import seam
from brigade_lib._store.restaurant import Restaurant, holder_prefix
from brigade_lib._store.vocab import ADMIN_DIR, ADMIN_NAME, COUNTED_STATES, TABLES
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


def open_restaurant(root, project_root, name, reporting="milestones", intake=(), workers=None, admin=False, mode=None,
                  context_cap=None, coordinator_provider=None):
    project_root = Path(project_root).resolve()
    if admin:
        name = ADMIN_NAME
    directory = root / slug(project_root.name) / (ADMIN_DIR if admin else slug(name))
    # The project directory is shared by every coordinator on this path slug.
    # The coordinator directory is the claim, so mkdir must fail when it exists.
    directory.parent.mkdir(parents=True, exist_ok=True)
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


def _rename_request(args):
    """The project root, old name, new name, and owner one open --rename works from, or a refusal.

    It refuses the executive admin, a missing --name, a new name whose slug is empty or equals --name's,
    and --intake, --workers, --mode, --context-cap, and --coordinator-provider.
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


def rename_restaurant(root, args):
    """Rename the --name restaurant to --rename, move its store, and return the lines open prints."""
    project_root, name, new_name, owner = _rename_request(args)
    parent = root / slug(project_root.name)
    directory = parent / slug(name)
    target = parent / slug(new_name)
    store = Restaurant(directory, owner)
    # A landing read never runs under the store lock, so the blocks below are gathered first.
    with store.locked():
        store.fence()
        blocks = [f"{row['id']} is {row['state']}; finish or drop it first"
                  for row in store.rows("dishes.tsv") if row["state"] in COUNTED_STATES]
        prefix = holder_prefix(store.meta)
    blocks.extend(_held_lines(project_root, prefix))
    refused = "open --rename refused, nothing renamed: "
    if blocks:
        raise BrigadeError(refused + "; ".join(blocks))
    with store.guarded():
        if target.exists():
            blocks.append(f"{target} already holds a coordinator; pick another --rename")
        if blocks:
            raise BrigadeError(refused + "; ".join(blocks))
        try:
            target.mkdir()
        except FileExistsError:
            raise BrigadeError(refused + f"{target} already holds a coordinator; pick another --rename") from None
        try:
            directory.rename(target)
        except OSError:
            target.rmdir()
            raise
    renamed = Restaurant(target, owner)
    before = renamed.meta["restaurant"]
    meta = renamed.change_meta(restaurant=new_name)
    for filename, heading in (("menu.md", "# Menu:"), ("house-rules.md", "# House rules:")):
        path = target / filename
        stated = path.read_text().split("\n", 1) if path.is_file() else []
        if stated and stated[0] == f"{heading} {before}":
            renamed.write(filename, "\n".join([f"{heading} {new_name}"] + stated[1:]))
    lines = [f"renamed {directory} to {target}",
             f"holder prefix {prefix} is now {holder_prefix(meta)}"]
    if meta.get("schedules"):
        lines.append("recreate schedules " + ", ".join(meta["schedules"])
                     + f" against {target}, then record each new id with set --schedule")
    else:
        lines.append("no schedules recorded")
    return "\n".join(lines)
