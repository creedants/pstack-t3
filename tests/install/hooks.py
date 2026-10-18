"""Wrappers that run a checkout installer with a hook."""

import errno
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests import in_process

from .support import _env
from .support import setUpModule

def _wrapper(home, stem, checkout):
    wrapper = home / f"{stem}-{checkout.name}.py"
    installer = str(checkout / "scripts" / "install.py")
    return wrapper, installer


def _invocation(home, wrapper, argv, env=None, cwd=None):
    return in_process.Invocation(program=wrapper, argv=tuple(argv), env={**_env(home), **(env or {})}, cwd=cwd or home)


def pause_after_load(home, checkout, args):
    ready = home / f"ready-{checkout.name}"
    go = home / f"go-{checkout.name}"
    wrapper, installer = _wrapper(home, "pause", checkout)
    wrapper.write_text(
        "import importlib.util, sys, time\n"
        "from pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "original = module.load\n"
        "def paused(*args):\n"
        "    result = original(*args)\n"
        f"    Path({str(ready)!r}).touch()\n"
        "    deadline = time.monotonic() + 30\n"
        f"    while not Path({str(go)!r}).exists():\n"
        "        if time.monotonic() > deadline:\n"
        "            raise RuntimeError('barrier timeout')\n"
        "        time.sleep(0.01)\n"
        "    return result\n"
        "module.load = paused\n"
        "sys.exit(module.main())\n"
    )
    proc = in_process.spawn(in_process.Reason.RACE, in_process.Invocation(
        program=wrapper,
        argv=tuple(args),
        env=_env(home),
        cwd=home,
    ))
    return proc, ready, go

def squat_uninstall(home, checkout, path, payload, *args):
    wrapper, installer = _wrapper(home, "squat", checkout)
    wrapper.write_text(
        "import importlib.util, sys, os\n"
        "from pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "changed = False\n"
        "def after(original):\n"
        "    def race(target, *args, **kwargs):\n"
        "        global changed\n"
        "        result = original(target, *args, **kwargs)\n"
        f"        if str(target) == {str(path)!r} and not changed:\n"
        "            changed = True\n"
        f"            Path(target).write_bytes({payload!r})\n"
        "        return result\n"
        "    return race\n"
        "os.rename = after(os.rename)\n"
        "os.unlink = after(os.unlink)\n"
        "sys.exit(module.main())\n"
    )
    return in_process.invoke(in_process.Invocation(
        program=wrapper,
        argv=tuple(args),
        env=_env(home),
        cwd=home,
    ))

def run_failing(home, checkout, call, path, *args):
    module, name = call.split(".")
    wrapper, installer = _wrapper(home, "fail", checkout)
    wrapper.write_text(
        "import errno, importlib.util, os, shutil, sys\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "installer = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = installer\n"
        "spec.loader.exec_module(installer)\n"
        f"original = getattr({module}, {name!r})\n"
        "def failing(*args, **kwargs):\n"
        f"    if {str(path)!r} in [str(arg) for arg in args]:\n"
        f"        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), {str(path)!r})\n"
        "    return original(*args, **kwargs)\n"
        f"setattr({module}, {name!r}, failing)\n"
        "sys.exit(installer.main())\n"
    )
    return in_process.invoke(in_process.Invocation(
        program=wrapper,
        argv=tuple(args),
        env=_env(home),
        cwd=home,
    ))

def interleave_removal(home, checkout, paths, call, *args, occupy=None):
    """Run `call` once, just before this installer first moves or deletes any of `paths`, then write `occupy` there."""
    wrapper, installer = _wrapper(home, "interleave", checkout)
    wrapper.write_text(
        "import importlib.util, os, subprocess, sys\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "fired = False\n"
        "def before(original):\n"
        "    def hooked(target, *args, **kwargs):\n"
        "        global fired\n"
        f"        if not fired and str(target) in {[str(path) for path in paths]!r}:\n"
        "            fired = True\n"
        f"            child = subprocess.run({[str(part) for part in call]!r}, env=os.environ, capture_output=True, text=True)\n"
        "            assert child.returncode == 0, child.stdout + child.stderr\n"
        "            result = original(target, *args, **kwargs)\n"
        f"            if {occupy!r} is not None:\n"
        f"                open(target, 'wb').write({occupy!r})\n"
        "            return result\n"
        "        return original(target, *args, **kwargs)\n"
        "    return hooked\n"
        "os.rename = before(os.rename)\n"
        "os.unlink = before(os.unlink)\n"
        "status = module.main()\n"
        "assert fired, 'the interleaved call never ran'\n"
        "sys.exit(status)\n"
    )
    return in_process.finished(in_process.Reason.RACE, in_process.Invocation(
        program=wrapper,
        argv=tuple(args),
        env=_env(home),
        cwd=home,
    ))

def uninstall_hooked(home, checkout, code, *, reason=None):
    """Run this checkout's uninstall with `code` run first against the loaded installer, named `module`."""
    wrapper, installer = _wrapper(home, "hooked", checkout)
    wrapper.write_text(
        "import importlib.util, os, sys\n"
        "from pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        f"{code}\n"
        "sys.exit(module.main())\n"
    )
    invocation = in_process.Invocation(
        program=wrapper,
        argv=("--harness", "grok", "uninstall"),
        env=_env(home),
        cwd=home,
    )
    if reason is None:
        return in_process.invoke(invocation)
    return in_process.finished(reason, invocation)

