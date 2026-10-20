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

def in_flight(db):
    """Leases not yet released: every submitted lease and every active lease that has not expired."""
    return db.execute("SELECT * FROM lease WHERE state = 'submitted' OR (state = 'active' AND expires > ?) ORDER BY id",
                      (seam.stamp(),)).fetchall()


def holders_of(rows):
    return ", ".join(sorted({row["holder"] for row in rows}))


def lease_paths(row):
    return row["paths"].replace("\n", ", ") or "(whole repo)"


def held_on(row):
    return f"L{row['id']} held by {row['holder']} on {lease_paths(row)}"


def standing(db):
    """Reservations that still refuse other holders: waiting, or armed and not expired."""
    return db.execute("SELECT * FROM reservation WHERE state = 'standing' AND (armed = '' OR expires > ?) ORDER BY id",
                      (seam.stamp(),)).fetchall()


def taken_ids(text):
    return [int(part) for part in text.split("\n") if part]


def add_taken(text, lease):
    ids = taken_ids(text)
    if lease not in ids:
        ids.append(lease)
    return "\n".join(str(ident) for ident in ids)


def counts(row, leases):
    """An armed reservation counts as one change in flight while the winner holds no live lease taken from it."""
    live = {lease["id"] for lease in leases}
    return bool(row["armed"]) and not any(ident in live for ident in taken_ids(row["taken"]))


def under(prefix, leases, reservations):
    return sum(lease["holder"].startswith(prefix) for lease in leases) + sum(r["prefix"].startswith(prefix) for r in reservations)


def reserved_for(row):
    hours = "1 hour" if row["ttl"] == 1 else f"{row['ttl']:g} hours"
    until = row["expires"][:16] if row["armed"] else f"{hours} after it arms"
    return f"paths reserved for {row['prefix']} by ruling {row['ruling']} until {until}"


def arm_reservations(store, db, leases, cap):
    """Start the clock of each waiting reservation, oldest first, once its paths are free and there is room for it."""
    shares = db.execute("SELECT * FROM share").fetchall()
    for row in standing(db):
        paths = row["paths"].split("\n")
        if row["armed"] or any(not lease["holder"].startswith(row["prefix"]) and seam.overlaps(paths, lease["paths"].split("\n"))
                               for lease in leases):
            continue
        counting = [r for r in standing(db) if counts(r, leases)]
        if cap and len(leases) + len(counting) >= cap:
            continue
        if any(row["prefix"].startswith(share["prefix"]) and under(share["prefix"], leases, counting) >= share["count"]
               for share in shares):
            continue
        moment = seam.now()
        db.execute("UPDATE reservation SET armed = ?, expires = ? WHERE id = ?",
                   (seam.stamp(moment), seam.stamp(moment + timedelta(hours=row["ttl"])), row["id"]))
        store.log(db, "reservation", row["id"], "armed")


def admission(store, db, holder, wanted, renewing=None, excluding=()):
    """Arm what can arm, then test a lease on these paths for holder.

    Returns the other holders' overlapping leases, a refusal, and the standing reservations the lease takes from.
    Run it inside the transaction that writes the lease, so two claims cannot both pass. `lease claim` and
    `lease renew` commit when refused, so arming is never lost to them. `lease widen` raises inside its
    transaction, which rolls arming back. lease check runs it in
    Store.trial, which rolls back, so a check arms no reservation and adds no log row. renewing is an expired
    lease id. A reservation that already lists it is taken again and left out of the count, so that renewal
    fits a full cap. excluding names lease ids left out of the cap and share count only. Arming and the clash
    and reservation checks still see every lease in flight, because the caller releases or rewrites the excluded
    leases in the same transaction that writes this one.
    """
    leases, cap = in_flight(db), store.contract.get("cap")
    arm_reservations(store, db, leases, cap)
    clash = [row for row in leases if row["holder"] != holder and seam.overlaps(wanted, row["paths"].split("\n"))]
    if clash:
        return clash, "", []
    reserved = [row for row in standing(db) if seam.overlaps(wanted, row["paths"].split("\n"))]
    blocked = [row for row in reserved if not holder.startswith(row["prefix"])]
    if blocked:
        return [], "; ".join(reserved_for(row) for row in blocked), []
    taken = [row["id"] for row in reserved]
    if renewing is not None:
        taken += [row["id"] for row in standing(db) if row["id"] not in taken and renewing in taken_ids(row["taken"])]
    counting = [row for row in leases if row["id"] not in excluding]
    others = [row for row in standing(db) if counts(row, leases) and row["id"] not in taken]
    count = len(counting) + len(others)
    if cap and count >= cap:
        names = ([holders_of(counting)] if counting else []) + [f"S{row['id']} for {row['prefix']}" for row in others]
        return [], f"repository at its cap: {count} of {cap} changes in flight ({', '.join(names)})", []
    for share in db.execute("SELECT * FROM share ORDER BY prefix"):
        used = under(share["prefix"], counting, others)
        if holder.startswith(share["prefix"]) and used >= share["count"]:
            return [], f"{share['prefix']} is at its share: {used} of {share['count']}", []
    return [], "", taken


