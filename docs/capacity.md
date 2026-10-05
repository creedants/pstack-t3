# Capacity of this machine

Forty idle T3 agents fit on this host on 2026-10-05, and so did those forty agents with four copies of this repo's build and unit tests running. Memory, swap, load, and T3 server latency all stayed inside the stop lines below. The run stopped at 40 because that was the top step, with 5.9 GiB of memory still available.

## Hardware

The CPU is an AMD Ryzen 7 7735HS, 8 cores and 16 threads, with a max clock of 4830 MHz. `nproc` is 16.

MemTotal is 27421264 KiB, 26.2 GiB. The 2026-10-04 estimate calls this machine 26 GB of RAM. SwapTotal is 54842712 KiB, 52.3 GiB.

The machine had been up for 2 days when the baseline was taken. The baseline 1-minute load median was 1.64.

## What was measured

`scripts/measure_capacity.py` prints one TSV row per sample. It uses the Python standard library only.

Memory used is MemTotal minus MemAvailable, in KiB. MemAvailable is what the kernel will give a new allocation without swapping. Swap used is SwapTotal minus SwapFree. `load1` is the first field of `/proc/loadavg`. `psi_full_avg10` is the 10-second full memory-stall average from `/proc/pressure/memory`.

The responsiveness probe is one HTTP GET `/` to the T3 server process. That process is the `t3code` command line that contains `bin.mjs` and does not contain `acp-mcp-bridge`, listening on 127.0.0.1. On this run the port was 3773. `probe_ok` is 1 when the status is 200 and the body starts with the T3 HTML doctype. The timeout is 5 seconds.

`sessions` counts live agent processes. A process counts when its command line contains `grok agent`, or when its comm is `claude` and its command line contains `--model`. claude-desktop does not count. The long-lived `codex app-server` hosts do not count. `build_procs` counts command lines that contain `scripts/build.py` or `unittest`.

Each step below is five samples, five seconds apart. The command was

```bash
python3 /home/marcus/Projects/pstack-t3/skills/landing/scripts/land.py slot --exclusive -- python3 scripts/measure_capacity.py --label <name> --samples 5 --interval 5
```

The exclusive slot holds every governor slot, so other governed builds and tests wait. The agents were already resident. The idle-10, idle-20, and idle-40 samples started about 60 seconds after the new processes were up, so `load1` is not the spawn spike. `load1` is a one-minute average, so five samples inside one minute are not five independent load readings. The median is the summary. The raw rows are below.

Stop a ramp when swap used exceeds half of swap total, or when a probe fails or takes longer than 2000 ms. A step is also past the line this run treated as degradation when MemAvailable drops under 1 GiB, when `load1` exceeds 16, or when the probe median is both over 100 ms and over five times the baseline median. Those lines were set before the ramp.

## How the agents were started

The agents were T3 child tasks on provider `claudeAgent`, model `claude-haiku-4-5`, with thinking set to false. That is Haiku's low reasoning setting. The catalog also has Cursor `gpt-5.4-nano` with reasoning `none`. This measurement used Haiku, because the Claude driver was already starting sessions on this host.

The pilot was asked to run `sleep 2400` and to do nothing else. The harness refused a standalone sleep. The Claude process stayed after the turn ended, in state sleeping, at about 266 MiB resident. The other 39 were asked to reply with the single word idle and not to use tools. Their processes stayed too. `task_cancel` on all 40 tasks returned completed. The processes stayed until they were signaled. After that signal, no `claude-haiku-4-5` process was left.

Each Haiku process started `cua` and `cua-driver` children. Those servers launch with a session on this host, so their memory is part of the agent cost.

The baseline already included other work. Workers were running in the `pstack-t3-d1` and `pstack-t3-d2` worktrees, and the d1 worker had started Claude children. The baseline `sessions` value is 8, which includes this run's own agent. Two `codex app-server` processes had been up for about two days and are outside that count. After the baseline, the background session count dropped by one and held. The later counts are 17, 27, and 47, which is that background plus 10, 20, and 40 Haiku processes.

The four build loops ran in four copies of the tree under `/tmp`, so they did not write this worktree. Each loop ran `python3 scripts/build.py` and then `python3 -m unittest discover -s tests`. They were children of the exclusive measurement. Each loop was started with `land.py slot`, which does not take a second lock while a slot is already held. `build_procs` was 13 on every builds-4 row.

