import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from brigade_lib import seam
from brigade_lib._tickets.goals import open_goal
from brigade_lib._tickets.labels import GOALS_ONLY, Label, frozen_label, in_flight_tickets, label_words, read_labels
from brigade_lib.shared import BrigadeError, plural


@dataclass(frozen=True)
class Drop:
    """Drop each listed ticket."""


@dataclass(frozen=True)
class Assign:
    """Give each listed ticket with no goal this goal."""
    goal: str


@dataclass(frozen=True)
class Candidate:
    """One waiting ticket filed long ago with no kind or no goal."""
    id: str
    age: int
    label: Label
    summary: str
    filed: str


@dataclass(frozen=True)
class Sweep:
    """The tickets one sweep lists, ordered by numeric id."""
    days: int
    candidates: tuple = ()

    @property
    def digest(self):
        """The first 32 hex digits of the sha256 of one line per listed ticket.

        Each line is the ticket's id, kind, goal, summary, and filed time, as five tab-separated cells, in listing order.
        The lines join with a newline.
        """
        joined = "\n".join("\t".join((candidate.id, candidate.label.kind, candidate.label.goal, candidate.summary,
                                      candidate.filed))
                           for candidate in self.candidates)
        return hashlib.sha256(joined.encode()).hexdigest()[:32]


def filed_at(row):
    """This row's filed time. Z reads as UTC, and a naive stamp reads as UTC."""
    filed = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
    if filed.tzinfo is None:
        filed = filed.replace(tzinfo=timezone.utc)
    return filed


def sweep_select(rows, labels, held, days, now):
    """The waiting rows filed strictly more than days ago whose kind or goal is empty.

    A row with both kind and goal is fully tagged and never selected.
    A row frozen_label holds back is never selected. Rows read through read_labels,
    so an illegal kind or goal reads as empty.
    """
    found = []
    for row in rows:
        if row["state"] != "waiting":
            continue
        if frozen_label(row, held):
            continue
        label = labels.tickets[row["id"]]
        if label.kind and label.goal:
            continue
        filed = filed_at(row)
        if now - filed <= timedelta(days=days):
            continue
        found.append(Candidate(row["id"], (now - filed).days, label, row["summary"], row["at"]))
    found.sort(key=lambda candidate: (int(candidate.id[1:]) if candidate.id[1:].isdigit() else 0, candidate.id))
    return Sweep(days, tuple(found))


def changes_for(action, candidate):
    """The cells one apply writes for this candidate, or None when it changes nothing.

    A drop always changes the ticket. A goal lands only when the ticket reads as unassigned,
    so it never overwrites a goal in goals.tsv.
    """
    if isinstance(action, Drop):
        return {"state": "dropped"}
    if isinstance(action, Assign):
        return None if candidate.label.goal else {"goal": action.goal}
    return None


def sweep_text(selected):
    """The list output. One line per ticket, then a count line with the digest."""
    if not selected.candidates:
        return "nothing to sweep"
    lines = [f"{candidate.id} {candidate.age}d{label_words(candidate.label)}: {candidate.summary}"
             for candidate in selected.candidates]
    lines.append(f"{plural(len(selected.candidates), 'ticket')}; digest {selected.digest}")
    return "\n".join(lines)


def sweep_tickets(restaurant, days, action, confirm):
    """List one sweep, or apply it when the digest still matches.

    A list prints each ticket and exits 0, or prints nothing to sweep when empty.
    An apply with no further change prints nothing to sweep and exits 0 before
    the digest compare. Any other apply writes only when confirm matches the
    digest of the same selector under the same lock hold, and a mismatch exits 1
    with nothing changed. A repeated ticket id in rail.tsv refuses the list and
    the apply with exit 1 and no change. The goal is validated before any write.
    """
    rows = restaurant.rows("rail.tsv")
    seen = set()
    for row in rows:
        if row["id"] in seen:
            raise BrigadeError(f"duplicate ticket {row['id']} in rail.tsv; nothing changed")
        seen.add(row["id"])
    labels = read_labels(restaurant, GOALS_ONLY)
    if isinstance(action, Assign):
        open_goal(labels, action.goal)
    held = in_flight_tickets(restaurant)
    now = datetime.fromisoformat(seam.now().replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    selected = sweep_select(rows, labels, held, days, now)
    if action is None:
        return sweep_text(selected)
    planned = [(candidate, cells) for candidate in selected.candidates
               if (cells := changes_for(action, candidate)) is not None]
    if not planned:
        return "nothing to sweep"
    if confirm != selected.digest:
        raise BrigadeError(f"digest {confirm!r} does not match {selected.digest!r}; nothing changed")
    for candidate, cells in planned:
        if isinstance(action, Drop):
            restaurant.update("rail.tsv", candidate.id, "ticket", f"sweep --older-than {days}", **cells)
        else:
            restaurant.update("rail.tsv", candidate.id, "ticket", **cells)
    if isinstance(action, Drop):
        return "\n".join(f"{candidate.id} dropped" for candidate, _ in planned)
    return "\n".join(f"{candidate.id} goal {action.goal}" for candidate, _ in planned)