def take(store, db, taken, lease, wanted):
    """Release the reserved paths a winner's lease covers. The rest stay reserved."""
    for ident in taken:
        row = db.execute("SELECT * FROM reservation WHERE id = ?", (ident,)).fetchone()
        left = [path for path in row["paths"].split("\n") if not seam.covered(path, wanted)]
        state = "standing" if left else "claimed"
        db.execute("UPDATE reservation SET paths = ?, taken = ?, state = ? WHERE id = ?",
                   ("\n".join(left), add_taken(row["taken"], lease), state, ident))
        store.log(db, "reservation", ident, state, f"L{lease}")


def wanted_paths(paths):
    return sorted({seam.canonical(p) for p in paths.split(",")})


def parse_owner(text):
    match = re.fullmatch(r"(.+/)@(\d+)", text or "")
    if not match:
        raise seam.LandError(f"{text!r} is not an owner such as docs/@2")
    return match.group(1), int(match.group(2))


def check_owner(db, holder, owner):
    """Refuse a write by a replaced coordinator. Run it inside the write's transaction."""
    if owner is not None:
        prefix, generation = parse_owner(owner)
        if not holder.startswith(prefix):
            raise seam.LandError(f"owner {owner} does not cover holder {holder}")
    for row in db.execute("SELECT * FROM owner ORDER BY prefix"):
        if not holder.startswith(row["prefix"]):
            continue
        if owner is None or prefix != row["prefix"]:
            raise seam.LandError(f"{holder} is under {row['prefix']} at generation {row['generation']}; "
                            f"pass --owner {row['prefix']}@{row['generation']}")
        if generation < row["generation"]:
            raise seam.LandError(f"owner {owner} is stale; {prefix} is at generation {row['generation']}")


def check_ruling_owner(db, owner):
    """Refuse a ruling write by a replaced admin. Run it first inside the write's transaction."""
    prefix, generation = parse_owner(owner)
    row = db.execute("SELECT generation FROM owner WHERE prefix = ?", (prefix,)).fetchone()
    if row and generation < row["generation"]:
        raise seam.LandError(f"owner {owner} is stale; {prefix} is at generation {row['generation']}")


def holder_prefix(prefix):
    if not prefix.endswith("/"):
        raise seam.LandError(f"{prefix!r} is not a holder prefix such as docs/")


def raise_floor(store, prefix, generation):
    holder_prefix(prefix)
    with store.tx() as db:
        row = db.execute("SELECT generation FROM owner WHERE prefix = ?", (prefix,)).fetchone()
        if row and row["generation"] > generation:
            raise seam.LandError(f"{prefix} is at generation {row['generation']}; a floor never lowers")
        if not row or row["generation"] < generation:
            db.execute("INSERT OR REPLACE INTO owner VALUES (?, ?)", (prefix, generation))
            store.log(db, "owner", generation, "raised", prefix)
    return f"{prefix} at generation {generation}"


def lease_claim(store, holder, paths, ttl_hours, owner=None):
    wanted = wanted_paths(paths)
    with store.tx() as db:
        check_owner(db, holder, owner)
        clash, refusal, taken = admission(store, db, holder, wanted)
        if not clash and not refusal:
            cursor = db.execute("INSERT INTO lease (at, holder, paths, state, expires) VALUES (?, ?, ?, 'active', ?)",
                                (seam.stamp(), holder, "\n".join(wanted), seam.stamp(seam.now() + timedelta(hours=ttl_hours))))
            store.log(db, "lease", cursor.lastrowid, "active", holder)
            take(store, db, taken, cursor.lastrowid, wanted)
    if clash:
        raise seam.LandError("paths overlap " + "; ".join(held_on(row) for row in clash))
    if refusal:
        raise seam.LandError(refusal)
    return f"L{cursor.lastrowid}"


def lease_check(store, holder, paths):
    with store.trial() as db:
        clash, refusal, _taken = admission(store, db, holder, wanted_paths(paths))
    if clash or refusal:
        return "\n".join(held_on(row) for row in clash) or refusal, 1
    return "free", 0


