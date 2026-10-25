from dataclasses import dataclass
from datetime import timedelta
from brigade_lib import seam
from brigade_lib._reviews.rounds import SEND_BACK_DAYS, _at_utc, earliest_record, item_verdicts, window_phrases
from brigade_lib.store import EDITS_VERDICT, ROUND_KIND

# The round-budget answers that accept the remaining findings instead of another round.
ACCEPTING_ANSWERS = ("known limits", "drop")


@dataclass
class SeatHits:
    """One verifier seat's rounds and the outcome of each judged round."""

    rounds: int = 0
    passes: int = 0
    edits: int = 0
    send_backs: int = 0
    upheld: int = 0
    overruled: int = 0

    def count(self, verdict, outcome):
        """Add one round with this verdict and this outcome."""
        self.rounds += 1
        if verdict == "send-back":
            self.send_backs += 1
        elif verdict == EDITS_VERDICT:
            self.edits += 1
        elif verdict == "pass":
            self.passes += 1
        if outcome == "upheld":
            self.upheld += 1
        elif outcome == "overruled":
            self.overruled += 1

    @property
    def rate(self):
        """The upheld share of the decided findings, or `n/a` when nothing was decided."""
        decided = self.upheld + self.overruled
        return f"{100 * self.upheld / decided:.1f}%" if decided else "n/a"


HITS_COLUMNS = ("verifier", "rounds", "pass", "edits", "send-back", "upheld", "overruled", "upheld rate")


def window_cutoff(now, days):
    """The start of the window, this many days back from now."""
    return _at_utc(now) - timedelta(days=days)


def send_back_outcome(pass_rows, position, row, budget):
    """`upheld`, `overruled`, or `""` for one send-back row, judged on the rows after it.

    The dish's next recorded round upholds the send-back when it is a pass at another SHA, which is the next
    review accepting the change. A next round that is a pass at the row's own SHA overrules it, and so does a
    round-budget decision that accepts the findings with no later non-member round at another SHA. A later round
    at another SHA that is not a pass shows only that the author committed again, so it upholds nothing.
    """
    later = item_verdicts(pass_rows[position + 1:], row["dish"])
    if later and later[0]["verdict"] == "pass":
        return "overruled" if later[0]["sha"] == row["sha"] else "upheld"
    if not any(later_row["sha"] != row["sha"] for later_row in later) and accepted_after(budget, row):
        return "overruled"
    return ""


def accepted_after(budget, row):
    """Whether a round-budget decision accepting the findings was answered after this row."""
    sent = _at_utc(row["at"])
    return any(decision["dish"] == row["dish"] and decision["kind"] == ROUND_KIND
               and decision["state"] == "answered" and decision["answer"] in ACCEPTING_ANSWERS
               and decision["answered"] and _at_utc(decision["answered"]) > sent
               for decision in budget)


def edits_outcome(edits_rows, row):
    """`upheld` when edits.tsv recorded this dish's edits from this row's SHA for this row's report, else `""`.

    The report cell must match too. Edits verified from one review report do not uphold another report's round at
    the same SHA.
    """
    if any(edit["dish"] == row["dish"] and edit["from"] == row["sha"] and edit["report"] == row["report"]
           for edit in edits_rows):
        return "upheld"
    return ""


def seat_hits(pass_rows, edits_rows, rows86, cutoff):
    """Each verifier seat's hits over the rows at or after cutoff, judged in table order."""
    hits = {}
    for position, row in enumerate(pass_rows):
        if _at_utc(row["at"]) < cutoff:
            continue
        seat = hits.setdefault(row["verifier"], SeatHits())
        if row["verdict"] == "send-back":
            seat.count(row["verdict"], send_back_outcome(pass_rows, position, row, rows86))
        elif row["verdict"] == EDITS_VERDICT:
            seat.count(row["verdict"], edits_outcome(edits_rows, row))
        else:
            seat.count(row["verdict"], "")
    return hits


def hits_table(hits):
    """The HITS_COLUMNS table for these seats, one row per seat sorted by verifier.

    The verifier column is left-justified to its widest cell and every other column is right-justified to its
    widest cell, with two spaces between columns and trailing spaces stripped from each line.
    """
    rows = [HITS_COLUMNS]
    for verifier in sorted(hits):
        seat = hits[verifier]
        rows.append((verifier, str(seat.rounds), str(seat.passes), str(seat.edits), str(seat.send_backs),
                     str(seat.upheld), str(seat.overruled), seat.rate))
    widths = [max(len(row[column]) for row in rows) for column in range(len(HITS_COLUMNS))]
    lines = []
    for row in rows:
        cells = [row[0].ljust(widths[0])]
        cells += [cell.rjust(width) for cell, width in zip(row[1:], widths[1:])]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def empty_window(days):
    """The sentence both surfaces print when no row falls in the window."""
    return f"no reviewed rounds in the last {days} days"


def seat_line(verifier, seat):
    """One seat's counters as a closeout line, in the order the command's columns print them."""
    return (f"{verifier}  rounds {seat.rounds}  pass {seat.passes}  pass-with-edits {seat.edits}  "
            f"send-backs {seat.send_backs}  upheld {seat.upheld}  overruled {seat.overruled}  "
            f"rate {seat.rate}")


def pass_hits(restaurant, days):
    """The `pass hits` output: the table over the rows at or after the window's start."""
    now = seam.now()
    cutoff = window_cutoff(now, days)
    with restaurant.read_lock(create=False):
        pass_rows = restaurant.rows("pass.tsv")
        hits = seat_hits(pass_rows, restaurant.rows("edits.tsv"),
                         restaurant.rows("86.tsv"), cutoff)
    if not hits:
        return empty_window(days)
    heading, _ = window_phrases(earliest_record(pass_rows), now, days)
    return "\n".join([f"{heading} by verifier seat", hits_table(hits)])


def hit_section(pass_rows, edits_rows, rows86, now):
    """The `## Reviewer hit rate` lines for these rows, naming the span their records cover."""
    hits = seat_hits(pass_rows, edits_rows, rows86, window_cutoff(now, SEND_BACK_DAYS))
    heading, _ = window_phrases(earliest_record(pass_rows), now, SEND_BACK_DAYS)
    body = [seat_line(verifier, seat) for verifier, seat in sorted(hits.items())]
    body = body or [empty_window(SEND_BACK_DAYS).capitalize() + "."]
    return [f"## Reviewer hit rate ({heading})", "", *body, ""]
