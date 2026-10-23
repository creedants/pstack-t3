from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY = Path(__file__).resolve().parents[1] / "run_tests.py"
SHARD_SIZE = 20
MAX_DEFAULT_JOBS = 4
JOBS_PER_SLOT = 4
DEFAULT_TIMEOUT = 300.0
ACTIVE = "PSTACK_RUN_TESTS"
POLL_SECONDS = 0.05
PROGRESS_SECONDS = 30.0
SWEEP_SECONDS = 10.0
NO_TESTS = (5, "NO TESTS RAN") if sys.version_info >= (3, 12) else (0, "OK")
PR_SET_CHILD_SUBREAPER = 36
PR_SET_PDEATHSIG = 1
QUOTED_LINES = 5
QUOTED_BYTES = 120
SEP1 = "=" * 70
SEP2 = "-" * 70


def positive(kind):
    def parse(text: str):
        value = kind(text)
        if not value > 0:
            raise argparse.ArgumentTypeError(f"{text} is not above 0")
        return value
    return parse


def parse_part(text: str) -> tuple[int, int]:
    matched = re.fullmatch(r"(-?\d+)/(-?\d+)", text)
    if matched is None:
        raise argparse.ArgumentTypeError(f"{text} is not i/n")
    index, count = int(matched.group(1)), int(matched.group(2))
    if count < 1:
        raise argparse.ArgumentTypeError(f"{text} has n below 1")
    if index < 1 or index > count:
        raise argparse.ArgumentTypeError(f"{text} has i outside 1 to n")
    return index, count


def jobs_in_slot(jobs: int, environ) -> tuple[int, str | None]:
    """Lower jobs to JOBS_PER_SLOT for each slot LAND_SLOT_FILES names while LAND_SLOT is nonempty. Return the count to run and the one line to print when it lowers the count."""
    from .events import plural

    if not environ.get("LAND_SLOT"):
        return jobs, None
    names = environ.get("LAND_SLOT_FILES", "").split()
    if not names:
        return jobs, None
    cap = JOBS_PER_SLOT * len(names)
    if jobs <= cap:
        return jobs, None
    return cap, f"run_tests: -j {jobs} lowered to {cap} for the {plural(len(names), 'slot')} this command holds"


def main(argv: list[str] | None = None, doc: str | None = None) -> int:
    from .pool import run
    from .worker import list_main, worker_main

    if doc is None:
        import run_tests as _facade

        doc = _facade.__doc__
    width = shutil.get_terminal_size().columns - 2
    told = [paragraph.replace("SWEEP_SECONDS", f"{SWEEP_SECONDS:g} seconds") for paragraph in doc.split("\n\n")[-2:]]
    parser = argparse.ArgumentParser(description=textwrap.fill(doc.splitlines()[0], width),
                                     epilog="\n\n".join(textwrap.fill(" ".join(paragraph.split()), width) for paragraph in told),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-j", "--jobs", type=positive(int), default=min(MAX_DEFAULT_JOBS, os.cpu_count() or 1),
                        help=f"The largest number of worker processes alive at once. The default is the smaller of {MAX_DEFAULT_JOBS} and the CPU count, or 1 when the CPU count is unknown. Inside a land.py slot the count is capped at {JOBS_PER_SLOT} for each slot the command holds. When the cap lowers the count, one line names both numbers.")
    parser.add_argument("-s", "--start-directory", default=str(ROOT / "tests"),
                        help="Directory to discover tests under. Each worker runs in the parent of this directory. The default is tests/ in this repository.")
    parser.add_argument("--timeout", type=positive(float), default=DEFAULT_TIMEOUT,
                        help=f"Seconds a worker may go without writing an event before the runner kills it. The listing of the tests gets the same number of seconds in all. The default is {DEFAULT_TIMEOUT:g}.")
    parser.add_argument("--part", type=parse_part,
                        help="Run part i of n, written as i/n. i is an integer from 1 to n. With no --part, the run selects every test discovery returned.")
    parser.add_argument("--list", help=argparse.SUPPRESS)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return worker_main(Path(args.worker))
    start = Path(args.start_directory).resolve()
    if args.list:
        return list_main(start, Path(args.list))
    jobs, lowered = jobs_in_slot(args.jobs, os.environ)
    if lowered:
        print(lowered, file=sys.stderr, flush=True)
    try:
        return run(start, jobs, args.timeout, args.part)
    except Exception as refusal:
        from .events import Refusal

        if isinstance(refusal, Refusal):
            print(f"run_tests: {refusal}", file=sys.stderr)
            return 2
        raise
