if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import shlex

from .support import BrigadeTest, setUpModule, SCRIPT, _owner_words, call_in_process


class ReviewCheckCueTest(BrigadeTest):
    READ = ("see Read reports/D1.md and the diff. t3_thread_read the worker thread. "
            "If that run is still open, interrupt it, hang the attempt, then review per Run a service step 6.")

    def report_back_in_process(self):
        outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                 *_owner_words(self.at), "next", "report-back", "D1"])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        return outcome.stdout_text().strip()

    def review_send_back(self):
        outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                 *_owner_words(self.at), "next", "review", "D1",
                                                 "--verdict", "send-back"])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        return outcome.stdout_text().strip()

    def contract(self, *checks):
        self.git_project()
        self.init_landing(*[word for check in checks for word in ("--check", check)])
        self.landing_env()

    def fire_one(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")

    def check_line(self, command):
        return (f"see After you read the diff and before dish D1 --state in-review and the verifier, "
                f"run this check in the worker's worktree at its head through land.py slot -- sh -c {shlex.quote(command)}")

    def test_stored_check_prints_on_a_ready_report_back_fails_when_the_wake_keeps_only_the_read_line(self):
        self.fired_bug_fix()
        self.brigade("set", "--check-command", "make lint")
        self.assertEqual(self.report_back_in_process(), "\n".join([
            "wake: report-back D1",
            "reporting: milestones",
            "run dish D1 --reported",
            self.READ,
            self.check_line("make lint"),
            "reply: no (routine: worker report)",
        ]))

    def test_compound_check_keeps_operators_inside_one_slot_fails_when_the_command_is_split(self):
        self.fired_bug_fix()
        self.brigade("set", "--check-command", "make lint && make test")
        self.assertEqual(self.report_back_in_process(), "\n".join([
            "wake: report-back D1",
            "reporting: milestones",
            "run dish D1 --reported",
            self.READ,
            "see After you read the diff and before dish D1 --state in-review and the verifier, "
            "run this check in the worker's worktree at its head through land.py slot -- sh -c 'make lint && make test'",
            "reply: no (routine: worker report)",
        ]))

    def test_contract_check_prints_without_a_stored_key_fails_when_an_unset_store_prints_no_line(self):
        self.contract("make lint")
        self.fire_one()
        ready = "\n".join([
            "wake: report-back D1",
            "reporting: milestones",
            "run dish D1 --reported",
            self.READ,
            self.check_line("make lint"),
            "reply: no (routine: worker report)",
        ])
        self.assertEqual(self.report_back_in_process(), ready)
        path = self.at / "restaurant.json"
        meta = json.loads(path.read_text())
        meta["checkCommand"] = ""
        path.write_text(json.dumps(meta, indent=2) + "\n")
        self.assertEqual(self.report_back_in_process(), ready)

    def test_stored_none_prints_no_check_line_fails_when_none_falls_through_to_the_contract(self):
        self.contract("make lint")
        self.fire_one()
        self.brigade("set", "--check-command", "none")
        self.assertEqual(self.report_back_in_process(), "\n".join([
            "wake: report-back D1",
            "reporting: milestones",
            "run dish D1 --reported",
            self.READ,
            "reply: no (routine: worker report)",
        ]))

    def test_missing_commands_print_no_check_line_fails_when_a_missing_or_split_contract_prints_a_line(self):
        self.git_project()
        self.landing_env()
        self.fire_one()
        plain = "\n".join([
            "wake: report-back D1",
            "reporting: milestones",
            "run dish D1 --reported",
            self.READ,
            "reply: no (routine: worker report)",
        ])
        self.assertEqual(self.report_back_in_process(), plain)
        self.init_landing("--check", "make lint", "--check", "make test")
        self.assertEqual(self.report_back_in_process(), plain)

    def test_held_dish_prints_no_check_line_fails_when_debt_or_an_open_decision_keeps_the_line(self):
        self.fired_bug_fix()
        self.brigade("set", "--check-command", "make lint")
        self.sent_back(3)
        self.assertEqual(self.report_back_in_process(), "\n".join([
            "wake: report-back D1",
            "reporting: milestones",
            "run 86 add --dish D1 --round-budget",
            "run close",
            "reply: yes (decision: D1)",
        ]))
        self.fresh()
        self.brigade("set", "--check-command", "make lint")
        self.park_item()
        sheet = self.report_back_in_process()
        self.assertEqual(sheet, "\n".join([
            "wake: report-back D1",
            "reporting: milestones",
            "run dish D1 --reported",
            "see D1: open decision Q1: D1: grok could not pass the seat's options: refused. "
            "Move this coordinator to a provider that passes them?; "
            "launch no worker or verifier until 86 answer Q1",
            "run close",
            "reply: yes (decision: Q1)",
        ]))

    def test_review_wake_prints_no_check_line_fails_when_the_line_leaks_onto_another_wake(self):
        self.fired_bug_fix()
        self.brigade("set", "--check-command", "make lint")
        self.assertEqual(self.review_send_back(), "\n".join([
            "wake: review D1",
            "reporting: milestones",
            "see Record pass record for D1 with verdict send-back. No sha is recorded.",
            "if the old worker's wait for D1 is terminal and D1 may take a fix round: "
            "run dish D1 --state in-progress",
            "reply: yes (sent-back: D1)",
        ]))
