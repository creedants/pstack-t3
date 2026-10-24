if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    HELD_QUESTION,
    ITEM_DECISION,
    Path,
    STILL_OPEN,
    THREAD_A,
    THREAD_B,
    THREAD_C,
    THREAD_D,
    datetime,
    json,
    os,
    shutil,
    subprocess,
    timezone,
    unittest,
)


class ReviewsSplit2Test(BrigadeTest):
    def test_86_add_takes_a_question_or_the_round_budget(self):
        self.started()
        self.sent_back(3)
        needs = "brigade: 86 add needs --question, --options, and --default"
        self.assertEqual(self.brigade("86", "add", ok=False), needs)
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?", "--options", "a, b", ok=False), needs)
        alone = "brigade: 86 add --round-budget takes --dish and no --question, --options, or --default"
        self.assertEqual(self.brigade("86", "add", "--round-budget", ok=False), alone)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, "--question", "Move?", ok=False), alone)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, "--default", "drop", ok=False), alone)
        self.assertEqual((self.at / "86.tsv").read_text().splitlines(),
                         ["id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\tkind\tanswered"])
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no"), "Q1")
        self.assertEqual(self.park_item(), "Q2")

    def test_the_count_starts_again_after_a_round_decision(self):
        self.started()
        self.sent_back(3)
        self.brigade(*self.ROUND_BUDGET)
        self.brigade("86", "answer", "Q1", "--answer", "redesign")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.sent_back(2, start=4), "D1 sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("a6"), "D1 sent-back; 3 send-backs, decision pending")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q2")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q2")["question"], self.ROUND_QUESTION)

    def test_a_pass_at_three_send_backs_settles_the_count(self):
        self.started()
        self.assertEqual(self.sent_back(3), "D1 sent-back; 3 send-backs, decision pending")
        self.assertEqual(self.brigade(*self.PASS, "a3"), "D1 passed")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")
        self.assertEqual(self.brigade(*self.FIX_BRIEF, ok=False),
                         "brigade: D1 is passed; brief a dish that is in progress or sent back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("passed"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued"), "D1 queued")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("queued"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "bounce-worker"), "D1 in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (thread bounce-worker)")
        self.assertEqual(self.send_back("a4"), "D1 sent-back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_a_merged_item_owes_nothing_and_its_ticket_starts_a_new_count(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.PASS, "a4"), "D1 passed")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "a4", "--verdict", "send-back", "--reason", "other", "--author", CODEX,
                                      "--verifier", CLAUDE, "--member"),
                         "D1: member send-back on record; D1 stays passed")
        self.assertEqual(self.brigade("dish", "D1", "--state", "merged"), "D1 merged")
        for sha in ("m1", "m2", "m3"):
            self.assertEqual(self.send_back(sha, "--late"), "D1: late send-back on record; D1 stays merged")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("merged"))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--state", "waiting"), "T1 waiting")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")
        self.assertEqual(self.brigade("pass", "record", "D2", "--sha", "b1", "--verdict", "send-back", "--reason", "other", "--author", CODEX,
                                      "--verifier", CLAUDE), "D2 sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD2: sent back")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_a_dropped_item_owes_nothing_and_its_ticket_starts_a_new_count(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("dropped"))
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")
        self.assertEqual(self.brigade("pass", "record", "D2", "--sha", "b1", "--verdict", "send-back", "--reason", "other", "--author", CODEX,
                                      "--verifier", CLAUDE), "D2 sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD2: sent back")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_a_dropped_item_put_back_in_progress_owes_its_decision_again(self):
        self.started()
        self.sent_back(3)
        self.brigade("dish", "D1", "--state", "dropped")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.brigade("watch").splitlines()[1], self.PENDING)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "fresh-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")

    def test_a_late_send_back_written_before_a_pass_stops_counting_at_the_pass(self):
        self.started()
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.send_back("a0", "--late"),
                         "D1: late send-back on record; D1 stays in-progress; 3 send-backs, decision pending")
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.passed(sha)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("a4"), "D1 sent-back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")

    def test_a_detour_through_review_starts_no_fix_round(self):
        self.started()
        self.sent_back(3)
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha), "D1 in-review")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        before = self.unchanged()
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "fresh-worker", ok=False),
                         self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "fresh-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--task", "fresh-task", ok=False), self.NO_FIX_ROUND)
        self.unchanged(before)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_late_rows_recorded_while_the_decision_is_open_stop_counting_at_its_answer(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        for count in (4, 5, 6):
            self.assertEqual(self.send_back(f"missed{count}", "--late"),
                             f"D1: late send-back on record; D1 stays sent-back; {count} send-backs, decision pending")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "redesign"), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertGreater(row["answered"], self.pass_rows()[-1][0])
        self.assertGreater(row["answered"], row["at"])
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("missed7", "--late"), "D1: late send-back on record; D1 stays in-progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")

    def test_a_decision_table_written_before_the_kind_and_answered_columns_still_reads(self):
        self.started()
        self.assertEqual(self.sent_back(3), "D1 sent-back; 3 send-backs, decision pending")
        stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        old = ["\t".join(["Q1", stamp, "answered", "D1", "Drop this item?", "drop, keep parked", "keep parked", "drop"]),
               "\t".join(["Q2", stamp, "open", "", "Ship it?", "yes, no", "no", ""])]
        table = self.at / "86.tsv"
        table.write_text("id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\n" + "\n".join(old) + "\n")
        self.assertEqual(self.brigade("86", "list"), "Q2: Ship it? Options: yes, no. Default: no.")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q3")
        lines = table.read_text().splitlines()
        self.assertEqual(lines[1:3], old)
        self.assertEqual(lines[3].split("\t")[5:], ["known limits, redesign, drop, keep parked", "keep parked", "",
                                                     "round-budget", ""])
        self.assertEqual(self.brigade("86", "answer", "Q3", "--answer", "drop"), "Q3 answered")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        stored = [self.table_row(self.at, "86.tsv", ident) for ident in ("Q1", "Q2", "Q3")]
        self.assertEqual([(row["state"], row["answer"], row["kind"], bool(row["answered"])) for row in stored],
                         [("answered", "drop", "", False), ("open", "", "", False), ("answered", "drop", "round-budget", True)])
        for fields, line in ((old[1].split("\t")[:7], 5), ([*old[1].split("\t"), "", "", "extra"], 5)):
            table.write_text(table.read_text() + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("86", "list", ok=False),
                             f"brigade: bridge-kit/perf/86.tsv line {line} is malformed; fix or remove it")
            table.write_text("\n".join(table.read_text().splitlines()[:-1]) + "\n")

    def test_an_unrelated_answered_item_decision_lifts_no_refusal(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.park_item(), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "moved"), "Q1 answered")
        self.assertNotEqual(self.table_row(self.at, "86.tsv", "Q1")["answered"], "")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q2")

    def test_86_list_names_the_item_and_keeps_a_blank_dish_in_the_old_format(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no"), "Q2")
        self.assertEqual(self.brigade("86", "list"), "\n".join([
            f"Q1 for D1: {HELD_QUESTION} Options: moved, keep parked. Default: keep parked.",
            "Q2: Ship it? Options: yes, no. Default: no.",
        ]))

    def test_moved_closes_an_item_decision_and_a_later_answer_writes_nothing(self):
        self.started()
        ordinary = self.brigade("watch")
        self.assertEqual(ordinary, "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")
        self.park_item()
        opened = [row for row in self.log_rows() if row["kind"] == "decision"]
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "Moved."), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["answer"]), ("answered", "moved"))
        answered = [row for row in self.log_rows() if row["kind"] == "decision" and row["state"] == "answered"]
        self.assertEqual(len(answered), 1)
        self.assertEqual(len([row for row in self.log_rows() if row["kind"] == "decision"]), len(opened) + 1)
        self.assertEqual(self.brigade("watch"), ordinary)
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), "Q1 answered")
        self.assertEqual(self.store_bytes(), before)

    def test_a_second_refusal_after_keep_parked_prints_the_open_id(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        table = (self.at / "86.tsv").read_text()
        decisions = [row for row in self.log_rows() if row["kind"] == "decision"]
        other = "D1: cursor could not pass the seat's options: refused again. Move this coordinator to a provider that passes them?"
        self.assertEqual(self.park_item(other), "Q1")
        self.assertEqual((self.at / "86.tsv").read_text(), table)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "decision"], decisions)

    def test_an_item_decision_needs_a_default_and_a_closing_option(self):
        self.started()
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "keep parked", "--default", "keep parked", ok=False), ITEM_DECISION)
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "moved, keep parked", "--default", "ship it", ok=False), ITEM_DECISION)
        self.assertEqual((self.at / "86.tsv").read_text().splitlines(),
                         ["id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\tkind\tanswered"])
        self.assertEqual(self.park_item(), "Q1")
        table = (self.at / "86.tsv").read_text()
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "keep parked", "--default", "keep parked"), "Q1")
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "moved, keep parked", "--default", "ship it"), "Q1")
        self.assertEqual((self.at / "86.tsv").read_text(), table)

    def test_an_item_decision_whose_options_collapse_to_the_default_once_stored_is_refused(self):
        self.started()
        before = tuple((self.at / name).read_bytes() for name in ("86.tsv", "log.tsv"))
        for options in ("keep parked, keep\nparked", "keep parked, keep\tparked", "keep parked, keep\r\nparked"):
            self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                          "--options", options, "--default", "keep parked", ok=False),
                             ITEM_DECISION, repr(options))
            self.assertEqual(tuple((self.at / name).read_bytes() for name in ("86.tsv", "log.tsv")), before,
                             repr(options))

    def test_an_item_decision_stores_its_options_cleaned_and_closes_only_on_the_other_option(self):
        self.started()
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move\tthis?",
                                      "--options", "moved\r\nelsewhere, keep\nparked",
                                      "--default", "keep\tparked"), "Q1")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["question"], row["options"], row["default"]),
                         ("Move this?", "moved elsewhere, keep parked", "keep parked"))
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"),
                         "Q1 still open; D1 stays held until 86 answer Q1 --answer 'moved elsewhere'")
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "Moved elsewhere."), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["answer"]), ("answered", "moved elsewhere"))
