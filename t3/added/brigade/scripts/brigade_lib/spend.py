import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from brigade_lib.reviews import item_verdicts, send_backs
from brigade_lib.shared import BrigadeError, age
from brigade_lib.store import PREFIX, WORKER_ROLES

OPEN = "open"
NOT_RECORDED = "not recorded"
NOT_REQUESTED = "not requested"
SEATS_DIFFER = "seats differ"
SEAT_MISSING = "seat not recorded"
CLOSED = ("merged", "dropped")
ABSENT_WORDS = (OPEN, NOT_RECORDED)
SINCE = re.compile(r"\d{4}-\d{2}-\d{2}")
DISH_ID = re.compile(re.escape(PREFIX["dishes.tsv"]) + r"\d+")


@dataclass(frozen=True)
class Measure:
    value: int | None
    gaps: tuple[tuple[str, int], ...]

    @staticmethod
    def of(value):
        return Measure(value, ())

    @staticmethod
    def missing(word):
        return Measure(None, ((word, 1),))


def _total(measures):
    value, counted, gaps = 0, False, {}
    for measure in measures:
        if measure.value is None:
            for word, count in measure.gaps:
                gaps[word] = gaps.get(word, 0) + count
        else:
            value += measure.value
            counted = True
    ordered = tuple((word, gaps[word]) for word in ABSENT_WORDS if gaps.get(word))
    if not counted:
        return Measure(None, ordered)
    return Measure(value, ordered)


@dataclass(frozen=True)
class Item:
    dish_id: str
    kind: str
    goal: str
    fired: datetime | None
    wall: Measure
    rounds: int
    send_backs: int
    worker_key: str
    reviewer_key: str
    threads: tuple[str, ...] | None


def _item_key(item):
    return item.dish_id


def _goal_key(item):
    return item.goal or "goal not recorded"


def _kind_key(item):
    return item.kind or "kind not recorded"


def _model_key(item):
    return item.worker_key


def _reviewer_key(item):
    return item.reviewer_key


DIMENSIONS = {
    "item": _item_key,
    "goal": _goal_key,
    "kind": _kind_key,
    "model": _model_key,
    "reviewer": _reviewer_key,
}


@dataclass(frozen=True)
class _Question:
    by: str
    item: str | None
    since: datetime | None
    tokens: bool
    t3_home: str | None
    as_json: bool


@dataclass(frozen=True)
class _Row:
    key: str
    wall: Measure
    rounds: Measure
    send_backs: Measure
    threads: Measure
    items: tuple


class _Store:
    """rows and warn only. Every other lookup is a write this command refuses."""

    def __init__(self, restaurant):
        object.__setattr__(self, "_restaurant", restaurant)

    def __getattribute__(self, name):
        if name not in ("rows", "warn"):
            raise BrigadeError("spend is read-only")
        restaurant = object.__getattribute__(self, "_restaurant")
        return getattr(restaurant, name)


