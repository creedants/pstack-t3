from brigade_lib._lookback.state import moment
from brigade_lib.store import WORKER_ROLES

ROUND_CAP = 50


def _role_thread(rows, dish, role_ok, at, forward):
    """The item's thread for one role, as it stood at this round.

    A reviewer row is written just after the pass row of its round, so the first row at or after the round is that
    round's reviewer. A worker row is written before the round it worked, so the last row at or before the round is
    the worker that attempt used. A store whose stamps do not order the rows falls back to the latest row of the role.
    """
    matches = [row for row in rows.get(dish, []) if role_ok(row.get("role") or "")]
    if not matches:
        return ""
    if at:
        if forward:
            later = [row for row in matches if (row.get("at") or "") >= at]
            if later:
                return later[0].get("thread") or ""
        else:
            earlier = [row for row in matches if (row.get("at") or "") <= at]
            if earlier:
                return earlier[-1].get("thread") or ""
    return matches[-1].get("thread") or ""


def rounds_after(restaurant, bound):
    """The review rounds recorded after bound, oldest first, each with the threads its item used then.

    A round is one pass.tsv row that carries the item's verdict. A panel member's row is part of the round its panel
    reviewed, so it is not a round of its own. bound is an ISO stamp, and every row counts when it is empty.
    """
    bound_at = moment(bound) if bound else None
    threads = {}
    for row in restaurant.rows("threads.tsv"):
        threads.setdefault(row.get("dish") or "", []).append(row)
    found = []
    for row in restaurant.rows("pass.tsv"):
        if row.get("member"):
            continue
        at = moment(row.get("at") or "")
        if at is None:
            continue
        if bound_at is not None and not at > bound_at:
            continue
        report = row.get("report") or ""
        dish = row.get("dish") or ""
        stamp = row.get("at") or ""
        found.append(
            {
                "at": stamp,
                "item": dish,
                "verdict": row.get("verdict") or "",
                "reason": row.get("reason") or "",
                "report": f"reports/{report}" if report else "",
                "worker": _role_thread(threads, dish, lambda role: role in WORKER_ROLES, stamp, False),
                "reviewer": _role_thread(threads, dish, lambda role: role == "reviewer", stamp, True),
            }
        )
    found.sort(key=lambda item: item["at"])
    return found