## Raw samples

```
label	ts	mem_used_kib	mem_available_kib	swap_used_kib	swap_total_kib	load1	probe_ms	probe_ok	probe_port	sessions	build_procs	psi_full_avg10
baseline	2026-10-05T01:57:06Z	16008772	11412492	2276392	54842712	1.67	0.7	1	3773	8	0	0.00
baseline	2026-10-05T01:57:11Z	15927416	11493848	2276392	54842712	1.70	1.5	1	3773	8	0	0.00
baseline	2026-10-05T01:57:16Z	15900124	11521140	2276392	54842712	1.64	4.9	1	3773	8	0	0.00
baseline	2026-10-05T01:57:21Z	15949180	11472084	2276392	54842712	1.51	1.7	1	3773	8	0	0.00
baseline	2026-10-05T01:57:26Z	15955284	11465980	2276392	54842712	1.47	1.7	1	3773	8	0	0.00
idle-10	2026-10-05T02:00:49Z	17459968	9961296	2318492	54842712	1.76	0.7	1	3773	17	0	0.00
idle-10	2026-10-05T02:00:54Z	17494056	9927208	2318492	54842712	2.02	2.1	1	3773	17	0	0.00
idle-10	2026-10-05T02:00:59Z	17511608	9909656	2318492	54842712	1.86	1.6	1	3773	17	0	0.00
idle-10	2026-10-05T02:01:05Z	17468356	9952908	2318492	54842712	1.79	56.5	1	3773	17	0	0.00
idle-10	2026-10-05T02:01:10Z	17434496	9986768	2318492	54842712	1.65	1.6	1	3773	17	2	0.00
idle-20	2026-10-05T02:02:59Z	18916076	8505188	2382888	54842712	1.56	1.7	1	3773	27	0	0.00
idle-20	2026-10-05T02:03:04Z	18941104	8480160	2382888	54842712	1.44	1.5	1	3773	27	0	0.00
idle-20	2026-10-05T02:03:09Z	18932912	8488352	2382888	54842712	1.32	1.3	1	3773	27	0	0.00
idle-20	2026-10-05T02:03:14Z	18854256	8567008	2382888	54842712	1.22	1.7	1	3773	27	0	0.00
idle-20	2026-10-05T02:03:19Z	18879136	8542128	2382888	54842712	1.12	2.1	1	3773	27	0	0.00
idle-40	2026-10-05T02:05:05Z	21312392	6108872	2505096	54842712	1.34	0.7	1	3773	47	0	0.00
idle-40	2026-10-05T02:05:10Z	21234544	6186720	2505096	54842712	1.36	6.1	1	3773	47	0	0.00
idle-40	2026-10-05T02:05:16Z	21318680	6102584	2505096	54842712	1.25	1.5	1	3773	47	0	0.00
idle-40	2026-10-05T02:05:21Z	21167752	6253512	2505096	54842712	1.15	2.1	1	3773	47	0	0.00
idle-40	2026-10-05T02:05:26Z	21160608	6260656	2505096	54842712	1.14	1.6	1	3773	47	0	0.00
builds-4	2026-10-05T02:05:56Z	21107792	6313472	2505096	54842712	1.36	1.2	1	3773	47	13	0.00
builds-4	2026-10-05T02:06:01Z	21093432	6327832	2505096	54842712	1.33	1.4	1	3773	47	13	0.00
builds-4	2026-10-05T02:06:06Z	21100520	6320744	2505096	54842712	1.46	1.5	1	3773	47	13	0.00
builds-4	2026-10-05T02:06:11Z	21104048	6317216	2505096	54842712	1.75	2.8	1	3773	47	13	0.00
builds-4	2026-10-05T02:06:16Z	21101096	6320168	2505096	54842712	1.93	1.6	1	3773	47	13	0.00
```

## What limited each step

The median of each column is the middle value of the five sorted samples.

| Step | Memory used KiB | Available KiB | Swap used KiB | load1 | Probe ms | Sessions | Build procs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 15949180 | 11472084 | 2276392 | 1.64 | 1.7 | 8 | 0 |
| idle-10 | 17468356 | 9952908 | 2318492 | 1.79 | 1.6 | 17 | 0 |
| idle-20 | 18916076 | 8505188 | 2382888 | 1.32 | 1.7 | 27 | 0 |
| idle-40 | 21234544 | 6186720 | 2505096 | 1.25 | 1.6 | 47 | 0 |
| builds-4 | 21101096 | 6320168 | 2505096 | 1.46 | 1.5 | 47 | 13 |

