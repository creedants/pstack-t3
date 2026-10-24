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
from .slot.base import SlotBase


class LandingTest(SlotBase, LandingTestBase):

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
        self.assertEqual((mode, statuses), (0o600, [0, 0]), oct(mode))

    def test_a_holder_under_umask_0o444_is_named_in_another_callers_wait_message(self):
        """The holder is its own process so the test can set that process's umask.

        The tickets and holds directories already exist, because this test pins the record file
        the holder writes and not the mode of the directories a first caller creates.
        """
        self.governor(1)
        governor = self.base / "state/pstack-t3/governor"
        (governor / "tickets").mkdir()
        (governor / "holds").mkdir()
        directory = self.base / "umask-holder"
        directory.mkdir()
        ready, release = directory / "ready", directory / "release"
        os.mkfifo(release)
        holder = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c",
             "import os, sys\n"
             "from pathlib import Path\n"
             "sys.path.insert(0, sys.argv[1])\n"
             "import land\n"
             "repo, command, ready, release = sys.argv[2:6]\n"
             "os.umask(0o444)\n"
             "with land.slot('worker', repo=repo, command=command):\n"
             "    Path(ready).touch()\n"
             "    os.read(os.open(release, os.O_RDONLY), 1)\n",
             str(SCRIPT.parent), str(self.work), "make test", str(ready), str(release)],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop():
            if holder.poll() is None:
                with open(release, "w") as handle:
                    handle.write("x")
            holder.wait(timeout=20)

        self.addCleanup(stop)
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), "the restricted-umask holder did not take its slot")
        mode = land._hold_path("worker-0").stat().st_mode & 0o777
        gate = os.open(release, os.O_WRONLY)
        try:
            code, stdout, stderr, line = self.run_waiter(WAIT_SCRIPT, gate, after="0.2")
        finally:
            os.close(gate)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, "ran\n")
        self.assertEqual(mode, 0o600, oct(mode))
        self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} "
                                rf"for \d+s: make test$")
