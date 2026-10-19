from dataclasses import dataclass
from brigade_lib.shared import clean, one_of
from brigade_lib.store import GOAL_STATES, IN_FLIGHT_STATES, KINDS

@dataclass(frozen=True)
class Goal:
    """One row of goals.tsv as read_labels reads it.

    state is one of GOAL_STATES. share is the digits of a whole number of 1 or more with no leading zero, or "" for no limit.
    """
    id: str
    state: str
    name: str
    purpose: str
    done: str
    share: str
    closed: str


@dataclass(frozen=True)
class Label:
    """A ticket's or an item's kind and goal as read_labels reads them.

    kind is one of KINDS, or "" for no kind. goal is a key of Labels.goals, or "" for unassigned.
    """
    kind: str = ""
    goal: str = ""


@dataclass(frozen=True)
class Fault:
    """One warning of read_labels. table is the table the row is in, id is the row's id cell, and text is the warning."""
    table: str
    id: str
    text: str


@dataclass(frozen=True)
class ActsOn:
    """The rows of rail.tsv and dishes.tsv whose kind and goal a command acts on.

    read_labels prints the warnings of these rows. tickets holds ticket ids and items holds item ids.
    every is true for a command that lists or counts every row.
    """
    tickets: tuple = ()
    items: tuple = ()
    every: bool = False

    def covers(self, fault):
        """True when read_labels prints this fault. Every fault of goals.tsv is printed."""
        return (fault.table == "goals.tsv" or self.every
                or fault.id in {"rail.tsv": self.tickets, "dishes.tsv": self.items}[fault.table])


EVERY_ROW = ActsOn(every=True)


GOALS_ONLY = ActsOn()


@dataclass(frozen=True)
class Labels:
    """The kinds and goals of one store, read under one hold of the store lock.

    goals is {goal id: Goal} in file order. tickets is {ticket id: Label} for the rows of rail.tsv, and items is
    {item id: Label} for the rows of dishes.tsv. faults is one Fault per cell whose value is not legal,
    and one per id of goals.tsv with a row that is not a goal.
    """
    goals: dict
    tickets: dict
    items: dict
    faults: tuple


def whole_share(text):
    """The digits of a whole number of 1 or more with no leading zero, or "" when the text is not such a number."""
    return text.lstrip("0") if text.isascii() and text.isdigit() else ""


def read_goal(row, faults):
    state, share = row["state"], whole_share(row["share"])
    if state not in GOAL_STATES:
        faults.append(Fault("goals.tsv", row["id"], f"{row['id']} has state {state!r}, which is not {one_of(GOAL_STATES)}; "
                                                    f"it reads as open; run goal set {row['id']} --state <state>"))
        state = "open"
    if row["share"] and not share:
        faults.append(Fault("goals.tsv", row["id"], f"{row['id']} has share {row['share']!r}, which is not a whole number of "
                                                    f"1 or more; it reads as no limit; run goal set {row['id']} --share <n>"))
    return Goal(row["id"], state, row["name"], row["purpose"], row["done"], share, row["closed"])


def in_flight_tickets(restaurant):
    """{ticket id: the first row of dishes.tsv whose state is in IN_FLIGHT_STATES and whose tickets cell names the id}."""
    found = {}
    for item in restaurant.rows("dishes.tsv"):
        if item["state"] in IN_FLIGHT_STATES:
            for ident in item["tickets"].split(","):
                found.setdefault(ident, item)
    return found


def frozen_label(ticket, held):
    """Why ticket set does not change this ticket's kind and goal now, or "" when it does. `held` is in_flight_tickets().

    It changes them when the ticket is waiting and no item in flight names it. fire wrote an item's kind and goal when the
    item started, and no command writes the item's two cells after that.
    """
    if ticket["state"] != "waiting":
        return f"{ticket['id']} is {ticket['state']}"
    item = held.get(ticket["id"])
    return f"{ticket['id']} is a ticket of {item['id']}, which is {item['state']}" if item else ""


def read_label(table, row, goals, faults, by_hand):
    """One row's Label. `by_hand` is the end of the warning for a bad cell of a row whose cells ticket set does not change now.

    It is "" for any other row, and the warning then ends with the ticket set line to run.
    """
    ident = row["id"]
    kind = row["kind"] if row["kind"] in KINDS else ""
    goal = row["goal"] if row["goal"] in goals else ""
    if row["kind"] and not kind:
        faults.append(Fault(table, ident, f"{ident} has kind {row['kind']!r}, which is not {one_of(KINDS)}; it reads as no kind; "
                                          + (by_hand or f"run ticket set {ident} --kind <kind>")))
    if row["goal"] and not goal:
        faults.append(Fault(table, ident, f"{ident} names goal {row['goal']!r}, which has no row in goals.tsv; it reads as unassigned; "
                                          + (by_hand or f"run ticket set {ident} --goal G<n> or --no-goal")))
    return Label(kind, goal)


