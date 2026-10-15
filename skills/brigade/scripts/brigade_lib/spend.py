import json
import math
import os
import re
import runpy
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from brigade_lib.reviews import item_verdicts, send_backs
from brigade_lib.shared import BrigadeError, age
from brigade_lib.store import PREFIX, WORKER_ROLES

OPEN = "open"
NOT_RECORDED = "not recorded"
NOT_REQUESTED = "not requested"
MODEL_MISSING = "model not recorded"
SEATS_DIFFER = "seats differ"
SEAT_MISSING = "seat not recorded"
CLOSED = ("merged", "dropped")
ABSENT_WORDS = (OPEN, NOT_RECORDED)
SINCE = re.compile(r"\d{4}-\d{2}-\d{2}")
DISH_ID = re.compile(re.escape(PREFIX["dishes.tsv"]) + r"\d+")
NATIVE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,80}$")
INDEX_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SCHEMA = "token schema is not the one this command knows"
UNREADABLE = "T3's database could not be opened read-only"
PRICE_KEYS = ("input", "cache_read", "cache_write", "output")
MILLION = Decimal(1000000)
PLACES = Decimal("0.000001")
PROVIDER_THREADS = "orchestration_v2_projection_provider_threads"
PROVIDER_TURNS = "orchestration_v2_projection_provider_turns"
THREADS = "orchestration_v2_projection_threads"
TURN_FIELDS = (
    "usageStatus", "inputTokens", "cachedInputTokens",
    "cacheCreationTokens", "outputTokens", "reasoningTokens",
)
DRIVERS = {
    "claudeAgent": "claude",
    "claudeLean": "claude",
    "codex": "turns",
    "opencode": "turns",
    "grok": "gap",
    "cursor": "gap",
}


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
    """rows and warn only. Every other attribute raises spend is read-only."""

    def __init__(self, restaurant):
        object.__setattr__(self, "_restaurant", restaurant)

    def __getattribute__(self, name):
        if name not in ("rows", "warn"):
            raise BrigadeError("spend is read-only")
        restaurant = object.__getattribute__(self, "_restaurant")
        return getattr(restaurant, name)


def add_arguments(sub):
    parser = sub.add_parser(
        "spend",
        help=(
            "print wall time, rounds, send-backs, and threads from the store. It writes nothing\n"
            "\n"
            "In text, a wholly missing measure prints its absence words and no counts. In JSON, one "
            "absence word is that string, and two are an object of word to count. Without `--tokens` "
            "the text ends `tokens not requested. Pass --tokens to read them.` and JSON sets "
            "`threads_without_a_token_source` to `not requested`. Token totals need `--tokens`.\n"
            "\n"
            "A Claude model line prints `reasoning not recorded` when "
            "`usage.output_tokens_details.thinking_tokens` is missing, null, negative, a bool, or not an integer. "
            "A recorded count prints as a number, and 0 stays 0. Adding two "
            "model totals prints `not recorded` for reasoning when either total was not recorded. Output "
            "and dollars stay the recorded totals.\n"
            "\n"
            "`--by reviewer` groups whole items by the seat that reviewed them. Its token lines "
            "include the worker's and every other ledger role's usage for those items. An item "
            "reviewed by several seats appears under one joined key."
        ),
    )
    parser.add_argument("--by", choices=tuple(DIMENSIONS), default="item", help="what one row groups (default: item)")
    parser.add_argument("--item", help="only this dish, such as D12")
    parser.add_argument("--since", help="only dishes fired at or after this UTC date, as YYYY-MM-DD")
    parser.add_argument(
        "--json", action="store_true",
        help=(
            "print the same report as JSON. Wall time is in whole seconds. A mixed measure is an object "
            "with value for the counted total and open or not recorded for the number of items with that gap"
        ),
    )
    parser.add_argument("--tokens", action="store_true",
                        help="also read token totals. A model lists dollars only when prices.json prices it")
    parser.add_argument("--t3-home", help="T3 Code base directory for --tokens (default $T3CODE_HOME, else ~/.t3)")


