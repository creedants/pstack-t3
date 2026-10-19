import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .names import CLAUDE, CODEX, ROOT, SCRIPT, SUPPORT, _owner_words
from .inprocess import ProcessReason, call, real_popen, run_globals
from .race_child import _race_args, _wait_for_path
from .report_texts import TRACKED


class GoalFlows:
    def goal(self, name="activity view", *extra, ok=True, owner=True):
        return self.brigade("goal", "add", "--name", name, "--purpose", "One page of activity", "--done", "The page ships", *extra,
                            ok=ok, owner=owner)


    def rows_of(self, table):
        return [line.split("\t") for line in (self.at / table).read_text().splitlines()[1:]]


    def goal_log(self):
        return [row[2:] for row in self.rows_of("log.tsv") if row[1] == "goal"]


    def hand_edit(self, table, ident, **cells):
        """Change cells of one row the way an editor would, with no command."""
        path = self.at / table
        header, *lines = path.read_text().splitlines()
        keys = header.split("\t")
        rows = [dict(zip(keys, line.split("\t"))) for line in lines]
        for row in rows:
            if row["id"] == ident:
                row.update(cells)
        path.write_text("\n".join([header] + ["\t".join(row.get(key, "") for key in keys) for row in rows]) + "\n")


    NO_UNASSIGNED_WORK = "unassigned: 0 waiting, 0 in flight, 0 merged, 0 send-backs"


    NEEDS_KIND = "brigade: ticket add needs --kind <feature|fix|polish|upkeep|study>"


    def label(self, table, ident):
        row = self.table_row(self.at, table, ident)
        return row["kind"], row["goal"]


    def raw_cells(self, table, ident, *keys):
        """These cells of the row with this id, as the bytes in the file. A carriage return stays, which splitlines would cut."""
        header, *lines = (self.at / table).read_bytes().split(b"\n")[:-1]
        names = header.decode().split("\t")
        found = [row for row in (dict(zip(names, line.split(b"\t"))) for line in lines) if row["id"] == ident.encode()]
        self.assertEqual(len(found), 1, f"{ident!r} in {table}")
        return tuple(found[0][key] for key in keys)


    def assert_closed_goal_takes_no_ticket(self, state):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("goal", "set", "G1", "--state", state, "--evidence", "why")
        before = self.store_files()
        refusal = (f"brigade: G1 is {state}; a ticket takes an open or paused goal; "
                   "run goal set G1 --state open first, or pick another goal")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G1", ok=False), refusal)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "G1", "--priority", "low", ok=False), refusal)
        self.assertEqual(self.store_files(), before)


    KIND_CHOICE = "<feature|fix|polish|upkeep|study>"


    def rail_before_kinds(self, *summaries):
        """rail.tsv as the tool wrote it before kinds and goals. Each row is a waiting user ticket of ten cells."""
        (self.at / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n"
            + "".join(f"T{number}\t2026-10-01T00:00:00+00:00\twaiting\tuser\t\t\t{summary}\t\t\t\n"
                      for number, summary in enumerate(summaries, 1)))


    def two_goals(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "9")
        self.goal("activity view")
        self.goal("current with upstream")


    def fake_land(self):
        """A land.py that records its words, waits at a claim until claim-proceed exists, and prints lease L7."""
        case = Path(self.temporary.name)
        (case / "land.py").write_text(
            "import sys, time\nfrom pathlib import Path\n"
            f"case = Path({str(case)!r})\n"
            "with open(case / 'land-calls', 'a') as f:\n    f.write(' '.join(sys.argv[3:]) + '\\n')\n"
            "if sys.argv[4] == 'claim':\n"
            "    (case / 'claiming').touch()\n"
            "    end = time.monotonic() + 30\n"
            "    while not (case / 'claim-proceed').exists() and time.monotonic() < end:\n        time.sleep(0.01)\n"
            "    print('L7')\n"
            "else:\n    print('L7 released')\n")
        return case


    def fire_with_fake_land(self, case, *words):
        """The child replaces `LAND` before `main`. The test does not overlap it with another started process."""
        support = SUPPORT
        tail = _race_args("fake-land", case, ["--store", str(self.store), "--at", str(self.at), "fire", *words])
        proc = real_popen(
            ProcessReason.PRE_MAIN,
            [sys.executable, str(support), *tail],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc


    def assert_goal_keeps_open(self, ident, held, advice):
        before = self.store_files()
        for state in ("done", "dropped"):
            with self.subTest(goal=ident, state=state):
                self.assertEqual(self.brigade("goal", "set", ident, "--state", state, "--evidence", "why", ok=False),
                                 f"brigade: {ident} still has work: {held}; {advice}; then run this again")
        self.assertEqual(self.store_files(), before)


    def worked_store(self):
        """Two goals, ten tickets, five items, and nine review rows. A test that reads it names the rows behind each number."""
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "9")
        self.goal("activity view", "--share", "2")
        self.goal("current with upstream")
        for summary, kind, *flags in (
                ("Activity view", "feature", "--goal", "G1"),
                ("Lease check arms a reservation", "fix", "--goal", "G1"),
                ("Tidy the lease help", "polish", "--goal", "G2", "--priority", "low"),
                ("Activity page drops a row", "fix", "--goal", "G1"),
                ("Tidy the lease docs", "polish", "--priority", "low"),
                ("Runtime follows the nightly", "upkeep", "--goal", "G2", "--priority", "urgent"),
                ("Capacity after the parallel runner", "study", "--priority", "low"),
                ("Installer keeps a relative config home", "fix"),
                ("Activity page filters by coordinator", "feature", "--goal", "G1"),
                ("Docs batch", "polish", "--priority", "low")):
            self.brigade("ticket", "add", "--summary", summary, "--kind", kind, *flags)
        self.hand_edit("rail.tsv", "T8", kind="")
        for tickets, station, *flags in (("T1", "feature"), ("T2,T3", "bug-fix", "--ride-along", "T3"), ("T6", "refactoring"),
                                         ("T7", "investigation"), ("T10", "docs")):
            self.brigade("fire", "--tickets", tickets, "--station", station, "--summary", f"Do {tickets}", *flags)
        self.brigade("goal", "set", "G2", "--state", "paused")
        for item, state in (("D1", "merged"), ("D2", "in-review"), ("D3", "sent-back"), ("D5", "merged")):
            self.hand_edit("dishes.tsv", item, state=state)
        for ticket in ("T1", "T10"):
            self.hand_edit("rail.tsv", ticket, state="done")
        with open(self.at / "pass.tsv", "a") as passes:
            for item, verdict, member in (("D1", "send-back", ""), ("D1", "send-back", ""), ("D1", "pass", ""),
                                          ("D2", "send-back", ""), ("D2", "send-back", "yes"),
                                          ("D3", "send-back", ""), ("D3", "pass-with-edits", ""),
                                          ("D5", "send-back", ""), ("D5", "pass", "")):
                passes.write(f"2026-10-01T00:00:00+00:00\t{item}\t\tabc\t{verdict}\t{CLAUDE}\t{CODEX}\t\t\t{member}\n")


    NOT_A_KIND = "which is not feature, fix, polish, upkeep, or study; it reads as no kind"


    NO_GOAL_ROW = "which has no row in goals.tsv; it reads as unassigned"


    NOT_WHILE = "ticket set does not change this cell while"


    def copy_goal_row(self, ident, name):
        """Append to goals.tsv a copy of its first row with this id and this name, the way an editor would."""
        path = self.at / "goals.tsv"
        cells = path.read_text().splitlines()[1].split("\t")
        cells[0], cells[3] = ident, name
        path.write_text(path.read_text() + "\t".join(cells) + "\n")


    PADDED_GOAL_ROW = ("goals.tsv has a row with id ' G1 ', which has white space at an end or a carriage return in it; "
                       "it is not a goal; correct the id in goals.tsv by hand to an id no other row has, or remove the row")


    def item_with_two_bad_cells(self):
        """D1 in flight with a kind cell and a goal cell that are not legal, and its ticket T1 with a bad kind cell. Returns D1's warnings."""
        self.fired_bug_fix()
        self.hand_edit("dishes.tsv", "D1", kind="chore", goal="G9")
        self.hand_edit("rail.tsv", "T1", kind="chore")
        by_hand = "no command changes this cell; correct it in dishes.tsv by hand"
        return (f"brigade: warning: D1 has kind 'chore', {self.NOT_A_KIND}; {by_hand}\n"
                f"brigade: warning: D1 names goal 'G9', {self.NO_GOAL_ROW}; {by_hand}\n")


    def store_from_before_kinds(self, rows=True):
        """A store as the tool wrote it before kinds and goals. Ticket rows of 10 cells, item rows of 15, and no goals.tsv.

        With `rows` it holds a ticket in each state but moved, an item in each state, review rows, and an open and an answered decision.
        """
        self.queue()
        (self.at / "goals.tsv").unlink(missing_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        tickets = [("T1", "assigned", "user", "", "D1", "Cold start is slow", "", "", ""),
                   ("T2", "assigned", "github", "https://github.com/o/r/issues/2", "D2", "Splash hangs", "urgent", "src/splash.py", ""),
                   ("T3", "assigned", "user", "", "D3", "Cache misses", "", "", ""),
                   ("T4", "assigned", "user", "", "D4", "Login loops", "", "", ""),
                   ("T5", "assigned", "user", "", "D5", "Export stalls", "", "", ""),
                   ("T6", "assigned", "user", "", "D6", "Footer overlaps", "low", "", ""),
                   ("T7", "done", "user", "", "D7", "Dates are wrong", "", "", ""),
                   ("T8", "waiting", "user", "", "", "Importer crashes", "", "", ""),
                   ("T9", "waiting", "user", "", "", "Tune the pool", "normal", "a.txt", ""),
                   ("T10", "waiting", "report", "perf/reports/D7.md#1", "", "Tidy the guide", "", "docs/guide.md", ""),
                   ("T11", "waiting", "user", "", "", "Pick a database", "urgent", "", "yes"),
                   ("T12", "waiting", "user", "", "", "Trim the pool", "low", "a.txt", ""),
                   ("T13", "dropped", "user", "", "", "Old idea", "", "", "")]
        items = [("D1", "in-progress", "bug-fix", "T1", "", "w1", "", "", "", "Fix the cold start", "60", "", "", ""),
                 ("D2", "in-review", "perf-issue", "T2", "", "w2", "perf/d2", "https://github.com/o/r/pull/2", "s2", "Fix the splash", "90", "", "", ""),
                 ("D3", "passed", "bug-fix", "T3", "", "w3", "", "", "s3", "Fix the cache", "60", "", "", ""),
                 ("D4", "sent-back", "bug-fix", "T4", "", "w4", "", "", "s4", "Fix the login", "60", "", "", ""),
                 ("D5", "blocked", "feature", "T5", "", "w5", "", "", "", "Rework the export", "60", "", "", ""),
                 ("D6", "queued", "bug-fix", "T6", "", "w6", "", "", "s6", "Fix the footer", "60", "", "", ""),
                 ("D7", "merged", "feature", "T7", "", "w7", "", "https://github.com/o/r/pull/7", "s7", "Fix the dates", "60", "", "", "yes"),
                 ("D8", "dropped", "refactoring", "T8", "", "w8", "", "", "", "Split the importer", "60", "", "", "")]
        passes = [("D3", "s3", "pass"), ("D4", "s4", "send-back"), ("D6", "s6", "pass"), ("D7", "s7", "pass")]
        decisions = [("Q1", "open", "", "Drop Windows 7 support?", "yes, no", "no", "", "", ""),
                     ("Q2", "answered", "D5", "Which export format?", "csv, json", "csv", "json", "", stamp)]
        log = [*(("ticket", ticket[0], "waiting", ticket[5]) for ticket in tickets),
               *(("dish", item[0], "in-progress", item[9]) for item in items),
               *(("ticket", ticket[0], "assigned", ticket[5]) for ticket in tickets[:7]),
               *(("dish", item[0], item[1], item[9]) for item in items[1:]),
               ("ticket", "T7", "done", "Dates are wrong"), ("ticket", "T13", "dropped", "Old idea"),
               ("86", "Q1", "open", "Drop Windows 7 support?"), ("86", "Q2", "answered", "json")]
        if not rows:
            tickets = items = passes = decisions = log = []
        tables = {
            "rail.tsv": ["id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision",
                         *("\t".join((ident, stamp, *rest)) for ident, *rest in tickets)],
            "dishes.tsv": [self.OLD_DISHES.rstrip("\n"), *("\t".join((ident, stamp, *rest)) for ident, *rest in items)],
            "pass.tsv": ["at\tdish\tpr\tsha\tverdict\tauthor\tverifier\tnote\treport\tmember",
                         *(f"{stamp}\t{item}\t\t{sha}\t{verdict}\t{CLAUDE}\t{CODEX}\t\t\t" for item, sha, verdict in passes)],
            "86.tsv": ["id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\tkind\tanswered",
                       *("\t".join((ident, stamp, *rest)) for ident, *rest in decisions)],
            "log.tsv": ["at\tkind\tid\tstate\tnote", *("\t".join((stamp, *row)) for row in log)],
        }
        for name, lines in tables.items():
            (self.at / name).write_text("".join(f"{line}\n" for line in lines))


    OLD_COUNTS = ("waiting tickets: 5, in progress: 1, in review: 1, passed review: 1, waiting to land: 1, sent back: 1, "
                  "blocked: 1, merged: 1, decisions for you: 1")


    OLD_WORKERS = "workers: 2 of 2 running, 0 idle, auto-start: normal"
