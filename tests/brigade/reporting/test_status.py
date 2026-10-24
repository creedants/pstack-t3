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
from ..support import BrigadeTest, CLAUDE, CODEX, Path, ProcessReason, SCRIPT, call, call_in_process, real_process, run_globals, _ReportingRace, _owner_words, _shown_root, datetime, io, json, mock, os, runpy, subprocess, sys, timezone
import shlex
import shutil
import sqlite3

class ReportingTest(BrigadeTest):
    def test_status_without_a_contract_ignores_a_stored_landing_field(self):
        self.open()
        self.assertNotIn("landing", json.loads((self.at / "restaurant.json").read_text()))
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)")
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["landing"] = "merge"
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)")
        walked = self.brigade("walk")
        self.assertEqual(walked.splitlines()[0], f"{_shown_root(self.project)}: no landing contract")
        self.assertNotIn("lands by", walked)
    def test_walk_prints_a_home_directory_as_a_tilde(self):
        from unittest import mock
        project = Path.home() / "brigade-no-such-contract-d57"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            directory = self.store / "brigade-no-such-contract-d57" / "docs"
            self.assertEqual(self.brigade("open", "--project-root", str(project), "--name", "Docs"), f"opened {directory}")
            self.assertEqual(self.brigade("walk"), "\n".join([
                "~/brigade-no-such-contract-d57: no landing contract",
                "  Docs (reports milestones): nothing on record",
                "    thread not recorded",
            ]))
    def test_status_and_walk_follow_a_landing_mode_change(self):
        from unittest import mock
        self.git_project()
        remote = Path(self.temporary.name) / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=self.project, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=self.project, check=True)
        gh = Path(self.temporary.name) / "gh"
        gh.write_text("#!/bin/sh\nexit 0\n")
        gh.chmod(0o755)
        subprocess.run(["git", "remote", "set-url", "origin", "https://github.com/o/r.git"], cwd=self.project, check=True)
        rewrite = f"url.{remote}/.insteadOf=https://github.com/o/r.git"
        git = shutil.which("git")
        git_wrap = Path(self.temporary.name) / "git-wrap"
        git_wrap.mkdir()
        wrapper = git_wrap / "git"
        wrapper.write_text(
            "#!/bin/sh\n"
            "case $1 in\n"
            "push|fetch|ls-remote|pull)\n"
            f"  exec {shlex.quote(git)} -c {shlex.quote(rewrite)} \"$@\" ;;\n"
            "esac\n"
            f"exec {shlex.quote(git)} \"$@\"\n")
        wrapper.chmod(0o755)
        environ = {"LAND_GH": str(gh), "PATH": str(git_wrap) + os.pathsep + os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, environ):
            self.land("init", "--trunk", "main", "--mode", "human")
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
                self.open()
                self.land("mode", "merge")
                self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, lands by merge\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)")
                header = self.brigade("walk").splitlines()[0]
                self.assertEqual(header, f"{_shown_root(self.project)}: {self.land('status')}")
                self.assertTrue(header.startswith(f"{_shown_root(self.project)}: merge mode onto "))
    def test_walk_groups_repositories_and_repo_selects_one(self):
        from unittest import mock
        other = Path(self.temporary.name) / "other-app"
        other.mkdir()
        self.init_landing("--cap", "4")
        run = lambda *command: subprocess.run(command, cwd=other, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        (other / "a.txt").write_text("a\n")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.land_repo(other, "init", "--trunk", "lane", "--mode", "local", "--base", "main", "--cap", "4")
        docs = self.store / "bridge-kit" / "docs"
        engine = self.store / "bridge-kit" / "engine"
        core = self.store / "other-app" / "core"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.assertEqual(self.brigade("walk"), f"no restaurants under {self.store}")
            self.run_at(docs, "open", "--project-root", str(self.project), "--name", "Docs")
            self.run_at(engine, "open", "--project-root", str(self.project), "--name", "Engine")
            self.run_at(core, "open", "--project-root", str(other), "--name", "Core")
            self.run_at(docs, "set", "--thread", "thread-docs")
            self.run_at(engine, "set", "--thread", "thread-engine")
            self.run_at(core, "set", "--thread", "thread-core")
            self.run_at(docs, "ticket", "add", "--kind", "fix", "--summary", "Write the guide")
            self.assertEqual(self.run_at(docs, "fire", "--tickets", "T1", "--station", "bug-fix",
                                         "--summary", "Write the guide", "--paths", "a.txt"),
                             "D1 (lease L1 held by docs/D1)")
            self.run_at(engine, "ticket", "add", "--kind", "fix", "--summary", "Blocked edit")
            refused = self.run_at(engine, "fire", "--tickets", "T1", "--station", "bug-fix",
                                  "--summary", "Blocked edit", "--paths", "a.txt", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by docs/D1", refused)
            self.assertEqual(self.land("lease", "claim", "--holder", "engine/D12", "--paths", "src"), "L2")
            self.assertEqual(self.land("lease", "claim", "--holder", "engine/D13", "--paths", "lib"), "L3")
            self.assertEqual(self.run_at(docs, "86", "add", "--question", "Ship the guide?",
                                         "--options", "yes, no", "--default", "no"), "Q1")
            app = _shown_root(self.project)
            other_root = _shown_root(other)
            expected = "\n".join([
                f"{app}: local mode onto refs/landing/lane. leases held: 3, changes in flight: 3 of 4. author policy: any",
                "  Docs (reports milestones): in progress: 1, decisions for you: 1",
                "    thread thread-docs, leases L1 (D1)",
                "    Q1: Ship the guide?",
                "  Engine (reports milestones): waiting tickets: 1 (1 blocked)",
                "    thread thread-engine, leases L2 (D12), L3 (D13)",
                f"{other_root}: local mode onto refs/landing/lane. changes in flight: 0 of 4. author policy: any",
                "  Core (reports milestones): nothing on record",
                "    thread thread-core",
            ])
            self.assertEqual(self.brigade("walk"), expected)
            self.assertEqual(self.brigade("walk", "--repo", str(self.project)), "\n".join(expected.splitlines()[:6]))
            self.assertEqual(self.brigade("walk", "--repo", str(other)), "\n".join(expected.splitlines()[6:]))
            missing = Path(self.temporary.name) / "empty-repo"
            missing.mkdir()
            self.assertEqual(self.brigade("walk", "--repo", str(missing)), f"no restaurants for {missing.resolve()}")
    def test_commands_outside_a_restaurant_fail_with_the_fix(self):
        self.assertIn("run brigade.py open", self.brigade("status", ok=False))
    def test_open_records_reporting_and_defaults_to_milestones(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        self.assertEqual(meta["reporting"], "milestones")
        self.assertNotIn("landing", meta)
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)")
        self.assertIn("reports milestones", self.brigade("walk"))
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                                      "--reporting", "every-turn"), f"exists {self.at}")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["reporting"], "milestones")
        other = self.brigade("open", "--project-root", str(self.project), "--name", "Quiet",
                             "--reporting", "digest")
        quiet = self.store / "bridge-kit" / "quiet"
        self.assertEqual(other, "\n".join([
            f"opened {quiet}",
            f"sibling Perf ({self.at}), thread not recorded",
            "  purpose: not written yet",
            "  off the menu: not written yet",
        ]))
        self.assertEqual(json.loads((quiet / "restaurant.json").read_text())["reporting"], "digest")
        self.assertIn("reports digest", self.brigade("walk"))
    def test_a_restaurant_file_without_reporting_reads_as_milestones(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        del meta["reporting"]
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.assertNotIn("reporting", json.loads((self.at / "restaurant.json").read_text()))
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)")
        self.assertIn("reports milestones", self.brigade("walk"))
        self.assertNotIn("reporting", json.loads((self.at / "restaurant.json").read_text()))
    def test_close_lists_an_escalation_with_its_tickets(self):
        self.fired_bug_fix()
        self.brigade("close")
        self.brigade("dish", "D1", "--mode", "full", "--reason", "contested: two owners")
        text = self.brigade("close", "--dry-run")
        self.assertEqual(text.split("\n\n", 2)[2], "\n\n".join([
            "## Moved to full mode", "- D1 (T1): contested: two owners",
            "## Spend", "goal not recorded  wall open  rounds 0  send-backs 0  threads not recorded",
            "## Follow-up share", "- 0 of 1 tickets created in the last 14 days came from a report (0%)."]))
    def test_the_second_send_back_moves_the_item_to_full_once(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back")
        escalated = self.head("Playbook: playbooks/feature.md", "Mode: full", "Mode source: escalated: second send-back",
                              "Attempt: fix")
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], escalated)
        self.assertNotIn("Contested:", text)
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D1", "full", "second send-back"]])
        self.brigade(*self.BRIEF)
        self.assertEqual(len(self.mode_rows()), 1)
        self.brigade("set", "--mode", "light")
        self.assertEqual(self.brigade(*self.BRIEF).split("\n\n", 1)[0], escalated)
        self.assertEqual(self.brigade("close", "--dry-run").split("## Moved to full mode\n\n", 1)[1].split("\n")[0],
                         "- D1 (T1): second send-back")
    def test_status_prints_the_workers_line_third_before_reports_to_mode_and_owner(self):
        self.open()
        self.assertEqual(self.brigade("status"),
                         "thread not recorded\nreporting: milestones, no landing contract\n"
                         "workers: 0 of 2 running, 2 idle, auto-start: normal\n"
                         "liveness: every 600000 ms (default for milestones)")
        self.brigade("set", "--thread", "t1", "--reports-to", "admin-thread", "--mode", "light")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a")
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread t1",
            "reporting: milestones, no landing contract, waiting tickets: 1",
            "workers: 0 of 2 running, 2 idle, auto-start: normal",
            "liveness: every 600000 ms (default for milestones)",
            "kinds: fix 1 waiting",
            "reports to admin-thread",
            "mode light",
            "owner t1@1",
        ])
    def test_status_prints_the_stored_liveness_interval(self):
        self.open()
        for level in ("every-turn", "milestones", "digest"):
            with self.subTest(level=level):
                self.brigade("set", "--reporting", level)
                self.brigade("set", "--liveness-ms", "3600000")
                self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["livenessMs"], 3600000)
                self.assertIn("liveness: every 3600000 ms", self.brigade("status").splitlines())
    def test_status_marks_the_liveness_interval_as_the_default_for_the_reporting_level(self):
        self.open()
        defaults = (("every-turn", "liveness: every 600000 ms (default for every-turn)"),
                    ("milestones", "liveness: every 600000 ms (default for milestones)"),
                    ("digest", "liveness: every 1800000 ms (default for digest)"))
        for level, line in defaults:
            with self.subTest(level=level):
                self.brigade("set", "--reporting", level)
                self.assertNotIn("livenessMs", json.loads((self.at / "restaurant.json").read_text()))
                self.assertIn(line, self.brigade("status").splitlines())
    def test_status_names_the_milestones_liveness_default_when_the_reporting_level_is_not_one_it_knows(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["reporting"] = "weekly"
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.assertIn("liveness: every 600000 ms (default for milestones)", self.brigade("status").splitlines())
    def test_clearing_liveness_ms_removes_the_key_and_restores_the_level_default(self):
        self.open()
        self.brigade("set", "--liveness-ms", "1200000")
        self.assertIn("liveness: every 1200000 ms", self.brigade("status").splitlines())
        self.brigade("set", "--liveness-ms", "")
        self.assertNotIn("livenessMs", json.loads((self.at / "restaurant.json").read_text()))
        self.assertIn("liveness: every 600000 ms (default for milestones)", self.brigade("status").splitlines())
    def test_set_refuses_a_liveness_ms_that_is_not_a_whole_number_of_at_least_60000(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        for value in ("10m", "1.5", "59999", "-1"):
            with self.subTest(value=value):
                error = self.brigade("set", "--liveness-ms", value, code=1)
                self.assertIn("--liveness-ms", error)
                self.assertIn(value, error)
                self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
    def test_set_refuses_a_hand_edited_liveness_ms_before_writing_restaurant_json(self):
        self.open()
        for value in ("soon", 1000):
            with self.subTest(value=value):
                meta = json.loads((self.at / "restaurant.json").read_text())
                meta["livenessMs"] = value
                (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
                before = (self.at / "restaurant.json").read_bytes()
                error = self.brigade("set", "--workers", "3", code=1)
                self.assertIn("livenessMs", error)
                self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
    def test_status_prints_the_liveness_line_immediately_after_the_workers_line(self):
        self.open()
        self.brigade("set", "--thread", "thread-1")
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread thread-1",
            "reporting: milestones, no landing contract",
            "workers: 0 of 2 running, 2 idle, auto-start: normal",
            "liveness: every 600000 ms (default for milestones)",
            "owner thread-1@1",
        ])
    def test_set_autofire_records_each_level_and_status_prints_it_as_auto_start(self):
        self.open()
        for level in ("off", "urgent", "normal", "low"):
            self.assertEqual(json.loads(self.brigade("set", "--autofire", level))["autofire"], level)
            status = self.brigade("status")
            self.assertEqual(status.splitlines()[2], f"workers: 0 of 2 running, 2 idle, auto-start: {level}")
            self.assertNotIn("autofire", status)
    def test_a_missing_or_empty_autofire_key_reads_as_normal(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        self.assertNotIn("autofire", meta)
        line = "workers: 0 of 2 running, 2 idle, auto-start: normal"
        self.assertEqual(self.brigade("status").splitlines()[2], line)
        (self.at / "restaurant.json").write_text(json.dumps(meta | {"autofire": ""}, indent=2) + "\n")
        self.assertEqual(self.brigade("status").splitlines()[2], line)
