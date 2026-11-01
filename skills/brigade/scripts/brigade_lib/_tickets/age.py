from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from brigade_lib import seam
from brigade_lib._notes import append_notes, note_rows
from brigade_lib._tickets.after import key_of
from brigade_lib._tickets.rail import sibling_rails
from brigade_lib._tickets.labels import GOALS_ONLY, Label, frozen_label, in_flight_tickets, label_words, read_labels
from brigade_lib._tickets.paths import paths_of, priority_of, tracked_paths
from brigade_lib._tickets.sweep import filed_at
from brigade_lib.compact import refuse_setting
from brigade_lib.shared import BrigadeError, plural

KEY = "ticketAge"
DEFAULT_DAYS = 14
AGE_KINDS = ("polish", "upkeep")
AGED_NOTE = "aged out"


def ticket_age_of(meta):
    """The days a waiting low-priority polish or upkeep ticket may sit before ticket age moves it.

    A missing key takes 14. 0 and an empty string turn aging off. A present value that is a boolean,
    is not an integer, or is negative is refused.
    """
    if KEY not in meta:
        return DEFAULT_DAYS
    value = meta[KEY]
    if value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        refuse_setting(KEY, value, "it is not a whole number of 0 or more")
    return value


def parse_ticket_age(text):
    """The integer a --ticket-age flag stores. Empty string stores 0, turning aging off."""
    if text == "":
        return 0
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--ticket-age takes a whole number of 0 or more, or empty string to turn aging off, got '{text}'") from None
    if value < 0:
        raise BrigadeError(f"--ticket-age takes a whole number of 0 or more, or empty string to turn aging off, got '{text}'")
    return value


@dataclass(frozen=True)
class Aged:
    """One waiting ticket filed long ago with low priority and polish or upkeep kind."""
    id: str
    age: int
    label: Label
    summary: str
    filed: str
    paths: tuple = ()


def awaited(rows, own):
    """The ids of this store's tickets another row waits on through after."""
    waited = set()
    for row in rows:
        cell = row.get("after") or ""
        if not cell:
            continue
        store, ident = key_of(own, cell)
        if store == own:
            waited.add(ident)
    return waited


def sibling_waits(restaurant, own):
    """The ids of this store's tickets that a sibling's waiting ticket waits on through after.

    Call it before the store lock. sibling_rails reads each sibling's rail without holding this
    store's lock, the way ticket add --after reads one.
    """
    found = set()
    for directory, table in sibling_rails(restaurant).items():
        for row in table:
            if row["state"] != "waiting" or not row["after"]:
                continue
            store, ident = key_of(directory.name, row["after"])
            if store == own:
                found.add(ident)
    return found


def age_select(rows, labels, held, waited, days, now):
    """The waiting low-priority polish or upkeep rows filed strictly more than days ago, as Aged.

    A row is selected only when it is waiting, no item in flight names it, its kind is polish or
    upkeep, its priority reads low, it is not marked as a decision, it has no ref, no ticket waits
    on it through after, and its filed time is strictly more than days ago.
    """
    found = []
    for row in rows:
        if row["state"] != "waiting":
            continue
        if frozen_label(row, held):
            continue
        label = labels.tickets[row["id"]]
        if label.kind not in AGE_KINDS:
            continue
        if priority_of(row) != "low":
            continue
        if row["decision"] == "yes":
            continue
        if row["ref"]:
            continue
        if row["id"] in waited:
            continue
        filed = filed_at(row)
        if now - filed <= timedelta(days=days):
            continue
        found.append(Aged(row["id"], (now - filed).days, label, row["summary"], row["at"], tuple(paths_of(row))))
    found.sort(key=lambda ticket: (int(ticket.id[1:]) if ticket.id[1:].isdigit() else 0, ticket.id))
    return tuple(found)


def aged_since(restaurant, since):
    """The count of log.tsv ticket rows dropped by an age-out after since."""
    count = 0
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "ticket" and row["state"] == "dropped" and row["note"].startswith(AGED_NOTE) and row["at"] > since:
            count += 1
    return count


def age_text(aged, write):
    """The list output. One line per ticket, then a count line naming what writing does."""
    if not aged:
        return "nothing old enough to age out"
    lines = [f"{ticket.id} {ticket.age}d{label_words(ticket.label)}: {ticket.summary}" for ticket in aged]
    lines.append(f"{plural(len(aged), 'ticket')}{' aged out' if write else ' would age out'}")
    return "\n".join(lines)


def age_tickets(restaurant, days, write, tracked, sibling_waited=()):
    """Move each selected ticket's summary to notes.tsv, then drop it.

    A list prints each ticket and exits 0, or prints nothing old enough when empty.
    An apply first appends one ledger row per recorded path, or one note row when the ticket
    records no path, then drops the ticket with a note naming the age-out. The ledger's own
    (path, item, same_text(text)) key drops a second note on retry, and the retry makes one
    state change. sibling_waited holds the ticket ids a sibling's waiting ticket waits on,
    read by sibling_waits before the lock. Call it under the caller's lock.
    """
    rows = restaurant.rows("rail.tsv")
    labels = read_labels(restaurant, GOALS_ONLY)
    held = in_flight_tickets(restaurant)
    waited = awaited(rows, restaurant.dir.name) | set(sibling_waited)
    now = datetime.fromisoformat(seam.now().replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    selected = age_select(rows, labels, held, waited, days, now)
    if not write:
        return age_text(selected, False)
    at = seam.now()
    for ticket in selected:
        if ticket.paths:
            ledger = [(path, ticket.id, at, ticket.summary) for path in ticket.paths]
        else:
            ledger = note_rows(ticket.summary, ticket.id, at, tracked)
        append_notes(restaurant, ledger)
        restaurant.update("rail.tsv", ticket.id, "ticket",
                          f"aged out after {ticket.age} days; noted in notes.tsv", state="dropped")
    return age_text(selected, True)


def register_age(t):
    """The ticket age subparser on the ticket subparsers."""
    a = t.add_parser("age", help="move waiting low-priority polish or upkeep tickets filed more than N days ago to notes.tsv")
    a.add_argument("--older-than", type=int, metavar="N",
                   help="use this many days instead of the stored set --ticket-age limit for this run; takes 1 or more")
    a.add_argument("--dry-run", action="store_true", help="print what it would move; it changes no table")


def prepare_age(restaurant, args):
    """Check the age flags and read the tracked paths, before the store lock."""
    if args.older_than is not None and args.older_than < 1:
        raise BrigadeError("--older-than takes a whole number of 1 or more")
    args.tracked = None if args.dry_run else tracked_paths(restaurant.meta["projectRoot"])
    args.sibling_waited = sibling_waits(restaurant, restaurant.dir.name)


def run_age(restaurant, args):
    """List or move the aged-out tickets. Call under the caller's lock."""
    days = args.older_than if args.older_than is not None else ticket_age_of(restaurant.meta)
    if days == 0:
        return "ticket aging is off; set --ticket-age N turns it on"
    if not args.dry_run:
        with restaurant.guarded():
            return age_tickets(restaurant, days, True, getattr(args, "tracked", None),
                               getattr(args, "sibling_waited", ()))
    with restaurant.checked():
        return age_tickets(restaurant, days, False, None, getattr(args, "sibling_waited", ()))
