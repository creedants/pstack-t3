"""Mode decisions, the light cap, lease matching, waivers, and the mode command."""


import fnmatch
import re
import subprocess


from _roles.base import ATTEMPTS, MODES, ModeSettingsError, PANEL_BACKUP_ROLE, PANEL_GATE_ROLE, RolesError
from _roles.config import DEFAULT_MODE, ModeDecision, canonical, merged_config
from _roles import seam


def effective_mode(config, brief=None, session=None, coordinator=None):
    """Brief, then session, then coordinator, then the merged file level, then light."""
    for value, label in ((brief, "brief"), (session, "session"), (coordinator, "restaurant.json")):
        if value is not None:
            return ModeDecision(value, label, label)
    return config.get("mode", DEFAULT_MODE)


def seat_budget(budget, mode, role):
    """Light caps only a default budget, and never verifiers or review backups. An explicit budget wins both ways."""
    capped = mode == "light" and budget == "default" and role not in (PANEL_GATE_ROLE, PANEL_BACKUP_ROLE)
    return "small" if capped else budget


NO_CATALOG_INFO = (
    "info: light mode caps reasoning at medium, but show had no catalog, "
    "so no seat was capped. Rerun with --catalog and --parent."
)


NO_PARENT_INFO = (
    "info: light mode caps reasoning at medium, but show had no --parent, "
    "so an inherit seat may keep the parent's reasoning. Rerun with --parent."
)


def light_cap_gap(decision, budget, names, has_catalog, has_parent):
    """The info line show prints when the light cap may not have reached every seat."""
    if all(seat_budget(budget, decision.mode, name) == budget for name in names):
        return None
    if not has_catalog:
        return NO_CATALOG_INFO
    if not has_parent:
        return NO_PARENT_INFO
    return None


def lease_paths(text):
    """Leases land.py would claim for this comma-separated list."""
    return sorted({canonical(part) for part in text.split(",")})


def segments_match(pattern, path):
    """Whole-segment glob. ** matches zero or more segments. Other segments never cross a slash."""

    def match(pattern_parts, path_parts):
        if not pattern_parts:
            return not path_parts
        if pattern_parts[0] == "**":
            return any(match(pattern_parts[1:], path_parts[index:]) for index in range(len(path_parts) + 1))
        return (
            bool(path_parts)
            and fnmatch.fnmatchcase(path_parts[0], pattern_parts[0])
            and match(pattern_parts[1:], path_parts[1:])
        )

    pattern_parts = [] if pattern == "" else pattern.split("/")
    path_parts = [] if path == "" else path.split("/")
    return match(pattern_parts, path_parts)


def escalated_path(leases, patterns, tracked):
    """First covered path that matches a pattern. Candidates are sorted, so pattern order does not matter."""
    covered = set()
    for lease in leases:
        if lease == "":
            covered.update(tracked)
            continue
        covered.add(lease)
        prefix = lease + "/"
        covered.update(path for path in tracked if path.startswith(prefix))
    for path in sorted(covered):
        if any(segments_match(pattern, path) for pattern in patterns):
            return path
    return None


