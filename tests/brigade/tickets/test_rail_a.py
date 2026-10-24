if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import Path, ProcessReason, SCRIPT, STILL_OPEN, _wait_for_path, json, os, real_process, shlex

class RailAMixin:
    def test_a_losing_open_keeps_the_owners_ticket_and_menu(self):
        case = Path(self.temporary.name) / "race"
        case.mkdir()
        project = Path(self.temporary.name) / "app"
        project.mkdir()
        directory = self.store / "app" / "docs"
        args = ["--store", str(self.store), "open", "--project-root", str(project), "--name", "Docs"]
        owner = loser = None
        try:
            owner = self._spawn_race("owner", case, args)
            _wait_for_path(case / "metadata-ready")
            loser = self._spawn_race("loser", case, args)
            _wait_for_path(case / "loser-saw-meta")
            self.assertIsNone(owner.poll())
            self.assertIsNone(loser.poll())
            if not (directory / "rail.tsv").exists():
                (case / "loser-after-meta").touch()
                _wait_for_path(case / "loser-at-scaffold")
                (case / "owner-proceed").touch()
                owner_code, owner_out, owner_err = self._finish_race(owner)
                owner = None
            else:
                owner_code = owner_out = owner_err = None
            # inherited environment
            added = real_process(
                ProcessReason.INHERITED_ENV, SCRIPT,
                ["--at", str(directory), "ticket", "add", "--kind", "fix", "--summary", "Keep this ticket"],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
            self.assertEqual((added.returncode, added.stdout), (0, "T1\n"), added.stderr)
            menu = directory / "menu.md"
            menu.write_text(menu.read_text() + "Keep the menu sentence.\n")
            if (case / "loser-at-scaffold").exists():
                (case / "loser-scaffold-proceed").touch()
            else:
                (case / "loser-after-meta").touch()
            loser_code, loser_out, loser_err = self._finish_race(loser)
            loser = None
            if owner is not None:
                (case / "owner-proceed").touch()
                owner_code, owner_out, owner_err = self._finish_race(owner)
                owner = None
        finally:
            for proc in (owner, loser):
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        self.assertEqual((owner_code, owner_err), (0, ""), owner_out)
        self.assertEqual(owner_out, f"opened {directory}")
        self.assertEqual((loser_code, loser_err), (0, ""), loser_out)
        self.assertEqual(loser_out, f"exists {directory}")
        self.assertIn("Keep this ticket", (directory / "rail.tsv").read_text())
        self.assertIn("Keep the menu sentence.", (directory / "menu.md").read_text())

    def test_a_priority_paths_or_decision_change_adds_no_log_row_and_keeps_a_blocked_ticket_blocked(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "two", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] two blocked: workers")
        before = ((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes())
        self.assertEqual(self.brigade("ticket", "set", "T2", "--priority", "urgent"), "T2 priority urgent")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--paths", "a.txt"), "T2 paths a.txt")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--decision"), "T2 decision")
        self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes()), before)
        self.assertEqual(self.cells("T2"), ("urgent", "a.txt", "yes"))
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting urgent fix [user] two blocked: workers")

    def test_a_rail_row_with_seven_ten_eleven_or_twelve_fields_is_read(self):
        self.open()
        stamp = "2026-10-01T00:00:00+00:00"
        for extra in ([], ["low", "a.txt", ""], ["low", "a.txt", "", ""], ["low", "a.txt", "", "", ""]):
            (self.at / "rail.tsv").write_text("id\n" + "\t".join(["T1", stamp, "waiting", "user", "", "", "s", *extra]) + "\n")
            self.assertEqual(self.brigade("ticket", "list"), f"T1 waiting {'low' if extra else 'normal'} [user] s", extra)

    def test_a_rail_row_with_six_fields_or_thirteen_is_malformed(self):
        self.open()
        stamp = "2026-10-01T00:00:00+00:00"
        for fields in (["T1", stamp, "waiting", "user", "", ""], ["T1", stamp, "waiting", "user", "", "", "s", *[""] * 6]):
            (self.at / "rail.tsv").write_text("id\n" + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("ticket", "list", ok=False),
                             "brigade: bridge-kit/perf/rail.tsv line 2 is malformed; fix or remove it")

    def test_a_recorded_priority_wins_over_the_default_of_the_source(self):
        self.open()
        self.brigade("set", "--intake", "upstream")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--source", "upstream", "--priority", "low")
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting low fix [upstream] a")

    def test_a_refused_fire_stays_blocked_until_the_lease_is_free(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate",
                                   "--paths", "src", "--timebox", "60", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate blocked: lease")
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on L1 (engine/D1)")
            self.land("lease", "release", "L1")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on L2 (engine/D1)")
            self.land("lease", "release", "L2")
            line = "T1: unblocked; run fire --tickets=T1 --station=bug-fix '--summary=Fix the gate' --paths=src --timebox=60"
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + line)
            started = self.brigade(*shlex.split(line.split("run ", 1)[1]))
            self.assertEqual(started, "D1 (lease L3 held by perf/D1)")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], "src,changes/perf%2Fd1.md")
            self.assertEqual(self.brigade("ticket", "list"), "T1 assigned normal fix [user] Fix the gate")

    def test_a_replayed_keep_parked_relay_logs_inbox_done_once(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.open_admin()
        before = (self.at / "86.tsv").read_bytes()
        self.admin("request", "--to", "perf", "answer perf Q1: keep parked")
        self.assertEqual(self.brigade("inbox", "take"), "A1: answer perf Q1: keep parked")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.brigade("inbox", "take"), "A1: answer perf Q1: keep parked")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.brigade("inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.brigade("inbox", "take"), "nothing handed to you")
        self.assertEqual(self.brigade("inbox", "done", "A1"), "A1 done")
        self.assertEqual((self.at / "86.tsv").read_bytes(), before)
        done = [row for row in self.log_rows() if row["kind"] == "inbox-done"]
        self.assertEqual([(row["id"], row["state"], row["note"]) for row in done],
                         [("A1", "done", "answer perf Q1: keep parked")])

    def test_a_rewrite_of_an_old_table_keeps_each_cell_and_adds_an_empty_kind_and_goal_cell_to_each_row(self):
        self.open()
        self.old_item()
        rail, dishes = ((self.at / name).read_text().splitlines()[1].split("\t") for name in ("rail.tsv", "dishes.tsv"))
        self.assertEqual(self.brigade("dish", "D1", "--timebox", "90"), "D1 in-review")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")
        dishes[11], rail[7] = "90", "low"
        self.assertEqual((self.at / "dishes.tsv").read_text().splitlines()[1].split("\t"), [*dishes, "", ""])
        self.assertEqual((self.at / "rail.tsv").read_text().splitlines()[1].split("\t"), [*rail, "", ""])
        self.assertEqual((self.at / "dishes.tsv").read_text().splitlines()[0].split("\t")[-3:], ["reported", "kind", "goal"])
        self.assertEqual((self.at / "rail.tsv").read_text().splitlines()[0].split("\t")[-3:], ["decision", "kind", "goal"])

    def test_a_store_with_no_goals_table_runs_status_watch_startable_ticket_list_and_close_and_gains_no_table(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "next")
        (self.at / "goals.tsv").unlink()
        files = sorted(self.store_files())
        self.assertEqual(self.brigade("status").splitlines()[0], "thread not recorded")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")
        self.assertEqual(self.brigade("startable").splitlines()[:2],
                         ["workers: 1 of 2 running, 1 idle, auto-start: normal", "waiting tickets: 1, unknown: 1"])
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] next")
        code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "# Perf report")
        self.assertEqual(sorted(self.store_files()), files)
        self.assertFalse((self.at / "goals.tsv").exists())
