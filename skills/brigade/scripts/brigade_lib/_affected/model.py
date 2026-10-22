import posixpath
import re
import shlex
from dataclasses import dataclass
from brigade_lib._affected.scan import _norm, _roots
from brigade_lib.compact import refuse_setting

AFFECTED_KEY = "affectedTests"


DEFAULT_SUITE = "python3 -m unittest discover -s tests"


_LAYOUT_KEYS = ("always", "workflowModule", "entryPoints", "helpers", "suite", "python", "importRoots", "env")


_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")


_PYTHON_NAME = re.compile(r"^python(\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class Layout:
    """One store's affectedTests settings.

    always is the module every modules result includes. workflow_module maps a .github/workflows/ path, and empty
    means such a path is unmapped. entry_points and helpers are repository paths. suite is the whole-suite
    command, and it starts with python3 when python is set. python is the second interpreter, an absolute path
    whose file name is python<major> or python<major>.<minor>, and empty means the paragraph omits it.
    import_roots are repo paths tried as prefixes for absolute imports, in candidate order after the loader directory and the root with every match kept.
    Relative imports never search them. They are normalized and deduped in order and never hold the root itself.
    An absent key means the directories of entry_points and helpers.
    env holds (name, value) pairs, and the paragraph prints each one in front of every test command.
    """
    always: str
    workflow_module: str
    entry_points: tuple
    helpers: tuple
    suite: str
    python: str
    import_roots: tuple
    env: tuple = ()


def _dotted(value):
    return isinstance(value, str) and bool(_MODULE_NAME.match(value))


def _repo_path(value):
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in value.split("/"):
        return False
    return not re.search(r"\s", value)


def _repo_paths(value):
    return isinstance(value, list) and all(_repo_path(one) for one in value)


def _command(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        return bool(shlex.split(value))
    except ValueError:
        return False


def layout(meta):
    """The picker's Layout from one store's restaurant.json, or None when meta is falsy or the key is absent.

    A present value that does not parse is refused through refuse_setting, so the message names the file,
    the key, and the value. python is an absolute path whose file name is python<major> or
    python<major>.<minor>, and a value that sets python also needs a suite that starts with python3.
    env is an object of shell variable names to non-empty values without whitespace, and absent means
    no assignment.
    """
    if not meta or AFFECTED_KEY not in meta:
        return None
    value = meta[AFFECTED_KEY]
    if not isinstance(value, dict):
        refuse_setting(AFFECTED_KEY, value, "it is not an object")
    unknown = [key for key in value if key not in _LAYOUT_KEYS]
    if unknown:
        refuse_setting(AFFECTED_KEY, value, f"it has unknown key {unknown[0]!r}")
    always = value.get("always")
    if not _dotted(always):
        refuse_setting(AFFECTED_KEY, value, "'always' needs a dotted module name")
    workflow = value.get("workflowModule")
    if workflow is not None and not _dotted(workflow):
        refuse_setting(AFFECTED_KEY, value, "'workflowModule' needs a dotted module name")
    entries = value.get("entryPoints")
    if not _repo_paths(entries):
        refuse_setting(AFFECTED_KEY, value, "'entryPoints' needs a list of repo paths")
    helpers = value.get("helpers", [])
    if not _repo_paths(helpers):
        refuse_setting(AFFECTED_KEY, value, "'helpers' needs a list of repo paths")
    suite = value.get("suite", DEFAULT_SUITE)
    if not _command(suite):
        refuse_setting(AFFECTED_KEY, value, "'suite' needs a command")
    python = value.get("python")
    if python is not None:
        named = (isinstance(python, str) and python.startswith("/")
                 and bool(_PYTHON_NAME.match(posixpath.basename(python))))
        if not named:
            refuse_setting(AFFECTED_KEY, value,
                           "'python' needs an absolute path whose file name is python<major> or python<major>.<minor>")
        if suite.split()[0] != "python3":
            refuse_setting(AFFECTED_KEY, value, "'suite' must start with python3 when 'python' is set")
    if "importRoots" not in value:
        roots = _roots(list(entries) + list(helpers))
    else:
        raw_roots = value["importRoots"]
        if not _repo_paths(raw_roots):
            refuse_setting(AFFECTED_KEY, value, "'importRoots' needs a list of repo paths")
        seen = []
        for raw in raw_roots:
            normed = _norm(raw)
            if not normed or normed in (".", "/"):
                continue
            if normed not in seen:
                seen.append(normed)
        roots = tuple(seen)
    env = value.get("env", {})
    if not isinstance(env, dict):
        refuse_setting(AFFECTED_KEY, value, "'env' needs an object of names to values")
    for name, one in env.items():
        if not isinstance(name, str) or not _ENV_NAME.match(name):
            refuse_setting(AFFECTED_KEY, value, f"'env' needs a variable name for {name!r}")
        if not isinstance(one, str) or not one or re.search(r"\s", one):
            refuse_setting(AFFECTED_KEY, value,
                           f"'env' needs a non-empty value without whitespace for {name!r}")
    return Layout(always, workflow or "", tuple(entries), tuple(helpers), suite, python or "", roots,
                  tuple(sorted(env.items())))


@dataclass(frozen=True)
class _Modules:
    names: tuple


@dataclass(frozen=True)
class _Full:
    unmapped: tuple


@dataclass(frozen=True)
class _Closed:
    table: dict


@dataclass(frozen=True)
class _Open:
    reason: str
