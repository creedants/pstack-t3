import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from .names import LAND_SCRIPT, SCRIPT, SUPPORT, _land_has_rulings
from .inprocess import ProcessReason, real_popen, real_process
from .race_child import _race_args, _wait_for_path


class _TicketsRace:
    def test_watch_rechecks_a_lease_outside_the_store_lock(self):
        case = Path(self.temporary.name)
        (case / "land.py").write_text(
            "import sys, time\nfrom pathlib import Path\n"
            f"case = Path({str(case)!r})\n"
            "action = sys.argv[4]\n"
            "if action == 'claim':\n"
            "    print('paths overlap L1 held by engine/D1 on src', file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "if action == 'check':\n"
            "    (case / 'checking').touch()\n"
            "    end = time.monotonic() + 30\n"
            "    while not (case / 'check-proceed').exists() and time.monotonic() < end:\n        time.sleep(0.01)\n"
            "    print('L1 held by engine/D1 on src')\n"
            "    sys.exit(1)\n"
            "print('ok')\n")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        refused = real_process(
            ProcessReason.PRE_MAIN, SUPPORT,
            _race_args("fake-land", case, ["--store", str(self.store), "--at", str(self.at),
                                           "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"]))
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("blocked: lease", self.brigade("ticket", "list"))
        watching = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(SUPPORT), *_race_args(
                "fake-land", case, ["--store", str(self.store), "--at", str(self.at), "watch"])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        try:
            _wait_for_path(case / "checking")
            # race between two processes
            added = real_process(
                ProcessReason.RACE, SCRIPT,
                ["--store", str(self.store), "--at", str(self.at),
                 "ticket", "add", "--kind", "fix", "--summary", "two"],
                timeout=10,
            )
            self.assertEqual(added.stdout.strip(), "T2", added.stderr)
            (case / "check-proceed").touch()
            out, err = watching.communicate(timeout=10)
        finally:
            watching.kill()
        self.assertEqual(watching.returncode, 0, err)
        self.assertEqual(out.strip(), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on L1 (engine/D1)")


class _ItemsRace:
    def test_fire_warns_when_releasing_a_conflicting_lease_fails(self):
        case = Path(self.temporary.name)
        (case / "land.py").write_text(
            "import sys, time\nfrom pathlib import Path\n"
            f"case = Path({str(case)!r})\n"
            "with open(case / 'land-calls', 'a') as handle:\n    handle.write(' '.join(sys.argv[3:]) + '\\n')\n"
            "if sys.argv[4] == 'claim':\n"
            "    (case / 'claiming').touch()\n"
            "    end = time.monotonic() + 30\n"
            "    while not (case / 'claim-proceed').exists() and time.monotonic() < end:\n        time.sleep(0.01)\n"
            "    print('L7')\n"
            "else:\n"
            "    print('lease L7 is held elsewhere', file=sys.stderr)\n"
            "    sys.exit(1)\n")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        firing = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(SUPPORT), *_race_args(
                "fake-land", case, ["--store", str(self.store), "--at", str(self.at),
                                    "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        try:
            _wait_for_path(case / "claiming")
            # race between two processes
            other = real_process(
                ProcessReason.RACE, SCRIPT,
                ["--store", str(self.store), "--at", str(self.at),
                 "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "other"],
                timeout=10,
            )
            self.assertEqual(other.stdout.strip(), "D1", other.stderr)
            (case / "claim-proceed").touch()
            _, err = firing.communicate(timeout=10)
        finally:
            firing.kill()
        self.assertEqual(firing.returncode, 1)
        self.assertIn("nothing fired: T1 is assigned, not waiting", err)
        self.assertIn("L7", err)
        self.assertIn("lease L7 is held elsewhere", err)


class _ReportingRace:
    def test_fire_claims_outside_the_store_lock_and_rechecks_before_it_writes(self):
        case = Path(self.temporary.name)
        calls = case / "land-calls"
        (case / "land.py").write_text(
            "import sys, time\nfrom pathlib import Path\n"
            f"case = Path({str(case)!r})\n"
            "with open(case / 'land-calls', 'a') as f:\n    f.write(' '.join(sys.argv[3:]) + '\\n')\n"
            "if sys.argv[4] == 'claim':\n"
            "    (case / 'claiming').touch()\n"
            "    end = time.monotonic() + 30\n"
            "    while not (case / 'claim-proceed').exists() and time.monotonic() < end:\n        time.sleep(0.01)\n"
            "    print('L7')\n"
            "else:\n    print('L7 released')\n")
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        firing = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(SUPPORT), *_race_args(
                "fake-land", case, ["--store", str(self.store), "--at", str(self.at),
                                    "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        try:
            _wait_for_path(case / "claiming")
            # race between two processes
            other = real_process(
                ProcessReason.RACE, SCRIPT,
                ["--store", str(self.store), "--at", str(self.at),
                 "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "other"],
                timeout=10,
            )
            self.assertEqual(other.stdout.strip(), "D1", other.stderr)
            (case / "claim-proceed").touch()
            _, err = firing.communicate(timeout=10)
        finally:
            firing.kill()
        self.assertEqual(firing.returncode, 1)
        self.assertEqual(err.strip(), "brigade: nothing fired: T1 is assigned, not waiting")
        self.assertEqual(calls.read_text().splitlines(),
                         ["status --json", "lease claim --holder perf/D1 --paths src,changes/perf%2Fd1.md", "lease release L7"])
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract, in progress: 1\nworkers: 1 of 2 running, 1 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)\nkinds: fix 1 in flight")


class _WakeRace:
    def test_a_failed_lease_check_keeps_the_landing_diagnostic(self):
        case = Path(self.temporary.name)
        (case / "land.py").write_text(
            "import sys\n"
            "action = sys.argv[4]\n"
            "if action == 'claim':\n"
            "    print('paths overlap L1 held by engine/D1 on src', file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "if action == 'check':\n"
            "    print('queue database is locked', file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "sys.exit(0)\n")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        refused = real_process(
            ProcessReason.PRE_MAIN, SUPPORT,
            _race_args("fake-land", case, ["--store", str(self.store), "--at", str(self.at),
                                           "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"]))
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        watched = real_process(
            ProcessReason.PRE_MAIN, SUPPORT,
            _race_args("fake-land", case, ["--store", str(self.store), "--at", str(self.at), "watch"]))
        self.assertEqual(watched.returncode, 0, watched.stderr)
        self.assertEqual(watched.stdout.strip(),
                         "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on the landing queue (queue database is locked)")


class _HandoffRace:
    def test_two_processes_appending_200_rows_each_leave_400_rows(self):
        self.open("core")
        support = SUPPORT
        procs = [
            real_popen(
                ProcessReason.RACE,
                [sys.executable, str(support), *_race_args("append", self.temporary.name, [str(self.dir("core")), label])],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True)
            for label in ("a", "b")]
        (Path(self.temporary.name) / "go").touch()
        for proc in procs:
            _, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
        rows = self.rows("core", "log.tsv")
        self.assertEqual(len(rows), 400)
        self.assertTrue(all(len(row) == 5 for row in rows))
        self.assertEqual(sorted(row[4] for row in rows),
                         sorted(f"{label} row {number}" for label in "ab" for number in range(200)))
        self.assertEqual(self.brigade("core", "ticket", "list"), "")


class _AdminRace:
    def admin_child(self, mode, label, *args):
        """Every caller overlaps another started process before this one resumes."""
        case = Path(self.temporary.name) / label
        case.mkdir()
        support = SUPPORT
        tail = _race_args(mode, case, ["--store", str(self.store), "--at", str(self.dir(".admin")), *args])
        proc = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(support), *tail],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return case, proc

    def append_row(self, name, *fields):
        """The caller overlaps an already started pause child and does not wait on that child's lock."""
        support = SUPPORT
        tail = _race_args("append-row", self.temporary.name, [str(self.dir(name)), *fields])
        proc = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(support), *tail],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    @unittest.skipUnless(_land_has_rulings(), "land.py has no share, lease reserve, or contest yet; change 8 adds them, "
                                              "and this test runs once it lands")
    def test_after_a_recovery_the_old_runs_paused_land_rulings_change_nothing(self):
        state = Path(self.temporary.name) / "state"
        env = dict(os.environ, XDG_STATE_HOME=str(state))
        from unittest import mock
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(state)})
        patcher.start()
        self.addCleanup(patcher.stop)

        def land(*args, ok=True):
            result = subprocess.run([sys.executable, str(LAND_SCRIPT), "--repo", str(self.project), *args],
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
            return (result.stdout if ok else result.stderr).strip()

        git = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        git("git", "init", "-q", "-b", "main")
        (self.project / "README.md").write_text("a\n")
        git("git", "add", "-A")
        git("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        land("init", "--trunk", "lane", "--mode", "local", "--base", "main", "--cap", "3")
        self.with_thread()
        contest = land("contest", "--holders", "docs/D1,engine/D1", "--owner", ".admin/@1")
        old = ("--owner", ".admin/@1")
        commands = [("share", "--for", "docs/", "1", *old),
                    ("lease", "reserve", "--for", "docs/", "--paths", "README.md", "--ruling", "R1", *old),
                    ("contest", "--settle", contest, "--first", "docs/D1", *old)]
        running = []
        for number, args in enumerate(commands):
            case = Path(self.temporary.name) / f"land{number}"
            case.mkdir()
            proc = subprocess.Popen([sys.executable, str(SUPPORT), "--race-child", "land-pause", str(case),
                                     "--repo", str(self.project), *args],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            self.addCleanup(lambda proc=proc: proc.poll() is None and proc.kill())
            running.append((case, proc))
        for case, _ in running:
            _wait_for_path(case / "paused", timeout=20)
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", "--stopped", "r1")
        before = (land("lease", "list"), land("status"))
        for case, proc in running:
            (case / "proceed").touch()
            code, _, err = self.finish(proc)
            self.assertEqual((code, err), (1, "land: owner .admin/@1 is stale; .admin/ is at generation 3"))
        self.assertEqual((land("lease", "list"), land("status")), before)
