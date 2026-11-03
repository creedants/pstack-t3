import json
import re
from datetime import datetime, timezone

NAME_RE = re.compile(r"L(\d+)\.json")


def moment(text):
    """The datetime an ISO stamp names, with a naive value read as UTC, or None when it does not parse."""
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


def newest(restaurant):
    items = briefs(restaurant)
    return items[-1] if items else None


def target(restaurant):
    items = briefs(restaurant)
    if not items:
        return None
    for data in reversed(items):
        if data.get("recorded") is None:
            return data
    return items[-1]


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
