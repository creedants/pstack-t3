from __future__ import annotations

import math
from pathlib import Path

from . import SHARD_SIZE
from .events import Shard, Test

def tests_in_part(plan: tuple[Test, ...], index: int, count: int) -> tuple[Test, ...]:
    grouped: dict[str, list[Test]] = {}
    for test in plan:
        grouped.setdefault(test.module, []).append(test)
    sequence: list[Test] = []
    for name in sorted(grouped, key=lambda name: (-len(grouped[name]), name)):
        sequence.extend(sorted(grouped[name], key=lambda test: test.id))
    chosen = {test.seq for position, test in enumerate(sequence) if position % count == index - 1}
    return tuple(test for test in plan if test.seq in chosen)


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
