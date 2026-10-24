if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import setUpModule
from ..support import BrigadeTest, CLAUDE, CODEX, Path, ProcessReason, SCRIPT, call, call_in_process, real_process, run_globals, _ReportingRace, _owner_words, _shown_root, datetime, io, json, mock, os, runpy, subprocess, sys, timezone
import sqlite3

class ReportingTest(BrigadeTest, _ReportingRace):
    def test_close_warns_about_a_review_report_with_no_row_and_still_exits_0(self):
        self.fired_bug_fix()
        code, before, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.review_file("D1-review-2.md")
        self.assertEqual(self.close_run("--dry-run"), (0, before, self.no_row("D1-review-2.md")))
        code, out, err = self.close_run("--to-file")
        self.assertEqual((code, err), (0, self.no_row("D1-review-2.md")))
        self.assertEqual(Path(out).read_text().strip(), before)
        code, out, err = self.close_run()
        self.assertEqual((code, err), (0, self.no_row("D1-review-2.md")))
        self.assertEqual(out.splitlines()[0], "# Perf report")
        self.assertNotIn("warning", out)
    def test_close_warns_about_the_report_of_a_passed_item_that_has_no_claims_check_line(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n\nStatus: done\n")
        self.record("abc", "pass")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("passed")))
    def test_close_does_not_warn_about_a_report_a_row_names(self):
        self.fired_bug_fix()
        self.aged("D1-review-1.md", 2099)
        self.aged("D1-review-1-panel-2.md", 2099)
        self.record("abc", "send-back", "--report", "D1-review-1.md")
        self.record("abc", "pass", "--member", "--report", "D1-review-1-panel-2.md", verifier="grok/grok-4.7")
        self.assertEqual(self.close_run("--dry-run")[2], "")
    def test_close_matches_a_row_that_names_no_report_to_the_reports_written_before_it(self):
        self.fired_bug_fix()
        self.aged("D1-review.md", 2020)
        self.aged("D1-review-bunny.md", 2020)
        self.assertEqual(self.close_run("--dry-run")[2],
                         "\n".join([self.no_row("D1-review-bunny.md"), self.no_row("D1-review.md")]))
        self.record("abc", "send-back")
        self.assertEqual(self.close_run("--dry-run")[2], "")
        self.aged("D1-review-2.md", 2099)
        self.assertEqual(self.close_run("--dry-run")[2], self.no_row("D1-review-2.md"))
    def test_a_row_that_names_a_report_matches_no_other_report(self):
        self.fired_bug_fix()
        self.aged("D1-review-1.md", 2020)
        self.aged("D1-review-2.md", 2020)
        self.aged("D7-review.md", 2020)
        self.record("abc", "send-back", "--report", "D1-review-2.md")
        self.assertEqual(self.close_run("--dry-run")[2],
                         "\n".join([self.no_row("D1-review-1.md"), self.no_row("D7-review.md", "D7")]))
    def test_close_warns_about_a_dangling_link_named_like_a_review_report_and_still_exits_0(self):
        self.fired_bug_fix()
        code, before, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.linked("D1-review-7.md", "gone.md")
        warning = ("brigade: warning: reports/D1-review-7.md is a symbolic link and no review row of D1 names it; "
                   "remove the link and leave its target as it is")
        self.assertEqual(self.close_run("--dry-run"), (0, before, warning))
        self.assertEqual(self.close_run(), (0, before, warning))
    def test_close_warns_about_a_review_report_replaced_by_a_dangling_link_while_close_classifies_it(self):
        self.fired_bug_fix()
        entry = self.review_file("D1-review-7.md")
        pending = self.linked("pending.md", "gone.md")
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        replaced = []

        def replacing(real):
            def hooked(path):
                result = real(path)
                if not replaced and path.name == entry.name and path.parent.name == "reports":
                    replaced.append(path)
                    os.replace(pending, entry)
                return result
            return hooked

        argv = ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), "close", "--dry-run"]
        with mock.patch.object(Path, "lstat", replacing(Path.lstat)), \
                mock.patch.object(Path, "is_symlink", replacing(Path.is_symlink)), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            glob["run"](argv)
        self.assertEqual(len(replaced), 1)
        self.assertTrue(entry.is_symlink())
        self.assertFalse(entry.exists())
        self.assertEqual(stderr.getvalue().strip(), self.no_row("D1-review-7.md"))
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.a_link("D1-review-7.md")))
    def test_close_warns_about_a_live_link_and_a_directory_named_like_review_reports_and_following_its_advice_ends_each_warning(self):
        self.fired_bug_fix()
        target = Path(self.temporary.name) / "findings.md"
        target.write_text("findings\n")
        link = self.linked("D1-review-7.md", "findings.md")
        directory = self.at / "reports" / "D1-review-8.md"
        directory.mkdir()
        (directory / "notes.md").write_text("notes\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, "\n".join([
            self.a_link("D1-review-7.md"),
            self.not_a_file("D1-review-8.md", "is not a regular file", "move it out of reports/"),
        ])))
        link.unlink()
        directory.rename(self.at / "D1-review-8.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.assertEqual(target.read_text(), "findings\n")
        self.assertEqual((self.at / "D1-review-8.md" / "notes.md").read_text(), "notes\n")
    def test_close_does_not_warn_about_a_dangling_link_a_row_names(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md")
        self.record("abc", "send-back", "--report", "D1-review-1.md")
        (self.at / "reports" / "D1-review-1.md").unlink()
        self.linked("D1-review-1.md", "gone.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
    def test_a_row_that_names_no_report_accounts_for_no_link_to_a_file_written_before_it(self):
        self.fired_bug_fix()
        target = Path(self.temporary.name) / "findings.md"
        target.write_text("findings\n")
        stamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(target, (stamp, stamp))
        self.linked("D1-review-1.md", "findings.md")
        self.record("abc", "send-back")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.a_link("D1-review-1.md")))
    def test_close_advice_for_every_entry_under_a_linked_reports_leaves_the_files_outside_the_store_in_place(self):
        self.fired_bug_fix()
        outside = Path(self.temporary.name) / "outside" / "reports"
        outside.mkdir(parents=True)
        (outside / "D1-review-5.md").write_text("findings\n")
        (outside / "D1-review-6.md").symlink_to(outside / "D1-review-5.md")
        (outside / "D1-review-7.md").mkdir()
        before = sorted(path.name for path in outside.iterdir())
        reports = self.at / "reports"
        reports.symlink_to(outside)
        self.assertEqual(self.close_run("--dry-run")[::2], (0, "\n".join(
            self.under_a_linked_reports(f"D1-review-{number}.md") for number in (5, 6, 7))))
        reports.unlink()
        reports.mkdir()
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.assertEqual(sorted(path.name for path in outside.iterdir()), before)
        self.assertEqual((outside / "D1-review-5.md").read_text(), "findings\n")
    def test_close_ignores_a_file_that_is_not_a_review_report(self):
        self.fired_bug_fix()
        for name in ("D1.md", "notes.md", "D1-review.txt", "D1-reviewed.md", "xD1-review.md"):
            self.aged(name, 2099)
        self.assertEqual(self.close_run("--dry-run")[2], "")
    def test_report_lists_only_what_changed_since_the_last_report(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Startup is slow")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Splash hangs")
        self.brigade("fire", "--tickets", "T1", "--station", "perf-issue", "--summary", "Cut cold start time")
        self.brigade("86", "add", "--question", "Drop Windows 7 support?", "--options", "yes, no", "--default", "no")
        first = self.brigade("close")
        self.assertIn("## In progress\n\n- fix D1 (T1): Cut cold start time\n", first)
        self.assertIn("## New tickets, not started\n\n- fix T2: Splash hangs\n", first)
        self.assertIn("- Q1: Drop Windows 7 support? Options: yes, no. Default if no answer: no.", first)
        self.brigade("pass", "record", "D1", "--pr", "https://github.com/o/r/pull/7", "--sha", "abc", "--verdict", "pass",
                     "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("86", "answer", "Q1", "--answer", "no")
        second = self.brigade("close")
        self.assertIn("## Merged\n\n- fix D1 (T1): Cut cold start time https://github.com/o/r/pull/7", second)
        self.assertNotIn("In progress", second)
        self.assertNotIn("Passed review", second)
        self.assertNotIn("Decisions for you", second)
        self.assertIn("Nothing new.", self.brigade("close"))
        self.assertEqual(len(list((self.at / "closeouts").glob("*.md"))), 3)
    def test_a_dish_that_moves_through_every_state_in_one_report_appears_once(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "UI", "--source", "user")
        self.brigade("ticket", "set", "T2", "--state", "dropped")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", "grok/grok-4.7", "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("close"), "\n".join([
            "# Perf report", "",
            f"Since opening. merged: 1.", "",
            "## Merged", "", "- fix D1 (T1): Fix s", "",
            "## Dropped", "", "- T2: UI", "",
            "## Spend", "", "goal not recorded  wall 0m  rounds 1  send-backs 0  threads not recorded", "",
            "## Follow-up share", "", "- 0 of 2 tickets created in the last 14 days came from a report (0%).",
        ]))
    def test_close_to_file_prints_the_written_path_and_advances_the_report(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "UI", "--source", "user")
        self.brigade("ticket", "set", "T2", "--state", "dropped")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", "grok/grok-4.7", "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        written = self.brigade("close", "--to-file")
        files = list((self.at / "closeouts").glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertEqual(written, str(files[0].resolve()))
        self.assertIn("## Merged\n\n- fix D1 (T1): Fix s", files[0].read_text())
        self.assertIn("Nothing new.", self.brigade("close"))
    def test_close_to_file_with_dry_run_writes_no_closeout(self):
        self.open()
        self.brigade("close", "--to-file", "--dry-run", ok=False)
        self.assertEqual(list((self.at / "closeouts").glob("*.md")), [])
    def test_status_help_names_the_thread_line_then_counts(self):
        env = os.environ | {"COLUMNS": "300"}
        # -h output
        result = real_process(ProcessReason.HELP, SCRIPT, ["--help"], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status              the thread line first, then counts, then workers outside the executive admin's store, then a kinds line, a goals line, and an untagged line when each has something to count, then reports to, mode, and owner when present", result.stdout)
        self.assertNotIn("one line of counts", result.stdout)
    def test_status_and_walk_speak_plain_engineering_prose(self):
        self.open()
        self.brigade("set", "--thread", "thread-1", "--schedule", "report=s-1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no")
        self.assertEqual(self.brigade("status"),
                         "thread thread-1\nreporting: milestones, no landing contract, waiting tickets: 1, decisions for you: 1\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)\nkinds: fix 1 waiting\nowner thread-1@1")
        self.assertEqual(self.brigade("walk"), "\n".join([
            f"{_shown_root(self.project)}: no landing contract",
            "  Perf (reports milestones): waiting tickets: 1, decisions for you: 1",
            "    thread thread-1",
            "    Q1: Ship it?",
        ]))
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"], {"report": "s-1"})
        for word in ("86", "plate", "heard", "chef", "fire", "pass "):
            self.assertNotIn(word, self.brigade("status").lower())
    def test_status_prints_the_round_budget_only_when_it_is_stored_off_the_default(self):
        self.open()
        before = self.brigade("status")
        self.assertNotIn("round budget", before)
        self.brigade("set", "--round-budget", "2")
        lines = before.splitlines()
        at = next(index for index, line in enumerate(lines) if line.startswith("liveness:"))
        self.assertEqual(self.brigade("status").splitlines(),
                         lines[:at + 1] + ["round budget: 2 send-backs"] + lines[at + 1:])
        self.assertIn("roundBudget", self.brigade("86", "add", "--help"))
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["roundBudget"] = 3
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.assertEqual(self.brigade("status"), before)
        self.brigade("set", "--round-budget", "")
        self.assertEqual(self.brigade("status"), before)
