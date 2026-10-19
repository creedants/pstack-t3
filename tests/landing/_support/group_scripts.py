GROUP_SLEEP = (
    "import os, sys, time\n"
    "from pathlib import Path\n"
    "child = os.fork()\n"
    "if child == 0:\n"
    "    Path(sys.argv[2]).write_text(str(os.getpid()))\n"
    "    time.sleep(3600)\n"
    "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
    "time.sleep(3600)\n"
)


IGNORING_GROUP = (
    "import os, signal, sys, time\n"
    "from pathlib import Path\n"
    "parent_path, child_path = sys.argv[1], sys.argv[2]\n"
    "Path(parent_path).write_text(str(os.getpid()))\n"
    "child = os.fork()\n"
    "if child == 0:\n"
    "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "    Path(child_path).write_text(f'{os.getpid()} {os.getpgrp()}\\n')\n"
    "    time.sleep(3600)\n"
    "time.sleep(3600)\n"
)


SURVIVING_IGNORING_GROUP = (
    "import os, signal, sys, time\n"
    "from pathlib import Path\n"
    "parent_path, child_path = sys.argv[1], sys.argv[2]\n"
    "Path(parent_path).write_text(str(os.getpid()))\n"
    "child = os.fork()\n"
    "if child == 0:\n"
    "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "    Path(child_path).write_text(f'{os.getpid()} {os.getpgrp()}\\n')\n"
    "    os.close(0)\n"
    "    os.close(1)\n"
    "    os.close(2)\n"
    "    time.sleep(3600)\n"
    "time.sleep(3600)\n"
)


BACKGROUND_CHILD = (
    "import os, signal, sys, time\n"
    "from pathlib import Path\n"
    "child_path = sys.argv[1]\n"
    "child = os.fork()\n"
    "if child == 0:\n"
    "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "    Path(child_path).write_text(f'{os.getpid()} {os.getpgrp()}\\n')\n"
    "    os.close(0)\n"
    "    os.close(1)\n"
    "    os.close(2)\n"
    "    time.sleep(3600)\n"
    "path = Path(child_path)\n"
    "deadline = time.monotonic() + 10\n"
    "while '\\n' not in (path.read_text() if path.exists() else '') and time.monotonic() < deadline:\n"
    "    time.sleep(0.01)\n"
    "raise SystemExit(0)\n"
)


SLOW_PUBLISH_MAIN = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "land.EXCLUSIVE_CAP_GRACE = 0.05\n"
    "original = land._publish\n"
    "def slow_publish(*args, **kwargs):\n"
    "    print(f'all-slots-taken {time.monotonic()}', flush=True)\n"
    "    time.sleep(0.6)\n"
    "    return original(*args, **kwargs)\n"
    "land._publish = slow_publish\n"
    "code = land.main(sys.argv[2:])\n"
    "print(f'exit {time.monotonic()}', flush=True)\n"
    "sys.exit(code)\n"
)


WAIT_SCRIPT = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "land.REPORT_AFTER = float(sys.argv[2])\n"
    "land.REPORT_EVERY = float(sys.argv[3])\n"
    "kind, repo, command = sys.argv[4:7]\n"
    "slot = land.exclusive_slot if kind == 'exclusive' else (lambda **kw: land.slot('worker', **kw))\n"
    "with slot(repo=repo, command=command):\n"
    "    print('ran', flush=True)\n"
)


REPORT_SCRIPT = (
    "import fcntl, os, sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "marker, release, target, after, every, repo, command = sys.argv[2:9]\n"
    "land.REPORT_AFTER = float(after)\n"
    "land.REPORT_EVERY = float(every)\n"
    "paused = []\n"
    "def slow_busy(handles, name):\n"
    "    try:\n"
    "        fcntl.flock(handles[name], fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "    except BlockingIOError:\n"
    "        return True\n"
    "    if name != target or paused:\n"
    "        fcntl.flock(handles[name], fcntl.LOCK_UN)\n"
    "        return False\n"
    "    paused.append(name)\n"
    "    Path(marker).touch()\n"
    "    os.read(os.open(release, os.O_RDONLY), 1)\n"
    "    fcntl.flock(handles[name], fcntl.LOCK_UN)\n"
    "    return False\n"
    "land._slot_busy = slow_busy\n"
    "with land.slot('worker', repo=repo, command=command):\n"
    "    print('ran', flush=True)\n"
)


RECORD_REPO_SCRIPT = (
    "import json, os\n"
    "from pathlib import Path\n"
    "print(json.loads(Path(os.environ['XDG_STATE_HOME'], 'pstack-t3', 'governor', 'holds', 'worker-0').read_text())['repo'])\n"
)


