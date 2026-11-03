from brigade_lib._lookback.state import moment
from brigade_lib.store import WORKER_ROLES

ROUND_CAP = 50
RANKS = {"send-back": 0, "blocked": 0, "pass-with-edits": 1, "pass": 2}
RESULTS = ("send-back", "blocked", "pass-with-edits", "pass")


def rank(verdict):
    """The result rank. An unknown result sorts with what went wrong, so a wrong result is never hidden."""
    return RANKS.get(verdict, 0)


def rank_rounds(rounds, cap):
    """The first cap rounds by rank then newest first, and the count of each result left out."""
    ordered = sorted(rounds, key=lambda item: moment(item["at"]), reverse=True)
    ordered.sort(key=lambda item: rank(item["verdict"]))
    counts = {}
    for item in ordered[cap:]:
        verdict = item["verdict"]
        counts[verdict] = counts.get(verdict, 0) + 1
    left_out = {}
    for result in RESULTS:
        if result in counts:
            left_out[result] = counts.pop(result)
    left_out.update(counts)
    return ordered[:cap], left_out


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
