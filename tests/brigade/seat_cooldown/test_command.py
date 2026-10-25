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
                         f'D1: {FAILED} cooling until 2026-02-01T00:30:00+00:00; relaunch on {BACKUP} '
                         'options {"reasoningEffort": "medium"}')
        row = self.cooldown_rows()[-1]
        self.assertEqual((row["dish"], row["attempt"], row["thread"], row["seat"], row["start"], row["end"]),
                         ("D1", "1", "", FAILED, NOW, "2026-02-01T00:30:00+00:00"))
        self.assertEqual(self.cooldown_events(),
                         [{"seat": FAILED, "role": "perf-issue", "start": NOW, "end": "2026-02-01T00:30:00+00:00",
                           "replacement": f'{BACKUP} options {{"reasoningEffort": "medium"}}', "seats": [],
                           "rule": {}, "chain": "0", "spent": True, "attempt": "1"}])

    def test_a_second_limit_error_after_the_retry_parks_the_dish_and_prints_the_end(self):
        """The defect: the retry raises the attempt number, so an attempt-number comparison relaunches a third worker."""
        self.cooldown_store()
        self.assertIn(f"relaunch on {BACKUP}", self.usage_limit(LIMIT_SPAN))
        self.brigade("dish", "D1", "--thread", "thread-10")
        self.assertEqual(self.dish_fields("thread"), ("thread-10",))
        self.assertEqual(self.usage_limit(LIMIT_SPAN),
                         "D1: parked until 2026-02-01T00:30:00+00:00; launch nothing before then")
        self.assertEqual(self.dish_fields("state"), ("blocked",))
        self.assertEqual([row["replacement"] for row in self.cooldown_events()],
                         [f'{BACKUP} options {{"reasoningEffort": "medium"}}', ""])
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
                         f'D1: {FAILED} cooling until 2026-02-01T12:30:00+00:00; relaunch on {BACKUP} '
                         'options {"reasoningEffort": "medium"}')
        self.assertEqual(self.cooldown_rows()[-1]["end"], "2026-02-01T12:30:00+00:00")
        self.brigade("set", "--cooldown-minutes", "5")
        self.fire_another(2, "perf/d2", "thread-11")
        self.assertEqual(self.usage_limit("You've hit your usage limit.", "D2"),
                         f'D2: {FAILED} cooling until 2026-02-01T00:05:00+00:00; relaunch on {BACKUP} '
                         'options {"reasoningEffort": "medium"}')
        self.assertEqual(self.cooldown_rows()[-1]["end"], "2026-02-01T00:05:00+00:00")

    def test_an_ended_cooldown_no_longer_passes_its_provider_as_out(self):
        """The defect: an ended cooldown keeps its provider out, so a usable seat is never chosen again."""
        self.cooldown_store()
        self.fire_another(2, "perf/d2", "thread-11")
        self.write_cooldown_row(seat=BACKUP, end="2026-02-01T00:45:00+00:00")
        self.assertEqual(self.usage_limit(LIMIT_SPAN), f'D1: {FAILED} cooling until 2026-02-01T00:30:00+00:00; '
                                                       f'relaunch on {SPARE} options {{"reasoningEffort": "medium"}}')
        self.write_cooldown_row(seat=BACKUP, end="2026-01-31T23:00:00+00:00")
        self.assertEqual(self.usage_limit(LIMIT_SPAN, "D2"),
                         f'D2: {FAILED} cooling until 2026-02-01T00:30:00+00:00; relaunch on {BACKUP} '
                         'options {"reasoningEffort": "medium"}')

    def test_four_limit_reports_on_one_attempt_relaunch_once_and_park_the_rest(self):
        """The defect: a parked event's empty replacement resets the guard, so a fourth report relaunches again."""
        self.cooldown_store()
        first, *rest = [self.usage_limit(LIMIT_SPAN) for _ in range(4)]
        self.assertEqual(first, f'D1: {FAILED} cooling until 2026-02-01T00:30:00+00:00; relaunch on {BACKUP} '
                                'options {"reasoningEffort": "medium"}')
        self.assertEqual(rest, ["D1: parked until 2026-02-01T00:30:00+00:00; launch nothing before then"] * 3)
        self.assertEqual(self.dish_fields("state"), ("blocked",))

    def test_a_sent_back_attempt_gets_its_own_retry_after_the_old_cooldown_expires(self):
        """The defect: comparing attempt numbers parks a fresh attempt whose own retry never went out."""
        self.cooldown_store()
        self.write_roles(BACKUP, SPARE, role="verifiers")
        self.assertEqual(self.usage_limit(LIMIT_SPAN, "D1", "verifiers", FAILED, "--author", CLAUDE),
                         f"D1: {FAILED} cooling until 2026-02-01T00:30:00+00:00; relaunch on {BACKUP}")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--reason", "other",
                     "--author", CODEX, "--verifier", CLAUDE)
        self.brigade("dish", "D1", "--state", "in-progress", "--thread", "thread-20")
        self.freeze("2026-02-01T03:00:00+00:00")
        self.assertEqual(self.usage_limit(LIMIT_SPAN, "D1", "verifiers", FAILED, "--author", CLAUDE),
                         f"D1: {FAILED} cooling until 2026-02-01T03:30:00+00:00; relaunch on {BACKUP}")
        self.assertEqual(self.dish_fields("state"), ("in-progress",))

    def test_a_past_or_zone_free_reset_time_falls_back_to_the_window(self):
        """The defect: a stale clock time rolls forward a day, a zone-free stamp is read as UTC, and an oversized span raises."""
        self.cooldown_store("2026-02-01T13:00:00+00:00")
        for text in ("You've hit your usage limit. Your limit will reset at 12:30 UTC.",
                     "You've hit your usage limit. Your limit will reset at 2026-02-02T12:30:00.",
                     "You've hit your usage limit. Try again after 999999999999999999999999999999 hours."):
            with self.subTest(text=text):
                self.assertIn("until 2026-02-01T14:00:00+00:00", self.usage_limit(text))
                self.assertEqual(self.cooldown_rows()[-1]["end"], "2026-02-01T14:00:00+00:00")

    def test_a_backup_with_options_prints_and_records_the_whole_target(self):
        """The defect: the printed and recorded target drops the backup's options, so the coordinator cannot launch it."""
        self.cooldown_store()
        self.write_roles_file({"perf-issue": [self.named(FAILED)]},
                              {"perf-issue": [self.named(BACKUP, {"reasoningEffort": "medium",
                                                                  "serviceTier": "priority"})]})
        line = self.usage_limit(LIMIT_SPAN)
        self.assertEqual(line, f'D1: {FAILED} cooling until 2026-02-01T00:30:00+00:00; relaunch on {BACKUP} options '
                               '{"reasoningEffort": "medium", "serviceTier": "priority"}')
        self.assertEqual(self.cooldown_events()[-1]["replacement"], line.partition("relaunch on ")[2])

    def test_a_review_backups_panel_prints_its_rule(self):
        """The defect: a panel step drops its rule, so the coordinator cannot apply the passes the panel needs."""
        self.cooldown_store()
        self.write_roles_file({"verifiers": [self.named(FAILED)],
                               "review backups": [self.named(BACKUP), self.named(SPARE)]}, {})
        self.assertEqual(self.usage_limit(LIMIT_SPAN, "D1", "verifiers", FAILED, "--author", CLAUDE),
                         f"D1: {FAILED} cooling until 2026-02-01T00:30:00+00:00; panel on {BACKUP}, {SPARE}; rule "
                         '{"maximumReproducedBlockers": 0, "minimumPasses": 2, "waitForAllTerminal": true}')
        self.assertEqual(self.cooldown_events()[-1]["rule"],
                         {"waitForAllTerminal": True, "minimumPasses": 2, "maximumReproducedBlockers": 0})
