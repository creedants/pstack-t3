Part of [the record](../capacity.md).

## Concurrent runs of the test runner

On 2026-10-10 one to four copies of `python3 scripts/run_tests.py` ran at once on this machine, and four copies ran at `-j 2`. Each of the 28 saved logs reports `Ran 1346 tests` and ends with `OK`. The runner reports `OK` only when no test is lost and no worker times out.

### Method

A script outside the repository started K copies together from one checkout, each as `python3 scripts/run_tests.py -j J` with its own output file. It started `scripts/measure_capacity.py` with `--interval 10` before starting the copies, and stopped the sampler after all copies exited. The worker reports that each group ran under one `land.py slot --` without `--exclusive`, so other agents' slotted commands could run beside every step.

Each configuration ran twice. The saved round 1 TSV files are empty. The worker attributes that loss to buffered output when the sampler was stopped. The sampler now flushes each row. Both rounds have test logs, and round 2 also has sampler rows. The harness waits for `load1` to reach 5 or less, with a configurable timeout of 120 seconds by default. The first samples in round 2 read 4.80 to 4.93 in the first four steps and 14.87 in the last.

### What else was running

The machine was not quiet. The first round 2 samples read `build_procs` of 0 for the 1-run step and 1 for each other step. The `sessions` column read 8 to 10 in the first four steps and rose to 21 during the `-j 2` step. About 4.5 minutes into that step `build_procs` rose from 13 or 14 to 19. It then read 18 or 19 until the last minute, reached 25, 31, and 27 in three samples, returned to 19 for two samples, and read 10 in the last. The worker reports seeing another worktree's test workers near the end. The TSV records only total process counts. It does not identify which processes belonged to this step, its nested test commands, or other work.

### Reported runner duration

The duration below is the longest `Ran 1346 tests in ...s` value in each group's logs. It is the runner's internal duration, not the wall time from group start to last exit. The harness prints group wall times and exit statuses, but that output was not saved in the evidence directory. The rate estimate is K times 3600 divided by the longest reported duration. It omits time outside the runner's timer and is not a measured group throughput. The duration ratio compares the longest reported duration with the 1-run step in the same round.

| Concurrent runs | `-j` | Worker limit | Round 1 duration | Round 2 duration | Round 1 rate estimate, suites per hour | Round 2 rate estimate, suites per hour | Round 2 duration ratio |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 4 | 4 | 223.748 s | 216.225 s | 16.1 | 16.6 | 1.00 |
| 2 | 4 | 8 | 286.627 s | 265.057 s | 25.1 | 27.2 | 1.23 |
| 3 | 4 | 12 | 357.141 s | 366.607 s | 30.2 | 29.5 | 1.70 |
| 4 | 4 | 16 | 435.521 s | 456.748 s | 33.1 | 31.5 | 2.11 |
| 4 | 2 | 8 | 547.151 s | 687.959 s | 26.3 | 20.9 | 3.18 |

### Machine readings, round 2

| Concurrent runs | `-j` | Samples | `load1` median | `load1` max | Probe median ms | Probe max ms | `psi_full_avg10` max | MemAvailable min MiB | Swap used max MiB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 4 | 22 | 6.5 | 6.8 | 2.2 | 961.3 | 0.00 | 11355 | 4464 |
| 2 | 4 | 27 | 9.1 | 9.9 | 2.5 | 458.9 | 0.28 | 11294 | 4735 |
| 3 | 4 | 37 | 15.0 | 17.2 | 3.2 | 946.1 | 0.00 | 11415 | 4746 |
| 4 | 4 | 45 | 19.2 | 20.5 | 6.8 | 1327.2 | 1.05 | 11165 | 4927 |
| 4 | 2 | 69 | 15.3 | 17.2 | 3.4 | 615.8 | 0.32 | 9979 | 5267 |

Every `probe_ok` value was 1. These readings include other work on the machine.

### What the numbers show

In this measurement, four runs at `-j 4` allowed 16 workers on 16 threads and 8 cores. Their `load1` median was 19.2, above the 16 line this document uses for degradation. Three runs allowed 12 workers and had a median of 15.0 and a maximum of 17.2. These points do not isolate the effect of adding a run, because other work was running too.

In round 2, MemAvailable stayed above 9.7 GiB in every sample, and swap used rose from 4.4 to 5.1 GiB over the five steps.

Four runs at `-j 2` and two at `-j 4` both allowed eight workers. Their rate estimates were 26.3 and 25.1 suites per hour in round 1, and 20.9 and 27.2 in round 2. Round 1 has no samples, so what else ran beside it is unknown. These points do not show how `-j` and the slot count trade off at other worker totals.

Probe maxima of 459 to 1327 ms stayed under the 2000 ms stop line. A maximum of 961 ms also appeared in the 1-run step, so a single slow probe is not specific to the larger steps in this measurement. The probe median rose from 3.2 to 6.8 ms between 3 and 4 runs.

The saved round 2 TSVs span 17:49:14 to 18:25:35 local time on that day, in the order one run, two, three, four, then four at `-j 2`. These durations and rate estimates describe this suite of 1,346 tests on a machine with other work running. They are not a general capacity result.
