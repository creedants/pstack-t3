from datetime import timedelta
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
