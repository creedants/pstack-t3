#!/usr/bin/env python3
"""Install pstack-t3 skills into every provider skill directory T3 reads.

T3's `$` picker lists each provider's native skills, so pstack-t3 links its
generated skills into each provider's directory. Every link and every entry
moved aside is recorded in a manifest so `uninstall` restores the prior state.
"""

from __future__ import annotations

import argparse
import codecs
import errno
import io
import os
import sys

import sys as _sys
import types as _types
import uuid as _uuid
from pathlib import Path as _Path
from pathlib import Path

IGNORED = "XDG_CONFIG_HOME={value!r} is not an absolute path, so it is ignored and the config home is {home!r}"


def config_home(environ, default):
    value = environ.get("XDG_CONFIG_HOME")
    if not value:
        return Path(default()), None
    if not os.path.isabs(value):
        home = Path(default())
        return home, IGNORED.format(value=value, home=str(home))
    return Path(value), None


def new_file(directory):
    """Create a file in `directory` under a name no entry has. Return its descriptor, open for writing, and its path.

    The path is os.path.join(directory, name) with `directory` as given. The name is "tmp" and eight hexadecimal digits.
    tempfile.mkstemp is not used because it passes its directory through os.path.abspath. Like mkstemp, this passes
    mode 0o600 to os.open. After 100 names that are taken it raises FileExistsError.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _attempt in range(100):
        temporary = os.path.join(directory, f"tmp{os.urandom(4).hex()}")
        try:
            return os.open(temporary, flags, 0o600), temporary
        except FileExistsError:
            continue
    raise FileExistsError(errno.EEXIST, "no unused temporary name", os.fspath(directory))


_libdir = _Path(__file__).resolve().parent / "_install"


def _drop_cached_install(*names):
    for _n in [n for n in _sys.modules if any(n == name or n.startswith(name + ".") for name in names)]:
        del _sys.modules[_n]


_prior_install = {n: m for n, m in _sys.modules.items() if n == "_install" or n.startswith("_install.")}
_drop_cached_install("_install")
_pkg = "_install_ns_" + _uuid.uuid4().hex
_alias = _types.ModuleType(_pkg)
_alias.__path__ = [str(_libdir)]
_sys.modules[_pkg] = _alias
_sys.modules["_install"] = _alias
_bw = _sys.dont_write_bytecode
_sys.dont_write_bytecode = True
try:
    from _install import layout as _layout
    from _install import records as _records
    from _install import plans as _plans
    from _install import moves as _moves
    from _install import restore as _restore
    from _install import strays as _strays
    from _install import run as _run
    from _install import doctor as _doctor
finally:
    _sys.dont_write_bytecode = _bw
    _drop_cached_install("_install", _pkg)
    _sys.modules.update(_prior_install)
del _bw, _drop_cached_install, _pkg, _alias, _libdir, _types, _uuid, _prior_install

_OWNERS = {}
_modules = (_layout, _records, _plans, _moves, _restore, _strays, _run, _doctor)
for _mod in _modules:
    for _name in dir(_mod):
        if _name.startswith("_"):
            continue
        if _name in ("config_home", "new_file"):
            continue
        if _name in globals():
            continue
        _value = getattr(_mod, _name)
        if _value in _modules:
            continue
        globals()[_name] = _value
        _OWNERS[_name] = _mod
_OWNERS["config_home"] = _layout
_OWNERS["new_file"] = _records
_layout.config_home = config_home
_records.new_file = new_file


class _Facade(_sys.modules[__name__].__class__):
    def __setattr__(self, name, value):
        if name in _OWNERS:
            try:
                setattr(_OWNERS[name], name, value)
            except Exception:
                pass
        super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _Facade
del _sys, _Path


ESCAPING = "pstack-t3-escaping"


def escape_refused(stream):
    if not isinstance(stream, io.TextIOWrapper):
        return
    try:
        current = codecs.lookup_error(stream.errors)
    except LookupError:
        return

    def escaping(error):
        try:
            return current(error)
        except UnicodeError:
            return codecs.backslashreplace_errors(error)

    codecs.register_error(ESCAPING, escaping)
    stream.reconfigure(errors=ESCAPING)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "uninstall", "doctor"), nargs="?", default="install")
    parser.add_argument("--project", help="install into this repository instead of the user's skill directories")
    parser.add_argument("--harness", default="all", help="comma list of " + ",".join(HARNESSES) + ", or all")
    parser.add_argument("--replace", action="store_true", help="move conflicting skills aside; uninstall restores them")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.harness = HARNESSES if args.harness == "all" else tuple(item.strip() for item in args.harness.split(","))
    unknown = set(args.harness) - set(HARNESSES)
    if unknown:
        parser.error(f"unknown harness {', '.join(sorted(unknown))}")
    escape_refused(sys.stdout)
    try:
        return {"install": install, "uninstall": uninstall, "doctor": doctor}[args.command](args) or 0
    except Unreadable as error:
        sys.exit(str(error))


if __name__ == "__main__":
    sys.exit(main())
