"""Race and platform hooks for installer tests."""

import ctypes
import errno
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .support import setUpModule

def put_back_race(path, swap, occupy=None, link_error=False, rename=None):
    """Hook code that swaps `path` for a foreign `swap` entry just before the move aside."""
    return (
        "import atexit, ctypes, errno, shutil, tempfile\n"
        f"target = {str(path)!r}\n"
        "moved = False\n"
        "def hooked(original):\n"
        "    def call(source, destination, *args, **kwargs):\n"
        "        global moved\n"
        "        if not moved and str(source) == target:\n"
        "            moved = True\n"
        "            os.unlink(target)\n"
        f"            if {swap!r} == 'directory':\n"
        "                os.mkdir(target)\n"
        "                Path(target, 'precious').write_bytes(b'directory bytes\\x00')\n"
        "            else:\n"
        "                os.symlink('/foreign/swarm', target)\n"
        "        elif moved and str(destination) == target:\n"
        f"            if {occupy!r} == 'directory':\n"
        "                os.mkdir(target)\n"
        f"            elif {occupy!r} == 'file':\n"
        "                Path(target).write_bytes(b'occupant\\x00\\xff')\n"
        "        return original(source, destination, *args, **kwargs)\n"
        "    return call\n"
        "os.rename = hooked(os.rename)\n"
        + without_noreplace(hooked="hooked", link_error=link_error, rename=rename)
    )

def without_noreplace(hooked, link_error, rename):
    """Hook code that wraps the no-replace rename in `hooked`, takes hard links away when `link_error` is set, and takes the rename away for `"symbol"` and `"libc"`, as `put_back_race` describes."""
    return (
        "noreplace = getattr(module, 'rename_noreplace', None)\n"
        "def record():\n"
        "    scratch = tempfile.mkdtemp(dir=os.getcwd())\n"
        "    Path(scratch, 'source').write_bytes(b'')\n"
        "    source, destination = os.path.join(scratch, 'source'), os.path.join(scratch, 'target')\n"
        "    code = noreplace(source, destination) if noreplace else errno.ENOSYS\n"
        "    Path(os.getcwd(), 'noreplace-result').write_text(str(code))\n"
        "    shutil.rmtree(scratch)\n"
        "atexit.register(record)\n"
        "if noreplace:\n"
        f"    module.rename_noreplace = {hooked}(noreplace)\n"
        f"if {link_error!r}:\n"
        "    def denied(*args, **kwargs):\n"
        "        raise OSError(errno.EPERM, 'hard links prohibited')\n"
        "    os.link = denied\n"
        f"if {rename is not None}:\n"
        "    linking = os.link\n"
        "    def no_proc(source, *args, **kwargs):\n"
        "        if str(source).startswith('/proc/'):\n"
        "            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), source)\n"
        "        return linking(source, *args, **kwargs)\n"
        "    os.link = no_proc\n"
        f"if {rename!r} == 'platform':\n"
        "    sys.platform = 'darwin'\n"
        f"elif {rename!r} == 'symbol':\n"
        "    ctypes.CDLL = lambda *args, **kwargs: object()\n"
        f"elif {rename!r} == 'libc':\n"
        "    def missing(*args, **kwargs):\n"
        "        raise OSError('no libc')\n"
        "    ctypes.CDLL = missing\n"
    )

