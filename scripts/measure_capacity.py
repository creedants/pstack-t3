#!/usr/bin/env python3
"""Sample memory, swap, load, and T3 server latency. One TSV row per sample.

Memory used is MemTotal minus MemAvailable, in KiB. MemAvailable is what the
kernel says a new process can have without swapping. Swap used is SwapTotal
minus SwapFree.

The responsiveness probe is one HTTP GET / to the T3 server listening on
127.0.0.1. That server is the t3code process whose command line contains
bin.mjs and not acp-mcp-bridge. probe_ok is 1 only when the status is 200
and the body starts with the T3 HTML doctype. The timeout is 5 seconds.

sessions counts live T3 agent processes. A process counts when its command
line contains "grok agent", or when its comm is claude and the command line
contains "--model". claude-desktop and the long-lived codex app-server hosts
do not count. build_procs counts command lines that contain scripts/build.py
or unittest, so a build step can show that the work was running.

psi_full_avg10 is the 10-second "full" memory-stall average from
/proc/pressure/memory, or "na" when that file is absent.

Stop a ramp when swap used exceeds half of swap total, or when a probe fails
or takes longer than 2000 ms. Those rules are the hard stop. They are not a
fit ceiling. The caller decides the ceiling from the rows.
"""

import argparse
import http.client
import os
import time
from datetime import datetime, timezone
from pathlib import Path

COLUMNS = (
    "label",
    "ts",
    "mem_used_kib",
    "mem_available_kib",
    "swap_used_kib",
    "swap_total_kib",
    "load1",
    "probe_ms",
    "probe_ok",
    "probe_port",
    "sessions",
    "build_procs",
    "psi_full_avg10",
)

PROBE_TIMEOUT_S = 5.0
LISTEN = "0A"


def meminfo():
    fields = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        fields[key] = int(rest.strip().split()[0])
    missing = [key for key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree") if key not in fields]
    if missing:
        raise SystemExit(f"meminfo missing {', '.join(missing)}")
    return fields


def memory_kib(fields):
    used = fields["MemTotal"] - fields["MemAvailable"]
    swap_used = fields["SwapTotal"] - fields["SwapFree"]
    return used, fields["MemAvailable"], swap_used, fields["SwapTotal"]


def load1():
    return float(Path("/proc/loadavg").read_text().split()[0])


def psi_full_avg10():
    path = Path("/proc/pressure/memory")
    if not path.exists():
        return "na"
    for line in path.read_text().splitlines():
        if not line.startswith("full "):
            continue
        for part in line.split():
            if part.startswith("avg10="):
                return part.split("=", 1)[1]
    return "na"


def cmdline(pid_path):
    try:
        raw = (pid_path / "cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace")


def comm(pid_path):
    try:
        return (pid_path / "comm").read_text().strip()
    except OSError:
        return ""


def each_pid():
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            yield entry


def count_sessions():
    total = 0
    for entry in each_pid():
        text = cmdline(entry)
        if "grok agent" in text:
            total += 1
            continue
        if comm(entry) == "claude" and "--model" in text and "claude-desktop" not in text:
            total += 1
    return total


def count_builds():
    total = 0
    for entry in each_pid():
        text = cmdline(entry)
        if "scripts/build.py" in text or "unittest" in text:
            total += 1
    return total


def socket_inodes(pid_path):
    found = []
    fd_dir = pid_path / "fd"
    try:
        fds = list(fd_dir.iterdir())
    except OSError:
        return found
    for fd in fds:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            found.append(target[8:-1])
    return found


def t3_server_inodes():
    inodes = set()
    for entry in each_pid():
        text = cmdline(entry)
        if "t3code" not in text or "bin.mjs" not in text or "acp-mcp-bridge" in text:
            continue
        inodes.update(socket_inodes(entry))
    return inodes


def listen_ports(inodes):
    ports = []
    path = Path("/proc/net/tcp")
    if not path.exists():
        return ports
    for line in path.read_text().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 10 or parts[3] != LISTEN or parts[9] not in inodes:
            continue
        address, _, port_hex = parts[1].partition(":")
        if address != "0100007F":
            continue
        ports.append(int(port_hex, 16))
    return ports


def probe_once(port):
    started = time.perf_counter()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=PROBE_TIMEOUT_S)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read(32)
        ok = response.status == 200 and body.lstrip().lower().startswith(b"<!doctype html")
    except OSError:
        ok = False
    finally:
        connection.close()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return elapsed_ms, 1 if ok else 0


def sample(label, port):
    used, available, swap_used, swap_total = memory_kib(meminfo())
    if port is None:
        probe_ms, probe_ok = 0.0, 0
        reported_port = 0
    else:
        probe_ms, probe_ok = probe_once(port)
        reported_port = port
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "label": label,
        "ts": stamp,
        "mem_used_kib": used,
        "mem_available_kib": available,
        "swap_used_kib": swap_used,
        "swap_total_kib": swap_total,
        "load1": f"{load1():.2f}",
        "probe_ms": f"{probe_ms:.1f}",
        "probe_ok": probe_ok,
        "probe_port": reported_port,
        "sessions": count_sessions(),
        "build_procs": count_builds(),
        "psi_full_avg10": psi_full_avg10(),
    }


def choose_port():
    ports = listen_ports(t3_server_inodes())
    if not ports:
        return None
    for port in ports:
        _, ok = probe_once(port)
        if ok:
            return port
    return ports[0]


def main():
    parser = argparse.ArgumentParser(description="Print one TSV sample row per period.")
    parser.add_argument("--label", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    if args.samples < 1:
        raise SystemExit("samples must be at least 1")
    if args.interval < 0:
        raise SystemExit("interval must be >= 0")
    port = choose_port()
    print("\t".join(COLUMNS))
    for index in range(args.samples):
        row = sample(args.label, port)
        print("\t".join(str(row[name]) for name in COLUMNS))
        if index + 1 < args.samples and args.interval:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