def tracked_files(cwd):
    """Repository-relative tracked paths. Any git failure means cwd is not a checkout."""
    message = f"--paths needs a git checkout to list tracked files, and {cwd} is not one"

    def git(args):
        try:
            return subprocess.run(args, capture_output=True)
        except OSError as error:
            raise RolesError(message) from error

    top = git(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"])
    lines = top.stdout.decode("utf-8", "surrogateescape").splitlines()
    if top.returncode != 0 or not lines:
        raise RolesError(message)
    listing = git(["git", "-C", lines[0], "ls-files", "-z"])
    if listing.returncode != 0:
        raise RolesError(message)
    parts = listing.stdout.decode("utf-8", "surrogateescape").split("\0")
    if parts and parts[-1] == "":
        del parts[-1]
    return parts


def escalation_reason(durable, hit, send_backs):
    """Durable reason first, then a covered path, then a second send-back."""
    if durable is not None:
        return durable
    if hit is not None:
        return f"lease covers {hit}"
    if send_backs >= 2:
        return "second send-back"
    return None


def escalate(decision, reason):
    """Escalation only moves toward full."""
    if reason is None:
        return decision
    label = f"escalated: {reason}"
    return ModeDecision("full", label, label)


class Waivers(dict):
    """Map (playbook stem, attempt kind) to waived step names, in table order.

    A missing playbook with a known attempt waives nothing. An unknown attempt kind is a KeyError.
    """

    def __missing__(self, key):
        if isinstance(key, tuple) and len(key) == 2 and key[1] in ATTEMPTS:
            return ()
        raise KeyError(key)


_WAIVER_ROWS = {
    "feature": (
        ("Arena", "Interrogate", "Comment Sicko"),
        ("How", "Architect", "Arena", "Interrogate", "Comment Sicko"),
    ),
    "bug-fix": (("Comment Sicko",), ("How", "Why", "Architect", "Comment Sicko")),
    "refactoring": (("Comment Sicko",), ("How", "Architect", "Comment Sicko")),
    "perf-issue": (("Comment Sicko",), ("How", "Architect", "Comment Sicko")),
    "hillclimb": (("Comment Sicko",), ("How", "Comment Sicko")),
    "authoring-a-skill": (
        ("Comment Sicko", "Second-provider test"),
        ("Comment Sicko", "Second-provider test"),
    ),
}


LIGHT_WAIVERS = Waivers({
    (playbook, attempt): first if attempt == "first" else retry
    for playbook, (first, retry) in _WAIVER_ROWS.items()
    for attempt in ATTEMPTS
})


def durable_reason(text):
    if text is None:
        return None
    if text.strip() == "" or "\n" in text or "\r" in text:
        raise ModeSettingsError("--escalated needs a one-line reason")
    return text.strip()


GRAMMAR_LABELS = ("Mode", "Mode source", "Attempt", "Waived by mode", "Gate")


_GRAMMAR_LINE = re.compile("(" + "|".join(map(re.escape, GRAMMAR_LABELS)) + r"):(.*)")


def waived_value(mode, key):
    """Waiver text for one item, or None when the brief carries no such line.

    Full mode returns None and does not read key. An empty light tuple is None.
    """
    if mode != "light":
        return None
    return ", ".join(LIGHT_WAIVERS[key]) or None


def brief_lines(decision, key):
    """Playbook, Mode, Mode source, Attempt, and Waived by mode, each when it applies."""
    lines = [] if key is None else [f"Playbook: playbooks/{key[0]}.md"]
    lines.append(f"Mode: {decision.mode}")
    lines.append(f"Mode source: {decision.label}")
    if key is not None:
        lines.append(f"Attempt: {key[1]}")
        value = waived_value(decision.mode, key)
        if value is not None:
            lines.append(f"Waived by mode: {value}")
    return lines


def _grammar_lines(text):
    """Label to stripped values, in brief order."""
    found = {label: [] for label in GRAMMAR_LABELS}
    for line in text.splitlines():
        match = _GRAMMAR_LINE.fullmatch(line)
        if match:
            found[match.group(1)].append(match.group(2).strip())
    return found


def _waiver_problem(mode, key, expected, actual):
    if expected is None:
        if mode == "full":
            return "unexpected Waived by mode line: Mode: full waives nothing, remove it"
        return (
            f"unexpected Waived by mode line: playbooks/{key[0]}.md "
            f"waives nothing on attempt {key[1]}, remove it"
        )
    if actual is None:
        return f"missing Waived by mode line: add 'Waived by mode: {expected}'"
    return f"wrong Waived by mode line: expected 'Waived by mode: {expected}'"


MISSING_MODE = (
    "missing Mode: paste the lines 'roles.py mode --playbook <name> --attempt <kind>' prints, "
    "which include one line 'Mode: full' or 'Mode: light'"
)


def mode_problems(text, stem):
    lines = _grammar_lines(text)
    if not lines["Mode"]:
        return [MISSING_MODE]
    problems = [
        f"more than one {label} line: keep one"
        for label in GRAMMAR_LABELS
        if len(lines[label]) > 1
    ]
    if len(lines["Mode"]) > 1:
        return problems
    mode = lines["Mode"][0]
    if mode not in MODES:
        problems.append(f"bad Mode value '{mode}': expected {' or '.join(MODES)}")
        return problems

    attempt = None
    attempts = lines["Attempt"]
    if len(attempts) == 1:
        if attempts[0] in ATTEMPTS:
            attempt = attempts[0]
        else:
            problems.append(
                f"bad Attempt value '{attempts[0]}': expected one of: {', '.join(ATTEMPTS)}"
            )
    elif not attempts and mode == "light":
        problems.append(
            "missing Attempt: Mode: light needs one line 'Attempt: <kind>', "
            f"where <kind> is one of: {', '.join(ATTEMPTS)}"
        )

    waived = lines["Waived by mode"]
    if len(waived) > 1:
        return problems
    if mode == "light" and (stem is None or attempt is None):
        return problems
    key = None if mode == "full" else (stem, attempt)
    expected = waived_value(mode, key)
    actual = waived[0] if waived else None
    if actual != expected:
        problems.append(_waiver_problem(mode, key, expected, actual))
    return problems


def work_key(playbook, attempt):
    if (playbook is None) != (attempt is None):
        raise ModeSettingsError("--playbook and --attempt go together: pass both or neither")
    if playbook is None:
        return None
    stems = seam.playbook_stems()
    if playbook not in stems:
        names = ", ".join(sorted(stems))
        raise ModeSettingsError(f"unknown playbook '{playbook}': expected one of: {names}")
    return (playbook, attempt)


def command_mode(args):
    config = merged_config(args.cwd, args.config, args.project_config)
    decision = effective_mode(config, args.brief_mode, args.session_mode, args.coordinator_mode)
    key = work_key(args.playbook, args.attempt)
    durable = durable_reason(args.escalated)
    leases = lease_paths(args.paths) if args.paths is not None else []
    patterns = [canonical(item) for item in (config["escalate"] or [])]
    hit = escalated_path(leases, patterns, tracked_files(args.cwd)) if leases and patterns else None
    print("\n".join(brief_lines(escalate(decision, escalation_reason(durable, hit, args.send_backs)), key)))


__all__ = [
    "effective_mode",
    "seat_budget",
    "NO_CATALOG_INFO",
    "NO_PARENT_INFO",
    "light_cap_gap",
    "lease_paths",
    "segments_match",
    "escalated_path",
    "tracked_files",
    "escalation_reason",
    "escalate",
    "Waivers",
    "LIGHT_WAIVERS",
    "durable_reason",
    "GRAMMAR_LABELS",
    "waived_value",
    "brief_lines",
    "MISSING_MODE",
    "mode_problems",
    "work_key",
    "command_mode",
]