def restore_race(path, occupy, link_error=False, rename=None):
    """Hook code that fills `path` just before the first call that would write a backup onto it."""
    return (
        "import atexit, ctypes, errno, shutil, subprocess, tempfile\n"
        f"target = {str(path)!r}\n"
        f"occupy = {occupy!r}\n"
        "filled = False\n"
        "def fill():\n"
        "    global filled\n"
        "    if filled:\n"
        "        return\n"
        "    filled = True\n"
        "    if occupy == 'file':\n"
        "        Path(target).write_bytes(b'occupant\\x00\\xff')\n"
        "    elif occupy == 'directory':\n"
        "        os.mkdir(target)\n"
        "        Path(target, 'precious').write_bytes(b'directory bytes\\x00')\n"
        "    elif occupy == 'link':\n"
        "        os.symlink('/foreign/occupant', target)\n"
        "    else:\n"
        "        child = subprocess.run(occupy, env=os.environ, capture_output=True, text=True)\n"
        "        assert child.returncode == 0, child.stdout + child.stderr\n"
        "def filling(original, hard_link=False):\n"
        "    def call(source, destination, *args, **kwargs):\n"
        "        entry = os.readlink(source) if str(source).startswith('/proc/self/fd/') else source\n"
        "        # A directory cannot be hard linked, so its occupant arrives at the rename that follows, when there is one.\n"
        f"        directory = hard_link and {rename is None} and os.path.isdir(entry) and not os.path.islink(entry)\n"
        "        if not filled and str(destination) == target and not directory:\n"
        "            fill()\n"
        "        return original(source, destination, *args, **kwargs)\n"
        "    return call\n"
        "shutil.move = filling(shutil.move)\n"
        "os.rename = filling(os.rename)\n"
        + without_noreplace(hooked="filling", link_error=link_error, rename=rename)
        # The occupant also arrives at a hard link that is then refused.
        + "os.link = filling(os.link, hard_link=True)\n"
        f"if {rename is not None}:\n"
        "    holding = module.link_held\n"
        "    def occupied_first(fd, path):\n"
        "        global filled\n"
        "        if not filled and str(path) == target:\n"
        "            fill()\n"
        "        return holding(fd, path)\n"
        "    module.link_held = occupied_first\n"
        + "atexit.register(lambda: Path(os.getcwd(), 'filled').write_text(str(filled)))\n"
    )

def cross_device(backups, path, occupy=None, rename=None):
    """Hook code that fails every move out of `backups` the way a move onto another filesystem fails."""
    return (
        "import atexit, ctypes, errno, shutil, tempfile\n"
        f"backups = {str(backups) + os.sep!r}\n"
        f"target = {str(path)!r}\n"
        f"occupy = {occupy!r}\n"
        "crossed = 0\n"
        "crossed_by_name = 0\n"
        "filled = False\n"
        "def crossing(original, raises=False):\n"
        "    def call(source, destination, *args, **kwargs):\n"
        "        global crossed, crossed_by_name, filled\n"
        "        entry = os.readlink(source) if str(source).startswith('/proc/self/fd/') else str(source)\n"
        "        if entry.startswith(backups) and not str(destination).startswith(backups):\n"
        "            crossed += 1\n"
        "            if not str(source).startswith('/proc/self/fd/'):\n"
        "                crossed_by_name += 1\n"
        "            if raises:\n"
        "                raise OSError(errno.EXDEV, os.strerror(errno.EXDEV))\n"
        "            return errno.EXDEV\n"
        "        if occupy and not filled and str(destination) == target:\n"
        "            filled = True\n"
        "            if occupy == 'file':\n"
        "                Path(target).write_bytes(b'occupant\\x00\\xff')\n"
        "            else:\n"
        "                os.mkdir(target)\n"
        "                Path(target, 'precious').write_bytes(b'directory bytes\\x00')\n"
        "        return original(source, destination, *args, **kwargs)\n"
        "    return call\n"
        "os.link = crossing(os.link, raises=True)\n"
        "os.rename = crossing(os.rename, raises=True)\n"
        + without_noreplace(hooked="crossing", link_error=False, rename=rename)
        + "atexit.register(lambda: Path(os.getcwd(), 'crossed').write_text(str(crossed)))\n"
        + "atexit.register(lambda: Path(os.getcwd(), 'crossed-by-name').write_text(str(crossed_by_name)))\n"
        + "atexit.register(lambda: Path(os.getcwd(), 'filled').write_text(str(filled)))\n"
    )

def crossings(home):
    """How many moves out of the backups a `cross_device` run failed."""
    return int((home / "crossed").read_text())

def crossings_by_name(home):
    """How many moves out of the backups a `cross_device` run failed by name, apart from a held-descriptor link."""
    return int((home / "crossed-by-name").read_text())

def noreplace_result(home):
    """What the installer's no-replace rename returned in a `put_back_race` run: 0 when it renames, else the errno."""
    return int((home / "noreplace-result").read_text())

