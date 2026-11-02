import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from brigade_lib._ci.setting import ci_target_of
from brigade_lib._ci.trigger import CiRun
from brigade_lib.shared import plural

NAME = "ci-times.tsv"
HEADER = ("PR", "merged_at", "ci_seconds", "open_to_merge_seconds", "clean", "lost", "time_limit")


@dataclass(frozen=True)
class Ledger:
    """The ci-times.tsv read. runs is sorted by merged_at, kept is every raw line but the
    header, and bad is the (line number, text) of the lines that are not rows."""

    runs: tuple
    kept: tuple
    bad: tuple


def _at(text):
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError(f"merged_at {text!r} is not a timestamp") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _int(cell, name):
    if isinstance(cell, bool):
        raise ValueError(f"{name} {cell!r} is not a whole number")
    try:
        return int(cell)
    except (TypeError, ValueError):
        raise ValueError(f"{name} {cell!r} is not a whole number") from None


def _parse(number, line):
    fields = line.split("\t")
    if len(fields) != len(HEADER):
        raise ValueError(f"has {len(fields)} fields, not {len(HEADER)}")
    pr, merged, ci, opened, clean, lost, stopped = fields
    number_ = _int(pr, "PR")
    if number_ < 1:
        raise ValueError(f"PR {pr!r} is not a whole number of 1 or more")
    moment = _at(merged)
    ci_seconds = _int(ci, "ci_seconds")
    open_seconds = _int(opened, "open_to_merge_seconds")
    if ci_seconds < 0 or open_seconds < 0:
        raise ValueError("a negative seconds cell is not a row")
    if clean == "yes":
        is_clean = True
    elif clean == "no":
        is_clean = False
    else:
        raise ValueError(f"clean {clean!r} is neither yes nor no")
    return CiRun(number_, moment, ci_seconds, open_seconds, is_clean, _int(lost, "lost"),
                 _int(stopped, "time_limit"))


def read_ledger(restaurant):
    """The ledger read leniently. A line that is not a row is kept verbatim and
    reported, never dropped and never fatal."""
    path = restaurant.dir / NAME
    if not path.is_file():
        return Ledger((), (), ())
    runs, kept, bad = [], [], []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if number == 1 and line == "\t".join(HEADER):
            continue
        kept.append(line)
        try:
            runs.append(_parse(number, line))
        except ValueError:
            bad.append((number, line))
    runs.sort(key=lambda run: (run.merged_at, run.pr))
    return Ledger(tuple(runs), tuple(kept), tuple(bad))


def _row(run):
    return "\t".join((str(run.pr), run.merged_at.isoformat(), str(run.ci_seconds),
                      str(run.open_to_merge_seconds), "yes" if run.clean else "no",
                      str(run.lost), str(run.time_limit)))


def append_runs(restaurant, fresh):
    """Append the fresh runs ci-times.tsv lacks, by PR, keeping every raw line. Returns the count appended.

    Call under the caller's lock. The write goes through restaurant.write, so it is atomic.
    """
    ledger = read_ledger(restaurant)
    have = {run.pr for run in ledger.runs}
    lines = list(ledger.kept)
    count = 0
    for run in fresh:
        if run.pr in have:
            continue
        have.add(run.pr)
        lines.append(_row(run))
        count += 1
    if count:
        restaurant.write(NAME, "\t".join(HEADER) + "\n" + "".join(line + "\n" for line in lines))
    return count


def _minutes(seconds):
    return f"{seconds / 60:.1f}"


def _whole(value):
    return str(int(value)) if float(value) == int(value) else str(value)


def ci_section(restaurant, stamp):
    """The close section lines for CI time, or [] when the target is off or no run is recorded.

    The last five clean runs print one line each. The median, the longest, and the
    count over the target cover the last ten clean runs. Runner losses and
    time-limit stops count the runs merged in the last seven days. Reads the
    ledger only, so close stays offline.
    """
    target = ci_target_of(restaurant.meta)
    if not target:
        return []
    ledger = read_ledger(restaurant)
    if not ledger.runs:
        return []
    now = _at(stamp)
    clean_runs = [run for run in ledger.runs if run.clean]
    lines = ["## CI time", ""]
    for run in clean_runs[-5:]:
        lines.append(f"- PR {run.pr}: {run.ci_seconds}s ({_minutes(run.ci_seconds)} min), clean")
    if clean_runs:
        last = clean_runs[-10:]
        seconds = [run.ci_seconds for run in last]
        over = sum(1 for value in seconds if value > target * 60)
        lines.append(f"- median {_whole(statistics.median(seconds))}s, longest {max(seconds)}s, "
                     f"{over} over the {target}m target in the last {len(last)} clean runs")
    else:
        lines.append("- no clean runs recorded")
    week = now - timedelta(days=7)
    recent = [run for run in ledger.runs if run.merged_at >= week]
    lost = sum(run.lost for run in recent)
    stopped = sum(run.time_limit for run in recent)
    losses = "loss" if lost == 1 else "losses"
    lines.append(f"- {lost} runner {losses} and {plural(stopped, 'time-limit stop')} in the last 7 days")
    for number, _ in ledger.bad:
        lines.append(f"- ci-times.tsv line {number} is not a row; kept as is")
    return lines + [""]