def spend_text(restaurant, args):
    question = _question(args)
    store = _Store(restaurant)
    with restaurant.read_lock(create=False):
        items = _items(store, question)
    _warn_shares(store, items)
    prices, totals = _read_prices_and_tokens(restaurant.dir, items, question)
    rows = _rows(items, question.by)
    if question.as_json:
        return json.dumps(_json_report(question, rows, items, totals, prices), indent=2)
    return _text(question, rows, items, totals, prices)


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


def _threads_by_item(items):
    groups = {}
    for item in items:
        for thread in item.threads or ():
            groups.setdefault(thread, []).append(item.dish_id)
    return groups


def _warn_shares(store, items):
    texts = []
    for ids in _threads_by_item(items).values():
        unique = sorted(set(ids), key=_dish_number)
        if len(unique) < 2:
            continue
        if len(unique) == 2:
            texts.append(f"{unique[0]} and {unique[1]} share a ledger thread")
        else:
            texts.append(f"{', '.join(unique[:-1])}, and {unique[-1]} share a ledger thread")
    for text in dict.fromkeys(texts):
        store.warn(text)


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


def _text(question, rows, items, totals, prices):
    lines = []
    for row in rows:
        lines.append(_row_line(row))
        models = _row_models(row, totals)
        if models == NOT_RECORDED:
            lines.append("  not recorded")
        elif models:
            for name, counts in models:
                lines.append(_model_line(name, counts, _dollars(counts, (prices or {}).get(name))))
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


def _json_report(question, rows, items, totals, prices):
    rendered = []
    for row in rows:
        entry = {
            "key": row.key,
            "wall": _json_measure(row.wall),
            "rounds": _json_measure(row.rounds),
            "send_backs": _json_measure(row.send_backs),
            "threads": _json_measure(row.threads),
        }
        models = _row_models(row, totals)
        if models is not None:
            entry["models"] = _json_models(models, prices)
        rendered.append(entry)
    body = {
        "by": question.by,
        "rows": rendered,
        "threads_without_a_token_source": NOT_REQUESTED if totals is None else _missing_threads(items, totals),
    }
    if question.since is not None:
        body["since"] = question.since.date().isoformat()
    return body


class _Bare(Exception):
    def __init__(self):
        # The failing log line must not leave this function inside the exception.
        Exception.__init__(self)


class _Schema(Exception):
    pass


class _Closed(Exception):
    pass


@dataclass(frozen=True)
class _Counts:
    incoming: int
    cache_read: int
    cache_write: int
    outgoing: int
    reasoning: int | None


@dataclass(frozen=True)
class _Call:
    ident: str
    model: str
    incoming: int
    cache_read: int
    cache_write: int
    outgoing: int
    reasoning: int | None


def _add(left, right):
    reasoning = None if left.reasoning is None or right.reasoning is None else left.reasoning + right.reasoning
    return _Counts(
        left.incoming + right.incoming, left.cache_read + right.cache_read,
        left.cache_write + right.cache_write, left.outgoing + right.outgoing,
        reasoning,
    )


def _read_prices_and_tokens(directory, items, question):
    if not question.tokens:
        return None, None
    return _prices(directory), _token_totals(items, question)


def _prices(directory):
    path = Path(directory) / "prices.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise BrigadeError("prices.json is not a price list") from None
    if not isinstance(raw, dict):
        raise BrigadeError("prices.json is not a price list")
    prices = {}
    for name, value in raw.items():
        if value == "subscription":
            prices[name] = value
            continue
        if not isinstance(value, dict) or set(value) != set(PRICE_KEYS):
            raise BrigadeError("prices.json is not a price list")
        prices[name] = {key: _rate(value[key]) for key in PRICE_KEYS}
    return prices


