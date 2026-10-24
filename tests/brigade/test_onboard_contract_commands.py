if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import BrigadeTest, json, setUpModule


class OnboardContractCommandTest(BrigadeTest):
    """The build and check hints name the landing contract check command, and the check row reads a contract check."""

    def detect(self):
        return json.loads(self.brigade("onboard", "--project-root", str(self.project), "--name", "Perf",
                                       "--detect"))

    def test_a_missing_check_names_the_landing_contract_command_fails_when_the_fix_names_only_set(self):
        # Processes: git builds the fixture repository. No land contract and no store exist.
        self.git_project()
        found = self.detect()
        self.assertEqual(found["facts"]["check_command"]["state"], "missing")
        self.assertIn("land.py init", found["facts"]["check_command"]["fix"])
        self.assertIn("--check", found["facts"]["check_command"]["fix"])
        self.assertIn("land.py init", found["facts"]["build_command"]["fix"])

    def test_a_contract_check_reads_as_configured_fails_when_the_reader_ignores_the_contract(self):
        # Processes: git and land build the fixture project and a contract holding one check.
        self.git_project()
        self.init_landing("--check", "python3 scripts/run_tests.py")
        self.landing_env()
        self.open()
        found = self.detect()
        self.assertEqual(found["facts"]["check_command"]["state"], "configured")
        self.assertEqual(found["facts"]["check_command"]["value"], ["python3 scripts/run_tests.py"])

    def test_a_stored_check_command_wins_over_a_contract_check_fails_when_the_contract_is_read_first(self):
        # Processes: git and land build the fixture project and a contract holding another check.
        self.git_project()
        self.init_landing("--check", "make lint")
        self.landing_env()
        self.open()
        self.brigade("set", "--check-command", "make test")
        found = self.detect()
        self.assertEqual(found["facts"]["check_command"]["state"], "configured")
        self.assertEqual(found["facts"]["check_command"]["value"], "make test")
