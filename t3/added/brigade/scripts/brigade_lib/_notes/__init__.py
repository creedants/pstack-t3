import re
from datetime import datetime, timedelta, timezone
from brigade_lib._reviews.rounds import earliest_record, window_phrases
from brigade_lib._tickets.paths import quoted_paths
from brigade_lib.shared import BrigadeError, clean, same_text
from brigade_lib.store import TABLES

MARKERS = ("safety", "privacy", "correctness")
LEDGER = "notes.tsv"
HEADER = ("path", "item", "at", "text")
SHARE_DAYS = 14


def store_earliest(restaurant):
    """The earliest at cell across every table of this store, or None when they hold none."""
    moments = []
    for table in TABLES:
        moment = earliest_record(restaurant.rows(table))
        if moment is not None:
            moments.append(moment)
    return min(moments) if moments else None


def marker(text):
    """The marker word a follow-up text starts with, or "".

    Case does not count. Remove leading whitespace, then one contiguous run of `*`, `_`, or backtick marks,
    then leading whitespace again. The marker word must be followed immediately by `:`.
    """
    stripped = text.lstrip().lstrip("*_`").lstrip()
    lowered = stripped.casefold()
    for name in MARKERS:
        if lowered.startswith(name + ":"):
            return name
    return ""


def _is_timestamp(value):
    if "T" not in value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def read_notes(restaurant):
    """Every row of the notes ledger as (path, item, at, text)."""
    path = restaurant.dir / LEDGER
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if number == 1 and line == "\t".join(HEADER):
            continue
        fields = line.split("\t")
        if len(fields) != 4 or not _is_timestamp(fields[2]):
            raise BrigadeError(f"{LEDGER} line {number} is malformed; fix or remove it")
        rows.append((fields[0], fields[1], fields[2], fields[3]))
    return rows


def note_rows(text, item, at, tracked):
    """The (path, item, at, text) rows one note makes."""
    paths = quoted_paths(text, tracked)
    if not paths:
        return [("", item, at, text)]
    return [(found, item, at, text) for found in paths]


def append_notes(restaurant, rows):
    """Write new note rows to the ledger and return the rows it appended."""
    existing = read_notes(restaurant)
    seen = {(path, item, same_text(text)) for path, item, _, text in existing}
    fresh = []
    for path, item, at, text in rows:
        key = (path, item, same_text(text))
        if key in seen:
            continue
        seen.add(key)
        fresh.append((path, item, at, text))
    kept = existing + fresh
    body = ["\t".join(HEADER)]
    body += ["\t".join([clean(path), clean(item), clean(at), clean(text)]) for path, item, at, text in kept]
    restaurant.write(LEDGER, "\n".join(body) + "\n")
    return fresh


def note_lines(restaurant, wanted, everything):
    """The printed lines for ticket notes."""
    rows = read_notes(restaurant)
    if everything:
        picked = rows
    else:
        names = set(wanted)
        picked = [row for row in rows if row[0] in names]
    if not picked:
        return "no notes"
    lines = []
    for path, item, at, text in picked:
        day = at[:10]
        if path:
            lines.append(f"{path}: {item} {day} {text}")
        else:
            lines.append(f"no path: {item} {day} {text}")
    return "\n".join(lines)


def share_lines(restaurant):
    """The close section lines for the follow-up share."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=SHARE_DAYS)
    total, reports = 0, 0
    for row in restaurant.rows("rail.tsv"):
        try:
            at = datetime.fromisoformat((row.get("at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at < cutoff:
            continue
        total += 1
        source = (row.get("source") or "").split(" (", 1)[0]
        if source == "report":
            reports += 1
    _, sentence = window_phrases(store_earliest(restaurant), now, SHARE_DAYS)
    if not total:
        return ["## Follow-up share", "", f"- no ticket was created {sentence}.", ""]
    percent = round(100 * reports / total)
    return ["## Follow-up share", "", f"- {reports} of {total} tickets created {sentence} came from a report ({percent}%).", ""]