def _rate(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise BrigadeError("prices.json is not a price list")
    return Decimal(str(value))


def _blank(threads):
    return {thread: None for thread in threads}


def _token_totals(items, question):
    groups = _threads_by_item(items)
    shared = [thread for thread, ids in groups.items() if len(set(ids)) > 1]
    wanted = [thread for thread in groups if thread not in set(shared)]
    if not wanted:
        return _blank(shared)
    try:
        loaded, connection = _open_tokens(question)
    except _Closed:
        print(UNREADABLE, file=sys.stderr)
        return _blank([*shared, *wanted])
    try:
        folded = _folded_tokens(connection, loaded, wanted)
    except _Schema:
        print(SCHEMA, file=sys.stderr)
        return _blank([*shared, *wanted])
    totals = _blank(shared)
    totals.update(folded)
    return totals


def _open_tokens(question):
    try:
        loaded = runpy.run_path(str(Path(__file__).resolve().parent.parent / "activity.py"))
    except OSError:
        raise _Closed() from None
    try:
        database = loaded["t3_database"](question.t3_home, os.environ, Path.home())
        connection = loaded["open_t3"](database)
    except (loaded["SourceError"], sqlite3.Error):
        raise _Closed() from None
    return loaded, connection


def _folded_tokens(connection, loaded, wanted):
    try:
        _probe(connection)
        provider_rows = _rows_in(connection, _provider_sql, wanted, loaded["BATCH"])
        by_thread = {thread: [] for thread in wanted}
        for thread_id, provider_id, driver, native in provider_rows:
            if thread_id in by_thread:
                by_thread[thread_id].append((provider_id, driver, native))
        models = _recorded_models(connection, wanted, loaded["BATCH"])
        turn_ids = list(dict.fromkeys(
            provider_id
            for providers in by_thread.values()
            for provider_id, driver, _native in providers
            if DRIVERS.get(driver) == "turns" and _viable(providers)
        ))
        turn_rows = _rows_in(connection, _turn_sql, turn_ids, loaded["BATCH"]) if turn_ids else []
    finally:
        connection.close()
    turns = {}
    for row in turn_rows:
        turns.setdefault(row[0], []).append(row[1:])
    folded = {}
    for thread, providers in by_thread.items():
        folded[thread] = _ledger_totals(providers, turns, models.get(thread))
    return folded


def _viable(providers):
    return bool(providers) and all(DRIVERS.get(driver) in ("claude", "turns") for _provider, driver, _native in providers)


def _ledger_totals(providers, turns, model):
    if not _viable(providers):
        return None
    parts = []
    for provider_id, driver, native in providers:
        kind = DRIVERS.get(driver)
        part = _claude_totals(native) if kind == "claude" else _turn_totals(turns.get(provider_id, ()), model)
        if part is None:
            return None
        parts.append(part)
    return _merge(parts)


def _merge(parts):
    merged = {}
    for part in parts:
        for name, counts in part.items():
            merged[name] = _add(merged[name], counts) if name in merged else counts
    return merged or None


def _probe(connection):
    try:
        got = connection.execute("SELECT json_extract('{\"n\": 1}', '$.n')").fetchone()
    except sqlite3.Error:
        raise _Schema() from None
    if got is None or got[0] != 1:
        raise _Schema()
    _require_table(
        connection, PROVIDER_THREADS,
        ("thread_id", "provider_thread_id", "driver", "payload_json"), "thread_id",
    )
    _require_table(connection, PROVIDER_TURNS, ("provider_thread_id", "payload_json"), "provider_thread_id")
    _searches(connection, _provider_sql(1), ("x",))
    _searches(connection, _turn_sql(1), ("x",))


def _require_table(connection, table, columns, lead):
    try:
        found = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,),
        ).fetchone()
        present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        indexes = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?", (table,),
        ).fetchall()
    except sqlite3.Error:
        raise _Schema() from None
    if found is None or any(column not in present for column in columns):
        raise _Schema()
    if not any(_leads(connection, name, lead) for (name,) in indexes):
        raise _Schema()


def _leads(connection, name, column):
    if INDEX_NAME.fullmatch(name or "") is None:
        return False
    try:
        info = connection.execute(f"PRAGMA index_info({name})").fetchall()
    except sqlite3.Error:
        raise _Schema() from None
    ordered = sorted(info, key=lambda row: row[0])
    return bool(ordered) and ordered[0][2] == column


