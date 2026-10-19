from ..support import setUpModule
from ..support import BrigadeTest, CLAUDE, CODEX, HELD_LINE, Path, ROOT, SCRIPT, STILL_OPEN, call, datetime, json, os, runpy, shlex, subprocess, sys, time, timedelta, timezone
from .fixtures import _Helpers

class WakeTest(BrigadeTest, _Helpers):
    def test_keep_parked_leaves_the_round_decision_open_and_the_refusal_in_place(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q1")["question"], self.ROUND_QUESTION)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"),
                         "Q1 still open; D1 stays held until 86 answer Q1 --answer 'known limits' or redesign or drop")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch").splitlines()[1], self.PENDING)
    def test_a_late_third_send_back_holds_an_item_in_progress(self):
        self.started()
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress", "--thread", "w3")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (thread w3)")
        self.assertEqual(self.send_back("a0", "--late"),
                         "D1: late send-back on record; D1 stays in-progress; 3 send-backs, decision pending")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        before = self.unchanged()
        self.assertEqual(self.brigade("dish", "D1", "--thread", "fresh-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--task", "fresh-task", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.unchanged(before)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "w3", "--reported"), "D1 in-progress")
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha), "D1 in-review")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
    def test_a_blank_dish_decision_does_not_hold_the_item(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.brigade("dish", "D1", "--state", "in-review", "--sha", sha)
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no"), "Q1")
        self.assertEqual(self.brigade("86", "list"), "Q1: Ship it? Options: yes, no. Default: no.")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")
    def test_a_second_86_add_for_a_held_item_prints_the_open_id_and_appends_nothing(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        table = (self.at / "86.tsv").read_text()
        decisions = [row for row in self.log_rows() if row["kind"] == "decision"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(len([line for line in table.splitlines() if line]) - 1, 1)
        other = "D1: cursor could not pass the seat's options: refused again. Move this coordinator to a provider that passes them?"
        self.assertEqual(self.park_item(other), "Q1")
        self.assertEqual((self.at / "86.tsv").read_text(), table)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "decision"], decisions)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
    def test_keep_parked_leaves_the_item_decision_open_and_writes_nothing(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
    def test_a_relayed_keep_parked_leaves_the_item_held(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.open_admin()
        before = (self.at / "86.tsv").read_bytes()
        self.admin("request", "--to", "perf", "answer perf Q1: keep parked")
        self.assertEqual(self.brigade("inbox", "take"), "A1: answer perf Q1: keep parked")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.brigade("inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual((self.at / "86.tsv").read_bytes(), before)
    def test_free_text_keeps_the_hold_and_writes_nothing(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "I moved it"), STILL_OPEN)
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
    def test_a_blank_dish_decision_still_closes_on_any_text(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.brigade("dish", "D1", "--state", "in-review", "--sha", sha)
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?",
                                      "--options", "keep parked", "--default", "keep parked"), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["answer"]), ("answered", "keep parked"))
        self.assertEqual(self.brigade("86", "add", "--question", "Really?",
                                      "--options", "yes", "--default", "no"), "Q2")
        self.assertEqual(self.brigade("86", "answer", "Q2", "--answer", "whatever"), "Q2 answered")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q2")["answer"], "whatever")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")
    def test_a_legacy_item_row_with_no_closing_option_closes_on_any_answer(self):
        self.started()
        ordinary = self.brigade("watch")
        question = "Hold this item?"
        path = self.at / "86.tsv"
        header = path.read_text().splitlines()[0]
        row = "\t".join(["Q1", "2026-10-06T00:00:00+00:00", "open", "D1", question, "keep parked", "keep parked", ""])
        path.write_text(header + "\n" + row + "\n")
        self.assertEqual(
            self.brigade("watch"),
            f"workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: open decision Q1: {question}; launch no worker or verifier until 86 answer Q1",
        )
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), "Q1 answered")
        stored = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((stored["state"], stored["answer"]), ("answered", "keep parked"))
        self.assertEqual(self.brigade("watch"), ordinary)
    def test_an_old_bounced_entry_is_ignored_for_the_current_sha(self):
        self.started("--check", "test ! -e BROKEN", paths="README.md,BROKEN")
        old = self.worker_commit("perf/d1", {"README.md": "fast\n", "BROKEN": "x\n"})
        self.passed(old)
        self.submit(old)
        self.brigade("dish", "D1", "--state", "queued")
        self.land("land")
        self.assertRegex(self.brigade("watch").splitlines()[1], r"^D1: E1 bounced: checks failed")
        self.brigade("dish", "D1", "--thread", "worker-1")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertIn("D1: in progress with no worker thread; launch a fresh worker", self.brigade("watch"))
        self.assertEqual(self.brigade("dish", "D1", "--thread", "worker-1", ok=False),
                         "brigade: thread worker-1 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.brigade("dish", "D1", "--thread", "worker-2")
        new = self.worker_commit("perf/d1", {"BROKEN": None})
        self.passed(new)
        self.assertEqual(self.submit(new), "E2")
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E2 queued")
    def test_a_refused_fire_mode_full_keeps_the_reason_in_the_unblocked_command(self):
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a")
        self.assertIn("nothing fired: 1 of 1 workers running",
                      self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix the lock order",
                                   "--mode", "full", "--reason", "tickets name a lock", ok=False))
        self.brigade("dish", "D1", "--state", "blocked")
        line = next(line for line in self.brigade("watch").splitlines() if line.startswith("T2:"))
        self.assertEqual(line, "T2: unblocked; run fire --tickets=T2 --station=bug-fix '--summary=Fix the lock order' "
                               "--timebox=60 --mode=full '--reason=tickets name a lock'")
        result = self.bash_unblocked(self.at, line)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "D2"), result.stderr)
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D2", "full", "tickets name a lock"]])
    def test_a_dishes_row_with_fourteen_fields_or_eighteen_is_malformed_and_one_with_sixteen_is_read(self):
        self.open()
        self.old_item()
        row = (self.at / "dishes.tsv").read_text().splitlines()[1].split("\t")
        for fields in (row[:14], [*row, "", "", ""]):
            (self.at / "dishes.tsv").write_text(self.OLD_DISHES + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("watch", ok=False),
                             "brigade: bridge-kit/perf/dishes.tsv line 2 is malformed; fix or remove it")
        (self.at / "dishes.tsv").write_text(self.OLD_DISHES + "\t".join([*row, ""]) + "\n")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")
    def test_status_and_watch_count_items_in_progress_and_in_review_as_running_workers(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "3")
        for summary in ("a", "b", "c"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b")
        self.brigade("dish", "D2", "--state", "in-review", "--sha", "abc1234")
        workers = "workers: 2 of 3 running, 1 idle, auto-start: normal"
        self.assertEqual(self.brigade("status"),
                         "thread not recorded\n"
                         "reporting: milestones, no landing contract, waiting tickets: 1, in progress: 1, in review: 1\n"
                         + workers + "\nliveness: every 600000 ms (default for milestones)\nkinds: fix 1 waiting, 2 in flight")
        self.assertEqual(self.brigade("watch"), workers + "\nD1: running 0m of 60m (no worker recorded)\nD2: in review")
        self.brigade("pass", "record", "D2", "--sha", "abc1234", "--author", CODEX, "--verifier", CLAUDE, "--verdict", "send-back")
        workers = "workers: 1 of 3 running, 2 idle, auto-start: normal"
        self.assertEqual(self.brigade("status").splitlines()[2], workers)
        self.assertEqual(self.brigade("watch"), workers + "\nD1: running 0m of 60m (no worker recorded)\nD2: sent back")
