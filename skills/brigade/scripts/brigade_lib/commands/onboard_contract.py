import json
import os
import sqlite3
import subprocess
from pathlib import Path
from brigade_lib import generated, seam
from brigade_lib.leases import repository_cap

MISSING_MAP_REFUSAL = (
    "contract lists generated paths but holds no generated map; "
    "run land.py init --generated-map <MAP> to install one"
)


def _contract(c):
    try:
        return c._contract
    except AttributeError:
        pass
    try:
        r = seam.land_result(str(c.root), "status", "--json")
    except Exception as e:
        c._contract = ("invalid", str(e) or type(e).__name__)
        return c._contract
    if r.returncode != 0:
        e = (r.stderr or r.stdout).strip() or 0
        c._contract = ("missing", e) if "has no landing contract" in (r.stderr or "") else ("invalid", e)
        return c._contract
    try:
        p = json.loads(r.stdout)
    except ValueError:
        c._contract = ("invalid", 0)
        return c._contract
    if not isinstance(p, dict):
        c._contract = ("invalid", 0)
        return c._contract
    c._contract = ("ok", p)
    return c._contract


def _expected_common(root):
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return Path(r.stdout.strip()).resolve()
    except (OSError, ValueError):
        return None


def _db_rows(c):
    expected = _expected_common(c.root)
    if expected is None:
        return None
    base = Path(os.getenv("XDG_STATE_HOME") or str(Path.home() / ".local/state")) / "pstack-t3/landing"
    try:
        candidates = list(base.glob("*/land.db"))
    except (OSError, ValueError):
        return None
    for q in candidates:
        try:
            s = sqlite3.connect(str(q))
            try:
                rows = dict(s.execute("SELECT key, value FROM contract").fetchall())
            finally:
                s.close()
            try:
                common = Path(json.loads(rows.get("commonDir"))).resolve()
            except Exception:
                continue
            if common == expected:
                return rows
        except Exception:
            continue
    return None


def _read_landing(c):
    s, d = _contract(c)
    if s == "invalid":
        return ("invalid", d)
    if s == "missing":
        return ("missing", "")
    m = d.get("mode")
    if not isinstance(m, str) or not m:
        return ("invalid", 0)
    return ("configured", "lands by " + m)


def _read_cap(c):
    s, d = _contract(c)
    if s == "invalid":
        return ("invalid", d)
    if s == "missing":
        return ("none", None)
    try:
        cap = repository_cap(str(c.root))
    except Exception as e:
        return ("invalid", str(e) or type(e).__name__)
    if cap is None:
        return ("none", None)
    return ("configured", cap)


def _read_generated(c):
    s, d = _contract(c)
    if s == "invalid":
        return ("invalid", d)
    if s == "missing":
        return ("none", [])
    if not d.get("generated"):
        return ("none", [])
    raw = d.get("map")
    if not isinstance(raw, (list, tuple)) or not raw:
        return ("invalid", MISSING_MAP_REFUSAL)
    parsed = generated.parse(raw)
    if not parsed.rules:
        return ("invalid", MISSING_MAP_REFUSAL)
    f = sorted("/".join(r.source.segments) + " -> " + "/".join(r.target.segments) + " (" + r.kind.value + ")" for r in parsed.rules)
    return ("detected", f)


def _read_contract_checks(c):
    s, d = _contract(c)
    if s == "invalid":
        return ("invalid", d)
    if s == "missing":
        return ("unknown", 0)
    rows = _db_rows(c)
    if not rows or "checks" not in rows:
        return ("unknown", 0)
    try:
        checks = json.loads(rows["checks"])
    except ValueError:
        return ("invalid", 0)
    if not isinstance(checks, list):
        return ("invalid", 0)
    if not checks:
        return ("none", [])
    for i in checks:
        if not isinstance(i, str) or not i:
            return ("invalid", 0)
    return ("detected", checks)
