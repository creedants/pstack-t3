"""Config file paths, JSON and catalog loaders, exclude and backup keys, parent handling, and mode decision values."""


import functools
import json
import os
import posixpath
import sys
from dataclasses import dataclass
from pathlib import Path


from _roles.base import BUDGETS, INHERIT, MODES, ModeSettingsError, PANEL_BACKUP_ROLE, Parent, ROLES, RolesError, SINGLE_ROLES
from _roles import seam


@dataclass(frozen=True)
class ModeDecision:
    """One mode decision and where it came from.

    label is the brief grammar's Mode source value. where is what show prints
    as modeSource. A file level uses the file path. Every other level uses the label.
    """

    mode: str
    label: str
    where: str


DEFAULT_MODE = ModeDecision("light", "default", "default")


@functools.lru_cache(maxsize=None)
def user_config_path():
    config, ignored = seam.config_home(os.environ, lambda: Path.home() / ".config")
    if ignored:
        print(ignored, file=sys.stderr)
    return config / "pstack-t3" / "roles.json"


def snapshot_path():
    return user_config_path().with_name("catalog.json")


def project_config_path(cwd):
    current = Path(cwd).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / ".pstack" / "t3-roles.json"
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            return directory / ".pstack" / "t3-roles.json"
    return current / ".pstack" / "t3-roles.json"


def parse_json(text, origin):
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RolesError(f"{origin}: invalid JSON: {error}") from error


def load_json(path):
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as error:
        raise RolesError(f"{path}: invalid JSON: {error}") from error
    return parse_json(text, path)


def load_catalog(path):
    data = parse_json(sys.stdin.read(), "-") if str(path) == "-" else load_json(path)
    if data is None:
        raise RolesError(f"{path}: catalog not found")
    if "providers" not in data:
        raise RolesError(f"{path}: not an orchestrator_capabilities result (no providers)")
    return data


def parse_exclude(value, origin):
    """Validate the exclude list and return it as a tuple of rule dicts.

    Every entry is an object with a non-empty "model", a non-empty "options", a
    non-empty "provider", a non-empty "roles" list, or a combination of them. A
    string entry is refused.
    """
    if not isinstance(value, list):
        raise RolesError(f"{origin}: exclude must be a list")
    rules = []
    for entry in value:
        if isinstance(entry, str):
            raise RolesError(
                f"{origin}: exclude entry {entry!r} is a string; write an object with model, options, provider, or roles"
            )
        if not isinstance(entry, dict) or not entry:
            raise RolesError(
                f"{origin}: exclude entries must be an object with model, options, provider, or roles"
            )
        unknown = sorted(set(entry) - {"model", "options", "provider", "roles"})
        if unknown:
            raise RolesError(f"{origin}: exclude entry has unknown key {unknown[0]!r}")
        rule = {}
        if "model" in entry:
            model = entry["model"]
            if not isinstance(model, str) or not model:
                raise RolesError(f"{origin}: exclude entry model must be a non-empty string")
            rule["model"] = model
        if "provider" in entry:
            provider = entry["provider"]
            if not isinstance(provider, str) or not provider:
                raise RolesError(f"{origin}: exclude entry provider must be a non-empty string")
            rule["provider"] = provider
        if "roles" in entry:
            names = entry["roles"]
            if not isinstance(names, list) or not names:
                raise RolesError(f"{origin}: exclude entry roles must be a non-empty list of role names")
            for name in names:
                if not isinstance(name, str) or not name:
                    raise RolesError(f"{origin}: exclude entry roles must be a non-empty list of role names")
                if name not in ROLES:
                    raise RolesError(f"{origin}: exclude entry roles names unknown role {name!r}")
            rule["roles"] = list(names)
        if "options" in entry:
            options = entry["options"]
            if not isinstance(options, dict) or not options:
                raise RolesError(f"{origin}: exclude entry options must be a non-empty object")
            for key, option in options.items():
                if not isinstance(option, (bool, str)):
                    raise RolesError(f"{origin}: exclude option {key!r} must be true, false, or a string")
            rule["options"] = dict(options)
        rules.append(rule)
    return tuple(rules)


