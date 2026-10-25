if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import BrigadeTest
from .fixtures import BACKUP, FAILED, PARENT, SPARE, _CooldownHelpers


COOLING_END = "2026-02-01T02:00:00+00:00"

BACKUP_SEAT = '{"instanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "medium"}}'

NORMAL_SEAT = '{"instanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}'


class SeatCooldownLaunchTest(BrigadeTest, _CooldownHelpers):
    def launch_outcome(self):
        """The launch command for D1, run in this process so the clock `freeze` pinned decides what is cooling."""
        return self.outcome_in_process("launch", "D1", "--parent", PARENT, "--catalog", str(self.catalog()))

    def test_launch_prints_the_backup_seat_when_the_resolved_seat_is_cooling(self):
        """The defect: launch suggests the seat whose provider just hit its usage limit."""
        self.cooldown_store()
        self.write_cooldown_row(seat=FAILED, end=COOLING_END)
        outcome = self.launch_outcome()
        self.assertEqual(outcome.code, 0, outcome.stderr_text())
        self.assertEqual(outcome.stdout_text(), BACKUP_SEAT + "\n")
        self.assertEqual(outcome.stderr_text(), f"D1: {FAILED} cooling until {COOLING_END}; launch on {BACKUP}\n")

    def test_launch_prints_the_normal_seat_after_the_cooldown_end_passes(self):
        """The defect: a cooldown whose end has passed still diverts the launch to a backup seat."""
        self.cooldown_store()
        self.write_cooldown_row(seat=FAILED, end="2026-01-31T23:00:00+00:00")
        outcome = self.launch_outcome()
        self.assertEqual((outcome.code, outcome.stderr_text()), (0, ""))
        self.assertEqual(outcome.stdout_text(), NORMAL_SEAT + "\n")

    def test_launch_skips_a_cooling_provider_when_another_of_its_seats_is_the_one_cooling(self):
        """The defect: matching only the exact seat suggests the same provider that is out through a sibling model."""
        self.cooldown_store()
        self.write_cooldown_row(seat="claudeAgent/claude-haiku-5-5", end=COOLING_END)
        outcome = self.launch_outcome()
        self.assertEqual(outcome.code, 0, outcome.stderr_text())
        self.assertEqual(outcome.stdout_text(), BACKUP_SEAT + "\n")
        self.assertEqual(outcome.stderr_text(),
                         f"D1: claudeAgent/claude-haiku-5-5 cooling until {COOLING_END}; launch on {BACKUP}\n")

    def test_launch_exits_1_naming_the_earliest_end_when_no_seat_is_runnable(self):
        """The defect: launch names a seat that cannot run instead of saying when one comes back."""
        self.cooldown_store()
        self.write_cooldown_rows([(BACKUP, "2026-02-01T01:00:00+00:00"),
                                  (FAILED, COOLING_END),
                                  (SPARE, "2026-02-01T03:00:00+00:00")])
        outcome = self.launch_outcome()
        self.assertEqual(outcome.code, 1, outcome.stdout_text())
        self.assertEqual(outcome.stdout_text(), "")
        self.assertEqual(outcome.stderr_text().strip(),
                         f"brigade: D1: {FAILED} cooling until {COOLING_END}; "
                         "no seat is runnable before 2026-02-01T01:00:00+00:00")
