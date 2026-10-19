from __future__ import annotations

import errno
import hashlib
import os
import sys
from pathlib import Path

from . import records

config_home = None


def cwd_error():
    try:
        os.getcwd()
    except OSError as error:
        return error
    return None

ROOT = Path(__file__).resolve().parents[2]

SKILLS = ROOT / "skills"

HARNESSES = ("claude", "codex", "grok", "cursor")

LEGACY_NAME = "install-manifest.json"

OWNERS_DIR = "install-owners"

SKIPPED = 3

def skill_dirs(scope_root, user):
    home = Path(os.environ.get("HOME", str(Path.home())))
    claude = Path(os.environ["CLAUDE_CONFIG_DIR"]) if user and os.environ.get("CLAUDE_CONFIG_DIR") else (home / ".claude" if user else scope_root / ".claude")
    base = home if user else scope_root
    return {
        "claude": claude / "skills",
        "codex": base / ".agents" / "skills",
        "grok": base / ".grok" / "skills",
        "cursor": base / ".cursor" / "skills",
    }

def extra_dirs(scope_root, user):
    """Other directories each provider also reads skills from, where stale copies can compete."""
    home = Path(os.environ.get("HOME", str(Path.home())))
    base = home if user else scope_root
    claude = skill_dirs(scope_root, user)["claude"]
    return {
        "claude": [],
        "codex": [base / ".codex" / "skills"],
        "grok": [],
        "cursor": [base / ".agents" / "skills", base / ".codex" / "skills", claude],
    }

def state_dir(scope_root, user):
    if not user:
        return scope_root / ".pstack"
    config, ignored = config_home(os.environ, lambda: Path(os.environ.get("HOME", str(Path.home()))) / ".config")
    if ignored:
        print(ignored, file=sys.stderr)
    return config / "pstack-t3"

def owner_path(state, checkout):
    digest = hashlib.sha256(str(checkout).encode()).hexdigest()[:16]
    return Path(state) / OWNERS_DIR / f"{digest}.json"

def link_target(checkout, path):
    return os.path.join(str(checkout), "skills", os.path.basename(path))

def proves(checkout, entry, original):
    try:
        text = os.readlink(entry)
    except (OSError, ValueError):
        return False
    base = os.path.realpath(os.path.dirname(original))
    return os.path.normpath(os.path.join(base, text)) == link_target(checkout, original)

def identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_ctime_ns

def slot_of(path):
    return (os.path.realpath(os.path.dirname(path)), os.path.basename(path))

def ordered(harnesses):
    have = {harness for harness in harnesses if harness in HARNESSES}
    return tuple(harness for harness in HARNESSES if harness in have)

def inside_checkout(path):
    real = Path(os.path.realpath(path))
    skills = Path(os.path.realpath(SKILLS))
    return real == skills or skills in real.parents

def describe(path):
    if os.path.islink(path):
        return f"link to {os.readlink(path)}"
    return "directory" if os.path.isdir(path) else "file"

# Path.exists() reads each of these as "not there" on Python 3.10 and 3.12.
ABSENT = (errno.ENOENT, errno.ENOTDIR, errno.ELOOP)

ADRIFT = ("{file} records the relative path {path}, and the system could not name this run's working directory "
          "({reason}); change to another directory and rerun")

NO_PROJECT = ("the --project path {project} is relative, and the system could not name this run's working directory "
              "({reason}); change to another directory and rerun, or give --project a full path")

UNANCHORED = ("{name}={value!r} is not an absolute path, and the system could not name this run's working directory "
              "({reason}); change to another directory and rerun")

def unanchored(environ):
    home, claude = environ.get("HOME"), environ.get("CLAUDE_CONFIG_DIR")
    if home is not None and not os.path.isabs(home):
        name, value = "HOME", home
    elif claude and not os.path.isabs(claude):
        name, value = "CLAUDE_CONFIG_DIR", claude
    else:
        return None
    error = cwd_error()
    if error is not None:
        return UNANCHORED.format(name=name, value=value, reason=error.strerror)
    return None

def anchored(file, path):
    """Return `path`, which a record in `file` holds. Raise Unreadable with ADRIFT when it is relative and os.getcwd() raises OSError."""
    if not os.path.isabs(path):
        error = cwd_error()
        if error is not None:
            raise records.Unreadable(ADRIFT.format(file=file, path=path, reason=error.strerror)) from None
    return path

def scope_of(args):
    """Return the directory --project names, resolved, or None when --project is not given or is empty.

    A relative --project while os.getcwd() raises OSError ends the run at exit 1 with NO_PROJECT on stderr.
    When that does not end the run and unanchored(os.environ) returns a line, the run ends at exit 1 with that line on stderr.
    """
    if args.project and not os.path.isabs(args.project):
        error = cwd_error()
        if error is not None:
            sys.exit(NO_PROJECT.format(project=args.project, reason=error.strerror))
    stop = unanchored(os.environ)
    if stop:
        sys.exit(stop)
    return Path(args.project).resolve() if args.project else None

def layout(scope, user, selected):
    directories = skill_dirs(scope, user)
    user_dirs = {}
    if not user:
        for directory in skill_dirs(None, True).values():
            user_dirs.setdefault(os.path.realpath(directory), directory)
    grouped = {}
    for harness in HARNESSES:
        directory = directories[harness]
        grouped.setdefault(os.path.realpath(directory), []).append((harness, directory))
    chosen = set(selected)
    groups, refusals = [], []
    for real, members in grouped.items():
        harnesses = tuple(harness for harness, _directory in members)
        if not chosen.intersection(harnesses):
            continue
        directory = members[0][1]
        label = next(harness for harness, _directory in members if harness in chosen)
        shown = directories[label]
        if real in user_dirs:
            refusals.append(f"{label}: {shown} already resolves to {user_dirs[real]}; nothing to link")
            continue
        if inside_checkout(directory):
            refusals.append(f"{label}: {shown} already resolves to {SKILLS}; nothing to link")
            continue
        groups.append((str(directory), harnesses))
    return groups, refusals

UNBUILT = "skills/ is missing; run python3 scripts/build.py first"

NOT_A_DIRECTORY = "skills is not a directory; move it away, then run python3 scripts/build.py"

NO_SKILL = "skills/ holds no skill; run python3 scripts/build.py first"

def skill_names():
    return sorted(path.name for path in SKILLS.iterdir() if (path / "SKILL.md").is_file())

def unbuilt():
    if not SKILLS.is_dir():
        return NOT_A_DIRECTORY if os.path.lexists(SKILLS) else UNBUILT
    return None if skill_names() else NO_SKILL
