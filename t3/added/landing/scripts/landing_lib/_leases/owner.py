import re
from landing_lib import seam

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
