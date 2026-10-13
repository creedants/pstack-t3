#!/usr/bin/env python3
"""Archive finished agent threads in one T3 Code project.

    t3-archive-threads.py [--config FILE] --project pstack-t3 [--older-than-hours 24]
                          [--coordinator ID] [--keep ID ...] [--top-level-settled] [--dry-run]
                          [--selection-only]

`--config FILE` counts only as the first two arguments. `ARCHIVE_T3_HOME` in that
file names T3 Code's home.

Run it from inside a T3 agent session: it calls T3's own `t3_thread_organize`
tool over the session's local tool endpoint, with the session's credential from
T3_CODE_MCP_AUTHORIZATION. It reads T3's state database read-only to choose the
threads and never writes to it. With `--selection-only` it prints candidates
and never reads the credential.

What it archives. A sub-agent thread that is not archived, has no run that is
running, queued, starting, preparing, or waiting and has no execution node or
provider-native subagent that is pending, running, or waiting including a null
run id and is not named as the child of such a subagent, and was last updated
at least --older-than-hours ago. A thread with a live ancestor is skipped
unless that ancestor is archived or is named with --coordinator, whose finished
children are archived by age like any other. A thread with a live runless
descendant is never archived. With
--top-level-settled it also archives a top-level thread that is settled, not
pinned, not the --coordinator thread, has no such live work, and is that old. A
thread named with --keep is never archived. Archiving is reversible in T3
(unarchive).
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

KEYS = ("ARCHIVE_T3_HOME",)
ME = Path(sys.argv[0]).name


def refuse(line) -> NoReturn:
    print(f"{ME}: {line}", file=sys.stderr)
    sys.exit(78)


def config_file_and_other_arguments(argv, environ):
    if argv[:1] == ["--config"]:
        if len(argv) < 2:
            print(f"{ME}: --config needs a file", file=sys.stderr)
            sys.exit(2)
        return Path(argv[1]), argv[2:]
    if environ.get("PSTACK_T3_OPS_CONFIG"):
        return Path(environ["PSTACK_T3_OPS_CONFIG"]), argv
    base = environ.get("XDG_CONFIG_HOME", "")
    base = Path(base) if base.startswith("/") else Path.home() / ".config"
    return base / "pstack-t3" / "ops.conf", argv


def read_config(path):
    if not os.path.exists(path):
        refuse(f"no config file at {path}. Copy ops/config.example to that path and set every key.")

    def bad(problem):
        refuse(f"config file {path} {problem}")

    try:
        if not path.is_file():
            raise OSError
        lines = path.read_bytes().decode("utf-8", "surrogateescape").split("\n")
    except OSError:
        bad("is not a readable file")
    seen, own = set(), {}
    for n, line in enumerate(lines, 1):
        line = line.strip(" \t\r")
        if not line or line.startswith("#"):
            continue
        key, eq, value = line.partition("=")
        if not eq or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            bad(f"line {n} is not KEY=value")
        if key in seen:
            bad(f"line {n} sets {key} a second time")
        seen.add(key)
        if key.startswith("ARCHIVE_"):
            if key not in KEYS:
                bad(f"line {n} sets {key}, and this script has no such key")
            own[key] = value.lstrip(" \t\r")
    for key in KEYS:
        if key not in own:
            bad(f"does not set {key}")
    home = own["ARCHIVE_T3_HOME"]
    if not re.fullmatch(r"/[A-Za-z0-9._/-]*", home):
        bad(f"puts '{home}' in ARCHIVE_T3_HOME. It must start with / and use only the characters A-Za-z0-9._/-.")
    return own


class Mcp:
    def __init__(self, runtime):
        self.url = json.loads(runtime.read_text())["origin"] + "/mcp"
        self.auth = os.environ["T3_CODE_MCP_AUTHORIZATION"]
        self.session = None
        self.n = 0
        self.call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "t3-archive-threads", "version": "1"}})
        self.call("notifications/initialized", None, notify=True)

    def call(self, method, params, notify=False):
        self.n += 1
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = self.n
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": self.auth, "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})
        if self.session:
            req.add_header("Mcp-Session-Id", self.session)
        with urllib.request.urlopen(req, timeout=60) as r:
            self.session = r.headers.get("Mcp-Session-Id") or self.session
            raw = r.read().decode()
        if notify or not raw.strip():
            return None
        if raw.lstrip().startswith("{"):
            return json.loads(raw)
        last = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
        return json.loads(last[-1]) if last else None

    def tool(self, name, arguments):
        out = self.call("tools/call", {"name": name, "arguments": arguments})
        if not out or "error" in out or (out.get("result") or {}).get("isError"):
            raise RuntimeError(json.dumps(out)[:300])
        return out["result"]


def choose(db, project, hours, keep, coordinator, top_level_settled):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    pids = [p for p, t in c.execute("select project_id, title from projection_projects") if t.lower() == project.lower()]
    if len(pids) != 1:
        sys.exit(f"expected one project titled {project!r}, found {len(pids)}")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    live = {t for (t,) in c.execute("select distinct thread_id from orchestration_v2_projection_runs where status in ('running','queued','starting','preparing','waiting')") if t is not None}
    runless = {t for (t,) in c.execute("select distinct thread_id from orchestration_v2_projection_nodes where status in ('pending','running','waiting')") if t is not None}
    runless |= {t for (t,) in c.execute("select distinct thread_id from orchestration_v2_projection_subagents where origin = 'provider_native' and status in ('pending','running','waiting')") if t is not None}
    runless |= {t for (t,) in c.execute("select distinct child_thread_id from orchestration_v2_projection_subagents where origin = 'provider_native' and status in ('pending','running','waiting')") if t is not None}
    live |= runless
    rows = c.execute("select thread_id, updated_at, archived_at, payload_json from orchestration_v2_projection_threads "
                     "where project_id = ? and deleted_at is null", (pids[0],)).fetchall()
    info = {}
    for tid, upd, arch, payload in rows:
        p = json.loads(payload or "{}")
        info[tid] = {"parent": (p.get("lineage") or {}).get("parentThreadId"), "updated": upd, "archived": arch,
                     "settled": bool(p.get("settledAt")), "pinned": bool(p.get("pinnedAt"))}
    parents, archived_at = {}, {}
    for tid, arch, payload in c.execute("select thread_id, archived_at, payload_json from orchestration_v2_projection_threads"):
        p = json.loads(payload or "{}")
        parents[tid] = (p.get("lineage") or {}).get("parentThreadId")
        archived_at[tid] = arch

    def ancestors(tid):
        out, seen = [], {tid}
        cur = parents.get(tid)
        while cur and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = parents.get(cur)
        return out

    def root(tid):
        seen = set()
        while info.get(tid, {}).get("parent") and tid not in seen:
            seen.add(tid)
            tid = info[tid]["parent"]
        return tid

    shielded = set()
    for tid in runless:
        shielded.update(ancestors(tid))

    picked, skipped = [], {"live": 0, "recent": 0, "under a running thread": 0, "kept": 0}
    for tid, i in info.items():
        if i["archived"]:
            continue
        top = root(tid)
        if tid in keep or top in keep and top != coordinator:
            skipped["kept"] += 1
        elif tid in live or tid in shielded:
            skipped["live"] += 1
        elif i["parent"]:
            block = any(a in live and a != coordinator and not archived_at.get(a) for a in ancestors(tid))
            if block:
                skipped["under a running thread"] += 1
            elif i["updated"] > cutoff:
                skipped["recent"] += 1
            else:
                picked.append(tid)
        elif top_level_settled and i["settled"] and not i["pinned"] and tid != coordinator and i["updated"] <= cutoff:
            picked.append(tid)
    return picked, skipped


def main():
    file, argv = config_file_and_other_arguments(sys.argv[1:], os.environ)
    userdata = Path(read_config(file)["ARCHIVE_T3_HOME"]) / "userdata"
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="project title, compared without regard to case")
    ap.add_argument("--older-than-hours", type=float, default=24)
    ap.add_argument("--coordinator", default="", help="a long-running top-level thread whose finished children may go")
    ap.add_argument("--keep", action="append", default=[], help="thread id to leave alone. A top-level id that is not the --coordinator also keeps every thread under it")
    ap.add_argument("--top-level-settled", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selection-only", action="store_true",
                    help="print candidate thread ids as a sorted JSON array of strings on stdout and exit without archiving")
    a = ap.parse_args(argv)
    picked, skipped = choose(userdata / "statev2.sqlite", a.project, a.older_than_hours, set(a.keep), a.coordinator, a.top_level_settled)
    if a.selection_only:
        print(json.dumps(sorted(picked)))
        return 0
    print(f"to archive: {len(picked)}; skipped: " + ", ".join(f"{k} {v}" for k, v in skipped.items()))
    if a.dry_run or not picked:
        return 0
    if "T3_CODE_MCP_AUTHORIZATION" not in os.environ:
        print(f"{ME}: T3_CODE_MCP_AUTHORIZATION is not set; re-run with --selection-only to print candidates without archiving", file=sys.stderr)
        return 1
    mcp = Mcp(userdata / "server-runtime.json")
    done = failed = 0
    for tid in picked:
        try:
            mcp.tool("t3_thread_organize", {"action": "archive", "threadId": tid})
            done += 1
        except Exception as error:  # report and continue with the rest
            failed += 1
            if failed <= 5:
                print(f"failed: {str(error)[:200]}")
    print(f"archived {done}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
