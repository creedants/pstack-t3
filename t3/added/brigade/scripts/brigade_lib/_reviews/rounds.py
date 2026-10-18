import functools
import json
import runpy
from brigade_lib.shared import BrigadeError
from brigade_lib.store import OWING_STATES, REASONS, ROUND_DECISIONS, ROUND_KIND, ROUND_PARKED, UNRECORDED_REASON, roles_script, round_budget_of

@functools.cache
def roles_family():
    """roles.py's family, loaded in process.

    pass record asks under the store lock, which forbids a subprocess.
    runpy writes no __pycache__ under skills/.
    """
    return runpy.run_path(str(roles_script()))["family"]


def cross_family(row):
    family = roles_family()
    return family(row["author"]) != family(row["verifier"]) and not row["note"].startswith("same model family")


def item_verdicts(rows, dish_id):
    """The rows that carry the item's verdict. A panel member's row is on record and decides nothing."""
    return [row for row in rows if row["dish"] == dish_id and not row["member"]]


def send_backs(rows, dish_id):
    return [row for row in item_verdicts(rows, dish_id) if row["verdict"] == "send-back"]


def round_count(restaurant, dish_id):
    """The item's send-backs written after the later of its last passing verdict and its last answered round decision.

    Only a row `86 add --round-budget` wrote is a round decision. Another decision's answer never settles the count.
    """
    verdicts = item_verdicts(restaurant.rows("pass.tsv"), dish_id)
    settled = [row["at"] for row in verdicts if row["verdict"] == "pass"]
    settled += [row["answered"] for row in restaurant.rows("86.tsv")
                if row["dish"] == dish_id and row["kind"] == ROUND_KIND and row["state"] == "answered"]
    since = max(settled, default="")
    return sum(row["verdict"] == "send-back" and row["at"] > since for row in verdicts)


def round_debt(restaurant, dish):
    """The item's round count when it owes its round decision, else 0.

    The budget stops another fix round, so only an item that could get one owes.
    """
    if dish["state"] not in OWING_STATES:
        return 0
    count = round_count(restaurant, dish["id"])
    budget = round_budget_of(restaurant.meta)
    return count if count >= budget else 0


def fix_round_refusal(restaurant, dish):
    """Why the item gets no fix round now, or None."""
    debt = round_debt(restaurant, dish)
    if not debt:
        return None
    return (f"{dish['id']} has {debt} send-backs; no fix round until its decision is answered; "
            f"run 86 add --dish {dish['id']} --round-budget")


def round_decision(restaurant, dish_id):
    """The question, options, and default of the decision an item owes at its round budget."""
    _, dish = restaurant.find("dishes.tsv", dish_id)
    if dish["state"] not in OWING_STATES:
        raise BrigadeError(f"{dish_id} is {dish['state']}; it owes no round decision")
    count = round_count(restaurant, dish_id)
    budget = round_budget_of(restaurant.meta)
    if count < budget:
        raise BrigadeError(f"{dish_id} has {count} send-back{'' if count == 1 else 's'} since its last pass or round decision; "
                           f"the decision opens at {budget}")
    question = (f"{dish_id} was sent back {count} times. Accept the remaining findings as known limits and land it, "
                "send it through architect for a redesign, or drop it?")
    report = send_backs(restaurant.rows("pass.tsv"), dish_id)[-1]["report"]
    if report:
        question += f" Findings: reports/{report}."
    return question, ", ".join((*ROUND_DECISIONS, ROUND_PARKED)), ROUND_PARKED


def latest_verdict(restaurant, dish_id, sha):
    restaurant.find("dishes.tsv", dish_id)
    verdicts = [row for row in item_verdicts(restaurant.rows("pass.tsv"), dish_id) if row["sha"] == sha]
    return verdicts[-1] if verdicts else None


def pass_json(row):
    return json.dumps({"verdict": row["verdict"], "author": row["author"], "verifier": row["verifier"],
                       "note": row["note"], "crossFamily": cross_family(row)}, indent=2)


def pass_check(restaurant, dish_id, sha):
    latest = latest_verdict(restaurant, dish_id, sha)
    if latest is None:
        return False, f"{dish_id} has no review verdict for {sha}"
    if latest["verdict"] != "pass":
        return False, f"{dish_id} at {sha}: {latest['verdict']} ({latest['note'] or 'no note'})"
    return True, f"{dish_id} at {sha} passed review by {latest['verifier']}"


def reviewed_items(rows, last):
    """The last `last` items that have a verdict, newest first, each with its non-member rows.

    An item is a dish with at least one non-member row of pass.tsv. Its rows keep the table's order, so its
    first row is its first verdict. An item's author is the author cell of that first verdict row.
    """
    by_dish = {}
    for row in rows:
        if not row["member"]:
            by_dish.setdefault(row["dish"], []).append(row)
    newest_first = sorted(by_dish.items(), key=lambda pair: pair[1][-1]["at"], reverse=True)
    return newest_first[:last]


def reason_counts(items):
    """How many send-back rows of these items carry each reason, and how many carry none.

    A row whose reason cell is empty, and a row whose reason cell is not one of REASONS, carries none.
    """
    counts = dict.fromkeys(REASONS, 0)
    counts[UNRECORDED_REASON] = 0
    for _, item_rows in items:
        for row in item_rows:
            if row["verdict"] == "send-back":
                counts[row["reason"] if row["reason"] in REASONS else UNRECORDED_REASON] += 1
    return counts


def pass_rate(passed, items):
    return f"{passed} of {items} ({100 * passed / items:.1f}%)"


def pass_stats(restaurant, last):
    """The first-pass rate overall and per author, and send-back counts by reason, over the last `last` items.

    An item's first pass is its first non-member row in table order, and it is a first pass when that row's
    verdict is `pass`; any other verdict, including pass-with-edits and blocked, is not. An item's author is
    that row's author. Send-back counts cover every non-member row of the selected items whose verdict is
    send-back, and a row with no reason, or a reason not in REASONS, counts as unrecorded. Items are newest
    first by their last verdict.
    """
    with restaurant.read_lock(create=False):
        items = reviewed_items(restaurant.rows("pass.tsv"), last)
    if not items:
        return "no items with a verdict"
    first = [item_rows[0] for _, item_rows in items]
    lines = [f"last {len(items)} item{'' if len(items) == 1 else 's'} with a verdict",
             f"first pass overall: {pass_rate(sum(row['verdict'] == 'pass' for row in first), len(items))}"]
    by_author = {}
    for row in first:
        by_author.setdefault(row["author"], []).append(row["verdict"] == "pass")
    for author in sorted(by_author):
        passes = by_author[author]
        lines.append(f"first pass {author}: {pass_rate(sum(passes), len(passes))}")
    counts = reason_counts(items)
    named = [f"{reason} {counts[reason]}" for reason in (*REASONS, UNRECORDED_REASON) if counts[reason]]
    lines.append(f"send-backs: {', '.join(named) if named else 'none'}")
    return "\n".join(lines)
