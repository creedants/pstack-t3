import json
import re
from pathlib import Path
from brigade_lib import seam
from brigade_lib._store.restaurant import Restaurant
from brigade_lib._store.vocab import ADMIN_DIR, ADMIN_NAME, TABLES
from brigade_lib.compact import effective_provider, existing_cap, sync_meta
from brigade_lib.shared import BrigadeError, slug

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

- You may write the change yourself when it changes one file outside tests, the added and removed lines outside tests are about 40 or fewer across the whole item, and the contract does not call its guarantee safety, privacy, or integrity. Count those lines from `git diff --numstat` against the item's base commit, quote that output in the report, and treat every round and commit as one diff. Past that size, or more than one file outside tests, the code delegate writes the change. The claims check and the gate review are never skipped.

1. A test earns its place when it fails for a defect that no other check reports; name that defect in the test name.
2. A test that pins a reviewed defect stays; remove it only with a mutation that shows another check fails for the same change.
3. Use a rule in the project's static check, when it has one, when the property is visible in the text or the tree without running the code, with one passing and one failing fixture test.
4. Use a test when the property is behavior: exit codes, written files, ordering, refusals.
5. Call the command in the test process through the project's in-process test helper, when it has one; start a process only for a race, a signal, exit status, the environment, or help output, and say which.
6. Never assert a library's own wording.
7. Add a case to an existing table when the setup is the same.
8. A test that sleeps to order two processes names the review finding it pins.
9. Do not add a test for what the project's build already refuses.
10. The reviewer asks of each new test: which change to the code makes this fail and nothing else fail?
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
    # A refused settings write happens before the store is published, so it leaves no store behind and claims no intake.
    if effective_provider(meta) == "claude":
        # Creation without --context-cap adopts an integer cap the user already set and writes nothing.
        # Creation with --context-cap, and creation with no integer cap in the file, writes it.
        held = existing_cap(meta["projectRoot"]) if context_cap is None else None
        if held is not None:
            meta["contextCap"] = held
        else:
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
