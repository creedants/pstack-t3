import shlex
import sys

from .support import BrigadeTest, setUpModule


class ContractCheckCommandTest(BrigadeTest):
    """The affected reader and edits verify take the landing contract's one check when no store records one."""

    CONTRACT_CHECK = "make lint"

    def contract(self, *checks):
        """A landing contract on the project recording these checks, read by in-process brigade calls."""
        self.git_project()
        self.init_landing(*[word for check in checks for word in ("--check", check)])
        self.landing_env()

    def failing_check(self):
        return f"{shlex.quote(sys.executable)} -c 'raise SystemExit(9)'"

    def affected(self):
        return self.brigade("affected", "--root", str(self.project), "--paths", "src/a.py")

    def test_affected_names_the_contract_check_when_the_store_records_no_check_fails_when_the_store_alone_is_read(self):
        # Processes: git and land build the fixture project and its contract.
        self.contract(self.CONTRACT_CHECK)
        self.open()
        out = self.affected()
        self.assertIn(f"slot -- {self.CONTRACT_CHECK}`", out)
        self.assertNotIn("No verify command is set", out)

    def test_edits_verify_runs_its_contract_check_when_the_store_records_no_check_fails_when_the_store_alone_is_read(self):
        # Processes: git and land build the fixture project and its contract; edits verify runs its tools' scripts.
        self.contract(self.CHECK_COMMAND)
        reviewed = self.edits_store()
        self.brigade("set", "--check-command", "")
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n"
                             f"{self.verified(reviewed, head)}", ""))
        self.assertEqual(len(self.edits_rows()), 1)

    def test_a_stored_check_command_wins_over_the_contract_check_fails_when_the_contract_is_read_first(self):
        # Processes: git and land build the fixture project and its contract.
        self.contract(self.CONTRACT_CHECK)
        self.open()
        self.brigade("set", "--check-command", "make test")
        out = self.affected()
        self.assertIn("slot -- make test`", out)
        self.assertNotIn(self.CONTRACT_CHECK, out)

    def test_a_stored_none_keeps_the_contract_check_unused_fails_when_an_unset_store_falls_back(self):
        # Processes: git and land build the fixture project and its contract.
        self.contract(self.CONTRACT_CHECK)
        self.open()
        self.brigade("set", "--check-command", "none")
        out = self.affected()
        self.assertIn("No verify command is set", out)
        self.assertNotIn(self.CONTRACT_CHECK, out)

    def test_edits_verify_keeps_the_store_check_over_the_contract_check_fails_when_the_contract_is_read_first(self):
        # Processes: git and land build the fixture project and its contract; edits verify runs its tools' scripts.
        self.contract(self.failing_check())
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n"
                             f"{self.verified(reviewed, head)}", ""))

    def test_no_check_in_the_store_or_the_contract_keeps_the_needs_commands_refusal_fails_when_an_empty_contract_is_taken_as_a_command(self):
        # Processes: git and land build the fixture project and a contract holding no check.
        self.contract()
        reviewed = self.edits_store()
        self.brigade("set", "--check-command", "")
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head), (1, "", self.NEEDS_COMMANDS))

    def test_two_contract_checks_give_affected_no_command_fails_when_one_of_several_is_taken(self):
        # Processes: git and land build the fixture project and a contract holding two checks.
        self.contract("make lint", "make test")
        self.open()
        out = self.affected()
        self.assertIn("No verify command is set", out)
