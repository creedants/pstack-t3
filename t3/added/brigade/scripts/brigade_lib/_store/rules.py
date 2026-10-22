import re
from brigade_lib.compact import refuse_setting
from brigade_lib.shared import plural

ROLES = ("worker", "reviewer", "coordinator")
CAP_KEY = "houseRulesCap"
TAG = re.compile(r"\[(" + "|".join(ROLES) + r")\](?: )?")


def _split(text):
    """The text's rules, each a list of its lines, after outer whitespace is stripped."""
    rules, current = [], []
    for line in text.strip().split("\n"):
        if line[:1] not in ("", " ", "\t") and current:
            rules.append(current)
            current = []
        current.append(line)
    rules.append(current)
    return rules


def _by_role(text):
    """Each role's rendered house rules, from one pass over the text.

    A rule tagged for one role is left out of every other role's text. In that role's text it loses the
    tag and at most one space after it. An untagged rule is in every role's text unchanged.
    """
    kept = {role: [] for role in ROLES}
    for lines in _split(text):
        tag = TAG.search(lines[0])
        if tag:
            lines = [TAG.sub("", lines[0], count=1), *lines[1:]]
        for role in ROLES:
            if tag is None or tag.group(1) == role:
                kept[role].append("\n".join(lines))
    return {role: "\n".join(lines) for role, lines in kept.items()}


def for_role(role, text):
    """The house rules a brief for role carries."""
    return _by_role(text)[role]


def role_sizes(text):
    """Each role's rendered house rules byte count, in ROLES order."""
    rendered = _by_role(text)
    return {role: len(rendered[role].encode("utf-8")) for role in ROLES}


def cap_of(meta):
    """The store's houseRulesCap, or None when it records none.

    A present value that is a boolean, is not an integer, or is under 1 is refused.
    """
    if CAP_KEY not in meta:
        return None
    value = meta[CAP_KEY]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        refuse_setting(CAP_KEY, value, "it is not a whole number of 1 or more")
    return value


def breach_lines(cap, text):
    """One line per role whose house rules are over cap bytes, in ROLES order."""
    sizes = role_sizes(text)
    return tuple(f"house rules for {role} are {plural(sizes[role], 'byte')}, "
                 f"over the houseRulesCap of {plural(cap, 'byte')}"
                 for role in ROLES if sizes[role] > cap)
