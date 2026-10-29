#!/usr/bin/env python3

"""Resolve pstack-t3 roles into T3 delegate_task targets.

Roles map a pstack role name to a list of seats. A seat is "inherit" or a
target {"providerInstanceId", "model", "options"} drawn from the catalog that
T3's orchestrator_capabilities tool returns. An unset role resolves to "inherit",
except "review backups", which resolves to "unset".

Two optional keys tune the file. "exclude" is a list of rules that refuse a seat
by model-id glob, by declared option, by the provider instance id, by the role
name, or by a combination of them. The merged list is the union of the user and
project lists. User rules come first. "backups" maps a role to the seats roles.py
backup tries, in order, after a usage limit.
"""


import argparse
import errno
import fnmatch
import functools
import importlib as _importlib
import json
import os
import posixpath
import re
import subprocess
import sys
import sys as _sys
import types as _types
import uuid as _uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path


# Persona and playbook roots resolve from this facade's location.
AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"
TREE_ROOT = Path(__file__).resolve().parents[2]


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


def _add_repo_arguments(command, *, project_config=True):
    """One --cwd, --config, and --project-config group for a subcommand, unless project_config is False."""
    command.add_argument("--cwd", default=os.getcwd())
    command.add_argument("--config", help="user roles file (default ~/.config/pstack-t3/roles.json)")
    if project_config:
        command.add_argument("--project-config", help="project roles file (default <repo>/.pstack/t3-roles.json)")


def _add_parent_argument(command):
    """One --parent argument for a subcommand."""
    command.add_argument("--parent", help="this thread's provider/model from orchestrator_capabilities (inheritedProviderInstanceId/inheritedModel)")


def non_negative_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid non-negative int value: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"invalid non-negative int value: {text!r}")
    return value


_libdir = Path(__file__).resolve().parent / "_roles"


def _drop_cached_roles(*names):
    """Drop every cached module under one of these names, so a load cannot reuse another load's copy."""
    for _n in [n for n in _sys.modules if any(n == name or n.startswith(name + ".") for name in names)]:
        del _sys.modules[_n]


_prior_roles = {n: m for n, m in _sys.modules.items() if n == "_roles" or n.startswith("_roles.")}
_drop_cached_roles("_roles")
_pkg = "_roles_ns_" + _uuid.uuid4().hex
# The alias keeps a unique name so seam metadata cannot resolve to a restored prior package.
_alias = _types.ModuleType(_pkg)
_alias.__path__ = [str(_libdir)]
_sys.modules[_pkg] = _alias
_sys.modules["_roles"] = _alias
_bw = _sys.dont_write_bytecode
_sys.dont_write_bytecode = True
try:
    from _roles import seam
    _seen = {}
    for _name in ("base", "seats", "config", "mode", "resolve", "panel", "problems", "commands", "backup", "brief"):
        _module = _importlib.import_module("_roles." + _name)
        for _key in _module.__all__:
            assert _key not in _seen, f"roles: {_key} is defined in both {_seen[_key]} and {_name}"
            _seen[_key] = _name
            _value = getattr(_module, _key)
            try:
                if getattr(_value, "__module__", None) == "_roles." + _name:
                    _value.__module__ = __name__
            except Exception:
                pass
            globals()[_key] = _value
    del _seen, _name, _module, _key, _value
    seam.bind(globals())
finally:
    _sys.dont_write_bytecode = _bw
    _drop_cached_roles("_roles", _pkg)
    _sys.modules.update(_prior_roles)
