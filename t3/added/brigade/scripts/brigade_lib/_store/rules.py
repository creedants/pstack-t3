import re
from dataclasses import dataclass
from brigade_lib.compact import refuse_setting
from brigade_lib.shared import BrigadeError, plural

ROLES = ("worker", "reviewer", "coordinator")
RECORDED = "standing rule"
CAP_KEY = "houseRulesCap"
TAG = re.compile(r"\[(" + "|".join((*ROLES, RECORDED)) + r")\](?: )?")


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


@dataclass(frozen=True)
class HouseRules:
    """One house-rules.md text, parsed once.

    orders maps each of ROLES to that role's STANDING ORDERS text. recorded holds the worker's STANDING RULES
    lines in file order, with the tag and at most one space after it removed, trailing whitespace stripped, and
    never empty. No recorded rule is in any orders text.
    """
    orders: dict
    recorded: tuple


def parse(text):
    """The house rules text, with each rule in the orders of the roles its tag allows, or in recorded.

    A rule is never in both. A recorded block that is empty after the tag is removed is dropped.
    """
    kept = {role: [] for role in ROLES}
    recorded = []
    for lines in _split(text):
        tag = TAG.search(lines[0])
        if tag:
            lines = [TAG.sub("", lines[0], count=1), *lines[1:]]
        if tag is not None and tag.group(1) == RECORDED:
            block = "\n".join(lines).rstrip()
            if block:
                recorded.append(block)
        for role in ROLES:
            if tag is None or tag.group(1) == role:
                kept[role].append("\n".join(lines))
    return HouseRules({role: "\n".join(lines) for role, lines in kept.items()}, tuple(recorded))


def bullet(text):
    """The rendered STANDING RULES line for text. The renderer and the sizer both use it."""
    return "- " + text


def for_role(role, text):
    """The house rules a brief for role carries."""
    return parse(text).orders[role]


def house_rules(path):
    """The house rules the file holds. A missing file is refused by name, and no brief is written."""
    try:
        return path.read_text()
    except FileNotFoundError:
        raise BrigadeError(f"{path} is missing; no brief was written") from None


def role_sizes(text):
    """Each role's rendered house rules byte count, in ROLES order.

    The worker's count includes each recorded line once, as its bullet, with no newline.
    """
    parsed = parse(text)
    sizes = {role: len(parsed.orders[role].encode("utf-8")) for role in ROLES}
    sizes["worker"] += sum(len(bullet(line).encode("utf-8")) for line in parsed.recorded)
    return sizes


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