def bind_mounts_refused():
    """Why `unshare -Urnm` cannot bind-mount a directory on this host, or None when it can."""
    if not all(shutil.which(tool) for tool in ("unshare", "mount", "sh")):
        return "unshare, mount, or sh is not installed, so no bind mount can be made"
    with tempfile.TemporaryDirectory() as directory:
        probe = subprocess.run(["unshare", "-Urnm", "mount", "--bind", directory, directory], capture_output=True, text=True)
    if probe.returncode:
        return f"`unshare -Urnm mount --bind` failed here, so no bind mount can be made ({probe.stderr.strip() or probe.returncode})"
    return None

def run_bound(home, checkout, binds, *args, env=None):
    """Run this checkout's installer with `args` in new user and mount namespaces, after bind-mounting each (source, target) of `binds` in order.

    The mounts exist only in those namespaces and end with the run.
    """
    script = 'while [ "$1" != -- ]; do mount --bind "$1" "$2" || exit 97; shift 2; done; shift; exec "$@"'
    pairs = [str(path) for pair in binds for path in pair]
    return subprocess.run(
        ["unshare", "-Urnm", "sh", "-c", script, "sh", *pairs, "--", sys.executable, str(Path(checkout) / "scripts" / "install.py"), *args],
        env={**_env(home), **(env or {})},
        cwd=home,
        capture_output=True,
        text=True,
    )

def install_hooked(home, checkout, code, *args):
    """Run this checkout's installer with `args`, with `code` run first against the loaded installer, named `module`."""
    wrapper, installer = _wrapper(home, "hooked-install", checkout)
    wrapper.write_text(
        "import importlib.util, os, sys\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        f"{code}\n"
        "sys.exit(module.main())\n"
    )
    return in_process.invoke(in_process.Invocation(
        program=wrapper,
        argv=tuple(args),
        env=_env(home),
        cwd=home,
    ))

def doctor_counting_plans(home, checkout, *args):
    """Run this checkout's doctor with `args`. Its stderr is the number of times it called `plan_install`."""
    code = (
        "import atexit\n"
        "calls = []\n"
        "original = module.plan_install\n"
        "def counting(*args, **kwargs):\n"
        "    calls.append(1)\n"
        "    return original(*args, **kwargs)\n"
        "module.plan_install = counting\n"
        "atexit.register(lambda: sys.stderr.write(str(len(calls))))\n"
    )
    return install_hooked(home, checkout, code, "doctor", *args)

def refused_move(path, *first):
    """Hook code that makes `shutil.move` of `path` raise before it copies anything.

    Each statement of `first` runs just before the error, with `destination` naming where the move was going.
    """
    return (
        "import errno, shutil\n"
        "moving = shutil.move\n"
        "def refused(source, destination, *args, **kwargs):\n"
        f"    if str(source) == {str(path)!r}:\n"
        + "".join(f"        {statement}\n" for statement in first)
        + f"        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), {str(path)!r})\n"
        "    return moving(source, destination, *args, **kwargs)\n"
        "shutil.move = refused\n"
    )

def raising(call, within=None):
    """Hook code that makes the installer's own `call` raise an I/O error, only while `within` runs when given."""
    if within is None:
        return (
            "import errno\n"
            "def boom(*args, **kwargs):\n"
            f"    raise OSError(errno.EIO, 'injected {call} failure')\n"
            f"module.{call} = boom\n"
        )
    return (
        "import errno\n"
        f"depth = [0]\n"
        f"real_{within} = module.{within}\n"
        f"def tracking_{within}(*args, **kwargs):\n"
        "    depth[0] += 1\n"
        "    try:\n"
        f"        return real_{within}(*args, **kwargs)\n"
        "    finally:\n"
        "        depth[0] -= 1\n"
        f"module.{within} = tracking_{within}\n"
        f"real_{call} = module.{call}\n"
        f"def boom_{call}(*args, **kwargs):\n"
        "    if depth[0] > 0:\n"
        f"        raise OSError(errno.EIO, 'injected {call} failure')\n"
        f"    return real_{call}(*args, **kwargs)\n"
        f"module.{call} = boom_{call}\n"
    )

def injected(call):
    """What a `raising` run prints for the error it raised."""
    return f"[Errno {errno.EIO}] injected {call} failure"

def unlock_failure(state, count):
    """Hook code that fails the unlock of the state lock, then writes to `count` how many descriptors the run still has open on `state`."""
    return (
        "import fcntl\n"
        "locking = fcntl.flock\n"
        "def flock(fd, operation):\n"
        "    if operation == fcntl.LOCK_UN:\n"
        "        raise OSError(5, 'injected unlock failure')\n"
        "    return locking(fd, operation)\n"
        "fcntl.flock = flock\n"
        "def still_open():\n"
        f"    target = os.stat({str(state)!r})\n"
        "    try:\n"
        "        candidates = [int(name) for name in os.listdir('/dev/fd')]\n"
        "    except OSError:\n"
        "        candidates = range(min(os.sysconf('SC_OPEN_MAX'), 4096))\n"
        "    found = 0\n"
        "    for fd in candidates:\n"
        "        try:\n"
        "            info = os.fstat(fd)\n"
        "        except OSError:\n"
        "            continue\n"
        "        found += (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino)\n"
        "    return found\n"
        "running = module.main\n"
        "def main(*args):\n"
        "    try:\n"
        "        return running(*args)\n"
        "    except OSError as error:\n"
        "        print(f'unlock raised: {error}')\n"
        f"        Path({str(count)!r}).write_text(str(still_open()))\n"
        "        return 0\n"
        "module.main = main\n"
    )
