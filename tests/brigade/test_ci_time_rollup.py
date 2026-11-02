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

RUNS_URL = "repos/o/r/actions/runs?head_sha={}&per_page=5&page={}"
JOBS_URL = "repos/o/r/actions/runs/{}/attempts/1/jobs?per_page=100&page={}"
CHECKS_URL = "repos/o/r/commits/{}/check-runs?per_page=100&page={}"
NOTES_URL = "repos/o/r/check-runs/{}/annotations?per_page=100&page={}"


def stamp(days=0, hours=0, clock="21:42:03"):
    day = (datetime.now(timezone.utc) - timedelta(days=days, hours=hours)).date()
    return f"{day}T{clock}+00:00"


def pr(number, head, merged="2026-10-30T10:00:00+00:00"):
    return {"number": number, "mergedAt": merged, "createdAt": "2026-10-30T09:00:00+00:00",
            "headRefOid": head}


def check(start, end, status="completed", conclusion="success"):
    row = {"name": "build", "status": status, "conclusion": conclusion}
    if start is not None:
        row["started_at"] = start
    if end is not None:
        row["completed_at"] = end
    return row


def attempt_job(ident, conclusion, url="", steps=()):
    row = {"id": ident, "status": "completed", "conclusion": conclusion, "steps": list(steps)}
    if url:
        row["check_run_url"] = f"https://api.github.com/repos/o/r/check-runs/{url}"
    return row


class CiTimeMixin:
    def after_done(self, minutes, ident="T1", directory=None):
        rows = ((directory or self.at) / "log.tsv").read_text().splitlines()[1:]
        done = max(line.split("\t")[0] for line in rows for cells in [line.split("\t")]
                   if cells[1] == "ticket" and cells[2] == ident and cells[3] in ("done", "dropped"))
        return (datetime.fromisoformat(done.replace("Z", "+00:00")) + timedelta(minutes=minutes)).isoformat()

    def stub_gh(self, mapping, prs):
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
            ("api", "repos/o/r/actions/runs/701/attempts/1/jobs?per_page=100&page=1"):
                (0, json.dumps({"jobs": [{"id": 11, "status": "completed", "conclusion": "failure",
                                          "started_at": stamp(d2, 0, "09:34:55"),
                                          "completed_at": stamp(d2, 0, "09:42:03"), "steps": [],
                                          "check_run_url": "https://api.github.com/repos/o/r/check-runs/7011"}]})),
            ("api", "repos/o/r/actions/runs/702/attempts/1/jobs?per_page=100&page=1"):
                (0, json.dumps({"jobs": [{"id": 12, "status": "completed", "conclusion": "success",
                                          "started_at": stamp(d2, 0, "09:44:00"),
                                          "completed_at": stamp(d2, 0, "09:50:00"), "steps": []}]})),
            ("api", "repos/o/r/actions/runs/703/attempts/1/jobs?per_page=100&page=1"):
                (0, json.dumps({"jobs": [{"id": 13, "status": "completed", "conclusion": "success",
                                          "started_at": stamp(d2, 0, "21:34:55"),
                                          "completed_at": stamp(d2, 0, "21:42:03"), "steps": []}]})),
            ("api", "repos/o/r/actions/runs/704/attempts/1/jobs?per_page=100&page=1"):
                (0, json.dumps({"jobs": [{"id": 14, "status": "completed", "conclusion": "cancelled",
                                          "started_at": stamp(d1, 0, "09:30:00"),
                                          "completed_at": stamp(d1, 0, "09:50:00"),
                                          "check_run_url": "https://api.github.com/repos/o/r/check-runs/7041",
                                          "steps": [{"name": "checks", "status": "cancelled"}]}]})),
            ("api", "repos/o/r/actions/runs/705/attempts/1/jobs?per_page=100&page=1"):
                (0, json.dumps({"jobs": [{"id": 15, "status": "completed", "conclusion": "success",
                                          "started_at": stamp(d1, 0, "10:30:00"),
                                          "completed_at": stamp(d1, 0, "10:36:30"), "steps": []}]})),
            ("api", "repos/o/r/check-runs/7011/annotations?per_page=100&page=1"):
                (0, json.dumps([{"message": "The runner lost communication with the server."}])),
            ("api", "repos/o/r/check-runs/7041/annotations?per_page=100&page=1"):
                (0, json.dumps([{"message": "The job has exceeded the maximum execution time of 10 minutes."}])),
        }
        for number, ids in ((712, (701, 702)), (713, (703,)), (714, (704,)), (715, (705,))):
            mapping[("api", RUNS_URL.format(f"sha{number}", 1))] = (
                0, json.dumps({"workflow_runs": [{"id": i, "run_attempt": 2 if i == 702 else 1} for i in ids]}))
        spans = {"sha712": (d2, (("09:34:55", "09:42:03", "failure"), ("09:44:00", "09:50:00", "success"))),
                 "sha713": (d2, (("21:34:55", "21:42:03", "success"),)),
                 "sha714": (d1, (("09:30:00", "09:50:00", "cancelled"),)),
                 "sha715": (d1, (("10:30:00", "10:36:30", "success"),))}
        for sha, (day, rows) in spans.items():
            mapping[("api", CHECKS_URL.format(sha, 1))] = (
                0, json.dumps({"check_runs": [{"status": "completed", "conclusion": done,
                                              "started_at": stamp(day, 0, start),
                                              "completed_at": stamp(day, 0, end)} for start, end, done in rows]}))
        return mapping

    def ready(self, target="5"):
        self.open()
        self.stub_gh(self.fixture(), None)
        self.brigade("set", "--ci-target", target)

    def ledger_lines(self):
        return (self.at / "ci-times.tsv").read_text().splitlines()

    def seed_runs(self, *rows):
        path = self.at / "ci-times.tsv"
        header = "PR\tmerged_at\tci_seconds\topen_to_merge_seconds\tclean\tlost\ttime_limit"
        existing = path.read_text().splitlines() if path.is_file() else [header]
        path.write_text("\n".join(existing + list(rows)) + "\n")

    def ci_mapping(self, prs):
        mapping = {
            ("repo", "view", "--json", "nameWithOwner"): (0, json.dumps({"nameWithOwner": "o/r"})),
            ("pr", "list", "--state", "merged", "--limit", "10",
             "--json", "number,mergedAt,createdAt,headRefOid"):
                (0, json.dumps([spec["pr"] for spec in prs])),
        }
        for spec in prs:
            head = spec["pr"]["headRefOid"]
            for page, batch in enumerate(spec.get("run_pages", [spec.get("runs", [])]), 1):
                mapping[("api", RUNS_URL.format(head, page))] = (
                    0, json.dumps({"workflow_runs": [{"id": i, "run_attempt": a} for i, a in batch]}))
            for page, batch in enumerate(spec.get("check_pages", [spec.get("checks", [])]), 1):
                mapping[("api", CHECKS_URL.format(head, page))] = (0, json.dumps({"check_runs": batch}))
            jobs = spec.get("jobs", {})
            if jobs == "success":
                jobs = {i: [attempt_job(i, "success")] for page in spec.get("run_pages", [spec.get("runs", [])])
                        for i, _ in page}
            for run, batch in jobs.items():
                mapping[("api", JOBS_URL.format(run, 1))] = (0, json.dumps({"jobs": batch}))
            for run, pages in spec.get("job_pages", {}).items():
                for page, batch in enumerate(pages, 1):
                    mapping[("api", JOBS_URL.format(run, page))] = (0, json.dumps({"jobs": batch}))
            for ident, batch in spec.get("notes", {}).items():
                mapping[("api", NOTES_URL.format(ident, 1))] = (0, json.dumps(batch))
            for ident, pages in spec.get("note_pages", {}).items():
                for page, batch in enumerate(pages, 1):
                    mapping[("api", NOTES_URL.format(ident, page))] = (0, json.dumps(batch))
            if spec.get("fail_runs"):
                mapping[("api", RUNS_URL.format(head, 1))] = (1, "")
        return mapping

    def ready_mapping(self, prs, target="5"):
        self.open()
        self.stub_gh(self.ci_mapping(prs), None)
        self.brigade("set", "--ci-target", target)
        return self.brigade("ci-time")


