"""The show, validate, and write commands, and launch helpers."""


import json
import os
import sys
from contextlib import suppress
from pathlib import Path


from _roles.base import INHERIT, ROLES, RolesError
from _roles.config import DEFAULT_MODE, ModeDecision, OpenCodeAllow, allow_for, check_shape, given_parent, given_runtime_mode, load_catalog, load_json, merged_config, parent_for, project_config_path, snapshot_path, stamp_parent, stored_allow, user_config_path
from _roles.mode import effective_mode, light_cap_gap
from _roles.problems import excluded_backup_problems, excluded_problems, validate
from _roles.resolve import resolve
from _roles.seats import exclude_reason, for_runtime_mode, opencode_allow_lines, parse_seat
from _roles import seam


def _entry_key(path):
    """The directory entry os.replace would replace, as (resolved parent, name)."""
    current = Path(path)
    try:
        parent = current.parent.resolve()
    except OSError:
        parent = current.parent.absolute()
    return (str(parent), current.name)


def _final_entry(path):
    """The entry a read of path follows to, or None when it cannot be resolved."""
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        return None
    try:
        parent = resolved.parent.resolve()
    except OSError:
        parent = resolved.parent.absolute()
    return (str(parent), resolved.name)


def _will_read_new(candidate, target):
    """True when a read of candidate after os.replace(target) sees the new file."""
    if _entry_key(candidate) == _entry_key(target):
        return True
    final = _final_entry(candidate)
    if final is None:
        return False
    return final == _entry_key(target)


