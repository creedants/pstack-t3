from .support import (
    setUpModule,
    LandingTest as LandingTestBase,
    _darwin_eperm_main,
    _run,
    ast,
    BACKGROUND_CHILD,
    CAP_CLOCK_MAIN,
    cap_line,
    contextlib,
    errno,
    EXCLUSIVE_MAIN,
    EXCLUSIVE_MAIN_WITHOUT_WAITID,
    fcntl,
    GROUP_SLEEP,
    IGNORING_GROUP,
    INTERRUPT_AFTER_THE_LEADER_EXITS,
    io,
    json,
    land,
    mock,
    OFD_SLOT_SCRIPT,
    os,
    Path,
    PREVIOUS_SLOT_SCRIPT,
    ProcessReason,
    re,
    real_popen,
    real_process,
    RECORD_REPO_SCRIPT,
    REPORT_SCRIPT,
    REVIEWER,
    SCRIPT,
    sh,
    signal,
    SlotCallers,
    SLOW_PUBLISH_MAIN,
    subprocess,
    SURVIVING_IGNORING_GROUP,
    sys,
    threading,
    time,
    unittest,
    WAIT_SCRIPT,
    _darwin_eperm_main,
)


class LandingTest(LandingTestBase):
    def wait_for_pid(self, path, timeout=10):
        """Return the pid in path once the whole file text parses as one int.

        Path.write_text opens and truncates the file before it writes, so a reader that
        trusts the file's existence can see an empty file. An empty, missing, or
        non-integer file is not ready.
        """
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                text = path.read_text()
            except OSError:
                text = None
            if text is not None:
                last = text
                try:
                    return int(text)
                except ValueError:
                    pass
            time.sleep(0.02)
        self.fail(f"{path} held no whole pid in {timeout}s: {last!r}")

    def test_a_pid_file_read_waits_past_an_empty_file_the_writer_fills_later(self):
        path = self.base / "empty-then-filled.pid"
        path.write_text("")
        filler = threading.Timer(0.2, path.write_text, args=("12345",))
        filler.start()
        try:
            self.assertEqual(self.wait_for_pid(path), 12345)
        finally:
            filler.cancel()

    def test_an_exclusive_slot_waits_for_every_other_slot_and_refuses_nesting(self):
        import time
        governor = self.base / "state/pstack-t3/governor"
        governor.mkdir(parents=True)
        (governor / "governor.json").write_text(json.dumps({"slots": 2}))
        marks = self.base / "marks"
        # land.py's slot gate reads LAND_SLOT. The queue sets it around this suite, so the
        # subprocesses under test must not inherit it. The nested command still sets it itself.
        env = {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}
        worker = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, str(SCRIPT), "slot", "--", "sh", "-c", f"echo worker-start >> {marks}; sleep 2; echo worker-end >> {marks}"],
            env=env, stdout=None, stderr=None)
        time.sleep(0.5)
        bench = real_process(
            ProcessReason.GOVERNOR, SCRIPT,
            ["slot", "--exclusive", "--", "sh", "-c", f"echo bench >> {marks}"],
            env=env, timeout=30)
        worker.wait(timeout=30)
        self.assertEqual(bench.returncode, 0, bench.stderr)
        self.assertEqual(marks.read_text().split(), ["worker-start", "worker-end", "bench"])
        nested = real_process(
            ProcessReason.GOVERNOR, SCRIPT,
            ["slot", "--", sys.executable, str(SCRIPT), "slot", "--exclusive", "--", "true"],
            env=env, timeout=30)
        self.assertNotEqual(nested.returncode, 0)
        self.assertIn("must be the outermost slot", nested.stderr)

    def test_nested_slots_do_not_deadlock_with_one_slot(self):
        governor = self.base / "state/pstack-t3/governor"
        governor.mkdir(parents=True)
        (governor / "governor.json").write_text(json.dumps({"slots": 1}))
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT,
            ["slot", "--", sys.executable, str(SCRIPT), "slot", "--", "true"],
            env=os.environ.copy(), timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_slots_span_directories_and_leave_landing_capacity(self):
        import signal

        governor = self.base / "state/pstack-t3/governor"
        governor.mkdir(parents=True)
        (governor / "governor.json").write_text(json.dumps({"slots": 2}))
        env = {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}
        directories = [self.base / f"command-{index}" for index in range(3)]
        releases = [self.base / f"release-{index}" for index in range(3)]
        retry = self.base / "retry"
        handles = []
        processes = []

        def wait_for(path):
            deadline = time.monotonic() + 20
            while not path.exists():
                if time.monotonic() >= deadline:
                    self.fail(f"barrier not reached: {path}")
                time.sleep(0.01)

        command = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "release = os.open(sys.argv[1], os.O_RDONLY)\n"
            "Path('started').touch()\n"
            "assert os.read(release, 1) == b'x'\n"
            "os.close(release)\n"
            "Path('finished').touch()\n"
        )
        third = (
            "import os, sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
            "import land\n"
            "count = 0\n"
            "def blocked(seconds):\n"
            "    global count\n"
            "    count += 1\n"
            "    gate = os.open(sys.argv[1], os.O_RDONLY)\n"
            "    Path(f'waiting-{count}').touch()\n"
            "    assert os.read(gate, 1) == b'x'\n"
            "    os.close(gate)\n"
            "land.time.sleep = blocked\n"
            "sys.exit(land.main(sys.argv[2:]))\n"
        )
        try:
            for path in [*releases, retry]:
                os.mkfifo(path)
                # Keep both ends open so a barrier write never waits for a reader.
                handles.append(os.open(path, os.O_RDWR | os.O_NONBLOCK))
            for directory in directories:
                directory.mkdir()
            for index in range(2):
                processes.append(real_popen(
                    ProcessReason.GOVERNOR,
                    [sys.executable, str(SCRIPT), "slot", "--",
                     sys.executable, "-c", command, str(releases[index])],
                    env=env, cwd=directories[index], start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                wait_for(directories[index] / "started")
            processes.append(real_popen(
                ProcessReason.GOVERNOR,
                [sys.executable, "-c", third, str(retry), "slot", "--",
                 sys.executable, "-c", command, str(releases[2])],
                env=env, cwd=directories[2], start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for(directories[2] / "waiting-1")
            self.assertFalse((directories[2] / "started").exists())
            os.write(handles[3], b"x")
            wait_for(directories[2] / "waiting-2")
            self.assertFalse((directories[2] / "started").exists())
            self.assertTrue(all(process.poll() is None for process in processes))

            landing_mark = self.base / "landing-progress"
            probe = (
                "import sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
                "import land\n"
                "with land.slot('landing'):\n"
                f"    Path({str(landing_mark)!r}).touch()\n"
            )
            result = _run(
                [sys.executable, "-c", probe], env=env, timeout=20, reason=ProcessReason.GOVERNOR)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(landing_mark.exists())
            self.assertFalse((directories[2] / "started").exists())

            os.write(handles[0], b"x")
            self.assertEqual(processes[0].wait(timeout=20), 0)
            self.assertTrue((directories[0] / "finished").exists())
            os.write(handles[3], b"x")
            wait_for(directories[2] / "started")
            self.assertIsNone(processes[1].poll())
            for index in (1, 2):
                os.write(handles[index], b"x")
                self.assertEqual(processes[index].wait(timeout=20), 0)
                self.assertTrue((directories[index] / "finished").exists())
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=20)
            for handle in handles:
                os.close(handle)

    def test_a_slot_alone_returns_the_command_status_and_prints_nothing(self):
        env = {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}

        def slot(*arguments):
            result = real_process(
                ProcessReason.GOVERNOR, SCRIPT, ["slot", *arguments], env=env, timeout=20)
            return result.returncode, result.stdout, result.stderr

        self.assertEqual(slot("--", "sh", "-c", "exit 7"), (7, "", ""))
        self.assertEqual(slot("--exclusive", "--", "true"), (0, "", ""))
        self.assertEqual(slot(), (1, "", "land: slot needs a command after --\n"))

    def test_a_file_finder_leaves_among_the_tickets_does_not_stop_a_slot(self):
        tickets = self.base / "state/pstack-t3/governor/tickets"
        tickets.mkdir(parents=True)
        (tickets / ".DS_Store").write_bytes(b"\0Bud1")
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"], env=self.slot_env(), timeout=20)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), [".DS_Store"])

    def test_a_ticket_shaped_fifo_directory_or_symlink_does_not_stop_a_slot(self):
        outside = self.base / "outside.pipe"
        os.mkfifo(outside)
        failures = []

        def admit(name, setup):
            home = self.base / "homes" / name
            tickets = home / "pstack-t3/governor/tickets"
            tickets.mkdir(parents=True)
            setup(tickets)
            env = {**self.slot_env(), "XDG_STATE_HOME": str(home)}
            try:
                result = real_process(
                    ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"], env=env, timeout=20)
            except subprocess.TimeoutExpired:
                failures.append(f"{name} timed out")
                return
            if (result.returncode, result.stderr) != (0, ""):
                tail = result.stderr.strip().splitlines()[-1] if result.stderr else ""
                failures.append(f"{name} rc={result.returncode} {tail}")

        admit("fifo", lambda tickets: os.mkfifo(tickets / "1-worker"))
        admit("directory", lambda tickets: (tickets / "1-worker").mkdir())
        admit("symlink", lambda tickets: (tickets / "1-worker").symlink_to(outside))
        self.assertEqual(failures, [])
        for name in ("fifo", "directory", "symlink"):
            left = self.base / "homes" / name / "pstack-t3/governor/tickets"
            self.assertEqual([path.name for path in left.iterdir()], [], name)
        self.assertTrue(outside.is_fifo())

        slots = SlotCallers(self, 1)
        tickets = slots.governor / "tickets"
        tickets.mkdir()
        directory = tickets / "1-worker"
        directory.mkdir()
        (directory / "child").write_text("stay")
        holder = slots.enter(slots.start("holder"))
        waiter = slots.queued(slots.start("waiter", marked=True))
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["1-worker", "2-worker"])
        self.assertTrue((tickets / "2-worker").is_file())
        slots.release(holder)
        slots.release(slots.enter(waiter))
        self.assertEqual([path.name for path in tickets.iterdir()], ["1-worker"])
        self.assertEqual((directory / "child").read_text(), "stay")

    def test_a_slot_probe_on_a_read_only_ticket_keeps_a_held_ticket_and_removes_a_free_one(self):
        tickets = self.base / "probe-tickets"
        tickets.mkdir()
        held_path = tickets / "1-worker"
        free_path = tickets / "2-worker"
        held_path.write_bytes(b"")
        free_path.write_bytes(b"")
        holder = real_popen(
            ProcessReason.LOCK,
            [sys.executable, "-c",
             "import fcntl, os, sys, time\n"
             "fd = os.open(sys.argv[1], os.O_RDWR)\n"
             "fcntl.flock(fd, fcntl.LOCK_EX)\n"
             "sys.stdout.write('held\\n')\n"
             "sys.stdout.flush()\n"
             "time.sleep(60)\n",
             str(held_path)],
            env=None, start_new_session=True)

        def stop():
            if holder.poll() is None:
                os.killpg(holder.pid, signal.SIGKILL)
            holder.wait(timeout=20)
            holder.stdout.close()
            holder.stderr.close()

        self.addCleanup(stop)
        self.assertEqual(holder.stdout.readline(), "held\n")
        recorded = []
        real = land.fcntl.flock

        def nfs_flock(handle, operation):
            descriptor = handle.fileno()
            read_only = (fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE) == os.O_RDONLY
            exclusive = (operation & (fcntl.LOCK_SH | fcntl.LOCK_EX | fcntl.LOCK_UN)) == fcntl.LOCK_EX
            if read_only and exclusive:
                raise OSError(errno.EBADF, "Bad file descriptor")
            recorded.append((read_only, operation))
            return real(handle, operation)

        with mock.patch.object(land.fcntl, "flock", nfs_flock):
            found = land._waiting(tickets, None)
        self.assertEqual(found, [(1, "worker")])
        self.assertEqual([path.name for path in tickets.iterdir()], ["1-worker"])
        shared = fcntl.LOCK_SH | fcntl.LOCK_NB
        self.assertEqual(recorded, [(True, shared), (True, shared)])

    def test_a_ticket_hard_linked_to_the_turn_mutex_does_not_stop_a_slot(self):
        governor = self.base / "state/pstack-t3/governor"
        tickets = governor / "tickets"
        tickets.mkdir(parents=True)
        lock = governor / "tickets.lock"
        lock.write_bytes(b"")
        os.link(lock, tickets / "1-worker")
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"],
            env=self.slot_env(), timeout=20)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual([path.name for path in tickets.iterdir()], [])
        self.assertEqual(lock.stat().st_nlink, 1)

    def test_a_sigkill_waiter_under_umask_0o444_does_not_stop_later_callers(self):
        """The waiter is its own process so the test can set that process's umask and kill it with SIGKILL."""
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        waiter = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c",
             "import os, sys\n"
             "sys.path.insert(0, sys.argv[1])\n"
             "import land\n"
             "os.umask(0o444)\n"
             "sys.exit(land.main(['slot', '--', 'true']))\n",
             str(SCRIPT.parent)],
            env=slots.env, stdout=None, stderr=subprocess.PIPE, start_new_session=True)

        def stop():
            if waiter.poll() is None:
                os.kill(waiter.pid, signal.SIGKILL)
                waiter.wait(timeout=20)
            waiter.stderr.close()

        self.addCleanup(stop)
        ticket = slots.governor / "tickets" / "1-worker"
        slots.wait(lambda: ticket.exists(), "the restricted-umask waiter created its ticket")
        mode = ticket.stat().st_mode & 0o777
        os.kill(waiter.pid, signal.SIGKILL)
        waiter.wait(timeout=20)
        slots.release(holder)
        statuses = []
        for _ in range(2):
            result = real_process(
                ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"],
                env=slots.env, timeout=20)
            statuses.append(result.returncode)
        self.assertEqual((bool(mode & 0o400), statuses), (True, [0, 0]), oct(mode))

    def test_a_ticket_name_hard_linked_to_an_unreadable_file_does_not_stop_a_slot(self):
        """The slot command is a process because the test asserts its exit status."""
        tickets = self.base / "state/pstack-t3/governor/tickets"
        tickets.mkdir(parents=True)
        outside = self.base / "outside-unreadable"
        outside.write_bytes(b"keep")
        outside.chmod(0)
        os.link(outside, tickets / "1-worker")
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"],
            env=self.slot_env(), timeout=20)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual([path.name for path in tickets.iterdir()], [])
        self.assertEqual(outside.stat().st_nlink, 1)
        outside.chmod(0o600)
        self.assertEqual(outside.read_bytes(), b"keep")

    def test_two_names_of_one_live_ticket_do_not_keep_a_free_landing_caller_waiting(self):
        """The waiter and the landing caller are processes so the test can order their attempts."""
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        waiter = slots.start("waiter", stepped=True)
        self.assertEqual(slots.settle(waiter), "waiting")
        tickets = slots.governor / "tickets"
        os.link(tickets / "1-worker", tickets / "2-exclusive")
        mutex = open(slots.governor / "tickets.lock", "a")
        self.addCleanup(mutex.close)
        fcntl.flock(mutex, fcntl.LOCK_EX)
        try:
            found = land._waiting(tickets, None)
        finally:
            fcntl.flock(mutex, fcntl.LOCK_UN)
        slots.release(holder)
        landing = slots.start("landing", pool="landing", stepped=True)
        landing_state = slots.settle(landing)
        self.assertEqual((found, landing_state), ([], "started"))
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), [])
        self.assertEqual(slots.step(waiter), "started")
        held = {}
        for word, name, *files in slots.events():
            if word == "exit":
                held.pop(name, None)
                continue
            own = set(files)
            for other, theirs in held.items():
                self.assertEqual(own & theirs, set(), f"{name} and {other}")
            held[name] = own
        slots.release(landing)
        slots.release(waiter)

    def test_a_slot_runs_when_its_ticket_is_removed_while_it_waits(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        waiter = slots.start("waiter", stepped=True)
        self.assertEqual(slots.settle(waiter), "waiting")
        names = sorted(path.name for path in (slots.governor / "tickets").iterdir())
        self.assertEqual(names, ["1-worker"])
        (slots.governor / "tickets" / names[0]).unlink()
        slots.release(holder)
        self.assertEqual(slots.step(waiter), "started")
        slots.release(waiter)
        self.assertEqual(slots.entries(), ["holder", "waiter"])

    def test_a_displaced_exclusive_that_retries_while_the_slot_is_busy_keeps_later_callers_waiting(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        tickets = slots.governor / "tickets"
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["1-exclusive"])
        (tickets / "1-exclusive").unlink()
        self.assertEqual(slots.step(exclusive), "waiting")
        self.assertEqual([path.stat().st_nlink for path in tickets.iterdir()], [1])
        ordinary = slots.start("ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(ordinary), "waiting")
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)
        self.assertEqual(slots.step(ordinary), "started")
        slots.release(ordinary)
        self.assertEqual(slots.entries(), ["holder", "exclusive", "ordinary"])
        self.assertEqual(slots.peak(), 1)
        held = {}
        for word, name, *files in slots.events():
            if word == "exit":
                held.pop(name, None)
                continue
            own = set(files)
            for other, theirs in held.items():
                self.assertEqual(own & theirs, set(), f"{name} and {other}")
            held[name] = own

    def test_a_number_2_retry_with_a_number_3_alias_cannot_keep_a_landing_caller_waiting(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        first = slots.start("first", stepped=True)
        self.assertEqual(slots.settle(first), "waiting")
        second = slots.start("second", stepped=True)
        self.assertEqual(slots.settle(second), "waiting")
        tickets = slots.governor / "tickets"
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["1-worker", "2-worker"])
        os.link(tickets / "1-worker", tickets / "3-exclusive")
        self.assertEqual(slots.step(second), "waiting")
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["2-worker"])
        self.assertEqual((tickets / "2-worker").stat().st_nlink, 1)
        slots.release(holder)
        landing = slots.start("landing", pool="landing", stepped=True)
        self.assertEqual(slots.settle(landing), "started")
        self.assertFalse(first.started)
        self.assertFalse(second.started)
        slots.release(landing)
        self.assertEqual(slots.step(second), "started")
        slots.release(second)
        self.assertEqual(slots.step(first), "started")
        slots.release(first)
        self.assertEqual(slots.entries(), ["holder", "landing", "second", "first"])
        self.assertEqual(slots.peak(), 1)
        held = {}
        for word, name, *files in slots.events():
            if word == "exit":
                held.pop(name, None)
                continue
            own = set(files)
            for other, theirs in held.items():
                self.assertEqual(own & theirs, set(), f"{name} and {other}")
            held[name] = own

    def test_a_filesystem_that_refuses_the_turn_mutex_flock_does_not_run_the_command(self):
        self.governor(1)
        marker = self.base / "ran"
        real = fcntl.flock

        def slot_status(refused, code):
            def flock(handle, operation):
                if operation == refused:
                    raise OSError(code, os.strerror(code))
                return real(handle, operation)

            stderr = io.StringIO()
            with mock.patch.dict(os.environ), mock.patch.object(land.fcntl, "flock", flock), \
                    contextlib.redirect_stderr(stderr):
                os.environ.pop("LAND_SLOT", None)
                return land.main(["slot", "--", "touch", str(marker)]), stderr.getvalue()

        for code in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOLCK):
            self.assertEqual(
                slot_status(fcntl.LOCK_EX, code),
                (1, "land: this filesystem does not support the slot governor's ordering lock\n"))
        with self.assertRaises(OSError) as raised:
            slot_status(fcntl.LOCK_EX | fcntl.LOCK_NB, errno.ENOLCK)
        self.assertEqual(raised.exception.errno, errno.ENOLCK)
        self.assertFalse(marker.exists())
        self.assert_slots_free()

    def test_a_hold_names_its_slot_files_and_a_nested_slot_changes_neither_variable(self):
        self.governor(2)

        def seen():
            return os.environ.get("LAND_SLOT"), os.environ.get("LAND_SLOT_FILES")

        with mock.patch.dict(os.environ):
            os.environ.pop("LAND_SLOT", None)
            os.environ.pop("LAND_SLOT_FILES", None)
            with land.slot("worker"):
                self.assertEqual(seen(), ("worker", "worker-0"))
                with land.slot("landing"):
                    self.assertEqual(seen(), ("worker", "worker-0"))
                self.assertEqual(seen(), ("worker", "worker-0"))
            self.assertEqual(seen(), (None, None))
            with land.slot("worker", count=2):
                self.assertEqual(seen(), ("worker", "worker-0 worker-1"))
                with land.slot("worker", count=2):
                    self.assertEqual(seen(), ("worker", "worker-0 worker-1"))
                self.assertEqual(seen(), ("worker", "worker-0 worker-1"))
            self.assertEqual(seen(), (None, None))
            with land.slot("landing"):
                self.assertEqual(seen(), ("landing", "landing-0"))
            with land.exclusive_slot():
                self.assertEqual(seen(), ("exclusive", "landing-0 worker-0 worker-1"))
            self.assertEqual(seen(), (None, None))

    def test_an_earlier_ordinary_waiter_runs_before_a_later_exclusive(self):
        slots = SlotCallers(self, 2)
        holders = [slots.enter(slots.start(f"holder-{index}")) for index in range(2)]
        ordinary = slots.start("ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        for holder in holders:
            slots.release(holder)
        self.assertEqual(slots.step(exclusive), "waiting")
        self.assertEqual(slots.step(ordinary), "started")
        self.assertEqual(slots.step(exclusive), "waiting")
        slots.release(ordinary)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)
        self.assertEqual(slots.entries(), ["holder-0", "holder-1", "ordinary", "exclusive"])

    def test_an_earlier_exclusive_waiter_runs_before_a_later_ordinary_once_every_slot_is_free(self):
        slots = SlotCallers(self, 2)
        holders = [slots.enter(slots.start(f"holder-{index}")) for index in range(2)]
        holders.append(slots.enter(slots.start("landing", pool="landing")))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        ordinary = slots.start("ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        for holder in holders:
            slots.release(holder)
        self.assertEqual(slots.step(ordinary), "waiting")
        self.assertEqual(slots.step(exclusive), "started")
        self.assertEqual(slots.step(ordinary), "waiting")
        slots.release(exclusive)
        self.assertEqual(slots.step(ordinary), "started")
        slots.release(ordinary)
        self.assertEqual(slots.entries(), ["holder-0", "holder-1", "landing", "exclusive", "ordinary"])

    def test_an_ordinary_caller_takes_a_free_worker_slot_while_an_exclusive_waits(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        ordinary = slots.enter(slots.start("ordinary"))
        self.assertFalse(exclusive.started)
        self.assertEqual(slots.step(exclusive), "waiting")
        slots.release(holder)
        slots.release(ordinary)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)
        self.assertEqual(slots.entries(), ["holder", "ordinary", "exclusive"])

    def test_an_exclusive_waiter_holds_no_slot_lock_while_it_waits(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        free = []
        for name in ("landing-0", "worker-0", "worker-1"):
            with open(slots.governor / name, "a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                free.append(name)
        self.assertEqual(free, ["landing-0", "worker-1"])
        slots.release(holder)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)

    def test_a_two_slot_caller_behind_one_busy_slot_holds_nothing(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        wide = slots.start("wide", "--slots", "2", stepped=True)
        self.assertEqual(slots.settle(wide), "waiting")
        free = []
        for name in ("landing-0", "worker-0", "worker-1"):
            with open(slots.governor / name, "a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                free.append(name)
        self.assertEqual(free, ["landing-0", "worker-1"])
        self.assertEqual(os.listdir(slots.governor / "tickets"), ["1-worker"])
        slots.release(holder)
        self.assertEqual(slots.step(wide), "started")
        slots.release(wide)

    def test_a_later_one_slot_caller_does_not_pass_an_earlier_two_slot_ticket(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        wide = slots.start("wide", "--slots", "2", stepped=True)
        self.assertEqual(slots.settle(wide), "waiting")
        narrow = slots.start("narrow", stepped=True)
        self.assertEqual(slots.settle(narrow), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(wide), "started")
        self.assertIn(("enter", "wide", "worker-0", "worker-1"), slots.events())
        self.assertEqual(slots.step(narrow), "waiting")
        slots.release(wide)
        self.assertEqual(slots.step(narrow), "started")
        slots.release(narrow)
        self.assertEqual(slots.entries(), ["holder", "wide", "narrow"])

    def test_slots_over_the_governor_are_refused_and_the_command_does_not_run(self):
        directory = self.governor(2)
        marker = self.base / "ran"

        def slot_status(*flags):
            stderr = io.StringIO()
            with mock.patch.dict(os.environ), contextlib.redirect_stderr(stderr):
                os.environ.pop("LAND_SLOT", None)
                return land.main(["slot", *flags, "--", "touch", str(marker)]), stderr.getvalue()

        integer = "land: --slots must be a positive integer\n"
        self.assertEqual(slot_status("--slots", "3"),
                         (1, "land: --slots 3 is more than the 2 worker slots this governor has\n"))
        self.assertEqual(slot_status("--slots", "0"), (1, integer))
        self.assertEqual(slot_status("--slots", "-1"), (1, integer))
        self.assertEqual(slot_status("--slots", "nope"), (1, integer))
        self.assertEqual(slot_status("--slots", "2", "--exclusive"),
                         (1, "land: --slots cannot be combined with --exclusive\n"))
        self.assertFalse(marker.exists())
        self.assertFalse((directory / "worker-0").exists())
        with mock.patch.dict(os.environ, {"LAND_SLOT": "worker", "LAND_SLOT_FILES": "worker-0"}):
            with land.slot("worker", count=3):
                self.assertEqual(os.environ["LAND_SLOT_FILES"], "worker-0")
        self.assertFalse((directory / "worker-0").exists())

    def test_a_waiter_killed_with_sigkill_does_not_block_a_later_caller(self):
        slots = SlotCallers(self, 1)

        def kill(caller):
            self.assertFalse(caller.started)
            os.kill(caller.process.pid, signal.SIGKILL)
            self.assertEqual(caller.process.wait(timeout=20), -signal.SIGKILL)

        holder = slots.enter(slots.start("holder"))
        kill(slots.queued(slots.start("ticketed", marked=True)))
        slots.release(holder)
        slots.release(slots.enter(slots.start("after-ticketed")))
        self.assertEqual(list((slots.governor / "tickets").iterdir()), [])

        with open(slots.governor / "tickets.lock", "a") as mutex:
            fcntl.flock(mutex, fcntl.LOCK_EX)
            blocked = slots.start("blocked", marked=True)
            slots.wait(lambda: (blocked.directory / "blocking").exists(), "the waiter blocks on the turn mutex")
            kill(blocked)
        slots.release(slots.enter(slots.start("after-blocked")))
        self.assertEqual(slots.entries(), ["holder", "after-ticketed", "after-blocked"])

    def test_many_callers_never_exceed_the_slot_count(self):
        slots = SlotCallers(self, 2)
        waiting = [slots.start(f"worker-{index}", marked=True) for index in range(5)]
        while waiting:
            expected = min(2, len(waiting))
            slots.wait(lambda: sum(caller.started for caller in waiting) >= expected
                       and all(caller.started or (caller.directory / "waiting-1").exists() for caller in waiting),
                       "the slots are full and the rest wait")
            running = [caller for caller in waiting if caller.started]
            self.assertEqual(len(running), expected)
            slots.release(running[0])
            waiting.remove(running[0])

        exclusive = slots.enter(slots.start("exclusive", "--exclusive"))
        waiting = [slots.queued(slots.start(f"later-{index}", marked=True)) for index in range(3)]
        waiting.append(slots.queued(slots.start("landing", pool="landing", marked=True)))
        self.assertEqual([caller.name for caller in waiting if caller.started], [])
        slots.release(exclusive)
        while waiting:
            slots.wait(lambda: any(caller.started for caller in waiting), "a waiting caller started")
            running = next(caller for caller in waiting if caller.started)
            slots.release(running)
            waiting.remove(running)

        running, peak = set(), 0
        for word, name, *_ in slots.events():
            if word == "exit":
                running.remove(name)
                continue
            running.add(name)
            peak = max(peak, len(running - {"landing"}))
            if "exclusive" in running:
                self.assertEqual(running, {"exclusive"})
        self.assertEqual(peak, 2)
        self.assertEqual(len(slots.entries()), 10)

    def test_contending_callers_never_share_a_slot_and_never_pass_an_earlier_ticket(self):
        kinds = ["worker", "exclusive", "landing", "worker", "landing", "exclusive", "worker", "worker", "landing", "exclusive"]
        self.assertEqual(self.contend(1, kinds, hold=0.1), 1)
        self.admit_latest_first(1, kinds)

    def test_contending_callers_fill_two_worker_slots_and_never_pass_an_earlier_ticket(self):
        kinds = ["worker", "worker", "landing", "exclusive", "worker", "landing", "worker", "worker", "exclusive", "worker"]
        self.assertEqual(self.contend(2, kinds, hold=1.5), 2)
        self.admit_latest_first(2, kinds)

    def test_an_ofd_caller_and_a_flock_caller_reorder_without_sharing_a_slot(self):
        if not self.ofd_locks_exist():
            self.skipTest("this platform does not provide OFD locks")
        ofd = self.base / "ofd/land.py"
        ofd.parent.mkdir()
        ofd.write_text(OFD_SLOT_SCRIPT)
        slots = SlotCallers(self, 1)

        def parked(name, *flags, script=SCRIPT):
            caller = slots.start(name, *flags, script=script, stepped=True)
            self.assertEqual(slots.settle(caller), "waiting", name)
            return caller

        def later_waiter_first(holder, earlier, later):
            slots.release(holder)
            self.assertEqual(slots.step(later), "started", later.name)
            self.assertEqual(slots.step(earlier), "waiting", earlier.name)
            slots.release(later)
            self.assertEqual(slots.step(earlier), "started", earlier.name)
            slots.release(earlier)

        def worker_behind_exclusive(exclusive, worker):
            self.assertEqual(slots.step(worker), "waiting", worker.name)
            slots.release(exclusive)
            self.assertEqual(slots.step(worker), "started", worker.name)
            slots.release(worker)

        later_waiter_first(slots.enter(slots.start("ofd-holder", script=ofd)),
                           parked("ofd-earlier", script=ofd), parked("flock-later"))
        later_waiter_first(slots.enter(slots.start("flock-holder")),
                           parked("flock-earlier"), parked("ofd-later", script=ofd))
        worker_behind_exclusive(slots.enter(slots.start("flock-exclusive", "--exclusive")),
                                parked("ofd-worker", script=ofd))
        worker_behind_exclusive(slots.enter(slots.start("ofd-exclusive", "--exclusive", script=ofd)),
                                parked("flock-worker"))
        self.assertEqual(slots.entries(), [
            "ofd-holder", "flock-later", "ofd-earlier", "flock-holder", "ofd-later", "flock-earlier",
            "flock-exclusive", "ofd-worker", "ofd-exclusive", "flock-worker"])
        self.assertEqual(slots.peak(), 1)

        slots = SlotCallers(self, 2)
        holders = [slots.enter(slots.start("ofd-holder", script=ofd)), slots.enter(slots.start("flock-holder"))]
        workers = []
        for index in range(3):
            workers.append(slots.queued(slots.start(f"ofd-{index}", script=ofd, marked=True, hold=0.2)))
            workers.append(slots.queued(slots.start(f"flock-{index}", marked=True, hold=0.2)))
        for holder in holders:
            slots.release(holder)
        for worker in workers:
            self.assertEqual(worker.process.wait(timeout=120), 0, worker.name)
        self.assertEqual(len(slots.entries()), 8)
        self.assertEqual(slots.peak(), 2)

    def test_an_ofd_caller_sees_every_file_of_a_two_slot_hold(self):
        if not self.ofd_locks_exist():
            self.skipTest("this platform does not provide OFD locks")
        ofd = self.base / "ofd/land.py"
        ofd.parent.mkdir()
        ofd.write_text(OFD_SLOT_SCRIPT)
        slots = SlotCallers(self, 2)
        wide = slots.enter(slots.start("wide", "--slots", "2"))
        narrow = slots.start("ofd-narrow", script=ofd, stepped=True)
        self.assertEqual(slots.settle(narrow), "waiting")
        self.assertEqual(slots.step(narrow), "waiting")
        slots.release(wide)
        self.assertEqual(slots.step(narrow), "started")
        slots.release(narrow)
        self.assertEqual(slots.entries(), ["wide", "ofd-narrow"])
        self.assertEqual(slots.peak(), 1)

    def test_an_old_slot_holder_blocks_a_new_caller_and_the_reverse(self):
        slots = SlotCallers(self, 1)
        old = self.base / "old/land.py"
        old.parent.mkdir()
        old.write_text(PREVIOUS_SLOT_SCRIPT)

        holder = slots.enter(slots.start("old-holder", script=old))
        ordinary = slots.start("new-ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        exclusive = slots.start("new-exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        self.assertEqual(slots.step(ordinary), "waiting")
        self.assertEqual(slots.step(exclusive), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(ordinary), "started")
        slots.release(ordinary)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)

        holder = slots.enter(slots.start("new-holder"))
        waiter = slots.start("old-waiter", script=old, stepped=True)
        self.assertEqual(slots.settle(waiter), "waiting")
        self.assertEqual(slots.step(waiter), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(waiter), "started")
        slots.release(waiter)
        self.assertEqual(slots.entries(), ["old-holder", "new-ordinary", "new-exclusive", "new-holder", "old-waiter"])

    def test_a_running_holder_is_released_before_the_fixture_directory_goes(self):
        self.governor(1)
        holders = []

        def check():
            self.assertEqual(holders[0].returncode, 0)
            self.assertTrue((self.base / "holder-worker" / "release").exists())

        self.addCleanup(check)
        holders.append(self.start_holder())
        self.assertIsNone(holders[0].poll())

    def test_a_waiter_behind_a_worker_holder_reports_the_holder_after_the_interval(self):
        self.governor(1)
        self.start_holder(kind="worker", command="pytest -q tests/integration")
        release = os.open(self.base / "holder-worker" / "release", os.O_WRONLY)
        try:
            _, stdout, stderr, line = self.run_waiter(
                WAIT_SCRIPT, release, command="npm run build", after="0.2")
        finally:
            os.close(release)
        self.assertEqual(stdout, "ran\n")
        self.assertEqual(stderr, line + "\n")
        self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} for \d+s: "
                                rf"pytest -q tests/integration$")

    def test_a_waiter_that_gets_a_slot_at_once_prints_nothing(self):
        self.governor(1)
        result = _run(
            [sys.executable, "-c", WAIT_SCRIPT, str(SCRIPT.parent), "0.2", "60", "worker", str(self.work), "make"],
            env=self.slot_env(), cwd=self.base, timeout=20, reason=ProcessReason.GOVERNOR)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "ran\n")
        self.assertEqual(result.stderr, "")
        holds = self.base / "state/pstack-t3/governor/holds"
        self.assertFalse(holds.exists() and list(holds.iterdir()))

    def test_a_waiter_behind_an_exclusive_holder_names_it_exclusive(self):
        self.governor(1)
        self.start_holder(kind="exclusive", command="bench --all")
        release = os.open(self.base / "holder-exclusive" / "release", os.O_WRONLY)
        try:
            _, stdout, _, line = self.run_waiter(WAIT_SCRIPT, release, after="0.2")
        finally:
            os.close(release)
        self.assertEqual(stdout, "ran\n")
        self.assertIn(f"exclusive {self.work} for ", line)
        self.assertIn(": bench --all", line)

    def test_a_stderr_error_on_the_slot_wait_line_does_not_cancel_the_waiter(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        release = self.base / "holder-worker" / "release"
        attempted = threading.Event()
        body = []

        class DeadStderr:
            def write(self, data):
                attempted.set()
                raise OSError(errno.EIO, "Input/output error")

            def flush(self):
                return None

        def release_holder():
            attempted.wait(timeout=5)
            with open(release, "w") as handle:
                handle.write("x")

        releaser = threading.Thread(target=release_holder)
        saved_after = land.REPORT_AFTER
        land.REPORT_AFTER = 0.05
        saved_stderr = sys.stderr
        releaser.start()
        try:
            sys.stderr = DeadStderr()
            with self.no_slot():
                with land.slot("worker", repo=str(self.work), command="npm test"):
                    body.append("ran")
        finally:
            sys.stderr = saved_stderr
            land.REPORT_AFTER = saved_after
            releaser.join(timeout=10)
            if holder.poll() is None:
                holder.wait(timeout=20)
        self.assertFalse(releaser.is_alive())
        self.assertTrue(attempted.is_set(), "the waiter never printed a wait line")
        self.assertEqual(body, ["ran"])

    def test_an_exclusive_command_that_outlives_its_cap_is_stopped(self):
        self.governor(1)
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
            cap=0.4, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "land: stopped the command at the 0.4s exclusive cap. "
            "--for sets a longer cap and needs --reason past 0.4s.\n")
        self.assert_slots_free()

    def test_an_exclusive_cap_stops_a_child_still_in_the_commands_process_group(self):
        self.governor(1)
        parent_path, child_path = self.base / "parent.pid", self.base / "child.pid"
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", GROUP_SLEEP, str(parent_path), str(child_path)],
            cap=1, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 124)
        self.assertIn("1s exclusive cap", result.stderr)
        self.assertIn("--for", result.stderr)
        self.assert_dead(self.wait_for_pid(parent_path))
        self.assert_dead(self.wait_for_pid(child_path))
        self.assert_slots_free()

    def test_an_exclusive_command_that_ignores_sigterm_is_killed_after_the_grace(self):
        self.governor(1)
        log_path = self.base / "term-log"
        command = (
            "import signal, sys, time\n"
            "from pathlib import Path\n"
            "log = Path(sys.argv[1])\n"
            "def note(word):\n"
            "    handle = log.open('a')\n"
            "    handle.write(f'{word} {time.time()}\\n')\n"
            "    handle.close()\n"
            "def on_term(signum, frame):\n"
            "    note('term')\n"
            "signal.signal(signal.SIGTERM, on_term)\n"
            "note('start')\n"
            "time.sleep(3600)\n"
        )
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", command, str(log_path)],
            cap=0.6, grace=0.3, timeout=4)
        self.assertEqual(result.returncode, 124)
        lines = log_path.read_text().splitlines()
        self.assertEqual([line.split()[0] for line in lines], ["start", "term"])
        start, term = (float(line.split()[1]) for line in lines)
        self.assertGreater(term - start, 0.2)
        self.assertGreater(time.time() - term, 0.12)
        self.assert_slots_free()

    def test_an_exclusive_command_that_finishes_before_the_cap_keeps_its_status_and_output(self):
        self.governor(1)
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c",
             "import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(7)"],
            cap=2, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "out\n")
        self.assertEqual(result.stderr, "err\n")
        self.assert_slots_free()

    def test_an_ordinary_ready_symlink_leaves_its_destination_absent(self):
        self.governor(1)
        link = self.base / "ready-link"
        destination = self.base / "ready-destination"
        link.symlink_to(destination)
        with mock.patch.dict(os.environ, {"LAND_EXCLUSIVE_READY": str(link)}):
            result = self.run_exclusive(
                ["slot", "--exclusive", "--", sys.executable, "-c", "pass"],
                cap=5, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(destination.exists(), "an ordinary ready symlink created its destination")
        self.assertNotIn("LAND_EXCLUSIVE_READY", SCRIPT.read_text())

    def test_an_exclusive_cap_starts_when_the_hold_takes_the_slots(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        release = self.base / "holder-worker" / "release"
        clock = self.base / "cap-clock"
        clock.write_text("1000")
        with mock.patch.dict(os.environ, {"LAND_CAP_CLOCK": str(clock)}):
            process = self.popen_exclusive(
                ["slot", "--exclusive", "--for", "2", "--", sys.executable, "-c",
                 "import time; time.sleep(0.2); print('ok', flush=True)"],
                main=CAP_CLOCK_MAIN)
            try:
                tickets = self.base / "state/pstack-t3/governor/tickets"
                deadline = time.monotonic() + 20
                seen = False
                while time.monotonic() < deadline:
                    try:
                        names = os.listdir(tickets)
                    except OSError:
                        names = []
                    if any(name.endswith("-exclusive") for name in names):
                        seen = True
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(seen, "the exclusive waiter took no ticket")
                self.assertIsNone(process.poll(), "the exclusive caller exited while the slots were still busy")
                clock.write_text("1010")
                with open(release, "w") as handle:
                    handle.write("x")
                stdout, stderr = process.communicate(timeout=20)
            finally:
                if process.poll() is None:
                    os.kill(process.pid, signal.SIGKILL)
                    process.wait(timeout=20)
                process.stdout.close()
                process.stderr.close()
                if holder.poll() is None:
                    holder.wait(timeout=20)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout, "ok\n")
        self.assertNotIn("exclusive cap", stderr)
        self.assert_slots_free()

    def test_an_exclusive_cap_kills_a_same_group_child_that_ignores_sigterm_after_the_leader_exits(self):
        self.governor(1)
        parent_path, child_path = self.base / "cap-parent.pid", self.base / "cap-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP,
             str(parent_path), str(child_path)],
            cap=0.4, grace=0.15)
        try:
            child_line = self.wait_for_line(child_path)
            code = process.wait(timeout=10)
            parent_pid = self.wait_for_pid(parent_path)
            child_pid, child_group = (int(part) for part in child_line.split())
            self.assertEqual(child_group, parent_pid)
            self.assert_dead(parent_pid)
            self.assert_dead(child_pid)
            stderr = process.stderr.read()
            self.assertEqual(code, 124)
            self.assertEqual(
                stderr,
                "land: stopped the command at the 0.4s exclusive cap. "
                "--for sets a longer cap and needs --reason past 0.4s.\n")
            self.assert_slots_free()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
            self.release_recorded_group(parent_path, child_path)

    def test_sigint_to_an_exclusive_land_py_kills_a_same_group_child_that_ignores_sigterm(self):
        self.governor(1)
        code, stderr = self.signal_exclusive_ignoring_child(signal.SIGINT)
        self.assertEqual(code, -2)
        self.assertIn("KeyboardInterrupt", stderr)
        self.assertNotIn("exclusive cap", stderr)

    def test_sigterm_to_an_exclusive_land_py_kills_a_same_group_child_that_ignores_sigterm(self):
        self.governor(1)
        code, stderr = self.signal_exclusive_ignoring_child(signal.SIGTERM)
        self.assertEqual(code, 143)
        self.assertNotIn("exclusive cap", stderr)

    def test_sighup_to_an_exclusive_land_py_kills_a_same_group_child_that_ignores_sigterm(self):
        self.governor(1)
        code, stderr = self.signal_exclusive_ignoring_child(signal.SIGHUP)
        self.assertEqual(code, 129)
        self.assertNotIn("exclusive cap", stderr)

    def test_cap_cases_without_os_waitid(self):
        self.governor(1)

        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
            cap=0.4, grace=0.05, timeout=10, main=EXCLUSIVE_MAIN_WITHOUT_WAITID)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (124, "", cap_line("0.4")))
        self.assert_slots_free()

        parent_path, child_path = self.base / "group-parent.pid", self.base / "group-child.pid"
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", GROUP_SLEEP, str(parent_path), str(child_path)],
            cap=1, grace=0.05, timeout=10, main=EXCLUSIVE_MAIN_WITHOUT_WAITID)
        self.assertEqual((result.returncode, result.stderr), (124, cap_line("1")))
        self.assert_dead(self.wait_for_pid(parent_path))
        self.assert_dead(self.wait_for_pid(child_path))
        self.assert_slots_free()

        parent_path, child_path = self.base / "ignoring-parent.pid", self.base / "ignoring-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP, str(parent_path), str(child_path)],
            cap=1, grace=0.15, main=EXCLUSIVE_MAIN_WITHOUT_WAITID)
        try:
            child_pid, child_group = (int(part) for part in self.wait_for_line(child_path).split())
            code = process.wait(timeout=10)
            parent_pid = self.wait_for_pid(parent_path)
            self.assertEqual(child_group, parent_pid)
            self.assert_dead(parent_pid)
            self.assert_dead(child_pid)
            self.assertEqual((code, process.stderr.read()), (124, cap_line("1")))
            self.assert_slots_free()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
            self.release_recorded_group(parent_path, child_path)

    @unittest.skipUnless(os.path.isdir("/proc"), "the test reads a zombie state from /proc")
    def test_a_signal_during_grace_after_the_leader_exits_kills_the_group(self):
        self.governor(1)

        def zombie(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] == "Z"
            except FileNotFoundError:
                return False

        statuses = {signal.SIGTERM: 143, signal.SIGHUP: 129, signal.SIGINT: -2}
        for label, main in (("waitid", EXCLUSIVE_MAIN), ("no-waitid", EXCLUSIVE_MAIN_WITHOUT_WAITID)):
            for signum, status in statuses.items():
                with self.subTest(signal=signum.name, os=label):
                    parent_path = self.base / f"grace-{label}-{signum.name}-parent.pid"
                    child_path = self.base / f"grace-{label}-{signum.name}-child.pid"
                    process = self.popen_exclusive(
                        ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP,
                         str(parent_path), str(child_path)],
                        cap=1, grace=30, main=main)
                    try:
                        child_pid, child_group = (int(part) for part in self.wait_for_line(child_path).split())
                        parent_pid = self.wait_for_pid(parent_path)
                        self.assertEqual(child_group, parent_pid)
                        deadline = time.monotonic() + 10
                        while not zombie(parent_pid) and time.monotonic() < deadline:
                            time.sleep(0.01)
                        self.assertTrue(zombie(parent_pid), "the leader did not exit at the cap")
                        os.kill(process.pid, signum)
                        code = process.wait(timeout=10)
                        self.assert_dead(child_pid)
                        self.assert_slots_free()
                        self.assertEqual(code, status)
                        self.assertNotIn("exclusive cap", process.stderr.read())
                    finally:
                        if process.poll() is None:
                            os.kill(process.pid, signal.SIGKILL)
                            process.wait(timeout=10)
                        process.stdout.close()
                        process.stderr.close()
                        self.release_recorded_group(parent_path, child_path)

    def test_an_exclusive_command_that_finishes_leaves_a_background_child(self):
        self.governor(1)
        child_path = self.base / "background-child.pid"
        child = None
        try:
            result = self.run_exclusive(
                ["slot", "--exclusive", "--", sys.executable, "-c", BACKGROUND_CHILD, str(child_path)],
                cap=30, timeout=10)
            child = int(child_path.read_text().split()[0])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("exclusive cap", result.stderr)
            time.sleep(0.2)
            os.kill(child, 0)
            self.assert_slots_free()
        finally:
            if child is None and child_path.exists():
                parts = child_path.read_text().split()
                if parts:
                    try:
                        child = int(parts[0])
                    except ValueError:
                        child = None
            if child is not None:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.kill(child, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)

    def test_an_exclusive_cap_stops_when_darwin_eperm_follows_an_exited_leader(self):
        """A killpg that macOS refuses for an exited leader still stops the command at its cap."""
        self.governor(1)
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
            cap=0.4, grace=0.05, timeout=20, main=_darwin_eperm_main("after-term"))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (124, "", cap_line("0.4")))
        self.assert_slots_free()

    def test_an_exclusive_darwin_eperm_is_reraised_when_the_witness_is_not_a_permitted_target(self):
        """Each witness that is not a permitted target fails the command, and the Linux branch does the same."""
        self.governor(1)
        rows = (
            ("live leader", dict(mode="always", probe="waitid"), True),
            ("unknown witness", dict(mode="always", probe="failed"), True),
            ("foreign uid", dict(mode="after-term", probe="foreign"), True),
            ("leader exits after a negative witness", dict(mode="always", kills=True, probe="waitid"), True),
            # darwin False keeps the host platform, so this row reaches the Linux branch on a Linux host.
            ("linux", dict(mode="always", probe="waitid"), False),
        )
        for label, options, darwin in rows:
            with self.subTest(witness=label):
                # The command ends on its own, so a caller that never signals it cannot outlive the test.
                result = self.run_exclusive(
                    ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(0.6)"],
                    cap=0.2, grace=0.05, timeout=20,
                    main=_darwin_eperm_main(darwin=darwin, **options))
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("PermissionError", result.stderr)
                self.assertNotIn("exclusive cap", result.stderr)
                self.assert_slots_free()

    def test_an_exclusive_darwin_eperm_leaves_a_sigterm_ignoring_member_alive_after_the_cap(self):
        """An injected EPERM after the leader exits leaves a SIGTERM-ignoring member running.

        The command still stops at its cap and frees the slots, so the caller never learns that the
        group has a live member.
        """
        self.governor(1)
        parent_path = self.base / "member-parent.pid"
        child_path = self.base / "member-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", SURVIVING_IGNORING_GROUP,
             str(parent_path), str(child_path)],
            cap=1, grace=0.15, main=_darwin_eperm_main("after-term"))
        try:
            child_line = self.wait_for_line(child_path)
            code = process.wait(timeout=20)
            parent_pid = self.wait_for_pid(parent_path)
            child_pid, child_group = (int(part) for part in child_line.split())
            self.assertEqual(child_group, parent_pid)
            self.assert_dead(parent_pid)
            self.assertEqual((code, process.stderr.read()), (124, cap_line("1")))
            self.assert_slots_free()
            self.assert_live(child_pid)
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=20)
            process.stdout.close()
            process.stderr.close()
            self.release_recorded_group(parent_path, child_path)

    def test_an_interrupt_during_the_exclusive_grace_survives_a_darwin_eperm(self):
        """The interrupt that ends the grace wait is the error the caller sees, not the EPERM."""
        self.governor(1)
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
            cap=0.4, grace=0.4, timeout=20,
            main=_darwin_eperm_main("after-term", after=INTERRUPT_AFTER_THE_LEADER_EXITS))
        self.assertNotEqual(result.returncode, 124, result.stderr)
        self.assertIn("KeyboardInterrupt", result.stderr)
        self.assertNotIn("PermissionError", result.stderr)
        self.assert_slots_free()

    def test_the_interrupt_fixture_needs_no_os_waitid(self):
        """Pin that the interrupt fixture needs no os.waitid.

        Removing os.waitid from the child models macOS Python before 3.13, which does not expose it.
        """
        self.governor(1)
        original = _darwin_eperm_main

        def without_waitid(*args, **kwargs):
            return "import os\nvars(os).pop('waitid', None)\n" + original(*args, **kwargs)

        with mock.patch.object(sys.modules[__name__], "_darwin_eperm_main", without_waitid):
            result = self.run_exclusive(
                ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
                cap=0.4, grace=0.4, timeout=20,
                main=_darwin_eperm_main("after-term", after=INTERRUPT_AFTER_THE_LEADER_EXITS))
        self.assertNotEqual(result.returncode, 124, result.stderr)
        self.assertIn("KeyboardInterrupt", result.stderr)
        self.assertNotIn("PermissionError", result.stderr)
        self.assert_slots_free()

    def test_run_build_treats_darwin_eperm_for_a_witnessed_zombie_like_a_successful_killpg(self):
        """A timed out command reports no status when macOS refuses the killpg for an exited leader."""
        self.governor(1)
        # The shell exits at once and leaves a member that holds the capture pipe, so the leader is an
        # unreaped zombie when the killpg runs and the member ends on its own.
        command = f"{sys.executable} -c 'import time; time.sleep(2)' & exit 0"
        with mock.patch.object(land.sys, "platform", "darwin"), \
                mock.patch.object(land.os, "killpg",
                                  side_effect=PermissionError(errno.EPERM, "Operation not permitted")):
            code, output = land.run_build(command, str(self.base), 0.4)
        self.assertIsNone(code)
        self.assertEqual(output, "")

    def test_an_exclusive_deadline_is_when_every_slot_is_locked(self):
        self.governor(1)
        result = _run(
            [sys.executable, "-c", SLOW_PUBLISH_MAIN, str(SCRIPT.parent),
             "slot", "--exclusive", "--for", "0.2", "--",
             sys.executable, "-c", "print('command-finished', flush=True)"],
            env=self.slot_env(), cwd=self.base, timeout=10, reason=ProcessReason.GOVERNOR)
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn("all-slots-taken", result.stdout)
        self.assertNotIn("command-finished", result.stdout)
        self.assertEqual(
            result.stderr,
            "land: stopped the command at the 0.2s exclusive cap. "
            "--for sets a longer cap and needs --reason past 600s.\n")
        lines = result.stdout.splitlines()
        self.assertEqual([line.split()[0] for line in lines], ["all-slots-taken", "exit"])
        taken = float(lines[0].split()[1])
        exited = float(lines[1].split()[1])
        self.assertGreaterEqual(exited - taken, 0.5)
        self.assert_slots_free()

    def test_bad_exclusive_flags_are_refused_before_a_slot_is_taken(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        refused = "land: --for must be a positive number of seconds\n"
        only = "land: --for and --reason are only for slot --exclusive\n"
        needs = "land: --for over 600 seconds needs --reason\n"
        cases = [
            (["slot", "--exclusive", "--for", "601", "--", "true"], needs, "--for"),
            (["slot", "--exclusive", "--for", "0", "--", "true"], refused, "--for"),
            (["slot", "--exclusive", "--for", "-2", "--", "true"], refused, "--for"),
            (["slot", "--exclusive", "--for", "nope", "--", "true"], refused, "--for"),
            (["slot", "--exclusive", "--for", "inf", "--", "true"], refused, "--for"),
            (["slot", "--exclusive", "--for", "7201", "--", "true"],
             "land: --for cannot pass 7200 seconds\n", "--for"),
            (["slot", "--exclusive", "--for", "7200", "--", "true"], needs, "--for"),
            (["slot", "--exclusive", "--for", "601", "--reason", "\n\x01", "--", "true"], needs, "--reason"),
            (["slot", "--for", "10", "--", "true"], only, "--for"),
            (["slot", "--reason", "because", "--", "true"], only, "--reason"),
        ]
        try:
            for argv, stderr, flag in cases:
                with self.subTest(argv=argv):
                    result = self.run_exclusive(argv, timeout=2)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stderr, stderr)
                    self.assertIn(flag, result.stderr)
                    self.assert_worker_holder_keeps_the_slot(holder)
            with self.subTest(argv="missing command"):
                result = self.run_exclusive(["slot", "--exclusive", "--for", "10"], timeout=2)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "land: slot needs a command after --\n")
                self.assert_worker_holder_keeps_the_slot(holder)
        finally:
            if holder.poll() is None:
                with open(self.base / "holder-worker" / "release", "w") as handle:
                    handle.write("x")
                holder.wait(timeout=20)

    def test_an_exclusive_cap_at_the_default_needs_no_reason_and_the_maximum_needs_one(self):
        self.governor(1)
        plain = self.run_exclusive(
            ["slot", "--exclusive", "--for", "0.4", "--", sys.executable, "-c", "print('ok')"],
            cap=0.4, cap_max=0.8, grace=0.05, timeout=10)
        self.assertEqual((plain.returncode, plain.stdout), (0, "ok\n"), plain.stderr)
        self.assertNotIn("exclusive cap", plain.stderr)
        longest = self.run_exclusive(
            ["slot", "--exclusive", "--for", "0.8", "--reason", "cold pricing bench", "--",
             sys.executable, "-c", "print('max')"],
            cap=0.4, cap_max=0.8, grace=0.05, timeout=10)
        self.assertEqual((longest.returncode, longest.stdout), (0, "max\n"), longest.stderr)
        shorter = self.run_exclusive(
            ["slot", "--exclusive", "--for", "0.2", "--reason", "shorter cap", "--",
             sys.executable, "-c", "print('short')"],
            cap=0.4, cap_max=0.8, grace=0.05, timeout=10)
        self.assertEqual((shorter.returncode, shorter.stdout), (0, "short\n"), shorter.stderr)
        named = self.run_exclusive(
            ["slot", "--exclusive", "--reason", "cold pricing bench", "--",
             sys.executable, "-c", "print('why')"],
            cap=2, grace=0.05, timeout=10)
        self.assertEqual((named.returncode, named.stdout), (0, "why\n"), named.stderr)
        refused = self.run_exclusive(
            ["slot", "--exclusive", "--for", "0.8", "--", sys.executable, "-c", "print('no')"],
            cap=0.4, cap_max=0.8, timeout=10)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(refused.stdout, "")
        self.assertEqual(refused.stderr, "land: --for over 0.4 seconds needs --reason\n")

    def test_an_exclusive_holder_record_and_its_wait_line_carry_the_cap_and_the_reason(self):
        self.governor(1)
        directory = self.base / "capped"
        directory.mkdir()
        ready, release = directory / "ready", directory / "release"
        os.mkfifo(release)
        command = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "Path(sys.argv[1]).touch()\n"
            "os.read(os.open(sys.argv[2], os.O_RDONLY), 1)\n"
        )
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--repo", str(self.work), "--for", "30", "--reason", "cold pricing bench", "--",
             sys.executable, "-c", command, str(ready), str(release)])
        gate = None
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            detail = process.stderr.read() if process.poll() is not None else "the hold did not start"
            self.assertTrue(ready.exists(), detail)
            record = json.loads(land._hold_path("landing-0").read_text())
            self.assertEqual(record["kind"], "exclusive")
            self.assertEqual(record["pid"], process.pid)
            self.assertEqual(record["cap"], 30)
            self.assertEqual(record["reason"], "cold pricing bench")
            worker = json.loads(land._hold_path("worker-0").read_text())
            self.assertEqual((worker["cap"], worker["reason"]), (30, "cold pricing bench"))
            gate = os.open(release, os.O_RDWR | os.O_NONBLOCK)
            code, stdout, stderr, line = self.run_waiter(WAIT_SCRIPT, gate, after="0.2")
            process.wait(timeout=10)
        finally:
            if gate is not None:
                os.close(gate)
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, "ran\n")
        self.assertIn(f"exclusive {self.work} for ", line)
        self.assertIn("left of 30s", line)
        self.assertIn(", cold pricing bench: ", line)
        self.assertIn(f": {record['command']}\n", stderr)
        self.assertNotIn("\n", line)
        self.assert_slots_free()

    def test_an_old_exclusive_record_with_no_cap_prints_as_it_does_today(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        path = holds / "landing-0"
        payload = {
            "kind": "exclusive", "pid": os.getpid(), "repo": str(self.work),
            "started": time.time(), "command": "bench --all",
        }
        self.assertNotIn("cap", payload)
        self.assertNotIn("reason", payload)
        writer = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            path.write_text(json.dumps(payload))
            holder = land._read_holder(path)
            self.assertIsNone(holder.cap)
            self.assertIsNone(holder.reason)
            line = land._format_wait(15, land._live_holders(["landing-0"]))
        finally:
            os.close(writer)
        self.assertRegex(
            line,
            rf"^land: waited 15s for a slot; exclusive {re.escape(str(self.work))} for \d+s: bench --all$")
        self.assertNotIn("left of", line)

    def test_an_exclusive_reason_drops_a_newline_and_a_control_character(self):
        self.governor(1)
        directory = self.base / "reason"
        directory.mkdir()
        ready, release = directory / "ready", directory / "release"
        os.mkfifo(release)
        command = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "Path(sys.argv[1]).touch()\n"
            "os.read(os.open(sys.argv[2], os.O_RDONLY), 1)\n"
        )
        raw = ("n" * 40) + "\n" + ("c" * 40) + "\x01\t" + ("z" * 40)
        expected = ("n" * 40) + ("c" * 40)
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--for", "30", "--reason", raw, "--",
             sys.executable, "-c", command, str(ready), str(release)])
        gate = None
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "the hold did not start")
            record = json.loads(land._hold_path("landing-0").read_text())
            self.assertEqual(record["reason"], expected)
            self.assertNotIn("\n", record["reason"])
            self.assertNotIn("z", record["reason"])
            gate = os.open(release, os.O_RDWR | os.O_NONBLOCK)
            code, stdout, _, line = self.run_waiter(WAIT_SCRIPT, gate, after="0.2")
            process.wait(timeout=10)
        finally:
            if gate is not None:
                os.close(gate)
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "ran\n")
        self.assertIn(expected, line)
        self.assertNotIn("z" * 40, line)
        self.assertNotIn("\n", line)
        self.assertNotIn("n" * 40 + " ", line)

    def test_a_direct_exclusive_slot_record_has_no_cap(self):
        self.governor(1)
        with self.no_slot():
            with land.exclusive_slot(repo=str(self.work), command="bench"):
                record = land._read_holder(land._hold_path("landing-0"))
                self.assertEqual(record.kind, "exclusive")
                self.assertIsNone(record.cap)
                self.assertIsNone(record.reason)
                line = land._format_wait(15, [record])
        self.assertNotIn("left of", line)
        self.assertIn(": bench", line)

    def test_sigterm_to_an_exclusive_land_py_stops_the_group_and_clears_the_record(self):
        self.governor(1)
        parent_path, child_path = self.base / "sig-parent.pid", self.base / "sig-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", GROUP_SLEEP, str(parent_path), str(child_path)],
            cap=30, grace=0.2)
        try:
            parent_pid = self.wait_for_pid(parent_path)
            child_pid = self.wait_for_pid(child_path)
            os.kill(process.pid, signal.SIGTERM)
            code = process.wait(timeout=10)
            stderr = process.stderr.read()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(code, 143)
        self.assertNotIn("exclusive cap", stderr)
        self.assert_dead(parent_pid)
        self.assert_dead(child_pid)
        self.assert_slots_free()

    def test_a_record_whose_holder_is_gone_is_not_shown_as_holding(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        (holds / "worker-0").write_text(json.dumps(
            {"kind": "worker", "pid": 2 ** 31 - 1, "repo": str(self.work), "started": time.time(), "command": "make"}))
        self.assertEqual(land._live_holders(["worker-0"]), [land._UNKNOWN])

    def test_an_unlocked_stale_record_reads_as_unknown_when_the_lock_fails_with_enolck(self):
        self.governor(1)
        directory = land.governor_dir()
        slot = os.open(directory / "worker-0", os.O_RDWR | os.O_CREAT, 0o666)
        fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(os.close, slot)
        holds = directory / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        record = holds / "worker-0"
        record.write_text(json.dumps(
            {"kind": "worker", "pid": 2147483647, "repo": "/dead/repo", "started": 1, "command": "dead-command"}))
        self.assertEqual(land._record_holder(record), land._UNKNOWN)
        with mock.patch.object(land.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")):
            holders = land._live_holders(["worker-0"])
            line = land._format_wait(15, holders)
        self.assertEqual(holders, [land._UNKNOWN])
        self.assertEqual(line, "land: waited 15s for a slot; unknown")
        self.assertNotIn("dead-command", line)
        self.assertNotIn("/dead/repo", line)

    def test_a_malformed_record_prints_unknown_without_failing(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        record = holds / "worker-0"
        # The reader names a record only while its writer holds the record's own lock, so lock it.
        writer = os.open(record, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for content in ("{ not json", ""):
                record.write_text(content)
                holders = land._live_holders(["worker-0"])
                self.assertEqual(holders, [land._UNKNOWN])
                self.assertEqual(land._format_wait(15, holders), "land: waited 15s for a slot; unknown")
            record.unlink()
            self.assertEqual(land._live_holders(["worker-0"]), [land._UNKNOWN])
        finally:
            os.close(writer)

    def test_a_record_start_no_float_can_hold_reads_as_unknown(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        record = holds / "worker-0"
        writer = os.open(record, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for started in ("-1e309", "1e400", "1" + "0" * 399):
                text = ('{"kind": "worker", "pid": %d, "repo": %s, "started": %s, "command": "make"}'
                        % (os.getpid(), json.dumps(str(self.work)), started)).encode()
                os.pwrite(writer, text, 0)
                os.ftruncate(writer, len(text))
                holders = land._live_holders(["worker-0"])
                self.assertEqual([holder.started for holder in holders], [None])
                self.assertIn("for unknown", land._format_wait(15, holders))
        finally:
            os.close(writer)

    def test_a_record_start_no_float_can_hold_leaves_the_waiter_running(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        record = land._hold_path("worker-0")
        saved = json.loads(record.read_text())
        damaged = re.sub(r'"started": [^,}]+', '"started": -1e309', json.dumps(saved))
        self.assertNotEqual(damaged, json.dumps(saved))
        descriptor = os.open(record, os.O_WRONLY)
        try:
            os.pwrite(descriptor, damaged.encode(), 0)
            os.ftruncate(descriptor, len(damaged))
        finally:
            os.close(descriptor)
        release = os.open(self.base / "holder-worker" / "release", os.O_WRONLY)
        try:
            code, stdout, _, line = self.run_waiter(WAIT_SCRIPT, release, after="0.2")
        finally:
            os.close(release)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "ran\n")
        self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} "
                                rf"for unknown: make test$")

    def test_an_old_holder_with_no_record_names_an_unknown_holder(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="dead-command")
        self.assertEqual(land._read_holder(land._hold_path("worker-0")).command, "dead-command")
        os.kill(holder.pid, signal.SIGKILL)
        self.assertEqual(holder.wait(timeout=20), -signal.SIGKILL)
        old = self.base / "old/land.py"
        old.parent.mkdir()
        old.write_text(PREVIOUS_SLOT_SCRIPT)
        directory = self.base / "old-holder"
        directory.mkdir()
        release, took = directory / "release", directory / "took"
        os.mkfifo(release)
        read_byte = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "Path(sys.argv[2]).touch()\n"
            "os.read(os.open(sys.argv[1], os.O_RDONLY), 1)\n"
        )
        previous = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, str(old), "slot", "--", sys.executable, "-c", read_byte, str(release), str(took)],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_previous():
            if previous.poll() is None:
                os.killpg(previous.pid, signal.SIGKILL)
            previous.wait(timeout=20)
        self.addCleanup(stop_previous)
        deadline = time.monotonic() + 20
        while not took.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(took.exists(), "the previous holder did not take the worker slot")

        gate = os.open(release, os.O_WRONLY)
        try:
            code, stdout, stderr, line = self.run_waiter(WAIT_SCRIPT, gate, after="0.2")
        finally:
            os.close(gate)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "ran\n")
        self.assertEqual(stderr, line + "\n")
        self.assertRegex(line, r"^land: waited \d+s for a slot; unknown$")

    def test_the_wrapped_command_exit_status_and_output_are_unchanged(self):
        self.governor(1)
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT,
            ["slot", "--repo", str(self.work), "--", "sh", "-c", "echo out; echo err >&2; exit 7"],
            env=self.slot_env(), cwd=self.base, timeout=20)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "out\n")
        self.assertEqual(result.stderr, "err\n")

    def test_the_record_holds_only_the_first_80_characters_of_the_command(self):
        self.governor(1)

        def stored(command):
            handles = {name: open(land.governor_dir() / name, "a") for name in land._slot_names("exclusive", 1)}
            try:
                records = land._publish("worker", handles, [handles["worker-0"]], str(self.work), command)
                return json.loads(land._hold_path("worker-0").read_text())
            finally:
                land._release_records(records)
                for handle in handles.values():
                    handle.close()

        record = stored("x" * 120)
        crlf = stored("x" * 60 + "\r\n" * 10 + "SECRET")
        self.assertEqual(record["command"], "x" * 80)
        self.assertEqual(len(record["command"]), 80)
        self.assertEqual(crlf["command"], "x" * 60 + " " * 10)
        self.assertNotIn("SECRET", crlf["command"])
        self.assertEqual(set(record), set(land._Holder._fields))
        self.assertIsNone(record["cap"])
        self.assertIsNone(record["reason"])
        self.assertIsNone(crlf["cap"])
        self.assertIsNone(crlf["reason"])

    def test_a_reporting_probe_cannot_let_a_later_landing_caller_pass_an_earlier_exclusive(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        directory = self.base / "caller-reporter"
        directory.mkdir()
        marker, release = directory / "probe", directory / "release"
        os.mkfifo(release)
        # Keep both ends open so the write that resumes the probe never waits for a reader.
        gate = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, gate)
        reporter = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", REPORT_SCRIPT, str(SCRIPT.parent), str(marker), str(release), "worker-0",
             "0.2", "0.2", str(self.work), "make test"],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_reporter():
            if reporter.poll() is None:
                os.killpg(reporter.pid, signal.SIGKILL)
            reporter.wait(timeout=20)
        self.addCleanup(stop_reporter)

        slots.release(holder)
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "the reporting probe did not pause holding the free slot")

        landing = slots.start("landing", pool="landing", stepped=True)
        slots.wait(lambda: (landing.directory / "blocking").exists() or landing.started,
                   "the landing caller blocks on the turn mutex the paused probe holds")
        self.assertTrue((landing.directory / "blocking").exists())
        self.assertFalse(landing.started)

        os.write(gate, b"x")
        # The probe unlocks the free slot inside its slow_busy probe and returns, and only after that
        # does _admit unlock the turn mutex the landing caller is blocked on. Settling the landing
        # caller before the probe is stopped keeps that order, so its first attempt wakes with the
        # free slot already released and yields to the earlier exclusive instead of starting.
        self.assertEqual(slots.settle(landing), "waiting")
        # Stopping the probe before that attempt leaves when each lock is released up to the
        # kernel, so the attempt can land on either side of the free slot's release. On the
        # early side it takes landing-0 instead of yielding to the earlier exclusive.
        stop_reporter()
        self.assertEqual(slots.step(exclusive), "started")
        self.assertFalse(landing.started)
        slots.release(exclusive)
        self.assertEqual(slots.step(landing), "started")
        slots.release(landing)
        self.assertEqual(slots.entries(), ["holder", "exclusive", "landing"])

    def test_a_worker_waiter_names_the_worker_holder_and_not_the_landing_holder(self):
        self.governor(1)
        self.start_holder(kind="worker", command="worker-command")
        landing = self.start_holder(kind="landing", command="landing-command")
        release = os.open(self.base / "holder-worker" / "release", os.O_WRONLY)
        try:
            _, stdout, stderr, line = self.run_waiter(WAIT_SCRIPT, release, after="0.2")
            self.assertEqual(stdout, "ran\n")
            self.assertEqual(stderr, line + "\n")
            self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} "
                                    rf"for \d+s: worker-command$")
            self.assertNotIn("landing-command", stderr)
            self.assertNotIn("landing ", line)
        finally:
            os.close(release)
            if landing.poll() is None:
                with open(self.base / "holder-landing" / "release", "w") as handle:
                    handle.write("x")
            landing.wait(timeout=20)

    def test_a_worker_waiter_never_locks_the_landing_slot_while_it_reports(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="worker-command")
        try:
            self._worker_waiter_leaves_the_landing_slot_unlocked()
        finally:
            if holder.poll() is None:
                with open(self.base / "holder-worker" / "release", "w") as handle:
                    handle.write("x")
            holder.wait(timeout=20)

    def test_a_holder_publishes_its_record_and_clears_it_on_release(self):
        self.governor(1)
        with self.no_slot():
            with land.slot("worker", repo=str(self.work), command="make test"):
                record = land._read_holder(land._hold_path("worker-0"))
                self.assertEqual((record.kind, record.repo, record.command), ("worker", str(self.work), "make test"))
                self.assertIsNone(record.cap)
                self.assertIsNone(record.reason)
                self.assertTrue(land._hold_path("worker-0").exists())
            self.assertFalse(land._hold_path("worker-0").exists())

    def test_a_failing_wrapped_command_clears_the_holders_record(self):
        self.governor(1)
        with self.no_slot():
            result = real_process(
                ProcessReason.GOVERNOR, SCRIPT,
                ["slot", "--", "sh", "-c", "exit 3"],
                env=self.slot_env(), cwd=self.base, timeout=20)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse(land._hold_path("worker-0").exists())

    def test_a_crashed_holders_temporary_record_file_is_cleaned(self):
        self.governor(1)
        pid = self.dead_pid()
        stale, descriptor = self.temporary_hold(pid, locked=False)
        os.close(descriptor)
        with self.no_slot():
            with land.slot("worker", repo=str(self.work), command="make test"):
                pass
        self.assertFalse(stale.exists())

    def test_a_locked_temporary_record_file_is_kept(self):
        self.governor(1)
        # The pid comes from a process that has exited, so the name cannot collide with the pid this
        # process publishes its own temporary file under.
        pid = self.dead_pid()
        live, descriptor = self.temporary_hold(pid, locked=True)
        try:
            with self.no_slot():
                with land.slot("worker", repo=str(self.work), command="make test"):
                    pass
            self.assertTrue(live.exists())
        finally:
            os.close(descriptor)

    def test_a_live_temporary_record_that_locks_before_the_unlink_is_kept_for_a_race(self):
        """Pin review finding D255-review-1 R2. Starts a live writer process for a race. Orders the writer and the cleanup with FIFOs and no sleep."""
        import select
        self.governor(1)
        holds = self.base / "state/pstack-t3/governor/holds"
        holds.mkdir(parents=True, exist_ok=True)
        fifo_dir = self.base / "r2-fifos"
        fifo_dir.mkdir()
        c2p = fifo_dir / "c2p"
        p2c = fifo_dir / "p2c"
        os.mkfifo(c2p)
        os.mkfifo(p2c)
        c2p_fd = os.open(c2p, os.O_RDWR)
        self.addCleanup(os.close, c2p_fd)
        p2c_fd = os.open(p2c, os.O_RDWR)
        self.addCleanup(os.close, p2c_fd)
        child_code = (
            "import os, fcntl\n"
            f"c2p = os.open({str(c2p)!r}, os.O_RDWR)\n"
            f"p2c = os.open({str(p2c)!r}, os.O_RDWR)\n"
            f"holds = {str(holds)!r}\n"
            "tmp = os.path.join(holds, 'worker-0.%d.tmp' % os.getpid())\n"
            "tmp_fd = os.open(tmp, os.O_RDWR | os.O_CREAT, 0o666)\n"
            "os.write(c2p, b'O')\n"
            "assert os.read(p2c, 1) == b'P'\n"
            "try:\n"
            "    fcntl.flock(tmp_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except OSError:\n"
            "    os.write(c2p, b'F')\n"
            "    raise SystemExit(1)\n"
            "os.pwrite(tmp_fd, str(os.getpid()).encode(), 0)\n"
            "os.write(c2p, b'L')\n"
            "assert os.read(p2c, 1) == b'D'\n"
            "fcntl.flock(tmp_fd, fcntl.LOCK_UN)\n"
            "os.close(tmp_fd)\n"
            "os.close(c2p)\n"
            "os.close(p2c)\n"
        )
        child = real_popen(
            ProcessReason.RACE,
            [sys.executable, "-c", child_code],
            env=self.slot_env(), cwd=self.base, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=20)

        self.addCleanup(stop)
        ready, _, _ = select.select([c2p_fd], [], [], 20)
        self.assertTrue(ready, "live writer did not open its record")
        self.assertEqual(os.read(c2p_fd, 1), b"O")
        found = sorted(holds.glob("worker-0.*.tmp"))
        self.assertEqual(len(found), 1)
        target = found[0]
        original_flock = land.fcntl.flock
        coordinated = []

        def coordinating_flock(fd, op):
            if op == fcntl.LOCK_UN and not coordinated:
                try:
                    number = fd.fileno() if hasattr(fd, "fileno") else fd
                    held = os.fstat(number)
                    named = os.stat(target)
                except (OSError, ValueError):
                    return original_flock(fd, op)
                if (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino):
                    result = original_flock(fd, op)
                    os.write(p2c_fd, b"P")
                    waiting, _, _ = select.select([c2p_fd], [], [], 20)
                    self.assertTrue(waiting, "live writer did not lock its record")
                    self.assertEqual(os.read(c2p_fd, 1), b"L")
                    coordinated.append(True)
                    return result
            return original_flock(fd, op)

        try:
            with mock.patch.object(land.fcntl, "flock", coordinating_flock):
                with self.no_slot():
                    with land.slot("worker", repo=str(self.work), command="make test"):
                        pass
            self.assertTrue(coordinated, "cleanup did not probe the live record")
            self.assertTrue(target.exists(), "cleanup removed a live record that locked before the unlink")
            self.assertEqual(target.read_bytes().decode(), str(child.pid))
        finally:
            try:
                os.write(p2c_fd, b"D")
            except OSError:
                pass
        self.assertEqual(child.wait(timeout=20), 0)
        self.assertTrue(target.exists())

    def test_a_publish_retries_when_cleanup_removes_the_temporary_name_before_the_lock(self):
        """Pin review finding D255-review-2 N1. Orders open-before-flock with FIFOs and no sleep. Starts a process for a race."""
        import select
        self.governor(1)
        holds = self.base / "state/pstack-t3/governor/holds"
        holds.mkdir(parents=True, exist_ok=True)
        fifo_dir = self.base / "n1-fifos"
        fifo_dir.mkdir()
        c2p = fifo_dir / "c2p"
        p2c = fifo_dir / "p2c"
        os.mkfifo(c2p)
        os.mkfifo(p2c)
        c2p_fd = os.open(c2p, os.O_RDWR)
        self.addCleanup(os.close, c2p_fd)
        p2c_fd = os.open(p2c, os.O_RDWR)
        self.addCleanup(os.close, p2c_fd)
        child_code = (
            "import os\n"
            f"c2p = os.open({str(c2p)!r}, os.O_RDWR)\n"
            f"p2c = os.open({str(p2c)!r}, os.O_RDWR)\n"
            f"holds = {str(holds)!r}\n"
            "import sys\n"
            f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
            "import land\n"
            "from pathlib import Path\n"
            "assert os.read(p2c, 1) == b'O'\n"
            "found = sorted(Path(holds).glob('worker-0.*.tmp'))\n"
            "assert len(found) == 1\n"
            "land._tmp_kept(found[0])\n"
            "os.write(c2p, b'U')\n"
            "assert os.read(p2c, 1) == b'D'\n"
        )
        child = real_popen(
            ProcessReason.RACE,
            [sys.executable, "-c", child_code],
            env=self.slot_env(), cwd=self.base, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=20)

        self.addCleanup(stop)
        real_open = os.open
        paused = []

        def pausing_open(path, flags, *args, **kwargs):
            try:
                text = os.fspath(path)
            except (TypeError, ValueError):
                return real_open(path, flags, *args, **kwargs)
            if (isinstance(text, str) and text.endswith(f".{os.getpid()}.tmp")
                    and (flags & os.O_CREAT) and not paused):
                descriptor = real_open(path, flags, *args, **kwargs)
                os.write(p2c_fd, b"O")
                ready, _, _ = select.select([c2p_fd], [], [], 20)
                self.assertTrue(ready, "the child did not signal after its cleanup step")
                self.assertEqual(os.read(c2p_fd, 1), b"U")
                paused.append(True)
                return descriptor
            return real_open(path, flags, *args, **kwargs)

        handles = {name: open(land.governor_dir() / name, "a") for name in land._slot_names("exclusive", 1)}
        records = []
        try:
            with mock.patch.object(land.os, "open", pausing_open):
                records = land._publish("worker", handles, [handles["worker-0"]], str(self.work), "make test")
            self.assertTrue(paused, "the writer did not pause after opening its temporary record")
            self.assertEqual(len(records), 1, "cleanup removed the temporary name and the holder published no record")
            record = land._read_holder(land._hold_path("worker-0"))
            self.assertEqual((record.kind, record.command), ("worker", "make test"))
        finally:
            try:
                os.write(p2c_fd, b"D")
            except OSError:
                pass
            for handle in handles.values():
                handle.close()
            land._release_records(records)
        self.assertEqual(child.wait(timeout=20), 0)

    def test_a_store_directory_that_cannot_be_searched_leaves_the_body_alone(self):
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.init(mode="human")
            self.store_item("w1", "polish", "G2")
            sha = self.worker("w1", {"a.txt": "agent\n"})
            self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1",
                      "--reviewer", REVIEWER)
            # Read the store's tables with no search permission on the store itself, so a stat inside it fails.
            store = self.base / "state/pstack-t3/brigade/proj/store"
            os.chmod(store, 0o444)
            try:
                self.land("land")
            finally:
                os.chmod(store, 0o755)
            self.assertTrue(log.exists(), "the queue opened no pull request")
            create = ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][0])
            self.assertEqual(create[create.index("--body") + 1], "")

    def test_a_nested_slot_yields_without_a_second_record(self):
        self.governor(1)
        with self.no_slot():
            with land.slot("worker", repo=str(self.work), command="outer"):
                with land.slot("worker", repo=str(self.work), command="inner"):
                    self.assertEqual(land._read_holder(land._hold_path("worker-0")).command, "outer")
            self.assertFalse(land._hold_path("worker-0").exists())

    def test_a_nested_exclusive_slot_raises(self):
        self.governor(1)
        with self.no_slot():
            with land.slot("worker", repo=str(self.work), command="outer"):
                with self.assertRaises(land.LandError):
                    with land.exclusive_slot(repo=str(self.work), command="bench"):
                        pass

    def test_a_catchable_signal_clears_the_holders_record(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        self.assertTrue(land._hold_path("worker-0").exists())
        os.kill(holder.pid, signal.SIGTERM)
        self.assertEqual(holder.wait(timeout=20), 128 + signal.SIGTERM)
        self.assertFalse(land._hold_path("worker-0").exists())

    def test_the_slot_repo_flag_names_the_repository_from_either_position(self):
        self.governor(1)
        chosen = self.base / "chosen"
        chosen.mkdir()
        for position in ("before", "after"):
            with self.subTest(position=position):
                chosen_flag = ["--repo", str(chosen)] if position == "before" else []
                result = real_process(
                    ProcessReason.GOVERNOR, SCRIPT,
                    [*chosen_flag, "slot",
                     *([] if position == "before" else ["--repo", str(chosen)]), "--",
                     sys.executable, "-c", RECORD_REPO_SCRIPT],
                    env=self.slot_env(), cwd=self.base, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), str(chosen))

    def test_the_slot_repo_flag_defaults_to_the_working_directory(self):
        args = land.parser().parse_args(["slot", "--", "true"])
        self.assertEqual((args.slot_repo, args.repo), (None, "."))
        self.assertEqual(land._repo_text(args.slot_repo or args.repo), os.getcwd())

    def test_slot_runs_with_a_core_worktree_that_names_a_missing_directory(self):
        self.init()
        sh("git", "config", "core.worktree", str(self.base / "gone"), cwd=self.work)
        env = {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "sh", "-c", "echo ran"],
            env=env, cwd=self.work, timeout=30)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "ran\n", ""))