def backup_race(backup, path, events, foreign="file", proc=True, links=True):
    """Hook code that changes what the backup's path names in the middle of a restore."""
    return (
        "import atexit, errno, shutil\n"
        f"backup, target, saved = {str(backup)!r}, {str(path)!r}, {str(backup) + '.saved'!r}\n"
        f"events = {list(events)!r}\n"
        "fired = []\n"
        "renaming = os.rename\n"
        "def change(action):\n"
        "    if action == 'swap':\n"
        "        if os.path.lexists(backup):\n"
        "            renaming(backup, saved)\n"
        f"        if {foreign!r} == 'directory':\n"
        "            os.mkdir(backup)\n"
        "            Path(backup, 'precious').write_bytes(b'foreign directory\\x00')\n"
        "        else:\n"
        "            Path(backup).write_bytes(b'foreign\\x00\\xff')\n"
        "    elif action == 'squat':\n"
        "        Path(backup).write_bytes(b'squatter\\x00')\n"
        "    elif action == 'vanish':\n"
        "        shutil.rmtree(os.path.dirname(backup))\n"
        "    else:\n"
        "        Path(target).write_bytes(b'occupant\\x00\\xff')\n"
        "def racing(name, original):\n"
        "    def call(source, destination, *args, **kwargs):\n"
        "        due = [\n"
        "            event for event in events\n"
        "            if event not in fired and event[0] == name\n"
        "            and (str(source) == backup if event[1] == 'from' else str(destination) == target)\n"
        "        ]\n"
        "        for event in due:\n"
        "            if event[2] == 'before':\n"
        "                fired.append(event)\n"
        "                change(event[3])\n"
        "        result = original(source, destination, *args, **kwargs)\n"
        "        # A hard link and a rename return nothing. The no-replace rename returns 0 when it worked.\n"
        "        if not result:\n"
        "            for event in due:\n"
        "                if event not in fired:\n"
        "                    fired.append(event)\n"
        "                    change(event[3])\n"
        "        return result\n"
        "    return call\n"
        "linking = os.link\n"
        "def limited(source, *args, **kwargs):\n"
        f"    if not {links!r}:\n"
        "        raise OSError(errno.EPERM, 'hard links prohibited')\n"
        f"    if not {proc!r} and str(source).startswith('/proc/'):\n"
        "        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), source)\n"
        "    return linking(source, *args, **kwargs)\n"
        "os.link = racing('link', limited)\n"
        "os.rename = racing('rename', renaming)\n"
        "module.rename_noreplace = racing('noreplace', module.rename_noreplace)\n"
        "atexit.register(lambda: Path(os.getcwd(), 'fired').write_text(str(len(fired))))\n"
    )

def fired(home):
    """How many events of a `backup_race` run fired."""
    return int((home / "fired").read_text())

SCRAP_LEFT = "an unfinished copy or an already restored backup; delete it by hand"

UNLINK_ASIDE = (
    "import errno\n"
    "unlinking = os.unlink\n"
    "def boom(path, *args, **kwargs):\n"
    "    if '.pstack-t3-' in str(path) and str(path).endswith('/swarm'):\n"
    "        raise OSError(errno.EIO, 'injected unlink failure')\n"
    "    return unlinking(path, *args, **kwargs)\n"
    "os.unlink = boom\n"
)

KILLED_ASIDE = "module.place_proven = lambda *args: os._exit(9)\n"

NO_PROC = (
    "import errno\n"
    "nop_linking = os.link\n"
    "def no_proc(source, *args, **kwargs):\n"
    "    if str(source).startswith('/proc/'):\n"
    "        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), source)\n"
    "    return nop_linking(source, *args, **kwargs)\n"
    "os.link = no_proc\n"
)

def darwin_renamex():
    """Hook code that leaves only `renamex_np` in libc, as on Darwin."""
    return (
        "import ctypes\n"
        "real = ctypes.CDLL(None, use_errno=True)\n"
        "forward = getattr(real, 'renameat2', None)\n"
        "if forward is not None:\n"
        "    forward.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)\n"
        "    forward.restype = ctypes.c_int\n"
        "    def renamex_np(source, target, flags):\n"
        "        assert flags == 4, flags\n"
        "        return forward(-100, source, -100, target, 1)\n"
        "else:\n"
        "    native = real.renamex_np\n"
        "    native.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)\n"
        "    native.restype = ctypes.c_int\n"
        "    def renamex_np(source, target, flags):\n"
        "        assert flags == 4, flags\n"
        "        return native(source, target, flags)\n"
        "fake = type('FakeLibc', (), {})()\n"
        "fake.renamex_np = renamex_np\n"
        "ctypes.CDLL = lambda *args, **kwargs: fake\n"
    )

