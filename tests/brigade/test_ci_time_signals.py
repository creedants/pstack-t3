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
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

from .support import BrigadeTest, StoresTest, setUpModule
from .test_ci_time_rollup import (JOBS_URL, NOTES_URL, RUNS_URL, CiTimeMixin, attempt_job, check, pr)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "t3/added/brigade/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))
from brigade_lib._ci.trigger import _latest


class CiTimeSignalsTest(BrigadeTest, CiTimeMixin):
    def test_gh_timeout_reports_one_line(self):
        from brigade_lib._ci import github
        with mock.patch.object(github.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("gh api x", 60)):
            with self.assertRaises(ValueError) as caught:
                github._run_gh(["api", "x"], self.temporary.name)
        self.assertIn("timed out after 60s", str(caught.exception))

    def test_stop_needs_annotation_and_unfinished_step_on_same_job(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1), (902, 1)],
                                           "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:01:00+00:00")],
                                           "jobs": {901: [attempt_job(11, "failure", url="8011")],
                                                    902: [attempt_job(12, "cancelled", url="8012",
                                                                      steps=({"name": "t", "status": "in_progress"},))]},
                                           "notes": {"8011": [{"message": "The job has exceeded the maximum "
                                                                          "execution time of 3 minutes."}],
                                                     "8012": []}}])
        self.assertIn("PR 801: 60s (about 1.0 minutes), clean", out)
        self.assertNotIn("stop", out)

    def test_two_loss_annotations_on_one_failed_job_count_one_loss(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)],
                                           "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:01:00+00:00")],
                                           "jobs": {901: [attempt_job(11, "failure", url="8011")]},
                                           "notes": {"8011": [{"message": "The runner lost communication."},
                                                              {"message": "Lost communication again."}]}}])
        self.assertIn("1 runner loss", out)

    def test_cancelled_job_with_numeric_steps_prints_one_line(self):
        job = attempt_job(11, "cancelled", url="8011")
        job["steps"] = 1
        self.open()
        before = self.store_files()
        self.stub_gh(self.ci_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": {901: [job]},
                                               "checks": [check("2026-10-30T10:00:00+00:00",
                                                                  "2026-10-30T10:01:00+00:00")],
                                               "notes": {"8011": [{"message": "The job has exceeded the maximum "
                                                                                "execution time of 3 minutes."}]}}]), None)
        self.brigade("set", "--ci-target", "10")
        before = self.store_files()
        out = self.brigade("ci-time")
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith("ci-time:"))
        self.assertEqual(self.store_files(), before)

    def test_wrong_typed_github_lists_fail_the_whole_run(self):
        good = {"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:01:00+00:00")]}
        base = self.ci_mapping([good])
        runs_key = ("api", RUNS_URL.format("sha801", 1))
        jobs_key = ("api", JOBS_URL.format(901, 1))
        notes_key = ("api", NOTES_URL.format(9011, 1))
        job = attempt_job(11, "failure", url="9011")
        failing = dict(good, jobs={901: [job]}, notes={9011: [{"message": "The runner lost communication."}]})
        cases = [
            ("workflow_runs", {**base, runs_key: (0, json.dumps({"workflow_runs": {}}))}),
            ("run", {**base, runs_key: (0, json.dumps({"workflow_runs": [5]}))}),
            ("run_attempt", {**base, runs_key: (0, json.dumps({"workflow_runs": [{"id": 901,
                                                                                 "run_attempt": "1"}]}))}),
            ("jobs", {**self.ci_mapping([failing]), jobs_key: (0, json.dumps({"jobs": {}}))}),
            ("job", {**self.ci_mapping([failing]), jobs_key: (0, json.dumps({"jobs": [5]}))}),
            ("annotations", {**self.ci_mapping([failing]), notes_key: (0, json.dumps({"a": 1}))}),
        ]
        for index, (name, mapping) in enumerate(cases):
            with self.subTest(shape=name):
                tag = f"extra{index}"
                at = self.store / "bridge-kit" / tag
                self.run_at(at, "open", "--project-root", str(self.project), "--name", tag)
                self.stub_gh(mapping, None)
                self.run_at(at, "set", "--ci-target", "10")
                out = self.run_at(at, "ci-time")
                self.assertEqual(len(out.splitlines()), 1, name)
                self.assertTrue(out.startswith("ci-time:"), name)
                self.assertFalse((at / "ci-times.tsv").is_file(), name)

    def test_completion_max_compares_instants_not_strings(self):
        self.assertEqual(_latest(["2026-10-30T17:00:00Z", "2026-10-30T12:00:00-06:00"]), "2026-10-30T12:00:00-06:00")
        from brigade_lib._ci.command import _finished
        rail = [{"id": "T1", "state": "done", "dish": "", "source": "ci-time"}]
        log = [{"at": "2026-10-30T17:00:00Z", "kind": "ticket", "id": "T1", "state": "done"},
               {"at": "2026-10-30T12:00:00-06:00", "kind": "ticket", "id": "T1", "state": "done"}]
        restaurant = types.SimpleNamespace(dir=Path("here"), rows={"rail.tsv": rail, "log.tsv": log}.get)
        self.assertEqual(_finished(restaurant, {"T1"}, {}).finished, {"T1": "2026-10-30T12:00:00-06:00"})

    def test_ci_target_refuses_bool_float_and_text_but_keeps_zero_off(self):
        from brigade_lib._ci.setting import ci_target_of
        from brigade_lib.shared import BrigadeError
        for bad in (False, True, 0.0, 2.5, "5", None, [5]):
            with self.subTest(value=bad):
                with self.assertRaises(BrigadeError):
                    ci_target_of({"ciTarget": bad})
        self.assertEqual(ci_target_of({}), 0)
        self.assertEqual(ci_target_of({"ciTarget": 0}), 0)
        self.assertEqual(ci_target_of({"ciTarget": ""}), 0)
        self.assertEqual(ci_target_of({"ciTarget": 5}), 5)