def _prospective_merged(user_dict, project_dict, user_path, project_path):
    """The merged configuration merged_config returns after the target is replaced."""
    roles, sources, allow_by_file = {}, {}, {}
    for origin, cfg in ((user_path, user_dict), (project_path, project_dict)):
        for name, seats in cfg.get("roles", {}).items():
            roles[name] = seats
            sources[name] = str(origin)
        allow_by_file[str(origin)] = OpenCodeAllow.from_json(cfg.get("opencodeAllow"))
    if "mode" in project_dict:
        mode = ModeDecision(project_dict["mode"], ".pstack/t3-roles.json", str(project_path))
    elif "mode" in user_dict:
        mode = ModeDecision(user_dict["mode"], "roles.json", str(user_path))
    else:
        mode = DEFAULT_MODE
    escalate = list(project_dict["escalate"]) if "escalate" in project_dict else None
    budget = project_dict["budget"] if "budget" in project_dict else user_dict.get("budget", "default")
    exclude = list(user_dict.get("exclude", ()))
    for rule in project_dict.get("exclude", ()):
        if rule not in exclude:
            exclude.append(rule)
    exclude = tuple(exclude)
    backups = dict(user_dict.get("backups", {}))
    backups.update(project_dict.get("backups", {}))
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


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = seam.new_file(path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise


def back_up(path):
    """Copy the target's current bytes to <name>.bak before a --replace rewrite."""
    if not path.exists():
        return
    path.with_name(path.name + ".bak").write_bytes(path.read_bytes())


def load_show_catalog(args):
    if args.catalog:
        return args.catalog, load_catalog(args.catalog)
    path = snapshot_path()
    if path.is_file():
        return path, load_catalog(path)
    return None, None


def catalog_for_check(args, catalog, catalog_path):
    """Parent for this invocation, and a catalog copy whose inherited keys match it."""
    parent = parent_for(args, catalog, catalog_path)
    if catalog is None:
        return parent, None
    return parent, stamp_parent(catalog, parent)


def command_show(args):
    given_parent(args)
    mode = given_runtime_mode(args)
    config = merged_config(args.cwd, args.config, args.project_config)
    decision = effective_mode(config, args.brief_mode, args.session_mode, args.coordinator_mode)
    config["mode"] = decision
    catalog_path, catalog = load_show_catalog(args)
    parent, catalog = catalog_for_check(args, catalog, catalog_path)
    catalog = for_runtime_mode(catalog, mode)
    launches = getattr(args, "launches_seats", False)
    if launches:
        roles = args.role or []
        if roles != ["skill tests"]:
            raise RolesError('--launches-seats is valid only with exactly one --role "skill tests"')
        if catalog is None:
            raise RolesError("--launches-seats requires a catalog")
        if args.parent is None:
            raise RolesError("--launches-seats requires --parent")
        payload = resolve(config, catalog, args.role, parent, launches_seats=True)
    else:
        payload = resolve(config, catalog, args.role, parent)
    print(json.dumps(payload, indent=2))
    line = light_cap_gap(decision, config["budget"], args.role or ROLES, catalog is not None, args.parent is not None)
    if line:
        print(line, file=sys.stderr)


def command_validate(args):
    given_parent(args)
    config = merged_config(args.cwd, args.config, args.project_config)
    exclude = config.get("exclude", ())
    backups = config.get("backups", {})
    catalog = load_catalog(args.catalog)
    _parent, catalog = catalog_for_check(args, catalog, args.catalog)
    for name, seats in config["roles"].items():
        kept = [seat for seat in seats if exclude_reason(seat, exclude, name) is None]
        for line in opencode_allow_lines({name: kept}, allow_for(config, name), catalog):
            print(line)
    for name, seats in backups.items():
        for line in opencode_allow_lines({f"{name} backups": list(seats)}, allow_for(config, name), catalog):
            print(line)
    problems = validate(config, catalog, exclude, backups)
    for problem in problems:
        print(problem)
    if not problems:
        print("ok")
    return 1 if problems else 0


def command_write(args):
    given_parent(args)
    catalog = load_catalog(args.catalog)
    user_path = Path(args.config) if args.config else user_config_path()
    project_path = project_config_path(args.cwd)
    target = project_path if args.project else user_path
    loaded = load_json(target)
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        if not args.replace:
            raise RolesError(f"{target}: expected an object")
        loaded = {}
    # A default write keeps the stored keys the flags do not name.
    # --replace starts over from the flags, the stored allow list, and a stored project escalate.
    raw = loaded
    allow = stored_allow(raw, target).remove_all(args.deny_opencode or []).add_all(args.allow_opencode or [])
    stored_roles = raw.get("roles", {})
    if not isinstance(stored_roles, dict):
        raise RolesError(f"{target}: roles must be an object")
    roles = {} if args.replace else dict(stored_roles)
    for assignment in args.set or []:
        name, equals, value = assignment.partition("=")
        name = name.strip()
        if not equals or name not in ROLES:
            raise RolesError(f"--set {assignment!r}: expected '<role>=<seat>[;<seat>...]' with a known role")
        roles[name] = [parse_seat(part) for part in value.split(";") if part.strip()]
    config = {"version": 1, "roles": roles}
    budget = args.budget if args.budget is not None else (None if args.replace else raw.get("budget"))
    if budget is not None or (not args.replace and "budget" in raw):
        config["budget"] = budget
    elif not args.project:
        config["budget"] = "default"
    mode = args.mode if args.mode is not None else (None if args.replace else raw.get("mode"))
    if mode is not None or (not args.replace and "mode" in raw):
        config["mode"] = mode
    elif not args.project:
        config["mode"] = DEFAULT_MODE.mode
    if args.project and not args.clear_escalate:
        if args.escalate is not None:
            config["escalate"] = list(args.escalate)
        elif "escalate" in raw:
            config["escalate"] = raw["escalate"]
    if allow.models:
        config["opencodeAllow"] = allow.to_json()
    if not args.replace:
        for key, value in raw.items():
            if key in config or key in ("version", "roles", "budget", "mode"):
                continue
            if key == "escalate" and args.clear_escalate:
                continue
            if key == "opencodeAllow" and (args.allow_opencode or args.deny_opencode):
                continue
            config[key] = value
    if args.replace:
        for key in ("exclude", "backups"):
            if key in raw:
                config[key] = raw[key]
    check_shape(config, target)
    if "escalate" in config:
        config["escalate"] = list(config["escalate"])
    exclude = config.get("exclude", ())
    backups = config.get("backups", {})
    excluded = excluded_problems(roles, exclude)
    excluded.extend(excluded_backup_problems(backups, exclude))
    if excluded:
        raise RolesError("refusing to write, even with --force:\n" + "\n".join(excluded))
    if _will_read_new(user_path, target):
        prospective_user = config
    else:
        raw_user = load_json(user_path)
        if raw_user is None:
            raw_user = {}
        prospective_user = check_shape(raw_user, user_path)
    if _will_read_new(project_path, target):
        prospective_project = config
    else:
        raw_project = load_json(project_path)
        if raw_project is None:
            raw_project = {}
        prospective_project = check_shape(raw_project, project_path)
    prospective = _prospective_merged(prospective_user, prospective_project, user_path, project_path)
    _parent, checking = catalog_for_check(args, catalog, args.catalog)
    problems = validate(prospective, checking, prospective["exclude"], prospective["backups"])
    for name, seats in prospective["roles"].items():
        kept = [seat for seat in seats if exclude_reason(seat, prospective["exclude"], name) is None]
        problems.extend(opencode_allow_lines({name: kept}, allow_for(prospective, name), checking))
    for name, seats in prospective["backups"].items():
        problems.extend(opencode_allow_lines(
            {f"{name} backups": list(seats)}, allow_for(prospective, name), checking
        ))
    if problems and not args.force:
        raise RolesError("refusing to write without --force:\n" + "\n".join(problems))
    if args.replace:
        back_up(target)
    write_atomic(target, config)
    if not args.project and not args.config:
        write_atomic(snapshot_path(), catalog)
    print(f"wrote {target}")


def launch_refusal(detail):
    return (
        "role 'skill tests' has no seat for a child that launches seats: "
        f"a seat that runs on the cursor driver cannot launch seats, and {detail}"
    )


def launch_provider(seat, parent):
    if seat == INHERIT:
        return parent.provider
    return seat["providerInstanceId"]


__all__ = [
    "write_atomic",
    "back_up",
    "load_show_catalog",
    "catalog_for_check",
    "command_show",
    "command_validate",
    "command_write",
    "launch_refusal",
    "launch_provider",
]
