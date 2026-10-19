# Capacity of this machine

**Sample record.** The host, the readings, and the tables on this page come from the pstack-t3 maintainers' own machine on the dates shown, not from yours. Measure your own host with `scripts/measure_capacity.py` as [What was measured](capacity/agents.md#what-was-measured) shows, and read your own agent count and free memory from its rows.

Forty idle Haiku agents fit on this host on 2026-10-05, with 5.9 GiB of memory still available. A rerun later that morning held forty Haiku agents again and ran four copies of this repo's build and unit suite. Each copy completed one passing iteration during the sample window. A heavier follow-up, four copies of the unit suite with no pause, also completed one passing iteration per copy. Swap, load, and the root HTTP probe stayed inside the stop lines. Both runs stopped because the plan stopped, with memory still available.

## Hardware

The CPU is an AMD Ryzen 7 7735HS, 8 cores and 16 threads, with a max clock of 4830 MHz. `nproc` is 16.

MemTotal is 27421264 KiB, 26.2 GiB. The 2026-10-04 estimate calls this machine 26 GB of RAM. SwapTotal is 54842712 KiB, 52.3 GiB.

The machine had been up for about 2 days at both baselines.


See [agents](capacity/agents.md#what-was-measured) for the tables and [test runner](capacity/test-runner.md#concurrent-runs-of-the-test-runner) for the runner numbers.

## Map

Hardware and the summary stay here. The readings live in parts.

| Section | Part |
| --- | --- |
| Measurements plus idle plus forty plus comparison | [agents](capacity/agents.md#what-was-measured) |
| Test runner | [test runner](capacity/test-runner.md#concurrent-runs-of-the-test-runner) |
