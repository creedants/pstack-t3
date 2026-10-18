"""Brief rules, playbook stems, and the check-brief command."""


import re
from pathlib import Path


from _roles.base import RolesError
from _roles.mode import mode_problems
from _roles import seam


def persona_path():
    return seam.AGENTS_DIR / "poteto-agent.md"


def playbook_stems():
    root = seam.TREE_ROOT
    rendered_tree = root / "poteto-mode" / "playbooks"
    source_checkout = root / "skills" / "poteto-mode" / "playbooks"
    candidates = (rendered_tree, source_checkout)
    directory = next((candidate for candidate in candidates if candidate.is_dir()), None)
    if directory is None:
        raise RolesError("no playbook directory: " + ", ".join(str(candidate) for candidate in candidates))
    stems = frozenset(
        entry.stem for entry in directory.iterdir() if entry.is_file() and entry.suffix == ".md"
    )
    if not stems:
        raise RolesError(f"{directory}: playbook directory is empty")
    return stems


def load_brief_rules():
    path = persona_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RolesError(f"{path}: persona not found") from error
    except UnicodeDecodeError as error:
        raise RolesError(f"{path}: persona is not valid UTF-8") from error
    except OSError as error:
        raise RolesError(f"{path}: cannot read persona: {error.strerror}") from error
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise RolesError(f"{path}: persona file has no frontmatter")
    fences = [index for index, line in enumerate(lines) if line.strip() == "---"]
    if len(fences) < 2:
        raise RolesError(f"{path}: persona file has no frontmatter")
    body = "".join(lines[fences[1] + 1:]).strip()
    if not body:
        raise RolesError(f"{path}: persona body is empty")
    return body, playbook_stems()


def playbook_values(text):
    values = []
    for line in text.splitlines():
        match = re.fullmatch(r"Playbook:\s*(\S+)\s*", line)
        if match:
            values.append(match.group(1))
    return values


def playbook_stem(value):
    prefix = "playbooks/"
    suffix = ".md"
    if not value.startswith(prefix) or not value.endswith(suffix):
        return None
    stem = value[len(prefix):-len(suffix)]
    if not stem or "/" in stem or stem in (".", ".."):
        return None
    return stem


def brief_problems(text, persona_body, playbooks):
    problems = []
    collapsed_body = " ".join(persona_body.split())
    collapsed_brief = " ".join(text.split())
    if not collapsed_brief.startswith(collapsed_body):
        problems.append(
            f"missing persona: open the brief with the body of {persona_path()}, without its frontmatter"
        )
    names = ", ".join(sorted(playbooks))
    values = playbook_values(text)
    stem = None
    if not values:
        problems.append(
            "missing playbook: add one line 'Playbook: playbooks/<name>.md', where <name> is one of: " + names
        )
    elif len(values) > 1:
        problems.append("more than one Playbook line: keep one")
    else:
        candidate = playbook_stem(values[0])
        if candidate in playbooks:
            stem = candidate
        else:
            problems.append(f"unknown playbook '{values[0]}': expected one of: {names}")
    problems.extend(mode_problems(text, stem))
    return problems


def command_check_brief(args):
    path = Path(args.brief)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RolesError(f"{path}: brief not found") from error
    except UnicodeDecodeError as error:
        raise RolesError(f"{path}: brief is not valid UTF-8") from error
    except OSError as error:
        raise RolesError(f"{path}: cannot read brief: {error.strerror}") from error
    persona_body, playbooks = load_brief_rules()
    problems = brief_problems(text, persona_body, playbooks)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    stem = playbook_stem(playbook_values(text)[0])
    print(f"ok playbooks/{stem}.md")
    return 0


__all__ = [
    "persona_path",
    "playbook_stems",
    "load_brief_rules",
    "playbook_values",
    "playbook_stem",
    "brief_problems",
    "command_check_brief",
]
