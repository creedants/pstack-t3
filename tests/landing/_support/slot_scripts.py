SLOT_HOLD = (
    "import os, sys, time\n"
    "from pathlib import Path\n"
    "log, name, fifo, *hold = sys.argv[1:]\n"
    "def mark(word):\n"
    "    files = os.environ.get('LAND_SLOT_FILES', '')\n"
    "    line = f'{word} {name}' + (f' {files}' if files else '')\n"
    "    out = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)\n"
    "    os.write(out, (line + '\\n').encode())\n"
    "    os.close(out)\n"
    "release = os.open(fifo, os.O_RDONLY)\n"
    "mark('enter')\n"
    "Path('started').touch()\n"
    "if hold:\n"
    "    time.sleep(float(hold[0]))\n"
    "else:\n"
    "    assert os.read(release, 1) == b'x'\n"
    "mark('exit')\n"
    "Path('finished').touch()\n"
)


SLOT_WRAPPED = (
    "import fcntl, os, subprocess, sys, time\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "flock = fcntl.flock\n"
    "def announced(handle, operation):\n"
    "    if operation == fcntl.LOCK_EX:\n"
    "        try:\n"
    "            return flock(handle, operation | fcntl.LOCK_NB)\n"
    "        except BlockingIOError:\n"
    "            Path('blocking').touch()\n"
    "    return flock(handle, operation)\n"
    "land.fcntl.flock = announced\n"
    "count = 0\n"
    "sleep = time.sleep\n"
    "def waiting(seconds):\n"
    "    global count\n"
    "    count += 1\n"
    "    Path(f'waiting-{count}').touch()\n"
    "    if sys.argv[2] == 'marked':\n"
    "        sleep(seconds)\n"
    "        return\n"
    "    gate = os.open(sys.argv[2], os.O_RDONLY)\n"
    "    assert os.read(gate, 1) == b'x'\n"
    "    os.close(gate)\n"
    "if sys.argv[2]:\n"
    "    land.time.sleep = waiting\n"
    "if sys.argv[3] == 'landing':\n"
    "    with land.slot('landing'):\n"
    "        sys.exit(subprocess.run(sys.argv[4:]).returncode)\n"
    "sys.exit(land.main(['slot', *sys.argv[4:]]))\n"
)


HOLD_SCRIPT = (
    "import os, sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "kind, repo, command, ready, release = sys.argv[2:7]\n"
    "slot = land.exclusive_slot if kind == 'exclusive' else (lambda **kw: land.slot(kind, **kw))\n"
    "with slot(repo=repo, command=command):\n"
    "    Path(ready).touch()\n"
    "    os.read(os.open(release, os.O_RDONLY), 1)\n"
)


EXCLUSIVE_MAIN = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "cap, grace, cap_max = sys.argv[2:5]\n"
    "if cap:\n"
    "    land.EXCLUSIVE_CAP = float(cap)\n"
    "if grace:\n"
    "    land.EXCLUSIVE_CAP_GRACE = float(grace)\n"
    "if cap_max:\n"
    "    land.EXCLUSIVE_CAP_MAX = float(cap_max)\n"
    "sys.exit(land.main(sys.argv[5:]))\n"
)


EXCLUSIVE_MAIN_WITHOUT_WAITID = "import os\nvars(os).pop('waitid', None)\n" + EXCLUSIVE_MAIN


CAP_CLOCK_MAIN = (
    "import os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "from pathlib import Path\n"
    "cap, grace, cap_max = sys.argv[2:5]\n"
    "if cap:\n"
    "    land.EXCLUSIVE_CAP = float(cap)\n"
    "if grace:\n"
    "    land.EXCLUSIVE_CAP_GRACE = float(grace)\n"
    "if cap_max:\n"
    "    land.EXCLUSIVE_CAP_MAX = float(cap_max)\n"
    "clock = Path(os.environ['LAND_CAP_CLOCK'])\n"
    "def file_clock():\n"
    "    try:\n"
    "        return float(clock.read_text().strip())\n"
    "    except (OSError, ValueError):\n"
    "        return 1000.0\n"
    "land._cap_monotonic = file_clock\n"
    "sys.exit(land.main(sys.argv[5:]))\n"
)


