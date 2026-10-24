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
            ("live leader", dict(mode="always", kills=True, probe="waitid"), True),
            ("unknown witness", dict(mode="always", kills=True, probe="failed"), True),
            ("foreign uid", dict(mode="after-term", probe="foreign"), True),
            ("leader exits after a negative witness", dict(mode="always", kills=True, probe="waitid"), True),
            # darwin False keeps the host platform, so this row reaches the Linux branch on a Linux host.
            ("linux", dict(mode="always", kills=True, probe="waitid"), False),
        )
        for label, options, darwin in rows:
            with self.subTest(witness=label):
                # The stub kills the leader on every always row and the first SIGTERM kills the foreign row, so no command outlives the test.
                result = self.run_exclusive(
                    ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
                    cap=4, grace=0.05, timeout=20,
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
