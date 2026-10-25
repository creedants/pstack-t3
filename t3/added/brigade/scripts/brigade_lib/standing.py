"""The worker brief's two rule sections."""
import re
from dataclasses import dataclass
from brigade_lib._store import rules
from brigade_lib.shared import BrigadeError, same_text
from brigade_lib.store import SCRATCH_KEY, SCRATCH_REFUSAL, scratch_parent_text


@dataclass(frozen=True)
class _Standing:
    """One rule the brief pastes verbatim, with the anchors that show the coordinator already stated it."""
    text: str
    anchors: tuple


# The standing rules, in the order the brief prints them.
_RULES = (
    _Standing(
        "Save the full output of any failed test run to a file under <reports>/ before you rerun it, "
        "and name that file in the report.",
        ("<reports>/", "failed test run", "before you rerun", "Save the full output"),
    ),
    _Standing(
        "Make every scratch directory, archive copy, and saved output with `mktemp -d -p <scratch>`, never "
        "directly under `/tmp`. Delete that directory when you are done. A test suite's own temporary files stay "
        "where the tests put them. Do not set TMPDIR.",
        ("mktemp -d -p <scratch>",),
    ),
)
STANDING_PLACEHOLDER = re.compile(r"<([a-z]+)>")
_PATH_CHAR = r"(?![A-Za-z0-9_.\-~])"


@dataclass(frozen=True)
class Sections:
    """The worker brief's rule sections.

    rules is the STANDING RULES bullets. An empty rules means the brief omits the heading. orders is the worker's
    STANDING ORDERS text.
    """
    rules: tuple
    orders: str


def sections(restaurant, context, verify):
    """Read house-rules.md once, then resolve the two sections the brief prints."""
    house = rules.house_rules(restaurant.dir / "house-rules.md")
    try:
        scratch = scratch_parent_text(restaurant.meta.get(SCRATCH_KEY))
    except BrigadeError:
        raw = restaurant.meta.get(SCRATCH_KEY)
        raise BrigadeError(f"restaurant.json {SCRATCH_KEY} {raw!r} is invalid: {SCRATCH_REFUSAL}") from None
    values = {"reports": str(restaurant.dir / "reports"), "scratch": scratch}
    seen = same_text(" ".join(context) + " " + verify)
    printed = [line for line, anchors, scoped in _built_ins(values) if not _stated(anchors, seen, scoped)]
    return Sections(tuple(f"- {line}" for line in printed), rules.for_role("worker", house))


def _built_ins(values):
    """The built-in rules, filled in and checked, as (text, anchors, whether an anchor is scoped)."""
    built = []
    for rule in _RULES:
        if any(not values.get(name) for name in STANDING_PLACEHOLDER.findall(rule.text)):
            continue
        text = _fill_standing(rule.text, values)
        anchors = tuple(_fill_standing(anchor, values) for anchor in rule.anchors)
        for anchor in anchors:
            if anchor not in text:
                raise BrigadeError(f"standing rule anchor {anchor!r} is not in {text!r}")
        built.append((text, anchors, "<scratch>" in rule.text))
    return built


def _stated(anchors, seen, scoped):
    """True when every anchor, normalized by same_text, is in seen. A scoped anchor must not be followed by a path character."""
    for anchor in anchors:
        needle = same_text(anchor)
        if scoped:
            if re.search(re.escape(needle) + _PATH_CHAR, seen) is None:
                return False
        elif needle not in seen:
            return False
    return True


def _fill_standing(text, values):
    return STANDING_PLACEHOLDER.sub(lambda match: values[match.group(1)], text)
