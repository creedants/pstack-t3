if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import (
    setUpModule,
    BrigadeTest,
    HELD_LINE,
    Path,
    datetime,
    json,
    mock,
    os,
    timedelta,
    timezone,
)
import sqlite3


def _stamp(minutes_ago):
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _local_of(stamp):
    ts = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes")


def _make_db(base, runs):
    dbdir = Path(base) / "userdata"
    dbdir.mkdir(parents=True, exist_ok=True)
    path = dbdir / "statev2.sqlite"
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE orchestration_v2_projection_metadata (projection_name TEXT, schema_version INTEGER)")
        con.execute("CREATE TABLE orchestration_v2_projection_threads (thread_id TEXT, title TEXT, default_provider TEXT, payload_json TEXT, project_id TEXT, updated_at TEXT)")
        con.execute("CREATE TABLE orchestration_v2_projection_runs (thread_id TEXT, status TEXT, requested_at TEXT, completed_at TEXT)")
        con.execute("CREATE TABLE orchestration_v2_projection_subagents (subagent_id TEXT, thread_id TEXT, child_thread_id TEXT, status TEXT, started_at TEXT, completed_at TEXT)")
        con.execute(
            "INSERT INTO orchestration_v2_projection_metadata (projection_name, schema_version) VALUES (?, ?)",
            ("thread-projections", 2),
        )
        for entry in runs:
            if len(entry) == 5:
                thread_id, status, requested, completed, updated = entry
            else:
                thread_id, status, requested, completed = entry
                updated = requested
            con.execute(
                "INSERT INTO orchestration_v2_projection_runs (thread_id, status, requested_at, completed_at) VALUES (?, ?, ?, ?)",
                (thread_id, status, requested, completed),
            )
            con.execute(
                "INSERT INTO orchestration_v2_projection_threads (thread_id, title, default_provider, payload_json, project_id, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (thread_id, "t", "codex", "{}", "p", updated),
            )
        con.commit()
    finally:
        con.close()
    return path


def _make_bad_schema(base):
    dbdir = Path(base) / "userdata"
    dbdir.mkdir(parents=True, exist_ok=True)
    path = dbdir / "statev2.sqlite"
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE junk (x TEXT)")
        con.execute("INSERT INTO junk (x) VALUES ('bad')")
        con.commit()
    finally:
        con.close()
    return path


