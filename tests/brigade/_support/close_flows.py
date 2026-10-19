import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .names import SCRIPT, _owner_words
from .inprocess import call


class CloseFlows:
    def follow_ups_in(self, body, *extra, name="D1.md", ok=True, owner=True):
        self.review_file(name, body)
        return self.brigade("ticket", "add", "--from-report", f"reports/{name}", *extra, ok=ok, owner=owner)


    def close_run(self, *flags):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                       *_owner_words(self.at), "close", *flags])
        return outcome.code, outcome.stdout_text().strip(), outcome.stderr_text().strip()


    def no_row(self, name, dish="D1"):
        return (f"brigade: warning: reports/{name} has no review row; record it with pass record {dish} --report {name}, "
                f"and --late when {dish} has moved past that round")


    def aged(self, name, year):
        path = self.review_file(name)
        stamp = datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(path, (stamp, stamp))
        return path


    def no_claims(self, state, dish="D1"):
        return f"brigade: warning: reports/{dish}.md has no Claims check line; {dish} is {state}"


    def not_a_file(self, name, words, advice):
        return f"brigade: warning: reports/{name} {words} and no review row of D1 names it; {advice}"


    def a_link(self, name):
        return self.not_a_file(name, "is a symbolic link", "remove the link and leave its target as it is")


    def under_a_linked_reports(self, name):
        return self.not_a_file(name, "resolves outside this store's reports/",
                               "replace the symbolic link at reports/ with a directory and leave the link's target as it is")


    def linked(self, name, target):
        link = self.at / "reports" / name
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(Path(self.temporary.name) / target)
        return link


    def close_setup(self):
        self.two_goals()
        for summary, kind, *flags in (("Capacity", "study"), ("Lease check", "fix", "--goal", "G1"),
                                      ("Tidy the help", "polish", "--goal", "G2", "--priority", "low"), ("Runtime", "upkeep"),
                                      ("Splash hangs", "fix", "--goal", "G2"), ("Drops a row", "fix", "--goal", "G1"),
                                      ("Later", "polish", "--goal", "G2"), ("Tidy the docs", "polish", "--priority", "low"),
                                      ("Installer", "fix")):
            self.brigade("ticket", "add", "--summary", summary, "--kind", kind, *flags)
        self.hand_edit("rail.tsv", "T9", kind="")
        self.brigade("fire", "--tickets", "T1", "--station", "investigation", "--summary", "Measure capacity")
        self.brigade("fire", "--tickets", "T2,T3", "--ride-along", "T3", "--station", "bug-fix", "--summary", "Fix the lease check")
        self.brigade("fire", "--tickets", "T4", "--station", "refactoring", "--summary", "Follow the nightly")
        self.brigade("fire", "--tickets", "T5", "--station", "bug-fix", "--summary", "Fix the splash")


    def printed(self):
        """What each of the five commands that print kinds or goals gives, as {command: (exit code, stdout, stderr)}."""
        return {" ".join(words): self.run_raw(*words)
                for words in (("status",), ("goal", "list"), ("ticket", "list"), ("startable",), ("close", "--dry-run"))}


    def assert_each_printing_command_warns(self, *warnings):
        """Each of the five commands exits 0 with exactly these warnings on standard error, and changes no file.

        Returns {command: its standard output with leading and trailing whitespace cut}.
        """
        before = self.store_files()
        printed = self.printed()
        for command, (code, _, err) in printed.items():
            with self.subTest(command=command):
                self.assertEqual((code, err), (0, "".join(f"brigade: warning: {text}\n" for text in warnings)))
        self.assertEqual(self.store_files(), before)
        return {command: out.strip() for command, (_, out, _) in printed.items()}


    def backdate_blocked(self, ident, hours):
        stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="microseconds")
        path = self.at / "log.tsv"
        lines = path.read_text().splitlines()
        rewritten = [lines[0]]
        found = False
        for line in lines[1:]:
            fields = line.split("\t")
            if not found and fields[1:4] == ["ticket", ident, "blocked"]:
                fields[0] = stamp
                line = "\t".join(fields)
                found = True
            rewritten.append(line)
        self.assertTrue(found, ident)
        path.write_text("\n".join(rewritten) + "\n")
        return stamp
