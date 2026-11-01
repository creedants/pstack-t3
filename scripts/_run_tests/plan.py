from __future__ import annotations

import json
import math
from pathlib import Path

from . import SHARD_SIZE
from .events import Shard, Test

DURATIONS = Path(__file__).resolve().parent / "durations.json"
DEFAULT_SECONDS = 5.0


def read_durations(path=None) -> tuple[dict[str, float], float]:
    """Return the recorded seconds per test id and the default for a test with no record.

    Read `path`, or the durations file beside this module. A missing, unreadable, empty, or malformed file
    yields no record and the default. Invalid entries are skipped when possible; a read error falls back to
    no record and the default. Coverage never depends on what the file holds.
    """
    try:
        return _read_durations(path)
    except Exception:
        return {}, DEFAULT_SECONDS


def _read_durations(path=None) -> tuple[dict[str, float], float]:
    target = Path(path) if path is not None else DURATIONS
    try:
        text = target.read_bytes().decode("utf-8")
    except (OSError, ValueError):
        return {}, DEFAULT_SECONDS
    if not text.strip():
        return {}, DEFAULT_SECONDS
    try:
        data = json.loads(text)
    except ValueError:
        return {}, DEFAULT_SECONDS
    if not isinstance(data, dict):
        return {}, DEFAULT_SECONDS
    default = data.get("default_seconds", DEFAULT_SECONDS)
    if isinstance(default, bool) or not isinstance(default, (int, float)):
        default = DEFAULT_SECONDS
    elif not default > 0 or not math.isfinite(default):
        default = DEFAULT_SECONDS
    seconds: dict[str, float] = {}
    raw = data.get("seconds", {})
    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(key, str):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not value > 0 or not math.isfinite(value):
                continue
            seconds[key] = float(value)
    return seconds, float(default)


def part_of(plan: tuple[Test, ...], count: int, seconds: dict[str, float], default: float) -> dict[int, int]:
    """Return the 1-based part each test's seq belongs to.

    Tests are ordered by recorded seconds, longest first, ties by test id and then by seq, and each goes to
    the part with the smallest running total, the lowest part number on a tie. Every test is placed exactly
    once for any count of 1 or more and any durations data, and the answer depends only on the test ids,
    their seconds, and count, so every part job of a run computes the same split.
    """
    ordered = sorted(plan, key=lambda test: (-seconds.get(test.id, default), test.id, test.seq))
    totals = [0.0] * count
    assignment: dict[int, int] = {}
    for test in ordered:
        part = min(range(count), key=lambda index: (totals[index], index))
        assignment[test.seq] = part + 1
        totals[part] += seconds.get(test.id, default)
    return assignment


def tests_in_part(plan: tuple[Test, ...], index: int, count: int, durations_path=None) -> tuple[Test, ...]:
    """Return the tests of part `index` of `count`, in plan order, by the recorded seconds."""
    seconds, default = read_durations(durations_path)
    assignment = part_of(plan, count, seconds, default)
    return tuple(test for test in plan if assignment.get(test.seq) == index)


def launch_order(shards: list[Shard], parted: bool) -> tuple[Shard, ...]:
    if not parted:
        return tuple(shards)
    ones = [shard for shard in shards if len(shard.seqs) == 1]
    rest = [shard for shard in shards if len(shard.seqs) != 1]
    return tuple(ones + rest)


def build_shards(plan: tuple[Test, ...], size: int = SHARD_SIZE) -> list[Shard]:
    by_seq = {test.seq: test for test in plan}
    modules: dict[str, list[int]] = {}
    for test in plan:
        modules.setdefault(test.module, []).append(test.seq)
    shards = []
    for module, seqs in sorted(modules.items(), key=lambda item: -len(item[1])):
        count = math.ceil(len(seqs) / size)
        # When a module needs more than one shard, a stride puts neighbours in discovery order in different shards.
        for offset in range(count):
            mine = tuple(seqs[offset::count])
            shards.append(Shard(len(shards) + 1, module, mine, tuple(by_seq[seq].cls for seq in mine)))
    return shards