del _bw, _drop_cached_roles, _pkg, _alias, _libdir, _prior_roles


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("show", "validate", "write"):
        command = sub.add_parser(name)
        _add_repo_arguments(command, project_config=name != "write")
        command.add_argument("--catalog", required=name != "show", help="saved orchestrator_capabilities JSON, or - for stdin")
        _add_parent_argument(command)
    sub.choices["show"].add_argument("--role", action="append")
    sub.choices["show"].add_argument(
        "--launches-seats",
        action="store_true",
        help="resolve skill tests for a child that launches seats (requires --catalog, --parent, and --role \"skill tests\")",
    )
    write = sub.choices["write"]
    write.add_argument("--budget", choices=list(BUDGETS))
    write.add_argument("--mode", choices=MODES)
    group = write.add_mutually_exclusive_group()
    # None means the flag was omitted. An empty list would replace a stored project list.
    group.add_argument("--escalate", action="append", default=None)
    group.add_argument("--clear-escalate", action="store_true")
    write.add_argument("--set", action="append", help="'<role>=<seat>[;<seat>]', seat = inherit | provider/model[?option=value]")
    write.add_argument("--project", action="store_true", help="write the project file instead of the user file")
    write.add_argument("--keep", action="store_true", help="accepted and ignored")
    write.add_argument("--replace", action="store_true", help="rewrite roles, budget, and mode from the flags and write <file>.bak first when the file exists")
    write.add_argument("--force", action="store_true", help="write despite the problems validate lists, except a seat the exclude list matches on its own form")
    write.add_argument(
        "--allow-opencode",
        action="append",
        default=None,
        help="Add this full OpenCode model id to the allow list in the file being written.",
    )
    write.add_argument(
        "--deny-opencode",
        action="append",
        default=None,
        help="Remove this full OpenCode model id from the allow list in the file being written.",
    )
    mode = sub.add_parser("mode")
    _add_repo_arguments(mode)
    mode.add_argument("--paths", help="comma-separated leases")
    mode.add_argument("--send-backs", type=non_negative_int, default=0,
                      help="send-back count; 2 or more forces full mode")
    mode.add_argument("--escalated",
                      help="recorded one-line escalation reason; forces full mode and wins over paths and send-backs")
    mode.add_argument("--playbook", help="playbook stem, such as bug-fix")
    mode.add_argument("--attempt", choices=ATTEMPTS)
    check_brief = sub.add_parser("check-brief")
    check_brief.add_argument("brief", help="brief file to check")
    backup = sub.add_parser("backup")
    _add_repo_arguments(backup)
    backup.add_argument("--catalog", help="saved orchestrator_capabilities JSON. Stdin is the error text, so - is refused")
    _add_parent_argument(backup)
    backup.add_argument("--role", required=True)
    backup.add_argument("--provider", required=True)
    backup.add_argument("--model", required=True)
    backup.add_argument("--options", help="JSON options array from t3_thread_configure, or a target options object")
    backup.add_argument(
        "--author", action="append", default=None,
        help="author model, or provider/model. Repeat for each model that wrote the diff. Required for a reviewer role",
    )
    backup.add_argument("--out", action="append", default=None, help="provider already out. Repeat for each")
    backup.add_argument("--resume", action="store_true", help="after a parked item's reset, print the seat to launch")
    for command in (sub.choices["show"], backup):
        command.add_argument(
            "--runtime-mode",
            help="this thread's runtimeMode from orchestrator_capabilities. With a catalog, a provider whose "
                 "driverKind is muse counts as not runnable under a mode other than approval-required and full-access",
        )
    for command in (sub.choices["show"], mode, backup):
        command.add_argument("--brief-mode", choices=MODES,
                             help="brief mode; overrides session, coordinator, project, and user modes")
        command.add_argument("--session-mode", choices=MODES,
                             help="session mode; used after brief and before coordinator, project, and user modes")
        command.add_argument("--coordinator-mode", choices=MODES,
                             help="coordinator mode; used after brief and session, before project and user modes")
    args = parser.parse_args(argv)
    if args.command == "write" and not args.project and (args.escalate is not None or args.clear_escalate):
        parser.error("--escalate and --clear-escalate require --project")
    try:
        return {
            "show": command_show,
            "validate": command_validate,
            "write": command_write,
            "check-brief": command_check_brief,
            "mode": command_mode,
            "backup": command_backup,
        }[args.command](args) or 0
    except ModeSettingsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except RolesError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
