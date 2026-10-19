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

    def test_an_absent_exclusive_max_refuses_a_hold_past_7200(self):
        self.governor(1)
        result = self.run_exclusive(["slot", "--exclusive", "--for", "7201", "--", "true"], timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "land: --for cannot pass 7200 seconds\n")

    def test_a_configured_exclusive_max_moves_the_ceiling(self):
        governor = self.governor(1)
        (governor / "governor.json").write_text(json.dumps({"slots": 1, "exclusiveMax": 10000}))
        allowed = self.run_exclusive(
            ["slot", "--exclusive", "--for", "9000", "--reason", "cold pricing bench", "--",
             sys.executable, "-c", "print('ok')"], timeout=10)
        self.assertEqual((allowed.returncode, allowed.stdout), (0, "ok\n"), allowed.stderr)
        refused = self.run_exclusive(
            ["slot", "--exclusive", "--for", "10001", "--reason", "cold pricing bench", "--", "true"],
            timeout=10)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(refused.stderr, "land: --for cannot pass 10000 seconds\n")

    def test_a_malformed_exclusive_max_is_refused_and_names_the_key(self):
        for value in (300, "x", True, 0, -1):
            with self.subTest(exclusiveMax=value):
                governor = self.governor(1)
                (governor / "governor.json").write_text(json.dumps({"slots": 1, "exclusiveMax": value}))
                result = self.run_exclusive(["slot", "--exclusive", "--for", "10", "--", "true"], timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("governor exclusiveMax", result.stderr)

    def test_a_bad_exclusive_max_leaves_a_plain_slot_alone(self):
        governor = self.governor(1)
        (governor / "governor.json").write_text(json.dumps({"slots": 1, "exclusiveMax": "x"}))
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"], env=self.slot_env(), timeout=20)
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_an_exclusive_cap_at_the_default_needs_no_reason_and_the_maximum_needs_one(self):
        self.governor(1)
        plain = self.run_exclusive(
            ["slot", "--exclusive", "--for", "4", "--", sys.executable, "-c", "print('ok')"],
            cap=4, cap_max=8, grace=0.05, timeout=10)
        self.assertEqual((plain.returncode, plain.stdout), (0, "ok\n"), plain.stderr)
        self.assertNotIn("exclusive cap", plain.stderr)
        longest = self.run_exclusive(
            ["slot", "--exclusive", "--for", "8", "--reason", "cold pricing bench", "--",
             sys.executable, "-c", "print('max')"],
            cap=4, cap_max=8, grace=0.05, timeout=10)
        self.assertEqual((longest.returncode, longest.stdout), (0, "max\n"), longest.stderr)
        shorter = self.run_exclusive(
            ["slot", "--exclusive", "--for", "2", "--reason", "shorter cap", "--",
             sys.executable, "-c", "print('short')"],
            cap=4, cap_max=8, grace=0.05, timeout=10)
        self.assertEqual((shorter.returncode, shorter.stdout), (0, "short\n"), shorter.stderr)
        named = self.run_exclusive(
            ["slot", "--exclusive", "--reason", "cold pricing bench", "--",
             sys.executable, "-c", "print('why')"],
            cap=4, grace=0.05, timeout=10)
        self.assertEqual((named.returncode, named.stdout), (0, "why\n"), named.stderr)
        refused = self.run_exclusive(
            ["slot", "--exclusive", "--for", "8", "--", sys.executable, "-c", "print('no')"],
            cap=4, cap_max=8, timeout=10)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(refused.stdout, "")
        self.assertEqual(refused.stderr, "land: --for over 4 seconds needs --reason\n")

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
