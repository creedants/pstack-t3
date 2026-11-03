from brigade_lib._lookback.state import moment
from brigade_lib.store import WORKER_ROLES

ROUND_CAP = 50


def _worker_thread(rows, dish, at):
    matches = [row for row in rows.get(dish, []) if (row.get("role") or "") in WORKER_ROLES]
    if not matches:
        return ""
    before = [row for row in matches if (row.get("at") or "") <= at]
    return (before[-1] if before else matches[-1]).get("thread") or ""


def _reviewer_thread(rows, dish, at):
    matches = [row for row in rows.get(dish, []) if (row.get("role") or "") == "reviewer"]
    if not matches:
        return ""
    after = [row for row in matches if (row.get("at") or "") >= at]
    return (after[0] if after else matches[-1]).get("thread") or ""


def _window_start(bound):
    return moment(bound) if bound else None


def _is_round_row(row):
    return not row.get("member")


def _inside_window(at, bound_at):
    return bound_at is None or at > bound_at


def rounds_after(restaurant, bound):
    bound_at = _window_start(bound)
    threads = {}
    for row in restaurant.rows("threads.tsv"):
        threads.setdefault(row.get("dish") or "", []).append(row)
    found = []
    for row in restaurant.rows("pass.tsv"):
        at = moment(row.get("at") or "")
        if at is None or not _is_round_row(row) or not _inside_window(at, bound_at):
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
                "worker": _worker_thread(threads, dish, stamp),
                "reviewer": _reviewer_thread(threads, dish, stamp),
            }
        )
    found.sort(key=lambda item: item["at"])
    return found
