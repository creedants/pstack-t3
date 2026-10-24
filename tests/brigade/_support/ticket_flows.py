import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .names import CLAUDE, CODEX, HELD_QUESTION, ROOT, SCRIPT, _owner_words
from .report_texts import TRACKED
from .inprocess import call


class TicketFlows:
    def fired_bug_fix(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")


    BEDROCK = "amazon-bedrock/us.anthropic.claude-sonnet-5-5-v1:0"


    NAMESPACED_MUSE = "opencode/opencode/muse-lite-2-free"


    def same_family_refusal(self, author, verifier):
        return (f"brigade: verifier {verifier} is the same model family as author {author}; "
                "pick a verifier from another family, or pass --same-family when no other family is runnable")


    def assert_one_family(self, author, verifier):
        self.fired_bug_fix()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", author, "--verifier", verifier)
        self.assertEqual(self.brigade(*record, ok=False), self.same_family_refusal(author, verifier))
        self.assertEqual(self.pass_rows(), [])
        self.assertEqual(self.brigade(*record, "--same-family"), "D1 passed")
        self.assertEqual([row[5:8] for row in self.pass_rows()], [[author, verifier, "same model family;"]])


    def fire_one(self, station="perf-issue", timebox="60"):
        (self.at / "menu.md").write_text("# Menu: Perf\n\n## Purpose\n\nMake startup fast.\n\n## Budget\n\nsmall\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Startup is slow")
        return self.brigade("fire", "--tickets", "T1", "--station", station, "--summary", "Cut startup", "--branch", "perf/d1",
                            "--thread", "thread-9", "--timebox", timebox)


    PENDING = "D1: 3 send-backs, decision pending"


    NO_FIX_ROUND = ("brigade: D1 has 3 send-backs; no fix round until its decision is answered; "
                    "run 86 add --dish D1 --round-budget")


    ROUND_QUESTION = ("D1 was sent back 3 times. Accept the remaining findings as known limits and land it, "
                      "send it through architect for a redesign, or drop it?")


    ROUND_BUDGET = ("86", "add", "--dish", "D1", "--round-budget")


    def send_back(self, sha, *extra):
        return self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "send-back",
                            "--reason", "other",
                            "--author", CODEX, "--verifier", CLAUDE, *extra)


    def sent_back(self, rounds, start=1):
        for number in range(start, start + rounds):
            if number > start:
                self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
            printed = self.send_back(f"a{number}")
        return printed


    def round_decision_lifts_the_refusal(self, answer, stored):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", answer), "Q1 answered")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q1")["answer"], stored)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 0 send-backs since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")


    PASS = ("pass", "record", "D1", "--verdict", "pass", "--author", CODEX, "--verifier", CLAUDE, "--sha")


    NO_DECISION = "brigade: D1 is {}; it owes no round decision"


    FIRE_AGAIN = ("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fourth fix", "--thread", "fresh-worker")


    NO_FIRE = ("brigade: T1 is D1's ticket; D1 has 3 send-backs; no fix round until its decision is answered; "
               "run 86 add --dish D1 --round-budget")


    FIX_BRIEF = ("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")


    def park_item(self, question=HELD_QUESTION, dish="D1"):
        dish_args = ("--dish", dish) if dish else ()
        return self.brigade("86", "add", *dish_args, "--question", question,
                            "--options", "moved, keep parked", "--default", "keep parked")


    def open_admin(self):
        return self.run_at(self.store / "bridge-kit" / ".admin", "open", "--admin", "--project-root", str(self.project))


    def admin(self, *args, ok=True):
        return self.run_at(self.store / "bridge-kit" / ".admin", *args, ok=ok)


    def cells(self, ident):
        row = self.table_row(self.at, "rail.tsv", ident)
        return row["priority"], row["paths"], row["decision"]


    def old_rail(self, stamp="2026-10-01T00:00:00+00:00"):
        (self.at / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\n"
            f"T1\t{stamp}\twaiting\tuser\t\t\tFix login\n"
            f"T2\t{stamp}\twaiting\treport\tperf/reports/D1.md#1\t\tTidy the guide\n"
            f"T3\t{stamp}\twaiting\tupstream\tabc123\t\tSync upstream\n")


    OLD_DISHES = "id\tat\tstate\tstation\ttickets\ttask\tthread\tbranch\tpr\tsha\tsummary\ttimebox\tlease\tpaths\treported\n"


    def old_item(self, stamp="2026-10-01T00:00:00+00:00"):
        """One assigned ticket and its item in review, each in a row of the width the tool wrote before the kind and goal cells."""
        (self.at / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n"
            f"T1\t{stamp}\tassigned\tuser\t\tD1\tFix login\t\t\t\n")
        (self.at / "dishes.tsv").write_text(
            self.OLD_DISHES + f"D1\t{stamp}\tin-review\tbug-fix\tT1\t\tthread-1\t\t\tabc\tFix login\t60\t\t\t\n")


    def tracked_project(self, *files):
        for name in files:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x\n")
        run = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")


    def paths_cells(self):
        return {row[0]: row[8] for row in (line.split("\t") for line in (self.at / "rail.tsv").read_text().splitlines()[1:])}


    def unrecorded_tickets(self):
        self.tracked_project(*TRACKED)
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `docs/guide.md:246` and `tests/test_install.py`")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Cover the `runId` wait")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Reword `README.md`", "--paths", "t3/runtime.md")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Drop `t3/scripts/roles.py`")
        self.brigade("ticket", "set", "T4", "--state", "dropped")


    NONE_RUNNING ="workers: 0 of 2 running, 2 idle, auto-start: normal"


    def queue(self, *open_flags):
        self.init_landing()
        self.landing_env()
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", *open_flags)


    def filed(self, summary, *flags):
        return self.brigade("ticket", "add", "--kind", "fix", "--summary", summary, *flags)


    def refile(self, ident, **ago):
        stamp = (datetime.now(timezone.utc) - timedelta(**ago)).isoformat()
        rail = self.at / "rail.tsv"
        lines = [line.split("\t") for line in rail.read_text().splitlines()]
        for fields in lines[1:]:
            if fields[0] == ident:
                fields[1] = stamp
        rail.write_text("".join("\t".join(fields) + "\n" for fields in lines))
