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
from .base import SlotBase


class LandingTest(SlotBase, LandingTestBase):

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