Every `probe_ok` value was 1. Every `psi_full_avg10` value was 0.00.

**Baseline.** 15.2 GiB of memory was in use and 10.9 GiB was available. Swap used was 2.17 GiB, 4.2 percent of swap. `load1` ranged from 1.47 to 1.70. The probe ranged from 0.7 ms to 4.9 ms. The host was not CPU bound. The memory in use was the desktop plus the agents already running, including the two other workers.

**Ten, twenty, and forty idle agents.** From the baseline median to the idle-40 median, available memory fell by 5285364 KiB. That is 132134 KiB, 129 MiB, per added Haiku across the whole step. The 10-agent step was 148 MiB each, and the 20-agent step was 145 MiB each. The per-agent drop got smaller as more processes shared the Claude binary's file pages. A snapshot of anonymous RSS taken just after the idle-40 samples, when 41 Haiku processes were present, was about 146 MiB per process tree, helpers included. Anonymous RSS and the MemAvailable drop are the same story at two resolutions. `psi_full_avg10` stayed 0.00, so the kernel was not stalling on memory.

Swap grew by 228704 KiB, 223 MiB, from baseline to idle-40, and sat at 4.6 percent. The stop line is 50 percent. `load1` at idle-40 was 1.25, range 1.14 to 1.36, which is under the baseline. Idle agents do not keep a core busy after the turn ends. The probe median stayed at 1.6 ms, range 0.7 to 6.1.

RAM is the resource that moved. It still had 5.9 GiB available at 40 agents. CPU, swap, and the settled T3 probe were not the bound. The step count was the bound of the test.

One idle-10 probe sample was 56.5 ms. The other four were 0.7, 2.1, 1.6, and 1.6 ms, so the median stays 1.6. The last idle-10 row also counted 2 build processes. Some other command started a build or a test during that sample. Memory on that row did not jump.

While the last 20 agents were still starting, two curls of the same server, outside the sampler, took 148 ms and 90 ms. The idle-40 samples, taken after a 60 second wait, were back near 1 ms. Creating the processes slowed T3. Leaving them idle did not.

A projection, not a measurement. Another 39 agents at 129 MiB of MemAvailable each would put available memory near 1 GiB. This run did not start them.

**Four builds at forty agents.** One `python3 scripts/build.py` in this tree took 0.23 seconds. One `python3 -m unittest discover -s tests` took 29.1 seconds and passed 85 tests when it was the outermost command. Four overlapping copies are a few Python processes, not four large compiles. `load1` during the five samples had median 1.46 and range 1.33 to 1.93. About a minute later, while those loops were still running, the 1-minute load was 3.24. Sixteen threads were not full. Available memory's median was 6320168 KiB, 6.0 GiB, a bit above the idle-40 median. Swap did not move. The probe median was 1.5 ms, range 1.2 to 2.8.

The builds were running. `build.py` printed `built` for each copy during the window, and `build_procs` stayed at 13, which is the four loop wrappers plus the Python processes. The unit test output was block-buffered and the loop truncated the log every iteration, so the four loops did not leave a pass or fail line. A later single run of the suite passed.

These builds cannot be the limiter. They do not fill RAM, swap, or the 16 threads, and they do not move the T3 probe.

## Comparison with the 2026-10-04 estimate

The estimate was about 40 idle agents, or about 25 agents with 4 builds running, on 26 GB of RAM.

Forty idle Haiku agents landed on the idle side of that estimate, with 5.9 GiB still available, swap at 4.6 percent, `load1` at 1.25, and the probe at 1.6 ms. The estimate reads as a ceiling. This run found 40 still inside the stop lines. The per-agent cost that sets the ceiling is about 129 MiB of MemAvailable for this Haiku session, on top of a baseline that already held the desktop and the other workers.

Forty agents plus four loops of this repo's build and unit tests stayed inside the same lines. The estimate's figure of about 25 agents with 4 builds is a lower ceiling than this workload produced. A heavier build than `scripts/build.py` and this 29 second unit suite would be a different measurement.
