import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from .inprocess import ProcessReason, real_popen
from . import paths
from .paths import SCRIPT
from .slot_scripts import SLOT_HOLD, SLOT_WRAPPED


class SlotCaller:
    def __init__(self, name, process, directory, release, retry):
        self.name, self.process, self.directory, self.release, self.retry = name, process, directory, release, retry
        self.waits = 0

    @property
    def started(self):
        return (self.directory / "started").exists()


class SlotCallers:
    """Real slot processes on one scratch governor. Each command holds its slot until the test writes to its fifo unless start is given hold, which leaves the slot after that many seconds.

    A stepped caller runs land.py with time.sleep replaced. It marks each failed attempt with a waiting-N file and
    makes its next attempt when the test writes to its retry fifo, so the test decides which waiter tries next.
    A marked caller writes the same waiting-N files and retries on its own.
    Each instance on one fixture writes its own log and its own caller directories.
    """

    def __init__(self, test, slots):
        self.test = test
        self.governor = test.base / "state/pstack-t3/governor"
        self.governor.mkdir(parents=True, exist_ok=True)
        (self.governor / "governor.json").write_text(json.dumps({"slots": slots}))
        # The suite may itself run inside a slot. A child that inherits LAND_SLOT takes no lock,
        # and an inherited LAND_SLOT_FILES would name the suite's slot files.
        self.env = {key: value for key, value in os.environ.items() if key not in {"LAND_SLOT", "LAND_SLOT_FILES"}}
        self.stamp = 0
        while (test.base / f"slot-log-{self.stamp}").exists():
            self.stamp += 1
        self.log = test.base / f"slot-log-{self.stamp}"
        self.log.touch()
        self.callers = []
        self.descriptors = []
        test.addCleanup(self.close)

    def close(self):
        for caller in self.callers:
            if caller.process.poll() is None:
                os.killpg(caller.process.pid, signal.SIGKILL)
            caller.process.wait(timeout=20)
        for descriptor in self.descriptors:
            os.close(descriptor)

    def fifo(self, path):
        os.mkfifo(path)
        # Keep both ends open so a write never waits for a reader.
        self.descriptors.append(os.open(path, os.O_RDWR | os.O_NONBLOCK))
        return self.descriptors[-1]

    def start(self, name, *flags, script=None, stepped=False, marked=False, pool="worker", hold=None):
        """Start a slot caller. With hold, its command leaves the slot after hold seconds and not on release."""
        if script is None:
            script = paths.SCRIPT
        directory = self.test.base / f"caller-{self.stamp}-{name}"
        directory.mkdir()
        release, retry = self.fifo(directory / "release"), self.fifo(directory / "retry")
        command = [*flags, "--", sys.executable, "-c", SLOT_HOLD, str(self.log), name, str(directory / "release"),
                   *([] if hold is None else [str(hold)])]
        if stepped or marked or pool == "landing":
            mode = str(directory / "retry") if stepped else "marked" if marked else ""
            argv = [sys.executable, "-c", SLOT_WRAPPED, str(Path(script).parent),
                    mode, pool, *(command if pool == "worker" else command[1:])]
        else:
            argv = [sys.executable, str(script), "slot", *command]
        process = real_popen(
            ProcessReason.GOVERNOR, argv, env=self.env, cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.callers.append(SlotCaller(name, process, directory, release, retry))
        return self.callers[-1]

    def wait(self, reached, barrier):
        deadline = time.monotonic() + 20
        while not reached():
            if time.monotonic() >= deadline:
                self.test.fail(f"barrier not reached: {barrier}")
            time.sleep(0.01)

    def enter(self, caller):
        self.wait(lambda: caller.started, f"{caller.name} started")
        return caller

    def settle(self, caller):
        """Wait for a stepped caller's attempt to end. Returns "started" or "waiting"."""
        waiting = caller.directory / f"waiting-{caller.waits + 1}"
        self.wait(lambda: caller.started or waiting.exists(), f"{caller.name} finished an attempt")
        if caller.started:
            return "started"
        caller.waits += 1
        return "waiting"

    def step(self, caller):
        os.write(caller.retry, b"x")
        return self.settle(caller)

    def queued(self, caller):
        """Wait until a marked caller has failed its first attempt, which it does only after it takes a ticket."""
        self.wait(lambda: (caller.directory / "waiting-1").exists(), f"{caller.name} waits with a ticket")
        return caller

    def release(self, caller):
        os.write(caller.release, b"x")
        self.test.assertEqual(caller.process.wait(timeout=20), 0)

    def events(self):
        return [tuple(line.split()) for line in self.log.read_text().splitlines()]

    def entries(self):
        return [fields[1] for fields in self.events() if fields[0] == "enter"]

    def peak(self, counted=lambda name: True):
        """Return the most counted callers inside a slot at once."""
        running, peak = set(), 0
        for word, name, *_ in self.events():
            if word == "exit":
                running.remove(name)
                continue
            running.add(name)
            peak = max(peak, sum(counted(caller) for caller in running))
        return peak
