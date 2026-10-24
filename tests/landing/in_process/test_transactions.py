if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    _close_new_fds,
    _descriptors,
    _fd_paths,
    contextlib,
    drive,
    errno,
    fcntl,
    gc,
    io,
    json,
    land,
    mock,
    os,
    ProcessReason,
    real_process,
    REVIEWER,
    SCRIPT,
    sh,
    sqlite3,
    stat,
    sys,
    unittest,
    weakref,
)
from .. import support
from .base import InProcessBase

class LandingTest(InProcessBase, LandingTestBase):
    def test_a_land_run_runs_no_subprocess_inside_a_write_transaction(self):
        self.init()
        self.queue_one()
        hits = self.subprocesses_during_land()
        self.assertEqual(self.origin_log()[0], "w1")
        self.assertIn("E1 landed", self.land("status", "E1"))
        with self.fake_gh():
            self.land("mode", "human")
        sh("git", "fetch", "-q", "origin", cwd=self.work)
        sh("git", "merge", "-q", "--ff-only", "origin/main", cwd=self.work)
        sha = self.worker("w2", {"b.txt": "same\n"})
        (self.work / "b.txt").write_text("same\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        lease = self.land("lease", "claim", "--holder", "r/D2", "--paths", "b.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)
        with self.fake_gh():
            hits += self.subprocesses_during_land()
        self.assertIn("already in trunk", self.land("status", "E2"))
        self.assertEqual(hits, [])

    def test_a_merge_mode_land_run_that_lands_bounces_and_rebuilds_runs_no_subprocess_inside_a_write_transaction(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "passed")
            self.post_check(10, "failed")
            self.post_check(11, "passed")
            hits = self.subprocesses_during_land()
            self.assertEqual([self.land("status", entry).split(" (")[0] for entry in ("E1", "E2", "E3")],
                             ["E1 landed", "E2 bounced", "E3 awaiting-merge"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(hits, [])

    def test_a_finished_row_before_a_non_utf8_unfinished_tail_is_read(self):
        path = self.base / "dishes.tsv"
        path.write_bytes(b"id\tbranch\nD1\tw1\n" + b"\xff\xfe")
        self.assertEqual(land._table_rows(path), [{"id": "D1", "branch": "w1"}])

    def test_a_store_from_before_pr_text_columns_is_migrated(self):
        self.init()
        with land.Store.for_repo(self.work) as store:
            store.db.execute("DROP TABLE entry")
            store.db.executescript(land.SCHEMA)
            self.assertNotIn("title", {row["name"] for row in store.db.execute("PRAGMA table_info(entry)")})
        self.assertEqual(self.queue_one(), "E1")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_a_command_leaves_no_open_landing_database_after_it_returns(self):
        self.init()
        database = os.path.realpath(next((self.base / "state").glob("pstack-t3/landing/*/land.db")))
        opened = []
        real_connect = land.sqlite3.connect

        def connect(path, *args, **kwargs):
            connection = real_connect(path, *args, **kwargs)
            opened.append((connection, os.path.realpath(path)))
            return connection

        def call(argv):
            opened.clear()
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(land.sqlite3, "connect", connect), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = land.main(argv)
            return code, out.getvalue(), err.getvalue()

        def assert_closed():
            self.assertTrue(opened, "the command opened no landing database")
            for connection, path in opened:
                self.assertEqual(path, database)
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

        base = ["--repo", str(self.work)]
        clash = ("land: the contract already sets trunk='main'; change the mode with land.py mode; "
                 "changing trunk or remote needs a new repository contract\n")
        self.assertEqual(call([*base, "status", "E9"]), (1, "", "land: no E9\n"))
        assert_closed()
        with self.fake_gh():
            self.assertEqual(call([*base, "init", "--trunk", "other", "--mode", "auto"]), (1, "", clash))
        assert_closed()

        with land.Store.for_repo(self.work) as store:
            directory = store.dir
            common = store.contract["commonDir"]
            store.db.execute("INSERT OR REPLACE INTO contract VALUES ('commonDir', ?)", (json.dumps(str(self.base)),))
        opened.clear()
        with mock.patch.object(land.sqlite3, "connect", connect):
            with self.assertRaises(land.LandError) as caught:
                land.Store.for_repo(self.work)
        assert_closed()
        self.assertEqual(str(caught.exception), f"store {directory} belongs to {self.base}, not {common}")
        with land.Store(directory) as store:
            store.db.execute("INSERT OR REPLACE INTO contract VALUES ('commonDir', ?)", (json.dumps(common),))

        opened.clear()
        broken = OSError("migration refused")
        with mock.patch.object(land.sqlite3, "connect", connect), \
                mock.patch.object(land, "migrate_entry_ids", side_effect=broken):
            with self.assertRaises(OSError) as failed:
                land.Store(directory)
        self.assertIs(failed.exception, broken)
        assert_closed()

        with self.fake_gh():
            self.assertEqual(call([*base, "init", "--trunk", "main", "--mode", "auto"]),
                             (0, f"updated {directory}\n", ""))
        assert_closed()

    def test_a_store_close_after_a_directory_failure_raises_nothing(self):
        """self.db is assigned before the directory conversion, so a close after that failure is a no-op."""
        store = land.Store.__new__(land.Store)
        with self.assertRaises(TypeError):
            store.__init__(None)
        store.close()
        store.close()

    def test_a_store_without_onto_or_autoincrement_opens_with_both_and_keeps_its_entry(self):
        self.init()
        self.assertEqual(self.queue_one(), "E1")
        self.rebuild_entry_without_autoincrement()
        self.assertNotIn("onto", self.entry_columns())
        self.assertEqual(self.land("status", "E1"), "E1 queued (r/D1, w1)")
        self.assertIn("onto", self.entry_columns())
        with self.raw_db() as db:
            self.assertIn("AUTOINCREMENT", db.execute("SELECT sql FROM sqlite_master WHERE name = 'entry'").fetchone()[0])
            self.assertEqual(db.execute("SELECT id, holder, onto FROM entry").fetchall(), [(1, "r/D1", "")])