def parse_backups(value, origin):
    """Validate the backups object and return role name -> tuple of seats.

    A seat is an object with a non-empty string "providerInstanceId" and "model".
    Its "options", when present, is an object of bool or string values.
    """
    if not isinstance(value, dict):
        raise RolesError(f"{origin}: backups must be an object")
    backups = {}
    for name, seats in value.items():
        if name not in ROLES:
            raise RolesError(f"{origin}: backups names unknown role {name!r}")
        if not isinstance(seats, list) or not seats:
            raise RolesError(f"{origin}: backups for {name!r} needs a non-empty list of seats")
        parsed = []
        for seat in seats:
            if seat == INHERIT:
                raise RolesError(f"{origin}: backups for {name!r} refuses inherit")
            if not isinstance(seat, dict):
                raise RolesError(f"{origin}: backups.{name}: each seat must be an object")
            for key in ("providerInstanceId", "model"):
                if not isinstance(seat.get(key), str) or not seat[key]:
                    raise RolesError(f"{origin}: backups.{name}: {key} must be a non-empty string")
            if "options" in seat:
                options = seat["options"]
                if not isinstance(options, dict):
                    raise RolesError(f"{origin}: backups.{name}: options must be an object")
                for key, value in options.items():
                    if not isinstance(value, (bool, str)):
                        raise RolesError(
                            f"{origin}: backups.{name}: option {key!r} must be true, false, or a string"
                        )
            parsed.append({**seat})
        backups[name] = tuple(parsed)
    return backups


def backups_for(role, backups=()):
    """The seats backup tries for this role. A review backups seat has no backup."""
    if role == PANEL_BACKUP_ROLE:
        return ()
    return tuple(backups.get(role) or ())


def parse_parent(text):
    """Provider is the text before the first slash. The model id may contain slashes."""
    message = (
        f"--parent {text!r}: expected '<inheritedProviderInstanceId>/<inheritedModel>' "
        "from orchestrator_capabilities"
    )
    if not isinstance(text, str) or "/" not in text or any(char.isspace() for char in text) or "?" in text:
        raise RolesError(message)
    provider, _, model = text.partition("/")
    if not provider or not model:
        raise RolesError(message)
    return Parent(provider, model)


def inherit_parent(catalog):
    """Parent stored on a catalog file, or None when those keys are missing or malformed."""
    if not isinstance(catalog, dict):
        return None
    provider = catalog.get("inheritedProviderInstanceId")
    model = catalog.get("inheritedModel")
    if not isinstance(provider, str) or not isinstance(model, str) or not provider or not model:
        return None
    try:
        return parse_parent(f"{provider}/{model}")
    except RolesError:
        return None


def stamp_parent(catalog, parent):
    return {
        **catalog,
        "inheritedProviderInstanceId": None if parent is None else parent.provider,
        "inheritedModel": None if parent is None else parent.model,
    }


def is_snapshot_path(path):
    if path is None or str(path) == "-":
        return False
    try:
        return Path(path).resolve() == snapshot_path().resolve()
    except OSError:
        return False


def given_parent(args):
    text = getattr(args, "parent", None)
    if text is None:
        return None
    return parse_parent(text)


def given_runtime_mode(args):
    text = getattr(args, "runtime_mode", None)
    if text is None:
        return None
    if not text or any(char.isspace() for char in text):
        raise RolesError(f"--runtime-mode {text!r}: expected runtimeMode from orchestrator_capabilities")
    return text


def parent_for(args, catalog, catalog_path):
    """--parent wins. A snapshot never supplies a parent. An explicit catalog file does."""
    given = given_parent(args)
    if given is not None:
        return given
    if catalog is None or is_snapshot_path(catalog_path):
        return None
    return inherit_parent(catalog)


def canonical(path, kind="lease"):
    """Repository-relative POSIX path with no aliases. An empty string is the whole repository."""
    value = posixpath.normpath(path.strip().replace("\\", "/")).lstrip("/")
    if value in (".", ""):
        return ""
    if value == ".." or value.startswith("../"):
        raise ModeSettingsError(f"{kind} path {path!r} leaves the repository")
    return value


@dataclass(frozen=True)
class OpenCodeAllow:
    """Sorted unique full model ids one roles file allows."""

    models: tuple[str, ...]

    @staticmethod
    def empty():
        return OpenCodeAllow(())

    @staticmethod
    def from_json(value):
        if not value:
            return OpenCodeAllow.empty()
        return OpenCodeAllow(tuple(sorted(set(value))))

    def names(self, model_id):
        return model_id in self.models

    def add_all(self, model_ids):
        return OpenCodeAllow(tuple(sorted(set(self.models).union(model_ids))))

    def remove_all(self, model_ids):
        gone = set(model_ids)
        return OpenCodeAllow(tuple(model for model in self.models if model not in gone))

    def to_json(self):
        return list(self.models)


def allow_shape_error(value):
    if isinstance(value, list) and all(isinstance(item, str) and item and " " not in item for item in value):
        return None
    return "opencodeAllow must be a list of non-empty strings with no spaces"


def allow_for(config, name):
    source = config.get("sources", {}).get(name)
    return config.get("allowByFile", {}).get(source, OpenCodeAllow.empty())


