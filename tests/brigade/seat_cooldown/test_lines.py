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
from ..support import BrigadeTest, CLAUDE, CODEX, HELD_LINE, Path, ROOT, SCRIPT, STILL_OPEN, call, _WakeRace, datetime, json, os, runpy, shlex, subprocess, sys, time, timedelta, timezone
from .fixtures import BACKUP, FAILED, LIMIT_CLOCK, LIMIT_SPAN, NOT_A_LIMIT, NOW, PARENT, SPARE, _CooldownHelpers


class SeatCooldownLinesTest(BrigadeTest, _CooldownHelpers):
    def test_watch_prints_a_live_cooldown_and_stops_once_its_end_passes(self):
        """The defect: a cooling seat is invisible in watch, so a coordinator launches a worker on it."""
        self.cooldown_store()
        self.write_cooldown_row(seat=BACKUP, end="2026-02-01T01:00:00+00:00")
        self.assertIn(f"cooling {BACKUP} until 2026-02-01T01:00:00+00:00 (D1)", self.brigade("watch").splitlines())
        self.freeze("2026-02-01T02:00:00+00:00")
        self.assertEqual([line for line in self.brigade("watch").splitlines() if line.startswith("cooling ")], [])

    def test_close_lists_the_cooldown_with_its_seat_and_the_seat_used_instead(self):
        """The defect: a closeout omits the cooldown, so the user cannot see which seat went out or what replaced it."""
        self.cooldown_store()
        self.usage_limit(LIMIT_SPAN)
        code, text, errors = self.close_run("--dry-run")
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("## Seat cooldowns\n\n"
                      f"- D1: {FAILED} cooled {NOW} to 2026-02-01T00:30:00+00:00; relaunched on {BACKUP}\n", text)
        self.brigade("dish", "D1", "--thread", "thread-10")
        self.usage_limit(LIMIT_SPAN)
        code, text, errors = self.close_run("--dry-run")
        self.assertEqual((code, errors), (0, ""))
        self.assertIn(f"- D1: {FAILED} cooled {NOW} to 2026-02-01T00:30:00+00:00; launched nothing\n", text)
        self.assertEqual([line for line in text.splitlines() if line.startswith("- D1:")],
                         [f"- D1: {FAILED} cooled {NOW} to 2026-02-01T00:30:00+00:00; relaunched on {BACKUP}",
                          f"- D1: {FAILED} cooled {NOW} to 2026-02-01T00:30:00+00:00; launched nothing"])