def lease_renew(store, number, ttl_hours, if_live, owner=None):
    """Extend a live lease. An expired lease is admitted again, as a new claim on its paths would be."""
    clash, refusal = [], ""
    with store.tx() as db:
        row = db.execute("SELECT * FROM lease WHERE id = ?", (number,)).fetchone()
        if not row:
            raise seam.LandError(f"no L{number}")
        check_owner(db, row["holder"], owner)
        if row["state"] == "submitted":
            raise seam.LandError(f"L{number} is submitted; the queue holds it")
        if row["state"] == "released":
            raise seam.LandError(f"L{number} is released")
        expired = row["expires"] <= seam.stamp()
        if expired and if_live:
            raise seam.LandError(f"L{number} expired at {row['expires'][:16]}")
        taken = []
        if expired:
            clash, refusal, taken = admission(store, db, row["holder"], row["paths"].split("\n"), renewing=number)
        if not clash and not refusal:
            db.execute("UPDATE lease SET expires = ? WHERE id = ?", (seam.stamp(seam.now() + timedelta(hours=ttl_hours)), number))
            store.log(db, "lease", number, "renewed", "after it expired" if expired else "")
            take(store, db, taken, number, row["paths"].split("\n"))
    if clash:
        covers = " and ".join(f"L{c['id']} held by {c['holder']} now covers {lease_paths(c)}" for c in clash)
        raise seam.LandError(f"L{number} expired and {covers}; claim again after {'it is' if len(clash) == 1 else 'they are'} released")
    if refusal:
        raise seam.LandError(refusal)
    return f"L{number} renewed"


def lease_widen(store, number, paths, ttl_hours=6, owner=None):
    """Add paths to one lease in place, folding that holder's other active unexpired leases into it.

    The kept expiry is the latest of its own expiry, the TTL when expired, and every folded expiry. An expired lease
    whose TTL expiry is at or before the post-admission clock reading is refused. The holder's other live leases are
    released in the same transaction, and the standing reservations they took name this lease before that release.
    """
    wanted = wanted_paths(paths)
    with store.tx() as db:
        row = db.execute("SELECT * FROM lease WHERE id = ?", (number,)).fetchone()
        if not row:
            raise seam.LandError(f"no L{number}")
        check_owner(db, row["holder"], owner)
        if row["state"] == "submitted":
            raise seam.LandError(f"L{number} is submitted; the queue holds it")
        if row["state"] == "released":
            raise seam.LandError(f"L{number} is released")
        orphans = [lease for lease in in_flight(db)
                   if lease["holder"] == row["holder"] and lease["state"] == "active" and lease["id"] != number]
        merged = sorted({*row["paths"].split("\n"), *(path for lease in orphans for path in lease["paths"].split("\n")), *wanted})
        excluding = {number, *(lease["id"] for lease in orphans)}
        clash, refusal, taken = admission(store, db, row["holder"], merged, excluding=excluding)
        if clash:
            raise seam.LandError("paths overlap " + "; ".join(held_on(held) for held in clash))
        if refusal:
            raise seam.LandError(refusal)
        added = [path for path in merged if path not in row["paths"].split("\n")]
        moment = seam.now()
        expires = row["expires"]
        if expires <= seam.stamp(moment):
            expires = seam.stamp(moment + timedelta(hours=ttl_hours))
            if expires <= seam.stamp(moment):
                raise seam.LandError(f"L{number} expired and a TTL of {ttl_hours:g} hours keeps it expired")
        expires = max([expires, *(lease["expires"] for lease in orphans)])
        folded = {lease["id"] for lease in orphans}
        held = [item["id"] for item in standing(db) if folded & set(taken_ids(item["taken"]))]
        db.execute("UPDATE lease SET paths = ?, expires = ? WHERE id = ?", ("\n".join(merged), expires, number))
        take(store, db, sorted({*taken, *held}), number, merged)
        for orphan in orphans:
            release_lease(store, db, orphan["id"], f"folded into L{number}")
        note = ", ".join(added)
        if orphans:
            folded_ids = ", ".join(f"L{orphan['id']}" for orphan in orphans)
            note = f"{note}, folded {folded_ids}" if note else f"folded {folded_ids}"
        store.log(db, "lease", number, "widened", note)
    return f"L{number} widened"


def lease_reserve(store, prefix, paths, ruling, ttl_hours, owner):
    holder_prefix(prefix)
    wanted = wanted_paths(paths)
    # take() never drops an empty path for a narrower lease, so reserving one blocks every other holder until expiry.
    if "" in wanted:
        raise seam.LandError("a reservation names the contested paths; it cannot hold the whole repository")
    with store.tx() as db:
        check_ruling_owner(db, owner)
        row = db.execute("SELECT * FROM reservation WHERE ruling = ?", (ruling,)).fetchone()
        if row:
            state = reservation_state(row)
            if state in ("expired", "lifted"):
                raise seam.LandError(f"S{row['id']} for ruling {ruling} {'expired' if state == 'expired' else 'was lifted'}")
            if state == "claimed":
                return f"S{row['id']} was claimed in full"
            return f"S{row['id']}"
        clash = [r for r in standing(db) if seam.overlaps(wanted, r["paths"].split("\n"))]
        if clash:
            raise seam.LandError("; ".join(f"paths overlap S{r['id']} reserved for {r['prefix']} by ruling {r['ruling']} on {lease_paths(r)}"
                                      for r in clash))
        cursor = db.execute("INSERT INTO reservation (at, prefix, paths, ruling, ttl, state) VALUES (?, ?, ?, ?, ?, 'standing')",
                            (seam.stamp(), prefix, "\n".join(wanted), ruling, ttl_hours))
        store.log(db, "reservation", cursor.lastrowid, "waiting", f"{prefix} by {ruling}")
        arm_reservations(store, db, in_flight(db), store.contract.get("cap"))
    return f"S{cursor.lastrowid}"