class WatchIdleTest(BrigadeTest):
    def _t3home(self):
        home = Path(self.temporary.name) / "t3home"
        home.mkdir(exist_ok=True)
        return home

    def _watch_with_home(self, home):
        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(home)}):
            return self.brigade("watch")

    def test_ended_run_without_report_prints_idle(self):
        self.open()
        self.fire_one(timebox="60")
        home = self._t3home()
        completed = _stamp(1)
        _make_db(home, [("thread-9", "completed", _stamp(5), completed)])
        out = self._watch_with_home(home)
        expected = f"D1: idle since {_local_of(completed)} with no report (thread thread-9)"
        self.assertIn(expected, out)

    def test_ended_old_prints_idle_at_once(self):
        self.open()
        self.fire_one(timebox="60")
        home = self._t3home()
        completed = _stamp(65)
        _make_db(home, [("thread-9", "completed", _stamp(70), completed)])
        out = self._watch_with_home(home)
        expected = f"D1: idle since {_local_of(completed)} with no report (thread thread-9)"
        self.assertIn(expected, out)

    def test_active_recent_prints_running(self):
        self.open()
        self.fire_one(timebox="60")
        home = self._t3home()
        _make_db(home, [("thread-9", "running", _stamp(2), None, _stamp(2))])
        out = self._watch_with_home(home)
        self.assertNotIn("idle since", out)
        self.assertIn("D1: running 0m of 60m (thread thread-9)", out)

    def test_quiet_open_past_threshold_prints_idle(self):
        self.open()
        self.fire_one(timebox="60")
        home = self._t3home()
        updated = _stamp(65)
        _make_db(home, [("thread-9", "waiting", _stamp(70), None, updated)])
        out = self._watch_with_home(home)
        expected = f"D1: idle since {_local_of(updated)} with no report (thread thread-9)"
        self.assertIn(expected, out)

    def test_long_open_with_recent_activity_prints_running(self):
        self.open()
        self.fire_one(timebox="60")
        home = self._t3home()
        _make_db(home, [("thread-9", "running", _stamp(65), None, _stamp(2))])
        out = self._watch_with_home(home)
        self.assertNotIn("idle since", out)
        self.assertIn("D1: running 0m of 60m (thread thread-9)", out)

    def test_running_with_no_completed_prints_running(self):
        self.open()
        self.fire_one(timebox="60")
        home = self._t3home()
        _make_db(home, [("thread-9", "running", _stamp(2), None, _stamp(2))])
        out = self._watch_with_home(home)
        self.assertNotIn("idle since", out)
        self.assertIn("D1: running 0m of 60m (thread thread-9)", out)

    def test_report_present_prints_report_not_idle(self):
        self.open()
        self.fire_one(timebox="60")
        self.attempt_report("done")
        home = self._t3home()
        _make_db(home, [("thread-9", "completed", _stamp(20), _stamp(15))])
        out = self._watch_with_home(home)
        self.assertNotIn("idle since", out)
        self.assertIn("report written", out)

    def test_missing_db_leaves_output_unchanged(self):
        self.open()
        self.fire_one(timebox="60")
        home = self._t3home()
        out = self._watch_with_home(home)
        self.assertEqual(out, "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (thread thread-9)")

    def test_bad_schema_leaves_dish_lines_plus_at_most_one_line(self):
        self.open()
        self.fire_one(timebox="60")
        home = self._t3home()
        _make_bad_schema(home)
        out = self._watch_with_home(home)
        self.assertIn("D1: running 0m of 60m (thread thread-9)", out)
        self.assertNotIn("idle since", out)
        self.assertLessEqual(len(out.splitlines()), 3)

    def test_wal_without_index_leaves_dish_lines_plus_at_most_one_line(self):
        self.open()
        self.fire_one(timebox="60")
        home = self._t3home()
        path = _make_db(home, [("thread-9", "completed", _stamp(20), _stamp(15))])
        (Path(str(path) + "-wal")).write_bytes(b"\x00" * 32)
        out = self._watch_with_home(home)
        self.assertIn("D1: running 0m of 60m (thread thread-9)", out)
        self.assertNotIn("idle since", out)
        self.assertLessEqual(len(out.splitlines()), 3)

    def test_held_dish_stays_silent(self):
        self.open()
        self.fire_one(timebox="60")
        self.assertEqual(self.park_item(), "Q1")
        home = self._t3home()
        _make_db(home, [("thread-9", "completed", _stamp(20), _stamp(15))])
        out = self._watch_with_home(home)
        self.assertIn(HELD_LINE, out)
        self.assertNotIn("idle since", out)

    def test_idle_beats_over_timebox(self):
        self.open()
        self.fire_one(timebox="30")
        self.backdate_attempt(45)
        home = self._t3home()
        completed = _stamp(15)
        _make_db(home, [("thread-9", "completed", _stamp(50), completed)])
        out = self._watch_with_home(home)
        self.assertIn(f"D1: idle since {_local_of(completed)} with no report (thread thread-9)", out)
        self.assertNotIn("over its", out)

    def test_missing_updated_at_prints_cannot_see_and_keeps_running(self):
        self.open()
        self.fire_one(timebox="60")
        home = self._t3home()
        path = _make_db(home, [("thread-9", "waiting", _stamp(70), None, _stamp(65))])
        con = sqlite3.connect(str(path))
        try:
            con.execute("ALTER TABLE orchestration_v2_projection_threads DROP COLUMN updated_at")
            con.commit()
        finally:
            con.close()
        out = self._watch_with_home(home)
        self.assertIn("cannot see worker runs:", out)
        self.assertIn("D1: running 0m of 60m (thread thread-9)", out)
        self.assertNotIn("idle since", out)
