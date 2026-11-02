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
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from .support import BrigadeTest, setUpModule

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "t3/added/brigade/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))
from brigade_lib._ci.trigger import (ASKING, ARMED, COOLING, RETRYING, STOPPED, TICKET_LIVE,
                                      CiRun, breach, phase_of)


def stamp(days=0, hours=0, clock="21:42:03"):
    """An ISO stamp this many days and hours ago at this wall clock time, in UTC."""
    day = (datetime.now(timezone.utc) - timedelta(days=days, hours=hours)).date()
    return f"{day}T{clock}+00:00"


def ahead(minutes):
    """An ISO stamp this many minutes after now, in UTC, for seeded ledger rows.

    Breach counts only runs merged after the last finished trigger ticket, so a
    seeded row must land after the run that files and finishes that ticket.
    """
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


class CiTimeMixin:
    def stub_gh(self, mapping, prs):
        """A fake gh earlier on PATH answering these argv tuples with (code, stdout)."""
        bindir = Path(self.temporary.name) / f"fakebin-ci-{time.time_ns()}"
        bindir.mkdir()
        encoded = {json.dumps(list(key)): value for key, value in mapping.items()}
        script = bindir / "gh"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"payload = {json.dumps(encoded)}\n"
            "key = json.dumps(sys.argv[1:])\n"
            "if key in payload:\n"
            "    code, out = payload[key]\n"
            "    if out:\n"
            "        print(out)\n"
            "    sys.exit(code)\n"
            "print('unexpected gh call: ' + ' '.join(sys.argv[1:]), file=sys.stderr)\n"
            "sys.exit(1)\n"
        )
        script.chmod(0o755)
        patcher = mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]})
        patcher.start()
        self.addCleanup(patcher.stop)
        return bindir

    def fixture(self):
        """The canned gh answers for PRs 712 through 715, and the pr list they share."""
        d2, d1 = 2, 1
        prs = [
            {"number": 712, "mergedAt": stamp(d2, 0, "10:00:00"), "createdAt": stamp(d2, 0, "09:00:00"),
             "headRefOid": "sha712"},
            {"number": 713, "mergedAt": stamp(d2, 0, "21:42:03"), "createdAt": stamp(d2, 0, "20:34:55"),
             "headRefOid": "sha713"},
            {"number": 714, "mergedAt": stamp(d1, 0, "10:00:00"), "createdAt": stamp(d1, 0, "09:00:00"),
             "headRefOid": "sha714"},
            {"number": 715, "mergedAt": stamp(d1, 0, "11:00:00"), "createdAt": stamp(d1, 0, "10:00:00"),
             "headRefOid": "sha715"},
        ]
        mapping = {
            ("repo", "view", "--json", "nameWithOwner"): (0, json.dumps({"nameWithOwner": "o/r"})),
            ("pr", "list", "--state", "merged", "--limit", "10",
             "--json", "number,mergedAt,createdAt,headRefOid"): (0, json.dumps(prs)),
            ("api", "repos/o/r/actions/runs?head_sha=sha712&per_page=5"):
                (0, json.dumps({"workflow_runs": [{"id": 701, "run_attempt": 1},
                                                  {"id": 702, "run_attempt": 2}]})),
            ("api", "repos/o/r/actions/runs?head_sha=sha713&per_page=5"):
                (0, json.dumps({"workflow_runs": [{"id": 703, "run_attempt": 1}]})),
            ("api", "repos/o/r/actions/runs?head_sha=sha714&per_page=5"):
                (0, json.dumps({"workflow_runs": [{"id": 704, "run_attempt": 1}]})),
            ("api", "repos/o/r/actions/runs?head_sha=sha715&per_page=5"):
                (0, json.dumps({"workflow_runs": [{"id": 705, "run_attempt": 1}]})),
            ("api", "repos/o/r/actions/runs/701/attempts/1/jobs"):
                (0, json.dumps({"jobs": [{"id": 11, "status": "completed", "conclusion": "failure",
                                          "started_at": stamp(d2, 0, "09:34:55"),
                                          "completed_at": stamp(d2, 0, "09:42:03"), "steps": [],
                                          "check_run_url": "https://api.github.com/repos/o/r/check-runs/7011"}]})),
            ("api", "repos/o/r/actions/runs/702/attempts/1/jobs"):
                (0, json.dumps({"jobs": [{"id": 12, "status": "completed", "conclusion": "success",
                                          "started_at": stamp(d2, 0, "09:44:00"),
                                          "completed_at": stamp(d2, 0, "09:50:00"), "steps": []}]})),
            ("api", "repos/o/r/actions/runs/703/attempts/1/jobs"):
                (0, json.dumps({"jobs": [{"id": 13, "status": "completed", "conclusion": "success",
                                          "started_at": stamp(d2, 0, "21:34:55"),
                                          "completed_at": stamp(d2, 0, "21:42:03"), "steps": []}]})),
            ("api", "repos/o/r/actions/runs/704/attempts/1/jobs"):
                (0, json.dumps({"jobs": [{"id": 14, "status": "completed", "conclusion": "cancelled",
                                          "started_at": stamp(d1, 0, "09:30:00"),
                                          "completed_at": stamp(d1, 0, "09:50:00"),
                                          "check_run_url": "https://api.github.com/repos/o/r/check-runs/7041",
                                          "steps": [{"name": "test", "status": "cancelled"}]}]})),
            ("api", "repos/o/r/actions/runs/705/attempts/1/jobs"):
                (0, json.dumps({"jobs": [{"id": 15, "status": "completed", "conclusion": "success",
                                          "started_at": stamp(d1, 0, "10:30:00"),
                                          "completed_at": stamp(d1, 0, "10:36:30"), "steps": []}]})),

            ("api", "repos/o/r/check-runs/7011/annotations"):
                (0, json.dumps([{"message": "The runner lost communication with the server."}])),
            ("api", "repos/o/r/check-runs/7041/annotations"):
                (0, json.dumps([{"message": "The job has exceeded the maximum execution time of 10 minutes."}])),
        }
        return mapping

    def ready(self, target="5"):
        self.open()
        self.stub_gh(self.fixture(), None)
        self.brigade("set", "--ci-target", target)

    def ledger_lines(self):
        return (self.at / "ci-times.tsv").read_text().splitlines()

    def seed_runs(self, *rows):
        """Append these raw ci-times.tsv rows, writing the header when the file is missing."""
        path = self.at / "ci-times.tsv"
        header = "PR\tmerged_at\tci_seconds\topen_to_merge_seconds\tclean\tlost\ttime_limit"
        existing = path.read_text().splitlines() if path.is_file() else [header]
        path.write_text("\n".join(existing + list(rows)) + "\n")

    def test_off_makes_no_gh_call_writes_no_file_and_leaves_close_and_status_alone(self):
        self.open()
        before = self.store_files()
        out = self.brigade("ci-time")
        self.assertEqual(out, "ci-time is off; run set --ci-target <minutes> to turn it on")
        self.assertEqual(self.store_files(), before)
        self.assertNotIn("## CI time", self.brigade("close", "--dry-run"))
        self.assertNotIn("ci-time", self.brigade("status"))

    def test_dry_run_writes_nothing_but_prints_each_run(self):
        self.ready()
        before = self.store_files()
        out = self.brigade("ci-time", "--dry-run")
        self.assertEqual(self.store_files(), before)
        self.assertIn("PR 712: 905s (about 15.1 minutes), not clean, 1 runner loss", out)
        self.assertIn("PR 713: 428s (about 7.1 minutes), clean", out)
        self.assertIn("PR 714: 1200s (about 20.0 minutes), clean, 1 time-limit stop", out)
        self.assertIn("PR 715: 390s (about 6.5 minutes), clean", out)

    def test_apply_appends_runs_and_a_second_apply_dedupes_by_pr(self):
        self.ready()
        first = self.brigade("ci-time")
        self.assertIn("PR 713: 428s (about 7.1 minutes), clean", first)
        rows = self.ledger_lines()
        self.assertEqual(rows[0], "PR\tmerged_at\tci_seconds\topen_to_merge_seconds\tclean\tlost\ttime_limit")
        by_pr = {line.split("\t")[0]: line.split("\t") for line in rows[1:]}
        self.assertEqual(by_pr["713"][2], "428")
        self.assertEqual(by_pr["712"][5], "1")
        self.assertEqual(by_pr["714"][6], "1")
        second = self.brigade("ci-time")
        self.assertEqual(self.ledger_lines(), rows)
        self.assertIn("ci-time ticket T1 is live; nothing filed", second)

    def test_gh_failure_prints_one_line_and_writes_nothing(self):
        self.open()
        self.brigade("set", "--ci-target", "10")
        self.stub_gh({("repo", "view", "--json", "nameWithOwner"): (0, json.dumps({"nameWithOwner": "o/r"})),
                      ("pr", "list", "--state", "merged", "--limit", "10",
                       "--json", "number,mergedAt,createdAt,headRefOid"): (1, "")}, None)
        before = self.store_files()
        out = self.brigade("ci-time")
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith("ci-time:"))
        self.assertEqual(self.store_files(), before)

    def test_breach_files_an_upkeep_ticket_naming_both_runs_and_the_target(self):
        self.ready()
        out = self.brigade("ci-time")
        self.assertIn("filed T1: CI time 390s (PR 715) and 1200s (PR 714) is over the 5m target", out)
        row = self.table_row(self.at, "rail.tsv", "T1")
        self.assertEqual((row["kind"], row["paths"], row["source"], row["ref"], row["priority"]),
                         ("upkeep", ".github/workflows", "ci-time", "ci-time", "normal"))
        self.assertIn("PR 715", row["summary"])
        self.assertIn("PR 714", row["summary"])
        self.assertIn("5m target", row["summary"])
        self.assertIn("T1", self.brigade("startable"))

    def test_a_live_trigger_ticket_blocks_a_second_filing(self):
        self.ready()
        self.brigade("ci-time")
        out = self.brigade("ci-time")
        self.assertIn("ci-time ticket T1 is live; nothing filed", out)
        self.assertEqual([fields[0] for fields in self.rows_of("rail.tsv")], ["T1"])

    def test_a_breach_after_a_finished_trigger_adds_a_marked_decision_not_a_ticket(self):
        self.ready()
        self.brigade("ci-time")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.seed_runs(f"716\t{ahead(60)}\t400\t3600\tyes\t0\t0",
                       f"717\t{ahead(120)}\t410\t3600\tyes\t0\t0")
        out = self.brigade("ci-time")
        self.assertIn("added Q1: CI time is still over the 5m target (410s at PR 717, 400s at PR 716).", out)
        self.assertEqual([fields[0] for fields in self.rows_of("rail.tsv")], ["T1"])
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["dish"], row["kind"], row["options"], row["default"]),
                         ("", "ci-time", "try again, leave it", "leave it"))

    def test_an_open_ci_time_decision_blocks_filing_while_other_work_continues(self):
        self.ready()
        self.brigade("ci-time")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.seed_runs(f"716\t{ahead(60)}\t400\t3600\tyes\t0\t0",
                       f"717\t{ahead(120)}\t410\t3600\tyes\t0\t0")
        self.brigade("ci-time")
        out = self.brigade("ci-time")
        self.assertIn("ci-time decision Q1 is open; nothing filed", out)
        sheet = self.brigade("next", "evening")
        self.assertIn("Q1", sheet)
        self.assertIn("Other work continues while it is open.", sheet)
        self.assertNotIn("Launch nothing until 86 answer Q1", sheet)

    def test_try_again_rearms_filing_after_a_decision(self):
        self.ready()
        self.brigade("ci-time")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.seed_runs(f"716\t{ahead(60)}\t400\t3600\tyes\t0\t0",
                       f"717\t{ahead(120)}\t410\t3600\tyes\t0\t0")
        self.brigade("ci-time")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "try again"), "Q1 answered")
        out = self.brigade("ci-time")
        self.assertIn("filed T2: CI time 410s (PR 717) and 400s (PR 716) is over the 5m target", out)

    def test_leave_it_stops_filing_until_the_target_is_set_again(self):
        self.ready()
        self.brigade("ci-time")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.seed_runs(f"716\t{ahead(60)}\t400\t3600\tyes\t0\t0",
                       f"717\t{ahead(120)}\t410\t3600\tyes\t0\t0")
        self.brigade("ci-time")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "leave it"), "Q1 answered")
        self.seed_runs(f"718\t{ahead(180)}\t420\t3600\tyes\t0\t0")
        stopped = self.brigade("ci-time")
        self.assertIn("ci-time is stopped; run set --ci-target <minutes> to start it again", stopped)
        self.assertEqual([fields[0] for fields in self.rows_of("rail.tsv")], ["T1"])
        self.brigade("set", "--ci-target", "5")
        rearmed = self.brigade("ci-time")
        self.assertIn("filed T2:", rearmed)

    def test_86_answer_refuses_a_non_option_for_a_ci_time_decision(self):
        self.ready()
        self.brigade("ci-time")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.seed_runs(f"716\t{ahead(60)}\t400\t3600\tyes\t0\t0",
                       f"717\t{ahead(120)}\t410\t3600\tyes\t0\t0")
        self.brigade("ci-time")
        out = self.brigade("86", "answer", "Q1", "--answer", "bogus")
        self.assertEqual(out, "Q1 still open; answer with one of try again, leave it")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q1")["state"], "open")

    def test_close_reports_ci_time_after_the_waiting_list_and_hides_it_when_off(self):
        self.ready()
        self.brigade("ci-time")
        path = self.at / "ci-times.tsv"
        path.write_text(path.read_text() + "bad line here\n")
        out = self.brigade("close", "--dry-run")
        self.assertLess(out.index("## Waiting list"), out.index("## CI time"))
        self.assertIn("- PR 713: 428s (7.1 min), clean", out)
        self.assertIn("median 428s, longest 1200s, 3 over the 5m target in the last 3 clean runs", out)
        self.assertIn("1 runner loss and 1 time-limit stop in the last 7 days", out)
        self.assertIn("ci-times.tsv line 6 is not a row; kept as is", out)
        self.assertIn("no ticket was created in the last 14 days", out)
        self.brigade("set", "--ci-target", "0")
        self.assertNotIn("## CI time", self.brigade("close", "--dry-run"))

    def test_a_non_utf8_byte_in_the_ledger_is_a_kept_bad_line_not_a_crash(self):
        self.ready()
        self.brigade("ci-time")
        path = self.at / "ci-times.tsv"
        path.write_bytes(path.read_bytes() + b"bad \xff line\n")
        out = self.brigade("close", "--dry-run")
        self.assertIn("## CI time", out)
        self.assertIn("ci-times.tsv line 6 is not a row; kept as is", out)
        again = self.brigade("ci-time")
        self.assertIn("ci-time ticket T1 is live; nothing filed", again)

    def test_follow_up_share_skips_ci_time_tickets(self):
        self.ready()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a user asks for work")
        self.brigade("ci-time")
        out = self.brigade("close", "--dry-run")
        self.assertIn("- 0 of 1 tickets created in the last 14 days came from a report (0%).", out)

    def test_evening_and_merge_sheets_run_ci_time_only_when_set(self):
        self.open()
        self.assertNotIn("run ci-time", self.brigade("next", "evening"))
        self.brigade("set", "--ci-target", "10")
        self.assertIn("run ci-time", self.brigade("next", "evening"))
        self.fired_bug_fix()
        self.brigade("set", "--ci-target", "10")
        self.assertIn("run ci-time", self.brigade("next", "merge", "D1"))

    def test_ci_target_refuses_a_negative_a_word_and_a_bad_stored_value(self):
        self.open()
        error = self.brigade("set", "--ci-target", "-1", ok=False)
        self.assertIn("--ci-target takes a whole number of 1 or more", error)
        error = self.brigade("set", "--ci-target", "soon", ok=False)
        self.assertIn("--ci-target takes a whole number of 1 or more", error)
        meta_path = self.at / "restaurant.json"
        meta = json.loads(meta_path.read_text())
        meta["ciTarget"] = True
        meta_path.write_text(json.dumps(meta))
        error = self.brigade("close", "--dry-run", ok=False)
        self.assertIn("restaurant.json ciTarget True is invalid", error)
        meta["ciTarget"] = float("nan")
        meta_path.write_text(json.dumps(meta).replace("NaN", "NaN"))
        error = self.brigade("close", "--dry-run", ok=False)
        self.assertIn("restaurant.json ciTarget nan is invalid", error)

    def test_breach_needs_two_clean_runs_over_the_target_after_since(self):
        def run(pr, seconds, clean=True, at="2026-10-30T10:00:00+00:00"):
            return CiRun(pr, datetime.fromisoformat(at), seconds, 3600, clean, 0, 0)
        old = run(710, 900, at="2026-10-29T10:00:00+00:00")
        first = run(713, 428, at="2026-10-30T21:42:03+00:00")
        second = run(715, 390, at="2026-10-31T11:00:00+00:00")
        self.assertIsNone(breach((first,), 5, ""))
        self.assertIsNone(breach((run(716, 200), second), 5, ""))
        self.assertIsNone(breach((first, run(717, 100)), 5, ""))
        found = breach((second, first), 5, "")
        self.assertEqual((found.older.pr, found.newer.pr), (713, 715))
        self.assertIsNone(breach((old, first, second), 5, "2026-10-30T21:42:03Z"))
        found = breach((old, first, second), 5, "2026-10-29T10:00:00+00:00")
        self.assertEqual((found.older.pr, found.newer.pr), (713, 715))
        same_instant = run(714, 428, at="2026-10-31T00:42:03+03:00")
        self.assertIsNone(breach((same_instant, second), 5, "2026-10-30T21:42:03Z"))

    def test_phase_moves_through_armed_live_cooling_asking_retrying_and_stopped(self):
        ticket = {"id": "T1", "source": "ci-time"}
        other = {"id": "T2", "source": "user"}
        asked = {"id": "Q1", "state": "open", "dish": "", "kind": "ci-time"}
        self.assertEqual(phase_of([], [], {}, "", set()), ARMED)
        self.assertEqual(phase_of([ticket], [], {}, "", {"T1"}), TICKET_LIVE)
        self.assertEqual(phase_of([ticket], [asked], {}, "", set()), ASKING)
        finished = {"T1": "2026-10-29T10:00:00+00:00"}
        self.assertEqual(phase_of([ticket], [], finished, "", set()), COOLING)
        tried = {"id": "Q1", "state": "answered", "dish": "", "kind": "ci-time",
                 "answer": "try again", "answered": "2026-10-30T10:00:00+00:00"}
        self.assertEqual(phase_of([ticket], [tried], finished, "", set()), RETRYING)
        left = dict(tried, answer="leave it")
        self.assertEqual(phase_of([ticket], [left], finished, "", set()), STOPPED)
        self.assertEqual(phase_of([ticket], [left], finished, "2026-10-31T10:00:00+00:00", set()), ARMED)
        self.assertEqual(phase_of([other], [], {}, "", set()), ARMED)


class CiTimeTest(BrigadeTest, CiTimeMixin):
    pass