def _searches(connection, sql, params):
    try:
        plan = connection.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    except sqlite3.Error:
        raise _Schema() from None
    detail = " ".join(str(row[3]) for row in plan if len(row) > 3)
    if "SEARCH" not in detail or "SCAN" in detail:
        raise _Schema()


def _rows_in(connection, sql_of, ids, batch):
    groups = [tuple(ids[start:start + batch]) for start in range(0, len(ids), batch)]
    planned = [(sql_of(len(group)), group) for group in groups]
    for sql, group in planned:
        _searches(connection, sql, group)
    found = []
    for sql, group in planned:
        try:
            found.extend(connection.execute(sql, group).fetchall())
        except sqlite3.Error:
            raise _Schema() from None
    return found


def _marks(count):
    return ", ".join("?" * count)


def _provider_sql(count):
    return (
        "SELECT thread_id, provider_thread_id, driver, "
        "json_extract(payload_json, '$.nativeThreadRef.nativeId') "
        f"FROM {PROVIDER_THREADS} WHERE thread_id IN ({_marks(count)})"
    )


def _turn_sql(count):
    extracts = ", ".join(f"json_extract(payload_json, '$.turnTokenUsage.{field}')" for field in TURN_FIELDS)
    types = ", ".join(f"json_type(payload_json, '$.turnTokenUsage.{field}')" for field in TURN_FIELDS)
    return (
        f"SELECT provider_thread_id, {extracts}, {types} "
        f"FROM {PROVIDER_TURNS} WHERE provider_thread_id IN ({_marks(count)})"
    )


def _thread_sql(count):
    return (
        "SELECT thread_id, json_extract(payload_json, '$.modelSelection.model') "
        f"FROM {THREADS} WHERE thread_id IN ({_marks(count)})"
    )


def _recorded_model(value):
    if not isinstance(value, str) or MODEL_NAME.fullmatch(value) is None:
        return None
    return value


def _recorded_models(connection, wanted, batch):
    try:
        _require_table(connection, THREADS, ("thread_id", "payload_json"), "thread_id")
        rows = _rows_in(connection, _thread_sql, wanted, batch)
    except _Schema:
        return {}
    return {thread: _recorded_model(model) for thread, model in rows}


def _turn_totals(rows, model):
    if not rows:
        return None
    incoming = cache_read = cache_write = outgoing = reasoning_total = 0
    for row in rows:
        status, values, kinds = row[0], row[1:6], row[6:12]
        # json_extract turns JSON true into 1, so only a JSON integer counts.
        if kinds[0] != "text" or status != "complete" or any(kind != "integer" for kind in kinds[1:]):
            return None
        if any(type(value) is not int or value < 0 for value in values):
            return None
        fresh, cached, created, output, reasoning = values
        # Claude input_tokens already excludes cache. T3 inputTokens includes it.
        fresh -= cached + created
        if fresh < 0:
            return None
        incoming += fresh
        cache_read += cached
        cache_write += created
        outgoing += output
        reasoning_total += reasoning
    return {model or MODEL_MISSING: _Counts(incoming, cache_read, cache_write, outgoing, reasoning_total)}


def _claude_base():
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured is not None and configured.strip() != "":
        return Path(configured)
    return Path.home() / ".claude"


def _contained(path, root):
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _claude_totals(native):
    if not isinstance(native, str) or NATIVE_ID.fullmatch(native) is None:
        return None
    projects = _claude_base() / "projects"
    try:
        children = list(projects.iterdir()) if projects.is_dir() else []
        sessions = [
            child / f"{native}.jsonl"
            for child in children
            if child.is_dir() and (child / f"{native}.jsonl").is_file()
        ]
    except OSError:
        return None
    if len(sessions) != 1:
        return None
    session = sessions[0]
    files = [session]
    subagents = session.with_suffix("") / "subagents"
    try:
        if subagents.is_dir():
            files.extend(sorted(
                (path for path in subagents.iterdir() if path.is_file() and path.suffix == ".jsonl"),
                key=lambda path: path.name,
            ))
    except OSError:
        return None
    if any(not _contained(path, projects) for path in files):
        return None
    try:
        calls = [call for path in files for call in _read_log(path)]
        kept = _dedupe(calls)
    except _Bare:
        return None
    if not kept:
        return None
    totals = {}
    for call in kept.values():
        counts = _Counts(call.incoming, call.cache_read, call.cache_write, call.outgoing, call.reasoning)
        totals[call.model] = _add(totals[call.model], counts) if call.model in totals else counts
    return totals


