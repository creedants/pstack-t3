import json
import re
from datetime import datetime, timezone

NAME_RE = re.compile(r"L(\d+)\.json")


def moment(text):
    try:
        found = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def folder(restaurant):
    return restaurant.dir / "look-backs"


def _number(name):
    match = NAME_RE.fullmatch(name)
    return int(match.group(1)) if match else None


def _read(restaurant, name):
    try:
        data = json.loads((folder(restaurant) / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return data


def briefs(restaurant):
    found = []
    directory = folder(restaurant)
    try:
        names = sorted(path.name for path in directory.iterdir() if path.is_file())
    except OSError:
        return []
    for name in names:
        if _number(name) is None:
            continue
        data = _read(restaurant, name)
        if data is not None:
            found.append(data)
    found.sort(key=lambda data: int(data["id"][1:]))
    return found


def unreadable(restaurant):
    directory = folder(restaurant)
    try:
        names = sorted(path.name for path in directory.iterdir() if path.is_file())
    except OSError:
        return []
    bad = []
    for name in names:
        if _number(name) is None:
            continue
        if _read(restaurant, name) is None:
            bad.append(name)
    return sorted(bad)


def outstanding(restaurant):
    return [data for data in briefs(restaurant)
            if data.get("recorded") is None and not data.get("superseded")]


def target(restaurant):
    waiting = outstanding(restaurant)
    if waiting:
        return waiting[-1]
    done = [data for data in briefs(restaurant) if not data.get("superseded")]
    return done[-1] if done else None


def last_recorded(restaurant):
    best = None
    for data in briefs(restaurant):
        recorded = data.get("recorded")
        if not isinstance(recorded, dict):
            continue
        at = recorded.get("at") or ""
        if best is None or at > (best.get("recorded") or {}).get("at", ""):
            best = data
    return best


def write(restaurant, data):
    text = json.dumps(data, indent=2) + "\n"
    relative = f"look-backs/{data['id']}.json"
    restaurant.write(relative, text)
    return restaurant.dir / relative


def supersede(restaurant, kept):
    for data in outstanding(restaurant):
        if data["id"] != kept:
            write(restaurant, dict(data, superseded=kept))


def next_id(restaurant):
    numbers = []
    directory = folder(restaurant)
    try:
        names = [path.name for path in directory.iterdir() if path.is_file()]
    except OSError:
        names = []
    for name in names:
        number = _number(name)
        if number is not None:
            numbers.append(number)
    return f"L{max(numbers, default=0) + 1}"