def read_labels(restaurant, acts_on):
    """The one reader of the kind and goal cells of rail.tsv and dishes.tsv, and of goals.tsv. It writes nothing to the store.

    It prints one warning line on standard error for each fault of goals.tsv and for each fault of a row in `acts_on`.
    A command prints a warning once, however many times it reads.
    Call it under the store lock. A goals.tsv that does not exist reads as no goals.
    A row of goals.tsv with an empty id is not a goal. Neither is a row whose id clean changes, which is an id with white
    space at an end or a carriage return in it. Of the other rows of goals.tsv with one id, the first is the goal.
    The rows with an empty id add one Fault between them. So do the rows with one id that are not a goal.
    A kind cell, a goal cell, and an id of goals.tsv are read as they are in the file, and stored writes each back as it
    is, so a rewrite changes the reading of none that the command did not set.
    A cell whose value is not legal reads as follows and adds one Fault.

    - A kind that is not one of KINDS reads as no kind.
    - A goal that is not the id of a goal reads as unassigned.
    - A goal's state that is not one of GOAL_STATES reads as open.
    - A goal's share that is not empty and not a whole number of 1 or more reads as no limit.
    """
    goals, faults, extra = {}, [], []
    for row in restaurant.rows("goals.tsv"):
        if row["id"] and clean(row["id"]) == row["id"] and row["id"] not in goals:
            goals[row["id"]] = read_goal(row, faults)
        elif row["id"] not in extra:
            extra.append(row["id"])
    for ident in extra:
        if not ident:
            text = "goals.tsv has a row with no id; it is not a goal; remove it from goals.tsv by hand"
        elif clean(ident) != ident:
            text = (f"goals.tsv has a row with id {ident!r}, which has white space at an end or a carriage return in it; "
                    "it is not a goal; correct the id in goals.tsv by hand to an id no other row has, or remove the row")
        else:
            text = (f"goals.tsv has more than one row with id {ident}; the first is the goal and the others are not read; "
                    "remove the others from goals.tsv by hand")
        faults.append(Fault("goals.tsv", ident, text))
    held, tickets = in_flight_tickets(restaurant), {}
    for row in restaurant.rows("rail.tsv"):
        frozen = frozen_label(row, held)
        tickets[row["id"]] = read_label("rail.tsv", row, goals, faults,
                                        frozen and f"ticket set does not change this cell while {frozen}; correct it in rail.tsv by hand")
    items = {row["id"]: read_label("dishes.tsv", row, goals, faults, "no command changes this cell; correct it in dishes.tsv by hand")
             for row in restaurant.rows("dishes.tsv")}
    for fault in faults:
        if acts_on.covers(fault):
            restaurant.warn(fault.text)
    return Labels(goals, tickets, items, tuple(faults))


@dataclass(frozen=True)
class Tally:
    """The work under one kind or one goal. waiting holds ticket ids. in_flight and merged hold item ids."""
    waiting: tuple = ()
    in_flight: tuple = ()
    merged: tuple = ()
    send_backs: int = 0


@dataclass(frozen=True)
class LabelCounts:
    """kinds is {kind: Tally} and goals is {goal id: Tally}. The key "" holds the work with no kind, and the unassigned work.

    riding is {goal id: ticket ids}. A key with no work is absent.
    """
    kinds: dict
    goals: dict
    riding: dict


def count_labels(restaurant, labels):
    """The counts by kind and by goal. Call it under the same hold of the store lock as the read_labels that made `labels`.

    Each row counts under its own kind and its own goal as `labels` gives them.

    - waiting. The rows of rail.tsv whose state is waiting.
    - in flight. The rows of dishes.tsv whose state is in IN_FLIGHT_STATES.
    - merged. The rows of dishes.tsv whose state is merged.
    - send-backs. The rows of pass.tsv whose verdict is send-back, whose member cell is empty, and whose dish cell is the id of
      a row of dishes.tsv. They count under that item's kind and goal, whatever the item's state.
    - riding, for a goal. The rows of rail.tsv whose state is assigned and whose goal is that goal, where the dish cell is not
      the id of an item under that goal.
    """
    kinds, goals, riding = {}, {}, {}

    def add(label, field, ident):
        for found, key in ((kinds, label.kind), (goals, label.goal)):
            found.setdefault(key, {"waiting": [], "in_flight": [], "merged": [], "send_backs": []})[field].append(ident)

    for row in restaurant.rows("rail.tsv"):
        label = labels.tickets[row["id"]]
        if row["state"] == "waiting":
            add(label, "waiting", row["id"])
        elif row["state"] == "assigned" and label.goal and labels.items.get(row["dish"], Label()).goal != label.goal:
            riding.setdefault(label.goal, []).append(row["id"])
    for row in restaurant.rows("dishes.tsv"):
        if row["state"] in IN_FLIGHT_STATES:
            add(labels.items[row["id"]], "in_flight", row["id"])
        elif row["state"] == "merged":
            add(labels.items[row["id"]], "merged", row["id"])
    for row in restaurant.rows("pass.tsv"):
        if row["verdict"] == "send-back" and not row["member"] and row["dish"] in labels.items:
            add(labels.items[row["dish"]], "send_backs", row["dish"])

    def tallies(found):
        return {key: Tally(tuple(lists["waiting"]), tuple(lists["in_flight"]), tuple(lists["merged"]), len(lists["send_backs"]))
                for key, lists in found.items()}

    return LabelCounts(tallies(kinds), tallies(goals), {goal: tuple(ids) for goal, ids in riding.items()})


def label_words(label):
    """What a ticket line prints for a label. ` fix G1`, ` fix`, ` G1`, or nothing for no kind and no goal."""
    return "".join(f" {word}" for word in (label.kind, label.goal) if word)
