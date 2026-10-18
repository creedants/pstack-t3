from brigade_lib import seam
from brigade_lib._tickets.labels import EVERY_ROW, GOALS_ONLY, Tally, count_labels, read_labels, whole_share
from brigade_lib.shared import BrigadeError, clean, same_text
from brigade_lib.store import CLOSED_GOAL_STATES, GOAL_MOVES

def open_goal(labels, ident):
    """The goal a ticket is being given. Refuses an id with no row, and a goal that is done or dropped."""
    if not ident:
        raise BrigadeError('--goal "" names no goal; run goal list --all to see every goal')
    goal = labels.goals.get(ident)
    if goal is None:
        raise BrigadeError(f"no goal {ident}; run goal list --all to see every goal")
    if goal.state in CLOSED_GOAL_STATES:
        raise BrigadeError(f"{ident} is {goal.state}; a ticket takes an open or paused goal; "
                           f"run goal set {ident} --state open first, or pick another goal")
    return goal


def goal_share(text):
    """--share as the cell to store. Empty text stays empty. Refuses text that is not a whole number of 1 or more."""
    text = clean(text)
    if text and not whole_share(text):
        raise BrigadeError(f'--share takes a whole number of 1 or more, or "" for no limit, got {text!r}')
    return whole_share(text)


def add_goal(restaurant, name, purpose, done, share=""):
    """Append one open goal and log it. Returns the new id.

    Refuses when an open or paused goal has the same name by same_text, so a command run a second time adds no second goal.
    """
    name, purpose, done, share = clean(name), clean(purpose), clean(done), goal_share(share)
    if not (name and purpose and done):
        raise BrigadeError("goal add needs a nonempty --name, --purpose, and --done")
    for goal in read_labels(restaurant, GOALS_ONLY).goals.values():
        if goal.state not in CLOSED_GOAL_STATES and same_text(goal.name) == same_text(name):
            raise BrigadeError(f"same name as {goal.id} ({goal.state}); nothing added")
    ident = restaurant.next_id("goals.tsv")
    restaurant.append("goals.tsv", {"id": ident, "at": seam.now(), "state": "open", "name": name, "purpose": purpose,
                                    "done": done, "share": share})
    restaurant.log("goal", ident, "open", name)
    return ident


def goal_work(counted, ident):
    """What keeps a goal from closing, as (words, advice) pairs. Empty when no ticket of the goal waits or rides along and no item is in flight."""
    tally = counted.goals.get(ident, Tally())
    held = ((tally.waiting, "waiting", "give each waiting ticket another goal with ticket set --goal, or none with --no-goal; "
             "a waiting ticket that an item in flight names keeps its goal until that item is merged or dropped"),
            (tally.in_flight, "in flight", "finish or drop each item in flight"),
            (counted.riding.get(ident, ()), "riding along", "finish or drop the item each of those tickets rides along in"))
    return [(f"{', '.join(ids)} {words}", advice) for ids, words, advice in held if ids]


def set_goal(restaurant, ident, name=None, purpose=None, done=None, share=None, state=None, evidence=None):
    """Change one goal. Returns one line such as `G1 paused; share 2`.

    GOAL_MOVES is the state machine. A state the goal's cell already holds is accepted and changes neither the state nor
    the closed cell. A state cell that is not one of GOAL_STATES reads as open, and --state open rewrites it.
    A move to done or dropped needs --evidence, and is refused while goal_work finds work under the goal.
    One update call writes every change. It logs a goal row when the state changed.
    """
    labels = read_labels(restaurant, EVERY_ROW if state in CLOSED_GOAL_STATES else GOALS_ONLY)
    goal = labels.goals.get(ident)
    if goal is None:
        raise BrigadeError(f"no goal {ident}; run goal list --all to see every goal")
    if all(value is None for value in (name, purpose, done, share, state)):
        raise BrigadeError("goal set needs --state, --name, --purpose, --done, or --share")
    for flag, value in (("--name", name), ("--purpose", purpose), ("--done", done)):
        if value is not None and not clean(value):
            raise BrigadeError(f"goal set {flag} takes text; a goal keeps a name, a purpose, and a done test")
    if evidence is not None and state not in CLOSED_GOAL_STATES:
        raise BrigadeError("--evidence goes only with --state done or --state dropped")
    if share is not None:
        share = goal_share(share)
    _, row = restaurant.find("goals.tsv", ident)
    moving = state is not None and state != row["state"]
    closing = moving and state in CLOSED_GOAL_STATES
    if moving and state != goal.state and state not in GOAL_MOVES[goal.state]:
        raise BrigadeError(f"{ident} is {goal.state}; a {goal.state} goal moves only to open; run goal set {ident} --state open first")
    if closing:
        if not clean(evidence):
            raise BrigadeError(f"--state {state} needs --evidence <one line>")
        work = goal_work(count_labels(restaurant, labels), ident)
        if work:
            raise BrigadeError(f"{ident} still has work: {'; '.join(words for words, _ in work)}; "
                               f"{'; '.join(advice for _, advice in work)}; then run this again")
    closed = None
    if closing:
        closed = f"{seam.now()} {clean(evidence)}"
    elif moving and goal.state in CLOSED_GOAL_STATES:
        closed = ""
    said = []
    if state is not None:
        said.append(state if moving else f"already {state}")
    if name is not None:
        said.append(f"name {clean(name)}")
    if purpose is not None:
        said.append("purpose changed")
    if done is not None:
        said.append("done test changed")
    if share is not None:
        said.append(f"share {share}" if share else "no share")
    kept = clean(name) if name is not None else goal.name
    restaurant.update("goals.tsv", ident, "goal", note=f"{kept}: {clean(evidence)}" if closing else kept,
                      state=state, name=name, purpose=purpose, done=done, share=share, closed=closed)
    return f"{ident} {'; '.join(said)}"
