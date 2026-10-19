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
