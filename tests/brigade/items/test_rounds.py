if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import CLAUDE, CODEX, THREAD_A, THREAD_B, json

class RoundsMixin:
    def test_a_late_send_back_below_the_budget_names_no_decision(self):
        self.started()
        self.sent_back(1)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.send_back("a0", "--late"), "D1: late send-back on record; D1 stays in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in progress with no worker thread; launch a fresh worker")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "w2"), "D1 in-progress")

    def test_a_member_send_back_recorded_after_the_passing_rows_changes_no_verdict(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/9", "--sha", "abc", "--verdict", "send-back",
                                      "--reason", "other",
                                      "--author", CLAUDE, "--verifier", "grok/grok-4.7", "--note", "nit", "--member"),
                         "D1: member send-back on record; D1 stays passed")
        self.assertEqual(self.dish_fields("state", "pr", "sha"), ("passed", "", "abc"))
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"verdict": "pass", "author": CLAUDE, "verifier": CODEX, "note": "", "crossFamily": True})
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued"), "D1 queued")

    def test_a_second_attempt_after_a_send_back_gets_role_fix_worker_attempt_two_and_closes_the_first_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record("abc", "send-back", "--note", "test asserts the bug")
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_B), "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", "stamp"],
                          ["D1", "2", "fix worker", THREAD_B, "", "stamp", ""]])

    def test_a_send_back_after_a_pass_with_edits_round_ends_the_refusal_of_a_pass(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.send_back("h2")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.record_pass_at("h3"), "D1 passed")

    def test_a_send_back_clears_the_worker_and_refuses_the_old_thread(self):
        self.open()
        self.fire_one()
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-8"), "D1 sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-9", ok=False),
                         "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "thread-8")
        refused = self.brigade("dish", "D1", "--state", "in-progress", "--thread", "thread-9", ok=False)
        self.assertEqual(refused, "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "sent-back")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "thread-8")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "thread-10"), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "thread-10")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in progress with no worker thread; launch a fresh worker")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-9", ok=False),
                         "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-10", ok=False),
                         "brigade: thread thread-10 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-11"), "D1 in-progress")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-11"), "D1 in-progress")

    def test_an_ordinary_decision_answered_with_a_round_option_lifts_no_refusal(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Redesign the title wording?",
                                      "--options", "redesign, keep parked", "--default", "keep parked"), "Q1")
        held = "brigade: D1 is held by Q1; answer it, then run 86 add --dish D1 --round-budget"
        table = (self.at / "86.tsv").read_bytes()
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), held)
        self.assertEqual((self.at / "86.tsv").read_bytes(), table)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "redesign"), "Q1 answered")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q1")["kind"], "")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q2")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q2")["kind"], "round-budget")
        self.assertEqual(self.brigade("86", "answer", "Q2", "--answer", "redesign"), "Q2 answered")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")

    def test_dish_mode_full_twice_keeps_the_first_reason(self):
        self.fired_bug_fix()
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "contested: x"),
                         "D1 in-progress, mode full: contested: x")
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "y"),
                         "D1 in-progress, mode full: contested: x")
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D1", "full", "contested: x"]])
        self.assertEqual(self.brigade("dish", "D1", "--state", "blocked", "--mode", "full", "--reason", "y"),
                         "D1 blocked, mode full: contested: x")
        self.record("abc", "pass")
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "z", ok=False),
                         "brigade: D1 is queued; only open work moves to full mode")
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "z", ok=False),
                         "brigade: D1 is merged; only open work moves to full mode")
        self.assertEqual(len(self.mode_rows()), 1)

    def test_dish_mode_full_with_a_stale_owner_appends_nothing(self):
        self.fired_bug_fix()
        self.brigade("set", "--thread", "c1")
        self.brigade("set", "--thread", "c2", "--replace")
        stale = "brigade: owner c1@1 is stale; this store is owned by c2@2"
        before = (self.at / "log.tsv").read_bytes()
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "r", ok=False), stale)
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)
        self.brigade("dish", "D1", "--mode", "full", "--reason", "r")
        after = (self.at / "log.tsv").read_bytes()
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "r", ok=False), stale)
        self.assertEqual((self.at / "log.tsv").read_bytes(), after)
        self.record("abc", "pass")
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "r", ok=False), stale)

    def test_drop_closes_the_round_decision_and_lifts_the_refusal(self):
        self.round_decision_lifts_the_refusal("drop", "drop")

    def test_edits_verify_refuses_a_from_with_no_pass_with_edits_round_and_a_sha_that_is_no_commit(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.send_back(head)
        self.assertEqual(self.verify(head, head), (1, "", f"brigade: D1 has no pass-with-edits round at {head}"))
        self.assertEqual(self.verify(reviewed[:12], head),
                         (1, "", f"brigade: D1 has no pass-with-edits round at {reviewed[:12]}"))
        absent = "0" * 40
        self.assertEqual(self.verify(reviewed, absent),
                         (1, "", f"brigade: {absent} is not a commit in {self.project.resolve()}"))

    def test_known_limits_closes_the_round_decision_and_lifts_the_refusal(self):
        self.round_decision_lifts_the_refusal("Known limits.", "known limits")

    def test_late_send_backs_written_after_a_pass_count_once_the_item_is_back_in_progress(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.passed(sha)
        for sha in ("m1", "m2", "m3"):
            self.assertEqual(self.send_back(sha, "--late"), "D1: late send-back on record; D1 stays passed")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("passed"))
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "bounce-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "known limits"), "Q1 answered")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "bounce-worker"), "D1 in-progress")

    def test_member_send_backs_do_not_count_toward_the_round_budget(self):
        self.started()
        self.sent_back(2)
        for seat in ("grok/grok-4.7", "opencode/kimi-k3"):
            self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "a2", "--verdict", "send-back", "--reason", "other", "--author", CODEX,
                                          "--verifier", seat, "--member"),
                             "D1: member send-back on record; D1 stays sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 2 send-backs since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")

    def test_redesign_closes_the_round_decision_and_lifts_the_refusal(self):
        self.round_decision_lifts_the_refusal("redesign", "redesign")

    def test_send_back_blocks_landing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "send-back",
                     "--reason", "other",
                     "--author", CLAUDE, "--verifier", CODEX, "--note", "test asserts the bug")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                         "brigade: D1 at abc: send-back (test asserts the bug)")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", ok=False),
                         "brigade: only reviewed work lands: D1 at abc: send-back (test asserts the bug)")

    def test_the_third_send_back_refuses_another_fix_round(self):
        self.started()
        self.assertEqual(self.sent_back(2), "D1 sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.send_back("a3"), "D1 sent-back; 3 send-backs, decision pending")
        before = tuple((self.at / name).read_bytes() for name in ("dishes.tsv", "log.tsv", "86.tsv"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "w9", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(tuple((self.at / name).read_bytes() for name in ("dishes.tsv", "log.tsv", "86.tsv")), before)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha), "D1 in-review")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