def stored_allow(raw, origin):
    if not isinstance(raw, dict) or "opencodeAllow" not in raw:
        return OpenCodeAllow.empty()
    message = allow_shape_error(raw["opencodeAllow"])
    if message:
        raise RolesError(f"{origin}: {message}")
    return OpenCodeAllow.from_json(raw["opencodeAllow"])


def check_shape(config, origin):
    if not isinstance(config, dict):
        raise RolesError(f"{origin}: expected an object")
    budget = config.get("budget", "default")
    if budget not in BUDGETS:
        raise RolesError(f"{origin}: budget {budget!r} is not one of {', '.join(BUDGETS)}")
    if "mode" in config and config["mode"] not in MODES:
        raise ModeSettingsError(f"{origin}: mode {config['mode']!r} is not one of {', '.join(MODES)}")
    if "escalate" in config:
        escalate = config["escalate"]
        if not isinstance(escalate, list) or not all(isinstance(item, str) and item for item in escalate):
            raise ModeSettingsError(f"{origin}: escalate must be a list of strings")
        for item in escalate:
            try:
                canonical(item, "escalate pattern")  # traversal check only; the stored pattern stays raw
            except ModeSettingsError:
                raise ModeSettingsError(f"{origin}: escalate pattern {item!r} leaves the repository") from None
    roles = config.get("roles", {})
    if not isinstance(roles, dict):
        raise RolesError(f"{origin}: roles must be an object")
    for name, seats in roles.items():
        if name not in ROLES:
            raise RolesError(f"{origin}: unknown role {name!r}")
        if not isinstance(seats, list) or not seats:
            raise RolesError(f"{origin}: role {name!r} needs a non-empty list of seats")
        if name in SINGLE_ROLES and len(seats) != 1:
            raise RolesError(f"{origin}: role {name!r} takes exactly one seat")
        for seat in seats:
            if seat == INHERIT and name == PANEL_BACKUP_ROLE:
                raise RolesError(f"{origin}: role {name!r} refuses inherit, because the parent can be the author")
            if seat == INHERIT:
                continue
            if not isinstance(seat, dict) or not seat.get("providerInstanceId") or not seat.get("model"):
                raise RolesError(f"{origin}: role {name!r} has a seat without providerInstanceId and model")
    if "opencodeAllow" in config:
        message = allow_shape_error(config["opencodeAllow"])
        if message:
            raise RolesError(f"{origin}: {message}")
    if "exclude" in config:
        config["exclude"] = parse_exclude(config["exclude"], origin)
    if "backups" in config:
        config["backups"] = parse_backups(config["backups"], origin)
    return config


def merged_config(cwd, user_path=None, project_path=None):
    user_path = Path(user_path) if user_path else user_config_path()
    project_path = Path(project_path) if project_path else project_config_path(cwd)
    user = check_shape(load_json(user_path) or {}, user_path)
    project = check_shape(load_json(project_path) or {}, project_path)
    roles, sources, allow_by_file = {}, {}, {}
    for origin, config in ((user_path, user), (project_path, project)):
        for name, seats in config.get("roles", {}).items():
            roles[name] = seats
            sources[name] = str(origin)
        allow_by_file[str(origin)] = OpenCodeAllow.from_json(config.get("opencodeAllow"))
    if "mode" in project:
        mode = ModeDecision(project["mode"], ".pstack/t3-roles.json", str(project_path))
    elif "mode" in user:
        mode = ModeDecision(user["mode"], "roles.json", str(user_path))
    else:
        mode = DEFAULT_MODE
    escalate = list(project["escalate"]) if "escalate" in project else None
    budget = project["budget"] if "budget" in project else user.get("budget", "default")
    exclude = list(user.get("exclude", ()))
    for rule in project.get("exclude", ()):
        if rule not in exclude:
            exclude.append(rule)
    exclude = tuple(exclude)
    backups = dict(user.get("backups", {}))
    backups.update(project.get("backups", {}))
    return {
        "budget": budget,
        "mode": mode,
        "escalate": escalate,
        "roles": roles,
        "sources": sources,
        "allowByFile": allow_by_file,
        "exclude": exclude,
        "backups": backups,
    }


__all__ = [
    "ModeDecision",
    "DEFAULT_MODE",
    "user_config_path",
    "snapshot_path",
    "project_config_path",
    "parse_json",
    "load_json",
    "load_catalog",
    "parse_exclude",
    "parse_backups",
    "backups_for",
    "parse_parent",
    "inherit_parent",
    "stamp_parent",
    "is_snapshot_path",
    "given_parent",
    "given_runtime_mode",
    "parent_for",
    "canonical",
    "OpenCodeAllow",
    "allow_shape_error",
    "allow_for",
    "stored_allow",
    "check_shape",
    "merged_config",
]
