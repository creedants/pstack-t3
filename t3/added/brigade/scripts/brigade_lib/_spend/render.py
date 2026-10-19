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
from brigade_lib._spend.model import MILLION, NOT_RECORDED, NOT_REQUESTED, PLACES, _Counts, _add

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