def reservation_state(row):
    if row["state"] != "standing":
        return row["state"]
    if not row["armed"]:
        return "waiting"
    return "armed" if row["expires"] > seam.stamp() else "expired"


def lease_unreserve(store, number, owner):
    """Every outcome but a missing reservation exits 0, so a replacing ruling converges."""
    with store.tx() as db:
        check_ruling_owner(db, owner)
        row = db.execute("SELECT * FROM reservation WHERE id = ?", (number,)).fetchone()
        if not row:
            raise seam.LandError(f"no S{number}")
        state = reservation_state(row)
        if state == "claimed":
            return f"S{number} was claimed in full"
        if state == "expired":
            return f"S{number} expired at {row['expires'][:16]}"
        if state != "lifted":
            db.execute("UPDATE reservation SET state = 'lifted' WHERE id = ?", (number,))
            store.log(db, "reservation", number, "lifted")
    return f"S{number} lifted"


def lease_list(store):
    lines = [f"L{r['id']} {r['state']} {r['holder']} until {r['expires'][:16]}: {lease_paths(r)}" for r in in_flight(store.db)]
    for row in standing(store.db):
        until = f" until {row['expires'][:16]}" if row["armed"] else ""
        lines.append(f"S{row['id']} {reservation_state(row)} {row['prefix']} by {row['ruling']}{until}: {lease_paths(row)}")
    return "\n".join(lines) or "no leases held"


def change_share(store, prefix, count, clear, owner):
    holder_prefix(prefix)
    if clear and count != 0:
        raise seam.LandError("--clear takes 0")
    if count < 0:
        raise seam.LandError(f"share {count} is negative")
    with store.tx() as db:
        check_ruling_owner(db, owner)
        row = db.execute("SELECT count FROM share WHERE prefix = ?", (prefix,)).fetchone()
        if clear:
            if row:
                db.execute("DELETE FROM share WHERE prefix = ?", (prefix,))
                store.log(db, "share", 0, "cleared", prefix)
            return f"{prefix} share cleared"
        if row and row["count"] == count:
            return f"{prefix} share is {count}"
        cap = store.contract.get("cap")
        total = count + db.execute("SELECT coalesce(sum(count), 0) FROM share WHERE prefix != ?", (prefix,)).fetchone()[0]
        if cap and total > cap:
            raise seam.LandError(f"shares would add up to {total}, over the cap of {cap}")
        db.execute("INSERT OR REPLACE INTO share VALUES (?, ?)", (prefix, count))
        store.log(db, "share", count, "set", prefix)
    return f"{prefix} share is {count}"


def lease_release(store, number, owner=None):
    with store.tx() as db:
        row = db.execute("SELECT * FROM lease WHERE id = ?", (number,)).fetchone()
        if row:
            check_owner(db, row["holder"], owner)
        if not row or row["state"] != "active":
            raise seam.LandError(f"L{number} is not active; a submitted lease is released when its entry lands or bounces")
        release_lease(store, db, number)
    return f"L{number} released"


def release_lease(store, db, number, note=""):
    """Release a lease and arm reservations that were waiting only on it."""
    db.execute("UPDATE lease SET state = 'released' WHERE id = ?", (number,))
    store.log(db, "lease", number, "released", note)
    arm_reservations(store, db, in_flight(db), store.contract.get("cap"))


def lease_id(text):
    if not re.fullmatch(r"L?\d+", text or ""):
        raise seam.LandError(f"{text!r} is not a lease id such as L3")
    return int(text.lstrip("L"))


def reservation_id(text):
    if not re.fullmatch(r"S?\d+", text or ""):
        raise seam.LandError(f"{text!r} is not a reservation id such as S3")
    return int(text.lstrip("S"))


def entry_label(ident):
    return f"E{ident}"


def entry_id(text):
    """E<n> names an entry. A bare number is the id."""
    if not re.fullmatch(r"E?\d+", text or ""):
        raise seam.LandError(f"{text!r} is not an entry id such as E3")
    return int(text.lstrip("E"))
