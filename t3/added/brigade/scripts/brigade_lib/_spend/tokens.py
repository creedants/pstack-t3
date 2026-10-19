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
from brigade_lib._spend.claude import _claude_totals
from brigade_lib._spend.items import _threads_by_item
from brigade_lib._spend.model import DRIVERS, INDEX_NAME, MODEL_MISSING, MODEL_NAME, PRICE_KEYS, PROVIDER_THREADS, PROVIDER_TURNS, SCHEMA, THREADS, TURN_FIELDS, UNREADABLE, _Bare, _Closed, _Counts, _Schema, _add, _merge

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
        loaded = runpy.run_path(str(Path(__file__).resolve().parents[2] / "activity.py"))
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