def add_arguments(sub):
    parser = sub.add_parser("spend", help="print wall time, rounds, send-backs, and threads from the store. It writes nothing")
    parser.add_argument("--by", choices=tuple(DIMENSIONS), default="item", help="what one row groups (default: item)")
    parser.add_argument("--item", help="only this dish, such as D12")
    parser.add_argument("--since", help="only dishes fired at or after this UTC date, as YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="print the same report as JSON")
    parser.add_argument("--tokens", action="store_true",
                        help="also read token totals. A model lists dollars only when prices.json prices it")
    parser.add_argument("--t3-home", help="T3 Code base directory for --tokens (default $T3CODE_HOME, else ~/.t3)")


def spend_text(restaurant, args):
    question = _question(args)
    store = _Store(restaurant)
    with restaurant.read_lock(create=False):
        items = _items(store, question)
    _warn_shares(store, items)
    totals = _unrecorded(items) if question.tokens else None
    rows = _rows(items, question.by)
    if question.as_json:
        return json.dumps(_json_report(question, rows, items, totals), indent=2)
    return _text(question, rows, items, totals)


def _question(args):
    since = None
    if args.since is not None:
        if SINCE.fullmatch(args.since) is None:
            raise BrigadeError(f"--since {args.since!r} is not YYYY-MM-DD")
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise BrigadeError(f"--since {args.since!r} is not YYYY-MM-DD") from None
    item = args.item
    if item is not None and DISH_ID.fullmatch(item) is None:
        raise BrigadeError(f"--item {item!r} is not a dish id")
    return _Question(args.by, item, since, bool(args.tokens), getattr(args, "t3_home", None), bool(args.json))


def _stamp(text):
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _seat_text(seat):
    _head, slash, tail = seat.partition("/")
    return tail if slash else seat


def _one_seat(seats):
    distinct = list(dict.fromkeys(seat for seat in seats if seat))
    if not distinct:
        return None
    if len(distinct) > 1:
        return SEATS_DIFFER
    return _seat_text(distinct[0])


def _worker_key(dish_id, thread_rows, pass_rows):
    seats = [row["seat"] for row in thread_rows if row["dish"] == dish_id and row["role"] in WORKER_ROLES]
    chosen = _one_seat(seats)
    if chosen is not None:
        return chosen
    authors = [row["author"] for row in item_verdicts(pass_rows, dish_id)]
    return _one_seat(authors) or SEAT_MISSING


def _reviewer_key(dish_id, pass_rows):
    seats = sorted({row["verifier"] for row in item_verdicts(pass_rows, dish_id) if row["verifier"]})
    return " + ".join(seats) if seats else SEAT_MISSING


def _wall(dish, log_rows):
    closes = [row for row in log_rows if row["kind"] == "dish" and row["id"] == dish["id"] and row["state"] in CLOSED]
    if not closes:
        if dish["state"] in CLOSED:
            return Measure.missing(NOT_RECORDED)
        return Measure.missing(OPEN)
    fired = _stamp(dish["at"])
    ends = [_stamp(row["at"]) for row in closes]
    if fired is None or any(end is None for end in ends):
        return Measure.missing(NOT_RECORDED)
    delta = (max(ends) - fired).total_seconds()
    if delta < 0:
        return Measure.missing(NOT_RECORDED)
    return Measure.of(int(delta))


def _threads(dish_id, thread_rows):
    found = []
    for row in thread_rows:
        if row["dish"] != dish_id or not row["thread"] or row["thread"] in found:
            continue
        found.append(row["thread"])
    return tuple(found) if found else None


def _items(store, question):
    dishes = store.rows("dishes.tsv")
    passed = store.rows("pass.tsv")
    log_rows = store.rows("log.tsv")
    thread_rows = store.rows("threads.tsv")
    if question.item is not None and all(row["id"] != question.item for row in dishes):
        raise BrigadeError(f"no {question.item} in dishes.tsv")
    items = []
    for dish in dishes:
        if question.item is not None and dish["id"] != question.item:
            continue
        fired = _stamp(dish["at"])
        if question.since is not None and (fired is None or fired < question.since):
            continue
        ident = dish["id"]
        items.append(Item(
            ident, dish["kind"], dish["goal"], fired, _wall(dish, log_rows),
            len(item_verdicts(passed, ident)), len(send_backs(passed, ident)),
            _worker_key(ident, thread_rows, passed), _reviewer_key(ident, passed),
            _threads(ident, thread_rows),
        ))
    return items


def _dish_number(dish_id):
    return int(dish_id[len(PREFIX["dishes.tsv"]):])


def _warn_shares(store, items):
    groups = {}
    for item in items:
        for thread in item.threads or ():
            groups.setdefault(thread, []).append(item.dish_id)
    texts = []
    for ids in groups.values():
        unique = sorted(set(ids), key=_dish_number)
        if len(unique) < 2:
            continue
        if len(unique) == 2:
            texts.append(f"{unique[0]} and {unique[1]} share a ledger thread")
        else:
            texts.append(f"{', '.join(unique[:-1])}, and {unique[-1]} share a ledger thread")
    for text in dict.fromkeys(texts):
        store.warn(text)


def _unrecorded(items):
    totals = {}
    for item in items:
        for thread in item.threads or ():
            totals.setdefault(thread, None)
    return totals


def _rows(items, by):
    order, buckets = [], {}
    for item in items:
        key = DIMENSIONS[by](item)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(item)
    rows = []
    for key in order:
        group = tuple(buckets[key])
        # A reviewer row has no wall clock, so the item duration is not copied onto it.
        walls = [Measure.missing(NOT_RECORDED) for _ in group] if by == "reviewer" else [item.wall for item in group]
        threads = [Measure.missing(NOT_RECORDED) if item.threads is None else Measure.of(len(item.threads)) for item in group]
        rows.append(_Row(
            key, _total(walls), _total(Measure.of(item.rounds) for item in group),
            _total(Measure.of(item.send_backs) for item in group), _total(threads), group,
        ))
    return rows


def _gap_suffix(measure):
    if not measure.gaps:
        return ""
    return " (" + ", ".join(f"{count} {word}" for word, count in measure.gaps) + ")"


def _measure_text(measure, wall=False):
    if measure.value is None:
        return ", ".join(word for word, _count in measure.gaps)
    shown = age(timedelta(seconds=measure.value)) if wall else str(measure.value)
    return shown + _gap_suffix(measure)


def _row_line(row):
    return (f"{row.key}  wall {_measure_text(row.wall, wall=True)}  rounds {_measure_text(row.rounds)}  "
            f"send-backs {_measure_text(row.send_backs)}  threads {_measure_text(row.threads)}")


def _missing_threads(items, totals):
    missing = {thread for item in items for thread in (item.threads or ()) if totals.get(thread) is None}
    return len(missing)


def _text(question, rows, items, totals):
    lines = []
    for row in rows:
        lines.append(_row_line(row))
        if totals is not None and any(item.threads for item in row.items):
            lines.append("  not recorded")
    if totals is None:
        lines.append("tokens not requested. Pass --tokens to read them.")
    else:
        lines.append(f"threads without a token source {_missing_threads(items, totals)}")
    return "\n".join(lines)


def _json_measure(measure):
    if measure.value is None and len(measure.gaps) == 1:
        return measure.gaps[0][0]
    if measure.value is not None and not measure.gaps:
        return measure.value
    body = {}
    if measure.value is not None:
        body["value"] = measure.value
    body.update({word: count for word, count in measure.gaps})
    return body


def _json_report(question, rows, items, totals):
    body = {
        "by": question.by,
        "rows": [{
            "key": row.key,
            "wall": _json_measure(row.wall),
            "rounds": _json_measure(row.rounds),
            "send_backs": _json_measure(row.send_backs),
            "threads": _json_measure(row.threads),
        } for row in rows],
        "threads_without_a_token_source": NOT_REQUESTED if totals is None else _missing_threads(items, totals),
    }
    if question.since is not None:
        body["since"] = question.since.date().isoformat()
    return body