READY_MAIN = (
    "import os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "from pathlib import Path\n"
    "cap, grace, cap_max = sys.argv[2:5]\n"
    "if cap:\n"
    "    land.EXCLUSIVE_CAP = float(cap)\n"
    "if grace:\n"
    "    land.EXCLUSIVE_CAP_GRACE = float(grace)\n"
    "if cap_max:\n"
    "    land.EXCLUSIVE_CAP_MAX = float(cap_max)\n"
    "ready = Path(os.environ['LAND_EXCLUSIVE_READY'])\n"
    "def ack():\n"
    "    try:\n"
    "        ready.touch()\n"
    "    except OSError:\n"
    "        pass\n"
    "land._exclusive_ready_ack = ack\n"
    "sys.exit(land.main(sys.argv[5:]))\n"
)


FAILED_WITNESS_PROBE = (
    "import os, subprocess\n"
    "vars(os).pop('waitid', None)\n"
    "_real_run = subprocess.run\n"
    "def _ps(*args, **kwargs):\n"
    "    command = args[0] if args else kwargs.get('args')\n"
    "    if isinstance(command, (list, tuple)) and command[:1] == ['ps']:\n"
    "        raise OSError('ps is unavailable')\n"
    "    return _real_run(*args, **kwargs)\n"
    "subprocess.run = _ps\n"
)


FOREIGN_UID_WITNESS_PROBE = (
    "import os, subprocess\n"
    "land.os.geteuid = lambda: 1000\n"
    "_real_run = subprocess.run\n"
    "_uid = next(uid for uid in (1, 2, 3) if uid not in (os.getuid(), 1000))\n"
    "_line = '%d %d %d\\n' % (_uid, _uid, _uid)\n"
    "def _ps(*args, **kwargs):\n"
    "    command = args[0] if args else kwargs.get('args')\n"
    "    if isinstance(command, (list, tuple)) and any('uid=' in str(part) for part in command):\n"
    "        return subprocess.CompletedProcess(list(command), 0, stdout=_line)\n"
    "    return _real_run(*args, **kwargs)\n"
    "subprocess.run = _ps\n"
)


EPERM_KILLPG_STUB = (
    "import errno, os, signal\n"
    "_real_killpg = os.killpg\n"
    "_sent = []\n"
    "_leader = []\n"
    "def _killpg(pid, sig):\n"
    "    _leader.append(pid)\n"
    "    if {kills!r}:\n"
    "        os.kill(pid, signal.SIGKILL)\n"
    "    if {mode!r} == 'always' or ({mode!r} == 'after-term' and _sent):\n"
    "        raise PermissionError(errno.EPERM, 'Operation not permitted')\n"
    "    _sent.append(sig)\n"
    "    _real_killpg(pid, sig)\n"
    "os.killpg = _killpg\n"
)


INTERRUPT_AFTER_THE_LEADER_EXITS = (
    "import os, time\n"
    "_real_time_sleep = time.sleep\n"
    "def _interrupt(seconds):\n"
    "    if _leader and land._leader_is_permitted_zombie(_leader[0]):\n"
    "        raise KeyboardInterrupt()\n"
    "    _real_time_sleep(seconds)\n"
    "land._real_sleep = _interrupt\n"
)


def _darwin_eperm_main(mode="after-term", kills=False, probe="waitid", darwin=True, after=""):
    """Return EXCLUSIVE_MAIN with os.killpg replaced by a stub, forcing the Darwin platform when darwin is true.

    mode picks the signals that raise EPERM, and kills kills the leader itself first. probe picks the witness probe, and is one of
    waitid, failed, or foreign. darwin False leaves the platform alone, so the Linux branch runs on a Linux host.
    after holds extra lines the wrapper runs after the killpg stub, and may read that stub's state.
    """
    lines = ["import land\n"]
    if darwin:
        lines.append("land.sys.platform = 'darwin'\n")
    if probe == "failed":
        lines.append(FAILED_WITNESS_PROBE)
    elif probe == "foreign":
        lines.append(FOREIGN_UID_WITNESS_PROBE)
    lines.append(EPERM_KILLPG_STUB.format(mode=mode, kills=kills))
    lines.append(after)
    return EXCLUSIVE_MAIN.replace("import land\n", "".join(lines), 1)
