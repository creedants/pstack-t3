from .support import (
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
from . import support


class LandingTest(LandingTestBase):
    def test_a_refused_command_leaves_cwd_environment_and_argv(self):
        self.init()
        cwd = os.getcwd()
        argv = sys.argv
        contents = list(sys.argv)
        before = dict(os.environ)
        widened = dict(before)
        widened["D231_LEAK_MARK"] = "set"
        code, out, err = drive(
            SCRIPT, ["--repo", str(self.work), "status", "E9"], env=widened, cwd=self.base)
        self.assertEqual((code, out, err), (1, "", "land: no E9\n"))
        self.assertEqual(os.getcwd(), cwd)
        self.assertEqual(dict(os.environ), before)
        self.assertIs(sys.argv, argv)
        self.assertEqual(list(sys.argv), contents)

    def test_an_interrupted_in_process_status_cannot_leave_land_db_open(self):
        self.init()
        database = next((self.base / "state").glob("pstack-t3/landing/*/land.db"))
        argv = ["--repo", str(self.work), "status"]
        env = os.environ.copy()
        fired = {"done": False}

        def _open_land_db(database):
            directory, paths = _fd_paths()
            prefix = os.fspath(database) if directory == "/proc/self/fd" else os.path.realpath(database)
            found = []
            for target in paths.values():
                if target.startswith(prefix):
                    found.append(target)
            for obj in gc.get_objects():
                if not isinstance(obj, sqlite3.Connection):
                    continue
                try:
                    rows = obj.execute("PRAGMA database_list").fetchall()
                except sqlite3.ProgrammingError:
                    continue
                for row in rows:
                    path = row[2]
                    if path and str(path).startswith(prefix):
                        found.append(str(path))
            return found

        def trace(frame, event, arg):
            if fired["done"] or event != "call":
                return trace
            filename = frame.f_globals.get("__file__", "")
            if frame.f_code.co_name == "status" and filename.endswith("drain.py"):
                fired["done"] = True
                raise KeyboardInterrupt
            return trace

        previous = os.environ.get("PSTACK_TEST_REAL_PROCESS")
        os.environ["PSTACK_TEST_REAL_PROCESS"] = "0"
        sys.settrace(trace)
        try:
            with self.assertRaises(KeyboardInterrupt):
                drive(SCRIPT, argv, env=env)
        finally:
            sys.settrace(None)
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous
        self.assertTrue(fired["done"])
        self.assertEqual(_open_land_db(database), [])
        code, out, err = drive(SCRIPT, argv, env=env)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(_open_land_db(database), [])

    @unittest.skipUnless(os.path.isdir("/proc"), "the test lists a real /proc/self/fd as /dev/fd")
    def test_descriptor_cleanup_keeps_a_pipe_whose_path_query_fails(self):
        """Pin that a pipe whose path query fails stays listed, and a later pipe is still cleaned up.

        The F_GETPATH mock raises for a FIFO, which models Darwin's rejection of a non-vnode
        descriptor. The second pipe is opened after the snapshot, and the cleanup closes it.
        """
        read_first, write_first = os.pipe()
        opened = [read_first, write_first]

        def labeled_directory():
            return "/dev/fd", os.open("/proc/self/fd", os.O_RDONLY | os.O_DIRECTORY)

        def get_path(descriptor, cmd, buffer):
            if stat.S_ISFIFO(os.fstat(descriptor).st_mode):
                raise OSError(errno.EINVAL, "Inappropriate ioctl for device")
            return os.readlink(f"/proc/self/fd/{descriptor}").encode()

        try:
            # Linux fcntl has no F_GETPATH, so the seam names its own command.
            with mock.patch.multiple(fcntl, fcntl=get_path, F_GETPATH=9999, create=True), \
                    mock.patch.object(support, "_open_fd_directory", labeled_directory):
                before = _descriptors()
                read_second, write_second = os.pipe()
                opened.extend((read_second, write_second))
                _close_new_fds(before)
            self.assertIn(read_first, before)
            self.assertIn(write_first, before)
            for number in (read_second, write_second):
                with self.assertRaises(OSError):
                    os.fstat(number)
        finally:
            for number in opened:
                try:
                    os.close(number)
                except OSError:
                    pass

    def test_a_retained_interrupt_cannot_close_a_descriptor_a_later_open_reused(self):
        self.init()
        target = next(
            number
            for number, line in enumerate((SCRIPT.parent / "landing_lib" / "drain.py").read_text().splitlines(), 1)
            if line == "    out = seam.Outcome([], [], [], [], [])"
        )
        captured = []
        opened = []

        def trace(frame, event, arg):
            if event != "line" or frame.f_lineno != target or frame.f_code.co_name != "land":
                return trace
            if not frame.f_code.co_filename.endswith("drain.py"):
                return trace
            captured.append(frame.f_locals["handle"].fileno())
            raise KeyboardInterrupt

        previous = os.environ.get("PSTACK_TEST_REAL_PROCESS")
        os.environ["PSTACK_TEST_REAL_PROCESS"] = "0"
        retained = None
        sys.settrace(trace)
        try:
            try:
                drive(SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            except KeyboardInterrupt as error:
                retained = error
            sys.settrace(None)
            self.assertEqual(len(captured), 1)
            self.assertIsNotNone(retained)
            self.assertIsNotNone(retained.__traceback__)
            number = captured[0]
            database = next((self.base / "state").glob("pstack-t3/landing/*/land.db"))
            probe = os.open(database.parent / ".queue.lock", os.O_RDWR)
            opened.append(probe)
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.close(probe)
            matched = None
            while matched is None:
                fd = os.open(self.base / "later-file", os.O_CREAT | os.O_RDWR, 0o600)
                opened.append(fd)
                if fd == number:
                    matched = fd
                elif fd > number:
                    break
            self.assertEqual(matched, number, opened)
            self.assertEqual(os.write(matched, b"before"), 6)
            retained.__traceback__ = None
            retained = None
            gc.collect()
            self.assertEqual(os.fstat(matched).st_size, 6)
            self.assertEqual(os.write(matched, b"after"), 5)
        finally:
            sys.settrace(None)
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous
            for fd in opened:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_an_unrelated_objects_systemexit_cannot_stop_interrupted_descriptor_cleanup(self):
        """Pin review finding D240-review-3 R1. The object is gc tracked and owns no descriptor."""
        self.init()

        class UnrelatedObject:
            @property
            def fileno(self):
                raise SystemExit("unrelated attribute lookup")

        unrelated = UnrelatedObject()
        self.assertTrue(gc.is_tracked(unrelated))

        target = next(
            number
            for number, line in enumerate((SCRIPT.parent / "landing_lib" / "drain.py").read_text().splitlines(), 1)
            if line == "    out = seam.Outcome([], [], [], [], [])"
        )
        captured = []
        opened = []

        def trace(frame, event, arg):
            if event != "line" or frame.f_lineno != target or frame.f_code.co_name != "land":
                return trace
            if not frame.f_code.co_filename.endswith("drain.py"):
                return trace
            captured.append(frame.f_locals["handle"].fileno())
            raise KeyboardInterrupt

        previous = os.environ.get("PSTACK_TEST_REAL_PROCESS")
        os.environ["PSTACK_TEST_REAL_PROCESS"] = "0"
        retained = None
        propagated = None
        sys.settrace(trace)
        try:
            try:
                drive(SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            except BaseException as error:
                propagated = error
            sys.settrace(None)
            self.assertIsInstance(propagated, KeyboardInterrupt)
            retained = propagated
            self.assertEqual(len(captured), 1)
            database = next((self.base / "state").glob("pstack-t3/landing/*/land.db"))
            probe = os.open(database.parent / ".queue.lock", os.O_RDWR)
            opened.append(probe)
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            retained.__traceback__ = None
            retained = None
        finally:
            sys.settrace(None)
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous
            for fd in opened:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_a_dead_weakref_proxy_cannot_stop_interrupted_descriptor_cleanup(self):
        self.init()

        class Referent:
            pass

        referent = Referent()
        proxy = weakref.proxy(referent)
        del referent

        target = next(
            number
            for number, line in enumerate((SCRIPT.parent / "landing_lib" / "drain.py").read_text().splitlines(), 1)
            if line == "    out = seam.Outcome([], [], [], [], [])"
        )
        captured = []
        opened = []

        def trace(frame, event, arg):
            if event != "line" or frame.f_lineno != target or frame.f_code.co_name != "land":
                return trace
            if not frame.f_code.co_filename.endswith("drain.py"):
                return trace
            captured.append(frame.f_locals["handle"])
            raise KeyboardInterrupt

        previous = os.environ.get("PSTACK_TEST_REAL_PROCESS")
        os.environ["PSTACK_TEST_REAL_PROCESS"] = "0"
        retained = None
        sys.settrace(trace)
        try:
            try:
                drive(SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            except KeyboardInterrupt as error:
                retained = error
            sys.settrace(None)
            self.assertEqual(len(captured), 1)
            self.assertIsInstance(retained, KeyboardInterrupt)
            self.assertTrue(captured[0].closed)
            database = next((self.base / "state").glob("pstack-t3/landing/*/land.db"))
            probe = os.open(database.parent / ".queue.lock", os.O_RDWR)
            opened.append(probe)
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            retained.__traceback__ = None
            retained = None
        finally:
            sys.settrace(None)
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous
            for fd in opened:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_an_in_process_owner_line_cannot_ignore_pythonioencoding(self):
        self.init()
        prefix = "caf\u00e9/"
        previous = os.environ.get("PSTACK_TEST_REAL_PROCESS")
        os.environ["PSTACK_TEST_REAL_PROCESS"] = "0"
        try:
            for generation, encoding, expected in (
                ("1", "ascii", (1, "")),
                ("2", "ascii:replace", (0, "caf?/ at generation 2\n")),
            ):
                with self.subTest(encoding=encoding):
                    argv = ["--repo", str(self.work), "owner", "--prefix", prefix,
                            "--generation", generation]
                    env = os.environ.copy()
                    env["PYTHONIOENCODING"] = encoding
                    code, out, _err = drive(SCRIPT, argv, env=env)
                    child = real_process(ProcessReason.INHERITED_ENV, SCRIPT, argv, env=env)
                    self.assertEqual((code, out), (child.returncode, child.stdout))
                    self.assertEqual((code, out), expected)
        finally:
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous

    def test_a_fresh_interpreter_prints_the_owner_line_when_pythonutf8_overrides_locale_c(self):
        self.init()
        argv = ["--repo", str(self.work), "owner", "--prefix", "caf\u00e9/", "--generation", "2"]
        env = os.environ.copy()
        env.pop("PYTHONIOENCODING", None)
        env.update({"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "1", "PYTHONCOERCECLOCALE": "0"})
        child = real_process(ProcessReason.INHERITED_ENV, SCRIPT, argv, env=env)
        self.assertEqual((child.returncode, child.stdout), (0, "caf\u00e9/ at generation 2\n"))

    def test_a_land_run_runs_no_subprocess_inside_a_write_transaction(self):
        self.init()
        self.queue_one()
        hits = self.subprocesses_during_land()
        self.assertEqual(self.origin_log()[0], "w1")
        self.assertIn("E1 landed", self.land("status", "E1"))
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
