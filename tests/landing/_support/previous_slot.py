PREVIOUS_SLOT_SCRIPT = '''\
import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path


class LandError(Exception):
    pass


def state_home():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "pstack-t3"


def governor_dir():
    return state_home() / "governor"


def governor_slots():
    config = governor_dir() / "governor.json"
    if config.exists():
        return max(1, int(json.loads(config.read_text()).get("slots", 1)))
    return max(1, min(4, (os.cpu_count() or 4) // 4))


@contextlib.contextmanager
def exclusive_slot():
    """Hold every slot, landing included, so a benchmark runs on a quiet machine.
    Slots are taken in a fixed order with blocking locks, so two exclusive callers cannot deadlock."""
    if os.environ.get("LAND_SLOT"):
        raise LandError("an exclusive slot must be the outermost slot; this command already runs inside one")
    directory = governor_dir()
    directory.mkdir(parents=True, exist_ok=True)
    names = ["landing-0"] + [f"worker-{index}" for index in range(governor_slots())]
    handles = [open(directory / name, "a") for name in names]
    try:
        for handle in handles:
            fcntl.flock(handle, fcntl.LOCK_EX)
        os.environ["LAND_SLOT"] = "exclusive"
        yield
    finally:
        os.environ.pop("LAND_SLOT", None)
        for handle in handles:
            handle.close()


@contextlib.contextmanager
def slot(pool="worker"):
    """Hold one slot of a pool. Landing checks use their own one-slot pool so workers never starve the queue.
    A command already inside a slot (LAND_SLOT set) runs without taking another, so nesting cannot deadlock."""
    if os.environ.get("LAND_SLOT"):
        yield
        return
    directory = governor_dir()
    directory.mkdir(parents=True, exist_ok=True)
    count = 1 if pool == "landing" else governor_slots()
    handles = [open(directory / f"{pool}-{index}", "a") for index in range(count)]
    held = None
    try:
        while held is None:
            for handle in handles:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = handle
                    break
                except BlockingIOError:
                    continue
            if held is None:
                time.sleep(0.5)
        os.environ["LAND_SLOT"] = pool
        yield
    finally:
        os.environ.pop("LAND_SLOT", None)
        for handle in handles:
            handle.close()


def main(argv=None):
    try:
        argv = sys.argv[1:] if argv is None else argv
        parser = argparse.ArgumentParser(prog="land.py")
        sub = parser.add_subparsers(dest="command", required=True)
        slot_parser = sub.add_parser("slot")
        slot_parser.add_argument("--exclusive", action="store_true")
        slot_parser.add_argument("cmd", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv)
        command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        if not command:
            raise LandError("slot needs a command after --")
        with (exclusive_slot() if args.exclusive else slot()):
            return subprocess.run(command).returncode
    except LandError as error:
        print(f"land: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
'''
