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


class SeatCooldownCommandTest(BrigadeTest, _CooldownHelpers):
    def test_a_limit_error_records_the_cooldown_and_prints_the_retry_seat(self):
        """The defect: a limit error leaves the store without a cooldown row and the coordinator without a seat."""
        self.cooldown_store()
        self.assertEqual(self.usage_limit(LIMIT_SPAN),
                         f"D1: {FAILED} cooling until 2026-02-01T00:30:00+00:00; relaunch on {BACKUP}")
        row = self.cooldown_rows()[-1]
        self.assertEqual((row["dish"], row["attempt"], row["thread"], row["seat"], row["start"], row["end"]),
                         ("D1", "1", "", FAILED, NOW, "2026-02-01T00:30:00+00:00"))
        self.assertEqual(self.cooldown_events(),
                         [{"seat": FAILED, "role": "perf-issue", "start": NOW, "end": "2026-02-01T00:30:00+00:00",
                           "replacement": BACKUP, "seats": [], "attempt": "1"}])

    def test_a_second_limit_error_after_the_retry_parks_the_dish_and_prints_the_end(self):
        """The defect: the retry raises the attempt number, so an attempt-number comparison relaunches a third worker."""
        self.cooldown_store()
        self.assertIn(f"relaunch on {BACKUP}", self.usage_limit(LIMIT_SPAN))
        self.brigade("dish", "D1", "--thread", "thread-10")
        self.assertEqual(self.dish_fields("thread"), ("thread-10",))
        self.assertEqual(self.usage_limit(LIMIT_SPAN),
                         "D1: parked until 2026-02-01T00:30:00+00:00; launch nothing before then")
        self.assertEqual(self.dish_fields("state"), ("blocked",))
        self.assertEqual([row["replacement"] for row in self.cooldown_events()], [BACKUP, ""])
        self.assertEqual([row["attempt"] for row in self.cooldown_rows()], ["1", "2"])

    def test_an_error_with_no_usage_limit_phrase_records_nothing(self):
        """The defect: a command that writes its rows before it classifies the error changes the store on a plain failure."""
        self.cooldown_store()
        names = ("threads.tsv", "log.tsv", "dishes.tsv", "restaurant.json")
        before = {name: (self.at / name).read_bytes() for name in names}
        self.assertEqual(self.usage_limit(NOT_A_LIMIT),
                         f"D1: {FAILED} failed without a usage limit; respawn per Failure handling")
        self.assertEqual({name: (self.at / name).read_bytes() for name in names}, before)

    def test_a_reset_time_in_the_error_sets_the_end_instead_of_the_default_window(self):
        """The defect: the store's window overrides the reset the error names, so the seat stays out past its own reset."""
        self.cooldown_store()
        self.assertEqual(self.usage_limit(LIMIT_CLOCK),
                         f"D1: {FAILED} cooling until 2026-02-01T12:30:00+00:00; relaunch on {BACKUP}")
        self.assertEqual(self.cooldown_rows()[-1]["end"], "2026-02-01T12:30:00+00:00")
        self.brigade("set", "--cooldown-minutes", "5")
        self.fire_another(2, "perf/d2", "thread-11")
        self.assertEqual(self.usage_limit("You've hit your usage limit.", "D2"),
                         f"D2: {FAILED} cooling until 2026-02-01T00:05:00+00:00; relaunch on {BACKUP}")
        self.assertEqual(self.cooldown_rows()[-1]["end"], "2026-02-01T00:05:00+00:00")

    def test_an_ended_cooldown_no_longer_passes_its_provider_as_out(self):
        """The defect: an ended cooldown keeps its provider out, so a usable seat is never chosen again."""
        self.cooldown_store()
        self.fire_another(2, "perf/d2", "thread-11")
        self.write_cooldown_row(seat=BACKUP, end="2026-02-01T00:45:00+00:00")
        self.assertEqual(self.usage_limit(LIMIT_SPAN), f"D1: {FAILED} cooling until 2026-02-01T00:30:00+00:00; "
                                                       f"relaunch on {SPARE}")
        self.write_cooldown_row(seat=BACKUP, end="2026-01-31T23:00:00+00:00")
        self.assertEqual(self.usage_limit(LIMIT_SPAN, "D2"),
                         f"D2: {FAILED} cooling until 2026-02-01T00:30:00+00:00; relaunch on {BACKUP}")
