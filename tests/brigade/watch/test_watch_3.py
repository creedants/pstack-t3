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
from .fixtures import _Helpers

class WatchTest(BrigadeTest, _Helpers):
    def test_trunk_green_adds_no_trunk_line(self):
        self.started()
        self._remote_trunk()
        payload = ('[{"conclusion":"success","status":"completed","headSha":"ddd444",'
                   '"createdAt":"2026-10-02T00:00:00Z","url":"https://example.com/run/3","workflowName":"CI"}]')
        bindir = self._gh_dir(payload=payload)
        from unittest import mock
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            before = self.brigade("watch")
        self.assertNotIn("trunk CI", before)
        greens = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(len(greens), 1)
        self.assertEqual((greens[0]["id"], greens[0]["state"]), ("main", "green"))
    def test_trunk_unseen_reports_cannot_see_via_missing_and_failing_gh(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        bindir = self._gh_dir(with_gh=False)
        with mock.patch.dict(os.environ, {"PATH": str(bindir)}):
            missing = self.brigade("watch")
        self.assertIn("cannot see trunk CI:", missing)
        unseen = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(len(unseen), 1)
        self.assertEqual(unseen[0]["state"], "unseen")
        bindir_fail = self._gh_dir(fail=True)
        with mock.patch.dict(os.environ, {"PATH": str(bindir_fail) + os.pathsep + os.environ.get("PATH", "")}):
            failing = self.brigade("watch")
        self.assertIn("cannot see trunk CI:", failing)
    def test_malformed_non_dict_rows_clear_stored_red(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        bindir = self._gh_dir(payload=self._valid_red_payload())
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since", out)
        for bad in ("[null]", "[42]", '["bad"]'):
            bindir = self._gh_dir(payload=bad)
            with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
                seen = self.brigade("watch")
            self.assertIn("cannot see trunk CI:", seen)
            self.assertEqual(seen.count("cannot see trunk CI:"), 1)
            rows = [row for row in self.log_rows() if row["kind"] == "trunk"]
            self.assertEqual(rows[-1]["state"], "unseen")
            self._assert_no_red_cues()
            bindir = self._gh_dir(payload=self._valid_red_payload())
            with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
                self.brigade("watch")
    def test_malformed_missing_fields_clear_stored_red(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        bindir = self._gh_dir(payload=self._valid_red_payload())
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            self.brigade("watch")
        base = {"status": "completed", "conclusion": "failure", "headSha": "aaa111",
                "createdAt": "2026-10-02T00:00:00Z", "url": "https://example.com/run/2",
                "workflowName": "CI"}
        for key in ("headSha", "createdAt", "url", "workflowName"):
            bad = dict(base)
            del bad[key]
            bindir = self._gh_dir(payload=json.dumps([bad]))
            with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
                seen = self.brigade("watch")
            self.assertIn("cannot see trunk CI:", seen)
            self.assertEqual(seen.count("cannot see trunk CI:"), 1)
            rows = [row for row in self.log_rows() if row["kind"] == "trunk"]
            self.assertEqual(rows[-1]["state"], "unseen")
            self._assert_no_red_cues()
            bindir = self._gh_dir(payload=self._valid_red_payload())
            with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
                self.brigade("watch")
    def test_in_progress_failure_never_reports_red(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        payload = json.dumps([{"status": "in_progress", "conclusion": "failure", "headSha": "aaa111",
                               "createdAt": "2026-10-02T00:00:00Z", "url": "https://example.com/run/2",
                               "workflowName": "CI"}])
        bindir = self._gh_dir(payload=payload)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            seen = self.brigade("watch")
        self.assertIn("cannot see trunk CI:", seen)
        self.assertNotIn("trunk CI failing since", seen)
        rows = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(rows[-1]["state"], "unseen")
    def test_non_default_remote_selects_explicit_repo(self):
        self.started()
        from contextlib import closing
        from pathlib import Path as _Path
        with closing(self.landing_db()) as db, db:
            db.execute("UPDATE contract SET value = ? WHERE key = 'mode'", (json.dumps("merge"),))
            db.execute("UPDATE contract SET value = ? WHERE key = 'trunk'", (json.dumps("release/stable"),))
            db.execute("UPDATE contract SET value = ? WHERE key = 'remote'", (json.dumps("upstream"),))
        subprocess.run(["git", "remote", "add", "upstream", "https://github.com/example/other.git"],
                       cwd=self.project, capture_output=True)
        subprocess.run(["git", "remote", "set-url", "upstream", "https://github.com/example/other.git"],
                       cwd=self.project, capture_output=True)
        payload = json.dumps([{"status": "completed", "conclusion": "failure", "headSha": "aaa111",
                               "createdAt": "2026-10-01T00:00:00Z", "url": "https://example.com/run/2",
                               "workflowName": "CI"},
                              {"status": "completed", "conclusion": "success", "headSha": "ccc333",
                               "createdAt": "2026-09-30T00:00:00Z", "url": "https://example.com/run/0",
                               "workflowName": "CI"}])
        args_file = _Path(self.temporary.name) / "gh-args.txt"
        bindir = self._gh_dir_recording(payload, args_file)
        from unittest import mock
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since 2026-10-01T00:00:00Z: CI https://example.com/run/2", out)
        recorded = args_file.read_text()
        self.assertIn("--repo", recorded)
        self.assertIn("example/other", recorded)
        self.assertIn("--branch", recorded)
        self.assertIn("release/stable", recorded)
        reds = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual((reds[-1]["id"], reds[-1]["state"]), ("release/stable", "red"))
    def test_unsupported_host_prints_cannot_see(self):
        self.started()
        self._remote_trunk()
        subprocess.run(["git", "remote", "set-url", "origin", "https://example.com/repo.git"],
                       cwd=self.project, capture_output=True)
        payload = self._valid_red_payload()
        bindir = self._gh_dir(payload=payload)
        from unittest import mock
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            seen = self.brigade("watch")
        self.assertIn("cannot see trunk CI:", seen)
        rows = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(rows[-1]["state"], "unseen")
    def test_missing_remote_prints_cannot_see(self):
        self.started()
        self._remote_trunk()
        subprocess.run(["git", "remote", "remove", "origin"], cwd=self.project, capture_output=True)
        payload = self._valid_red_payload()
        bindir = self._gh_dir(payload=payload)
        from unittest import mock
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            seen = self.brigade("watch")
        self.assertIn("cannot see trunk CI:", seen)
        rows = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(rows[-1]["state"], "unseen")
    def test_long_failure_streak_reports_streak_start(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        from pathlib import Path as _Path
        rows = []
        for day in range(10, 0, -1):
            rows.append({"status": "completed", "conclusion": "failure", "headSha": f"aaa{day:03d}",
                         "createdAt": f"2026-10-{day:02d}T00:00:00Z",
                         "url": f"https://example.com/run/{day}", "workflowName": "CI"})
        rows.append({"status": "completed", "conclusion": "success", "headSha": "ccc999",
                     "createdAt": "2026-09-30T00:00:00Z", "url": "https://example.com/run/0",
                     "workflowName": "CI"})
        args_file = _Path(self.temporary.name) / "gh-args-long.txt"
        bindir = self._gh_dir_recording(json.dumps(rows), args_file)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since 2026-10-01T00:00:00Z: CI https://example.com/run/10", out)
        reds = [row for row in self.log_rows() if row["kind"] == "trunk"]
        data = json.loads(reds[-1]["note"])
        self.assertEqual(data["since"], "2026-10-01T00:00:00Z")
    def test_in_progress_window_finds_newest_completed(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        from pathlib import Path as _Path
        rows = []
        for number in range(5):
            rows.append({"status": "in_progress", "conclusion": "", "headSha": f"zzz{number:03d}",
                         "createdAt": f"2026-10-0{6+number}T00:00:00Z",
                         "url": f"https://example.com/run/w{number}", "workflowName": "CI"})
        rows.append({"status": "completed", "conclusion": "failure", "headSha": "aaa111",
                     "createdAt": "2026-10-01T00:00:00Z", "url": "https://example.com/run/old",
                     "workflowName": "CI"})
        args_file = _Path(self.temporary.name) / "gh-args-window.txt"
        bindir = self._gh_dir_recording(json.dumps(rows), args_file)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since 2026-10-01T00:00:00Z: CI https://example.com/run/old", out)
        reds = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(reds[-1]["state"], "red")
    def test_truncated_failure_streak_reports_before_oldest_fetched(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        from pathlib import Path as _Path
        head = "1bd3ecf6510a180e34f1391d0910a869aae30f8a"
        base = datetime(2026, 10, 17, tzinfo=timezone.utc)
        rows = []
        for i in range(101):
            stamp = (base - timedelta(hours=i)).isoformat()
            rows.append({"status": "completed", "conclusion": "failure", "headSha": head,
                         "createdAt": stamp, "url": f"https://example.com/run/{i}", "workflowName": "CI"})
        rows.append({"status": "completed", "conclusion": "success", "headSha": "ccc999",
                     "createdAt": (base - timedelta(hours=101)).isoformat(),
                     "url": "https://example.com/run/101", "workflowName": "CI"})
        args_file = _Path(self.temporary.name) / "gh-args-cutoff-streak.txt"
        bindir = self._gh_dir_recording(json.dumps(rows), args_file)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        oldest = (base - timedelta(hours=99)).isoformat()
        self.assertIn(f"trunk CI failing since before {oldest}: CI https://example.com/run/0", out)
        reds = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(reds[-1]["state"], "red")
        data = json.loads(reds[-1]["note"])
        self.assertEqual(data["since"], f"before {oldest}")
        self.assertEqual(data["sha"], head)
        sheet = self.brigade("next", "liveness")
        self.assertIn("Read the failing jobs", sheet)
        self.assertTrue(sheet.splitlines()[-1].startswith("reply: yes"))
    def test_hidden_completed_failure_reports_no_completed_run_in_window(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        from pathlib import Path as _Path
        head = "1bd3ecf6510a180e34f1391d0910a869aae30f8a"
        base = datetime(2026, 10, 17, tzinfo=timezone.utc)
        rows = []
        for i in range(100):
            stamp = (base - timedelta(hours=i)).isoformat()
            rows.append({"status": "in_progress", "conclusion": "", "headSha": f"zzz{i:03d}",
                         "createdAt": stamp, "url": f"https://example.com/run/w{i}", "workflowName": "CI"})
        rows.append({"status": "completed", "conclusion": "failure", "headSha": head,
                     "createdAt": (base - timedelta(hours=100)).isoformat(),
                     "url": "https://example.com/run/100", "workflowName": "CI"})
        args_file = _Path(self.temporary.name) / "gh-args-cutoff-hidden.txt"
        bindir = self._gh_dir_recording(json.dumps(rows), args_file)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("cannot see trunk CI: no completed run in the last 100 runs", out)
        unseen = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(unseen[-1]["state"], "unseen")
        self.assertIn("no completed run in the last 100 runs", unseen[-1]["note"])
        self._assert_no_red_cues()
    def test_stored_red_owes_next_liveness_and_merge(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        bindir = self._gh_dir(payload=self._valid_red_payload())
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since", out)
        for wake in (("liveness",), ("merge", "D1")):
            sheet = self.brigade("next", *wake)
            self.assertIn("Read the failing jobs", sheet)
            self.assertIn("urgent fix", sheet)
            self.assertIn("Tell the owner", sheet)
            self.assertTrue(sheet.splitlines()[-1].startswith("reply: yes"))
    def test_stored_green_owes_no_next_red_step(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        payload = json.dumps([{"status": "completed", "conclusion": "success", "headSha": "ddd444",
                               "createdAt": "2026-10-02T00:00:00Z", "url": "https://example.com/run/3",
                               "workflowName": "CI"}])
        bindir = self._gh_dir(payload=payload)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertNotIn("trunk CI", out)
        self._assert_no_red_cues()
