from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from . import layout

new_file = None

@dataclass(frozen=True)
class LinkRec:
    raw: object
    path: str
    harnesses: tuple
    checkout: str | None

@dataclass(frozen=True)
class BackupRec:
    raw: object
    original: str
    backup: str
    harnesses: tuple

@dataclass(frozen=True)
class View:
    claims: dict
    links: tuple
    backups: tuple

@dataclass(frozen=True)
class Mine:
    path: str
    harnesses: tuple
    claim_paths: tuple
    tagged: tuple
    voucher: object

class Unreadable(Exception):
    """A record file that cannot be used, or that could not be removed. Its text is the line install and uninstall exit 1 with."""

def harnesses_for(path, entry, scope, user):
    if isinstance(entry, dict) and isinstance(entry.get("harnesses"), list):
        return layout.ordered(entry["harnesses"])
    if isinstance(entry, dict) and isinstance(entry.get("harness"), str) and entry["harness"] in layout.HARNESSES:
        return (entry["harness"],)
    parent = os.path.realpath(os.path.dirname(path))
    found = [harness for harness, directory in layout.skill_dirs(scope, user).items() if os.path.realpath(directory) == parent]
    return layout.ordered(found) if found else layout.HARNESSES

def parse_link(entry, scope, user, file):
    if isinstance(entry, str):
        return LinkRec(entry, layout.anchored(file, entry), harnesses_for(entry, None, scope, user), None)
    if not isinstance(entry, dict):
        return None
    path = entry.get("path")
    if not isinstance(path, str):
        return None
    checkout = entry.get("checkout") if isinstance(entry.get("checkout"), str) else None
    return LinkRec(entry, layout.anchored(file, path), harnesses_for(path, entry, scope, user), checkout)

def parse_backup(entry, scope, user, file):
    if not isinstance(entry, dict):
        return None
    original, backup = entry.get("original"), entry.get("backup")
    if not isinstance(original, str) or not isinstance(backup, str):
        return None
    return BackupRec(entry, layout.anchored(file, original), backup, harnesses_for(original, entry, scope, user))

def read_object(path):
    """Read one record file. Return its JSON object, or None when no file is at `path`.

    No file is there when the read fails with an errno in ABSENT. Any other OSError from the read, and any content
    that is not a JSON object, raises Unreadable.
    """
    try:
        data = json.loads(path.read_text())
    except OSError as error:
        if error.errno in layout.ABSENT:
            return None
        raise Unreadable(f"{path} could not be read ({error.strerror}); clear that error and rerun") from None
    except ValueError:
        raise Unreadable(f"{path} is not valid JSON; fix or move it and rerun") from None
    if not isinstance(data, dict):
        raise Unreadable(f"{path} is not a JSON object; fix or move it and rerun")
    return data

def legacy_lists(path):
    """Return the manifest's object and its links and backups lists, or None when `read_object` finds no file."""
    data = read_object(path)
    if data is None:
        return None
    found = []
    for key in ("links", "backups"):
        value = data.get(key, [])
        if not isinstance(value, list):
            raise Unreadable(f"{path} has a {key} entry that is not a list; fix or move it and rerun")
        found.append(value)
    return data, found[0], found[1]

def read_legacy(state, scope, user):
    file = Path(state) / layout.LEGACY_NAME
    found = legacy_lists(file)
    if found is None:
        return (), ()
    _data, raw_links, raw_backups = found
    links = tuple(item for item in (parse_link(entry, scope, user, file) for entry in raw_links) if item)
    backups = tuple(item for item in (parse_backup(entry, scope, user, file) for entry in raw_backups) if item)
    return links, backups

def load(state, scope, user):
    root = str(layout.ROOT)
    links, backups = read_legacy(state, scope, user)
    return View(current_claims(state, root), links, backups)

def records(view, root):
    parts = {}

    def bucket(path):
        return parts.setdefault(layout.slot_of(path), {"claims": [], "tagged": [], "untagged": []})

    for path, harnesses in view.claims.items():
        bucket(path)["claims"].append((path, harnesses))
    for link in view.links:
        if link.checkout == root:
            bucket(link.path)["tagged"].append(link)
        elif link.checkout is None:
            bucket(link.path)["untagged"].append(link)
    present = stacks(view)
    found = {}
    for slot, group in parts.items():
        live = None
        for path, _harnesses in group["claims"]:
            if layout.proves(root, path, path):
                live = path
                break
        if live is None:
            for link in group["tagged"] + group["untagged"]:
                if layout.proves(root, link.path, link.path):
                    live = link.path
                    break
        proving_live = live is not None
        proving = proving_live or set_aside(present, root, slot)
        claims = group["claims"]
        tagged = group["tagged"]
        untagged = group["untagged"]
        if not (claims or tagged or (untagged and proving)):
            continue
        if live is None:
            if claims:
                live = claims[0][0]
            elif tagged:
                live = tagged[0].path
            else:
                live = untagged[0].path
        harnesses = layout.ordered(
            [harness for _path, owned in claims for harness in owned]
            + [harness for link in tagged + untagged for harness in link.harnesses]
        )
        voucher = untagged[-1].raw if untagged and proving_live and not tagged else None
        found[slot] = Mine(live, harnesses, tuple(path for path, _owned in claims), tuple(tagged), voucher)
    return found