def extended_attribute(path, name, value=None):
    """Set an xattr when `value` is given, else get it."""
    target = Path(path)
    if value is not None:
        try:
            os.setxattr(target, name, value)
            return os.getxattr(target, name) == value
        except (AttributeError, OSError, NotImplementedError):
            pass
        try:
            import ctypes
            import ctypes.util

            libc_name = ctypes.util.find_library("c")
            if libc_name is None:
                return False
            libc = ctypes.CDLL(libc_name, use_errno=True)
            raw_path = os.fsencode(target)
            raw_name = os.fsencode(name)
            if sys.platform == "darwin":
                mac = getattr(libc, "setxattr", None)
                if mac is not None:
                    mac.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
                    mac.restype = ctypes.c_int
                    if mac(raw_path, raw_name, value, len(value), 0, 0) == 0:
                        return extended_attribute(target, name) == value
            else:
                linux = getattr(libc, "setxattr", None)
                if linux is not None:
                    linux.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
                    linux.restype = ctypes.c_int
                    if linux(raw_path, raw_name, value, len(value), 0) == 0:
                        return extended_attribute(target, name) == value
        except (OSError, AttributeError):
            return False
        return False
    try:
        return os.getxattr(target, name)
    except (AttributeError, OSError, NotImplementedError):
        pass
    try:
        import ctypes
        import ctypes.util

        libc_name = ctypes.util.find_library("c")
        if libc_name is None:
            return None
        libc = ctypes.CDLL(libc_name, use_errno=True)
        raw_path = os.fsencode(target)
        raw_name = os.fsencode(name)
        size = 4096
        buffer = ctypes.create_string_buffer(size)
        if sys.platform == "darwin":
            mac = getattr(libc, "getxattr", None)
            if mac is not None:
                mac.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
                mac.restype = ctypes.c_ssize_t
                got = mac(raw_path, raw_name, buffer, size, 0, 0)
                if got >= 0:
                    return bytes(buffer.raw[:got])
        else:
            linux = getattr(libc, "getxattr", None)
            if linux is not None:
                linux.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t)
                linux.restype = ctypes.c_ssize_t
                got = linux(raw_path, raw_name, buffer, size)
                if got >= 0:
                    return bytes(buffer.raw[:got])
    except (OSError, AttributeError):
        return None
    return None

def holder_swap(saved, foreign="file"):
    """Hook code that swaps a holder entry for a foreign one after its proof, as a same-user race would."""
    file_actor = "import os, sys; from pathlib import Path; os.rename(sys.argv[1], sys.argv[2]); Path(sys.argv[1]).write_bytes(b'foreign\\x00\\xff')"
    dir_actor = "import os, sys; from pathlib import Path; os.rename(sys.argv[1], sys.argv[2]); os.mkdir(sys.argv[1]); Path(sys.argv[1], 'precious').write_bytes(b'foreign directory\\x00')"
    return (
        "import subprocess\n"
        f"saved = {str(saved)!r}\n"
        f"foreign_kind = {foreign!r}\n"
        f"file_actor = {file_actor!r}\n"
        f"dir_actor = {dir_actor!r}\n"
        "swapped = False\n"
        "real_proof = module.same_entry\n"
        "def proof(path, fd):\n"
        "    global swapped\n"
        "    result = real_proof(path, fd)\n"
        "    if result and not swapped and '.pstack-t3-' in str(path):\n"
        "        swapped = True\n"
        "        actor = dir_actor if foreign_kind == 'directory' else file_actor\n"
        "        subprocess.run([sys.executable, '-c', actor, str(path), saved], check=True)\n"
        "    return result\n"
        "module.same_entry = proof\n"
    )
