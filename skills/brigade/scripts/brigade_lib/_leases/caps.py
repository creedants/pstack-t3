from dataclasses import dataclass
from brigade_lib.compact import refuse_setting
from brigade_lib.shared import BrigadeError
from brigade_lib.store import COUNTED_STATES, KINDS, MODES

def worker_cap(meta):
    value = meta.get("workers")
    if value in (None, ""):
        return 2
    return int(value)


def autofire_of(meta):
    return meta.get("autofire") or "normal"


@dataclass(frozen=True)
class KindLimits:
    """The per-kind settings restaurant.json holds. caps is {kind: cap} and review is {kind: mode}.

    A kind that is absent has no cap, or no review depth.
    """
    caps: dict
    review: dict


@dataclass(frozen=True)
class Flight:
    """How many items hold a worker slot, by kind and by goal. A kind or a goal with none is absent."""
    by_kind: dict
    by_goal: dict


@dataclass(frozen=True)
class Hold:
    """Why a spend control holds an item, as the standing's kind and the words both doors print."""
    standing: str
    words: str


def kind_limits(meta):
    """The caps and review depths restaurant.json holds, as a KindLimits.

    A key absent from meta reads as no caps and no review depths. A present value that does not parse is refused with a
    warning, and so is a caps or review entry of a known kind whose value does not parse. A cap is a whole number of 1
    or more and a depth is full or light. An entry whose kind is not in KINDS is skipped.
    """
    caps, review = {}, {}
    for key, found in (("caps", caps), ("review", review)):
        if key not in meta:
            continue
        stored = meta[key]
        if not isinstance(stored, dict):
            refuse_setting(key, stored, "it is not an object")
        for kind, value in stored.items():
            if kind not in KINDS:
                continue
            if key == "review":
                if value not in MODES:
                    refuse_setting("review", value, f"{kind!r} needs full or light")
                review[kind] = value
            else:
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    refuse_setting("caps", value, f"{kind!r} needs a whole number of 1 or more")
                caps[kind] = value
    return KindLimits(caps, review)


def counted_pairs(dishes, labels):
    """(kind, goal) per row of dishes.tsv that holds a worker slot, as read_labels reads the two cells."""
    return tuple((labels.items[row["id"]].kind, labels.items[row["id"]].goal)
                 for row in dishes if row["state"] in COUNTED_STATES)


def flight_of(pairs):
    """The Flight these pairs give. An empty kind or goal is not counted."""
    by_kind, by_goal = {}, {}
    for kind, goal in pairs:
        if kind:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        if goal:
            by_goal[goal] = by_goal.get(goal, 0) + 1
    return Flight(by_kind, by_goal)


def with_item(flight, label):
    """The Flight one more item of this label gives."""
    by_kind, by_goal = dict(flight.by_kind), dict(flight.by_goal)
    if label.kind:
        by_kind[label.kind] = by_kind.get(label.kind, 0) + 1
    if label.goal:
        by_goal[label.goal] = by_goal.get(label.goal, 0) + 1
    return Flight(by_kind, by_goal)


@dataclass(frozen=True)
class StandingText:
    count_label: str
    line_words: str


STANDINGS = {
    "start": StandingText("startable", "startable"),
    "rides": StandingText("riding", "rides with {why}"),
    "blocked": StandingText("blocked", "blocked, {why}"),
    "unknown": StandingText("unknown", "unknown, no paths recorded"),
    "decision": StandingText("decisions", "decision for the owner"),
    "below": StandingText("below auto-start", "below auto-start"),
    "paused": StandingText("goal paused", "{why}"),
    "cap": StandingText("kind cap", "{why}"),
    "share": StandingText("goal share", "{why}"),
    "reserve": StandingText("held back", "held back, low tickets wait while an urgent or normal ticket can start "
                                         "now and the workers still idle are at most {reserve}"),
}


def admit(label, goal, flight, limits):
    """Why a spend control holds an item of this kind under this goal, or None.

    `goal` is the Goal the ticket's goal names, or None for unassigned work. A paused goal comes first, then a
    kind cap, then a goal share. Each compares what is in flight with the setting.
    """
    if goal is not None and goal.state == "paused":
        return Hold("paused", "its goal is paused")
    held = flight.by_kind.get(label.kind, 0)
    cap = limits.caps.get(label.kind)
    if cap is not None and held >= cap:
        return Hold("cap", f"the {label.kind} cap holds ({held} of {cap})")
    if goal is not None and goal.share and flight.by_goal.get(goal.id, 0) >= int(goal.share):
        at = flight.by_goal.get(goal.id, 0)
        return Hold("share", f"the {goal.id} share holds ({at} of {goal.share})")
    return None


@dataclass(frozen=True)
class Refill:
    """What the spend controls hold now and what is in flight under them, as the refill and fire read them.

    limits is what kind_limits read, goals is {goal id: Goal} from goals.tsv, and flight counts the dishes that
    hold a worker slot by kind and by goal.
    """
    limits: KindLimits
    goals: dict
    flight: Flight

    def hold(self, label):
        """Why a spend control holds an item of this label, or None."""
        return admit(label, self.goals.get(label.goal), self.flight, self.limits)

    def started(self, label):
        """The Refill one more item of this label gives, for the next ticket in the same pass."""
        return Refill(self.limits, self.goals, with_item(self.flight, label))


def refill_of(meta, dishes, labels):
    """The Refill of this store, read under the caller's store lock."""
    return Refill(kind_limits(meta), labels.goals, flight_of(counted_pairs(dishes, labels)))


def workers_line(meta, running):
    cap = worker_cap(meta)
    return f"workers: {running} of {cap} running, {max(0, cap - running)} idle, auto-start: {autofire_of(meta)}"


def require_workers(workers):
    if workers is None:
        return
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise BrigadeError("workers must be 1 or more")


def running_workers(restaurant):
    return sum(row["state"] in COUNTED_STATES for row in restaurant.rows("dishes.tsv"))