def _read_log(path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise _Bare() from None
    calls = []
    for line in text.splitlines():
        if line.strip() == "":
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            raise _Bare() from None
        call = _call_from(obj)
        if call is not None:
            calls.append(call)
    return calls


def _usage_count(usage, key, required):
    if key not in usage:
        return None if required else 0
    value = usage[key]
    if type(value) is not int or value < 0:
        return None
    return value


def _thinking_tokens(usage):
    details = usage.get("output_tokens_details")
    if not isinstance(details, dict):
        return None
    value = details.get("thinking_tokens")
    if type(value) is not int or value < 0:
        return None
    return value


def _call_from(obj):
    if not isinstance(obj, dict):
        return None
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    if obj.get("type") != "assistant" and message.get("role") != "assistant":
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    ident = message.get("id")
    model = message.get("model")
    incoming = _usage_count(usage, "input_tokens", True)
    outgoing = _usage_count(usage, "output_tokens", True)
    cache_read = _usage_count(usage, "cache_read_input_tokens", False)
    cache_write = _usage_count(usage, "cache_creation_input_tokens", False)
    if not isinstance(ident, str) or ident == "" or None in (incoming, outgoing, cache_read, cache_write):
        raise _Bare()
    if not isinstance(model, str):
        model = ""
    if MODEL_NAME.fullmatch(model) is None:
        if (incoming, outgoing, cache_read, cache_write) == (0, 0, 0, 0):
            return None
        raise _Bare()
    return _Call(ident, model, incoming, cache_read, cache_write, outgoing, _thinking_tokens(usage))


def _dedupe(calls):
    found = {}
    for call in calls:
        previous = found.get(call.ident)
        if previous is None or call.outgoing > previous.outgoing:
            found[call.ident] = call
            continue
        if call.outgoing == previous.outgoing and call != previous:
            raise _Bare()
    return found


def _row_models(row, totals):
    if totals is None:
        return None
    combined = {}
    saw = False
    for item in row.items:
        for thread in dict.fromkeys(item.threads or ()):
            saw = True
            models = totals.get(thread)
            if not models:
                continue
            for name, counts in models.items():
                combined[name] = _add(combined[name], counts) if name in combined else counts
    if not saw:
        return None
    if not combined:
        return NOT_RECORDED
    return tuple((name, combined[name]) for name in sorted(combined))


def _dollars(counts, rate):
    if not isinstance(rate, dict):
        return None
    amount = (
        Decimal(counts.incoming) * rate["input"]
        + Decimal(counts.cache_read) * rate["cache_read"]
        + Decimal(counts.cache_write) * rate["cache_write"]
        + Decimal(counts.outgoing) * rate["output"]
    ) / MILLION
    return f"{amount.quantize(PLACES, rounding=ROUND_HALF_EVEN):f}"


def _model_line(name, counts, dollars):
    reasoning = NOT_RECORDED if counts.reasoning is None else counts.reasoning
    line = (
        f"  {name}  in {counts.incoming}  cache-read {counts.cache_read}  "
        f"cache-write {counts.cache_write}  out {counts.outgoing}  reasoning {reasoning}"
    )
    if dollars is not None:
        line += f"  ${dollars}"
    return line


def _json_models(models, prices):
    if models == NOT_RECORDED:
        return NOT_RECORDED
    body = []
    for name, counts in models:
        entry = {
            "model": name,
            "in": counts.incoming,
            "cache_read": counts.cache_read,
            "cache_write": counts.cache_write,
            "out": counts.outgoing,
            "reasoning": NOT_RECORDED if counts.reasoning is None else counts.reasoning,
        }
        dollars = _dollars(counts, (prices or {}).get(name))
        if dollars is not None:
            entry["dollars"] = dollars
        body.append(entry)
    return body