class CiTimeMovedTest(StoresTest, CiTimeMixin):
    def move_setup(self):
        self.open("core")
        self.open("engine")
        self.stub_gh(self.fixture(), None)
        self.brigade("core", "set", "--ci-target", "5")
        self.assertIn("filed T1", self.brigade("core", "ci-time"))
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertIn("T1", self.brigade("engine", "ticket", "take"))
        self.brigade("engine", "ticket", "set", "T1", "--state", "done")

    def test_finished_move_files_no_second_ticket(self):
        self.move_setup()
        out = self.brigade("core", "ci-time")
        self.assertIn("no breach of the 5m target", out)
        self.assertNotIn("filed T2", out)
        self.assertEqual([row[0] for row in self.rows("core", "rail.tsv")], ["T1"])

    def test_breach_after_finished_move_adds_a_decision_not_a_ticket(self):
        self.move_setup()
        engine = self.dir("engine")
        path = self.dir("core") / "ci-times.tsv"
        header = "PR\tmerged_at\tci_seconds\topen_to_merge_seconds\tclean\tlost\ttime_limit"
        rows = [f"716\t{self.after_done(60, directory=engine)}\t400\t3600\tyes\t0\t0",
                f"717\t{self.after_done(120, directory=engine)}\t410\t3600\tyes\t0\t0"]
        existing = path.read_text().splitlines() if path.is_file() else [header]
        path.write_text("\n".join(existing + rows) + "\n")
        out = self.brigade("core", "ci-time")
        self.assertIn("added Q1", out)
        self.assertEqual([row[0] for row in self.rows("core", "rail.tsv")], ["T1"])
        row = {cells[0]: cells for cells in [line.split("\t") for line in (self.dir("core") / "86.tsv").read_text().splitlines()] if cells}["Q1"]
        keys = (self.dir("core") / "86.tsv").read_text().splitlines()[0].split("\t")
        self.assertEqual(dict(zip(keys, row))["kind"], "ci-time")

    def test_unreadable_finished_log_files_no_second_ticket_and_no_decision(self):
        self.move_setup()
        engine = self.dir("engine")
        path = self.dir("core") / "ci-times.tsv"
        header = "PR\tmerged_at\tci_seconds\topen_to_merge_seconds\tclean\tlost\ttime_limit"
        rows = [f"716\t{self.after_done(60, directory=engine)}\t400\t3600\tyes\t0\t0",
                f"717\t{self.after_done(120, directory=engine)}\t410\t3600\tyes\t0\t0"]
        existing = path.read_text().splitlines() if path.is_file() else [header]
        path.write_text("\n".join(existing + rows) + "\n")
        self.stub_gh(self.ci_mapping([{"pr": pr(718, "sha718", self.after_done(180, directory=engine)),
                                       "runs": [],
                                       "checks": [check("2026-10-30T10:00:00+00:00",
                                                        "2026-10-30T10:05:00+00:00")],
                                       "jobs": {}}]), None)
        target = self.dir("engine") / "log.tsv"
        target.unlink()
        target.mkdir()
        out = self.brigade("core", "ci-time")
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("PR 718", lines[0])
        self.assertEqual(lines[1], "ci-time ticket T1 is live; engine/log.tsv cannot be read; nothing filed")
        self.assertEqual([row[0] for row in self.rows("core", "rail.tsv")], ["T1"])
        self.assertEqual(self.rows("core", "86.tsv"), [])
        self.assertTrue(any(line.startswith("718\t")
                            for line in (self.dir("core") / "ci-times.tsv").read_text().splitlines()))

    def test_unreadable_finished_rail_files_no_second_ticket_and_no_decision(self):
        self.move_setup()
        engine = self.dir("engine")
        path = self.dir("core") / "ci-times.tsv"
        header = "PR\tmerged_at\tci_seconds\topen_to_merge_seconds\tclean\tlost\ttime_limit"
        rows = [f"716\t{self.after_done(60, directory=engine)}\t400\t3600\tyes\t0\t0",
                f"717\t{self.after_done(120, directory=engine)}\t410\t3600\tyes\t0\t0"]
        existing = path.read_text().splitlines() if path.is_file() else [header]
        path.write_text("\n".join(existing + rows) + "\n")
        self.stub_gh(self.ci_mapping([{"pr": pr(718, "sha718", self.after_done(180, directory=engine)),
                                       "runs": [],
                                       "checks": [check("2026-10-30T10:00:00+00:00",
                                                        "2026-10-30T10:05:00+00:00")],
                                       "jobs": {}}]), None)
        target = self.dir("engine") / "rail.tsv"
        target.unlink()
        target.mkdir()
        out = self.brigade("core", "ci-time")
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("PR 718", lines[0])
        self.assertEqual(lines[1], "ci-time ticket T1 is live; engine/rail.tsv cannot be read; nothing filed")
        self.assertEqual([row[0] for row in self.rows("core", "rail.tsv")], ["T1"])
        self.assertEqual(self.rows("core", "86.tsv"), [])
        self.assertTrue(any(line.startswith("718\t")
                            for line in (self.dir("core") / "ci-times.tsv").read_text().splitlines()))
