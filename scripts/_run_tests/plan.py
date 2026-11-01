from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from . import MAX_DEFAULT_JOBS, SHARD_SIZE
from .events import Shard, Test

DURATIONS = Path(__file__).resolve().parent / "durations.json"
DEFAULT_SECONDS = 5.0
DEFAULT_OVERHEAD = 1.0


@dataclass(frozen=True)
class ModuleCost:
    """What one module cost in the recording. A record read from the file holds one test whose seconds are that module's measured seconds per test, and `overhead` is the typical span of one shard of it less the gaps of that shard's tests."""

    tests: int
    seconds: float
    overhead: float


@dataclass(frozen=True)
class Costs:
    """The recorded cost of each module and the defaults for a module the record does not name."""

    modules: dict[str, ModuleCost]
    default_seconds: float
    default_overhead: float

    def estimate(self, module: str, count: int) -> float:
        """Return the seconds `count` tests of `module` are expected to take in one part, with the overhead of each shard its count makes of SHARD_SIZE."""
        if count <= 0:
            return 0.0
        record = self.modules.get(module)
        if record is not None and record.tests > 0:
            return count * (record.seconds / record.tests) + math.ceil(count / SHARD_SIZE) * record.overhead
        return count * self.default_seconds + math.ceil(count / SHARD_SIZE) * self.default_overhead


def read_durations(path=None) -> Costs:
    """Return the recorded module costs and the defaults for a module with no record.

    Read `path`, or the durations file beside this module. Each module's record is a list of its measured
    seconds per test and the typical overhead of one shard of it. A missing, unreadable, empty, or malformed
    file yields no module record and the built-in defaults; a file whose module entries are partly invalid
    keeps its own defaults and the entries it can read, except that a number too large to read as a float
    costs the whole record. Nothing a file holds can raise out of the split, and coverage never depends on
    what the file holds.
    """
    try:
        return _read_durations(path)
    except Exception:
        return Costs({}, DEFAULT_SECONDS, DEFAULT_OVERHEAD)


def _read_durations(path=None) -> Costs:
    target = Path(path) if path is not None else DURATIONS
    try:
        text = target.read_bytes().decode("utf-8")
    except (OSError, ValueError):
        return Costs({}, DEFAULT_SECONDS, DEFAULT_OVERHEAD)
    if not text.strip():
        return Costs({}, DEFAULT_SECONDS, DEFAULT_OVERHEAD)
    try:
        data = json.loads(text)
    except ValueError:
        return Costs({}, DEFAULT_SECONDS, DEFAULT_OVERHEAD)
    if not isinstance(data, dict):
        return Costs({}, DEFAULT_SECONDS, DEFAULT_OVERHEAD)
    default_seconds = data.get("default_seconds", DEFAULT_SECONDS)
    if isinstance(default_seconds, bool) or not isinstance(default_seconds, (int, float)):
        default_seconds = DEFAULT_SECONDS
    elif not default_seconds > 0 or not math.isfinite(default_seconds):
        default_seconds = DEFAULT_SECONDS
    default_overhead = data.get("default_overhead", DEFAULT_OVERHEAD)
    if isinstance(default_overhead, bool) or not isinstance(default_overhead, (int, float)):
        default_overhead = DEFAULT_OVERHEAD
    elif not default_overhead > 0 or not math.isfinite(default_overhead):
        default_overhead = DEFAULT_OVERHEAD
    modules: dict[str, ModuleCost] = {}
    raw = data.get("modules", None)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(key, str):
                continue
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                continue
            rate, overhead = value
            valid = True
            for number in (rate, overhead):
                if isinstance(number, bool) or not isinstance(number, (int, float)):
                    valid = False
                elif not number >= 0 or not math.isfinite(number):
                    valid = False
            if not valid:
                continue
            modules[key] = ModuleCost(1, float(rate), float(overhead))
    return Costs(modules, float(default_seconds), float(default_overhead))


def part_of(plan: tuple[Test, ...], count: int, costs: Costs) -> dict[int, int]:
    """Return the 1-based part each test's seq belongs to.

    Tests of one module run in one part, with two exceptions. A plan with fewer modules than parts places
    every test on its own, so a part that can hold one gets one. A module estimated above one part's share
    of the default worker count is placed one test at a time, so its tests can land in different parts. The
    estimate is the module's recorded seconds per test times its count plus one recorded overhead for each
    shard its count makes; a test placed on its own by the heavy-module rule costs its module's seconds per
    test plus one overhead, and the fewer-modules rule shares its module's estimate evenly.
    The answer depends only on the plan discovery returned and the durations data, so every part job of a
    run computes the same split. Every test is placed exactly once for any count of 1 or more and any
    durations data.
    """
    by_module: dict[str, list[Test]] = {}
    for test in plan:
        by_module.setdefault(test.module, []).append(test)
    items: list[tuple[float, str, list[Test]]] = []
    if len(by_module) >= count:
        total = sum(costs.estimate(module, len(tests)) for module, tests in by_module.items())
        limit = total / (count * MAX_DEFAULT_JOBS) if count > 0 else 0.0
        for module, tests in by_module.items():
            full = costs.estimate(module, len(tests))
            if full > limit:
                single = costs.estimate(module, 1)
                for test in tests:
                    items.append((single, test.id, [test]))
            else:
                items.append((full, module, tests))
    else:
        for module, tests in by_module.items():
            estimate = costs.estimate(module, len(tests))
            share = estimate / len(tests) if tests else 0.0
            for test in tests:
                items.append((share, test.id, [test]))
    items.sort(key=lambda item: (-item[0], item[1]))
    totals = [0.0] * count
    assignment: dict[int, int] = {}
    for cost, _name, tests in items:
        part = min(range(count), key=lambda index: (totals[index], index))
        for test in tests:
            assignment[test.seq] = part + 1
        totals[part] += cost
    return assignment


def tests_in_part(plan: tuple[Test, ...], index: int, count: int, durations_path=None, costs: Costs | None = None) -> tuple[Test, ...]:
    """Return the tests of part `index` of `count`, in plan order, by the recorded module costs."""
    used = costs if costs is not None else read_durations(durations_path)
    assignment = part_of(plan, count, used)
    return tuple(test for test in plan if assignment.get(test.seq) == index)


def launch_order(shards: list[Shard], parted: bool, cost=None) -> tuple[Shard, ...]:
    """Return the shards in the order the pool starts them.

    With `cost` given and `parted` true, return the shards sorted by `cost` descending, ties by module
    name and then shard number. With `cost` given and `parted` false, return them in build order. With no
    `cost`, keep the old order: one-test shards first in a part run and build order in a full run. The
    cost order is there because the pool starts the next shard as a worker frees, so starting the longest
    shard first keeps it out of the tail.
    """
    if cost is not None:
        if not parted:
            return tuple(shards)
        return tuple(sorted(shards, key=lambda shard: (-cost(shard), shard.module, shard.number)))
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