def set_aside(present, root, slot):
    """Whether a present backup row of `slot` holds this checkout's link. `present` is `stacks(view)`."""
    return any(layout.proves(root, row.backup, row.original) for row in present.get(slot, ()))

def stacks(view):
    found = {}
    for row in view.backups:
        if os.path.lexists(row.backup):
            found.setdefault(layout.slot_of(row.original), []).append(row)
    return found

def atomic_write(directory, name, text):
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = new_file(directory)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(temporary, directory / name)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise

def write_claims(state, root, claims):
    path = layout.owner_path(state, root)
    if not claims:
        try:
            path.unlink()
        except OSError as error:
            if error.errno not in layout.ABSENT:
                raise Unreadable(f"{path} could not be removed ({error.strerror}); clear that error and rerun") from None
        return
    body = {"checkout": root, "links": {key: {"harnesses": list(claims[key])} for key in claims}}
    atomic_write(path.parent, path.name, json.dumps(body, indent=2) + "\n")

def current_claims(state, root):
    path = layout.owner_path(state, root)
    data = read_object(path)
    if data is None:
        return {}
    recorded = data.get("checkout")
    if recorded != root:
        raise Unreadable(f"{path} records {recorded}, not this checkout")
    claims = claims_in(data)
    for key in claims:
        layout.anchored(path, key)
    return claims

def claims_in(data):
    links = data.get("links")
    if not isinstance(links, dict):
        return {}
    claims = {}
    for key, value in links.items():
        harnesses = value.get("harnesses") if isinstance(value, dict) else None
        if isinstance(key, str) and isinstance(harnesses, list):
            claims[key] = layout.ordered(harnesses)
    return claims

def patch_legacy(state, add_links, add_backups, remove_links, remove_backups, adding):
    """Return the link and backup rows this call appended."""
    if not add_links and not add_backups and not remove_links and not remove_backups:
        return (), ()
    path = Path(state) / layout.LEGACY_NAME
    found = legacy_lists(path)
    # Older installers still read this file, so a cleanup leaves empty lists in place.
    if found is not None:
        data, links, backups = found
    elif adding:
        data, links, backups = {}, [], []
    else:
        return (), ()
    added_links, added_backups = [], []
    changed = False
    if adding:
        for row in add_links:
            if isinstance(row, dict) and any(
                isinstance(have, dict) and have.get("path") == row.get("path") and have.get("checkout") == row.get("checkout")
                for have in links
            ):
                continue
            links.append(row)
            added_links.append(row)
            changed = True
        for row in add_backups:
            backups.append(row)
            added_backups.append(row["backup"])
            changed = True
    else:
        for raw in remove_links:
            for index in range(len(links) - 1, -1, -1):
                if links[index] == raw:
                    del links[index]
                    changed = True
                    break
        for backup in remove_backups:
            for index in range(len(backups) - 1, -1, -1):
                have = backups[index]
                if isinstance(have, dict) and have.get("backup") == backup:
                    del backups[index]
                    changed = True
                    break
    if changed:
        data["links"] = links
        data["backups"] = backups
        atomic_write(Path(state), layout.LEGACY_NAME, json.dumps(data, indent=2) + "\n")
    return tuple(added_links), tuple(added_backups)

def add_records(step, state, root):
    """Save a step's adds before its act. Return a function that takes back exactly the records that were new."""
    previous = {}
    if step.add_claims:
        claims = current_claims(state, root)
        for path, harnesses in step.add_claims:
            previous[path] = claims.get(path)
            claims[path] = layout.ordered(harnesses)
        write_claims(state, root, claims)
    links, backups = patch_legacy(state, step.add_links, step.add_backups, (), (), True)

    def undo():
        patch_legacy(state, (), (), links, backups, False)
        if previous:
            claims = current_claims(state, root)
            for path, harnesses in previous.items():
                if harnesses is None:
                    claims.pop(path, None)
                else:
                    claims[path] = harnesses
            write_claims(state, root, claims)

    return undo

def remove_records(step, state, root):
    # Owner file first on add and last on remove, so a crash keeps an extra claim.
    patch_legacy(state, (), (), step.remove_links, step.remove_backups, False)
    if step.remove_claims:
        claims = current_claims(state, root)
        for path in step.remove_claims:
            claims.pop(path, None)
        write_claims(state, root, claims)

def buried(state, root, path):
    """Whether a backup row now holds this checkout's entry for the slot of `path`."""
    found = legacy_lists(Path(state) / layout.LEGACY_NAME)
    if found is None:
        return False
    _data, _links, backups = found
    return any(
        isinstance(row, dict) and isinstance(row.get("original"), str) and isinstance(row.get("backup"), str)
        and layout.slot_of(row["original"]) == layout.slot_of(path) and layout.proves(root, row["backup"], row["original"])
        for row in backups
    )