class CiTimeRollupTest(BrigadeTest, CiTimeMixin):
    def test_rollup_span_beats_attempt1_job_sum(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)],
                                           "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:10:00+00:00")],
                                           "jobs": {901: [{"id": 1, "status": "completed", "conclusion": "success",
                                                           "steps": [],
                                                           "started_at": "2026-10-30T10:09:00+00:00",
                                                           "completed_at": "2026-10-30T10:10:00+00:00"}]}}])
        self.assertIn("PR 801: 600s (about 10.0 minutes), clean", out)

    def test_second_page_run_attempt_two_marks_pr_not_clean(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"),
                                           "run_pages": [[(901, 1), (902, 1), (903, 1), (904, 1), (905, 1)], [(906, 2)]],
                                           "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:01:00+00:00")],
                                           "jobs": "success"}])
        self.assertIn("PR 801: 60s (about 1.0 minutes), not clean", out)

    def test_running_check_defers_pr_with_no_row(self):
        spec = {"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:01:00+00:00"),
                           check("2026-10-30T10:01:00+00:00", None, status="in_progress", conclusion=None)]}
        out = self.ready_mapping([spec])
        self.assertIn("PR 801: check build is still running; deferred to the next run", out)
        self.assertFalse((self.at / "ci-times.tsv").is_file())
        spec["checks"] = [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:01:00+00:00")]
        self.stub_gh(self.ci_mapping([spec]), None)
        again = self.brigade("ci-time")
        self.assertIn("PR 801: 60s (about 1.0 minutes), clean", again)

    def test_year_one_completion_defers_pr(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                                           "checks": [check("2026-10-30T10:00:00+00:00", "0001-01-01T00:00:00Z")]}])
        self.assertIn("deferred to the next run", out)
        self.assertFalse((self.at / "ci-times.tsv").is_file())

    def test_completion_before_start_skips_pr(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                                           "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T09:00:00+00:00")]}])
        self.assertIn("PR 801: no usable check time; no row written", out)
        self.assertFalse((self.at / "ci-times.tsv").is_file())

    def test_year_one_start_skips_pr(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                                           "checks": [check("0001-01-01T00:00:00Z", "2026-10-30T10:00:00+00:00")]}])
        self.assertIn("PR 801: no usable check time; no row written", out)
        self.assertFalse((self.at / "ci-times.tsv").is_file())

    def test_pr_without_runs_records_rollup_as_clean(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [],
                                           "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:05:00+00:00")],
                                           "jobs": {}}])
        self.assertIn("PR 801: 300s (about 5.0 minutes), clean", out)

    def test_pr_without_usable_time_writes_no_row(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                                           "checks": [check(None, None, conclusion="skipped")]}])
        self.assertIn("PR 801: no usable check time; no row written", out)
        self.assertFalse((self.at / "ci-times.tsv").is_file())

    def test_skipped_check_ending_before_a_late_start_does_not_stretch_the_span(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                                           "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:05:00+00:00"),
                                                      check("2026-10-30T11:00:00+00:00", "2026-10-30T10:59:59+00:00",
                                                            conclusion="skipped")]}])
        self.assertIn("PR 801: 300s (about 5.0 minutes), clean", out)

    def test_skipped_check_with_equal_times_after_the_real_check_does_not_stretch_the_span(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                                           "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:05:00+00:00"),
                                                      check("2026-10-30T11:00:00+00:00", "2026-10-30T11:00:00+00:00",
                                                            conclusion="skipped")]}])
        self.assertIn("PR 801: 300s (about 5.0 minutes), clean", out)

    def test_skipped_check_before_the_real_check_does_not_shrink_the_span(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                                           "checks": [check("2026-10-30T09:00:00+00:00", "2026-10-30T09:00:00+00:00",
                                                            conclusion="skipped"),
                                                      check("2026-10-30T10:00:00+00:00",
                                                            "2026-10-30T10:05:00+00:00")]}])
        self.assertIn("PR 801: 300s (about 5.0 minutes), clean", out)

    def test_completed_check_without_a_start_leaves_the_pr_unrecorded(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                                           "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:05:00+00:00"),
                                                      check(None, "2026-10-30T10:20:00+00:00")]}])
        self.assertIn("PR 801: no usable check time; no row written", out)
        self.assertFalse((self.at / "ci-times.tsv").is_file())

    def test_completed_check_without_a_completion_leaves_the_pr_unrecorded(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                                           "checks": [check("2026-10-30T10:00:00+00:00", "2026-10-30T10:05:00+00:00"),
                                                      check("2026-10-30T10:00:00+00:00", None)]}])
        self.assertIn("PR 801: no usable check time; no row written", out)
        self.assertFalse((self.at / "ci-times.tsv").is_file())

    def test_pr_with_no_checks_files_no_ticket_and_no_decision(self):
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success", "checks": []}])
        self.assertIn("PR 801: no usable check time; no row written", out)
        self.assertIn("no breach of the 5m target", out)
        self.assertNotIn("filed T", out)
        self.assertNotIn("added Q", out)

    def test_mid_fetch_failure_writes_nothing(self):
        self.open()
        self.stub_gh(self.ci_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)], "jobs": "success",
                                               "checks": [check("2026-10-30T10:00:00+00:00",
                                                                  "2026-10-30T10:01:00+00:00")]},
                                              {"pr": pr(802, "sha802"), "fail_runs": True}]), None)
        self.brigade("set", "--ci-target", "10")
        before = self.store_files()
        out = self.brigade("ci-time")
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith("ci-time:"))
        self.assertEqual(self.store_files(), before)

    def test_unparseable_output_writes_nothing(self):
        self.open()
        before = self.store_files()
        self.stub_gh({("repo", "view", "--json", "nameWithOwner"): (0, json.dumps({"nameWithOwner": "o/r"})),
                      ("pr", "list", "--state", "merged", "--limit", "10",
                       "--json", "number,mergedAt,createdAt,headRefOid"): (0, "not json")}, None)
        self.brigade("set", "--ci-target", "10")
        before = self.store_files()
        out = self.brigade("ci-time")
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith("ci-time:"))
        self.assertEqual(self.store_files(), before)

    def test_rollup_pages_until_a_short_page(self):
        base = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)
        pages = []
        for page in range(3):
            batch = []
            for i in range(100 if page < 2 else 50):
                start = base + timedelta(seconds=60 * (page * 100 + i))
                batch.append(check(start.isoformat(), (start + timedelta(seconds=60)).isoformat()))
            pages.append(batch)
        out = self.ready_mapping([{"pr": pr(801, "sha801"), "runs": [(901, 1)],
                                           "check_pages": pages, "jobs": "success"}])
        self.assertIn("PR 801: 15000s (about 250.0 minutes), clean", out)
